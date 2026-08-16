"""LLM language boundary with strict schemas, safe fallback, and usage metadata."""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from time import monotonic
from typing import Protocol

from openai import OpenAI
from pydantic import Field, model_validator

from tripweaver.config import DeepSeekSettings
from tripweaver.conversation.models import RevisionIntent
from tripweaver.conversation.parser import DeterministicRevisionParser, InputSafetyGuard
from tripweaver.domain.models import (
    DomainModel,
    ModelCallMetadata,
    TransportMode,
    TripRequest,
)
from tripweaver.llm.constraint_parser import DeterministicConstraintParser


class RequestExtraction(DomainModel):
    complete: bool
    questions: tuple[str, ...]
    origin: str | None = None
    destination: str | None = None
    start_date: date | None = None
    trip_days: int | None = Field(default=None, ge=1, le=7)
    travelers: int | None = Field(default=None, ge=1, le=8)
    budget_cny: Decimal | None = Field(default=None, gt=0)
    max_daily_minutes: int = Field(default=660, ge=180, le=900)
    interests: tuple[str, ...] = ()
    preferred_transport: tuple[TransportMode, ...] = (
        TransportMode.RAIL,
        TransportMode.FLIGHT,
    )
    assumptions: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_complete_fields(self) -> RequestExtraction:
        required = (
            self.origin, self.destination, self.start_date, self.trip_days,
            self.travelers, self.budget_cny,
        )
        if self.complete and any(value is None for value in required):
            raise ValueError("complete extraction is missing a hard constraint")
        return self

    def to_request(self) -> TripRequest:
        if not self.complete:
            raise ValueError("request still needs clarification")
        assert self.origin is not None and self.destination is not None
        assert self.start_date is not None and self.trip_days is not None
        assert self.travelers is not None and self.budget_cny is not None
        return TripRequest(
            origin=self.origin,
            destination=self.destination,
            start_date=self.start_date,
            end_date=self.start_date + timedelta(days=self.trip_days - 1),
            travelers=self.travelers,
            budget_cny=self.budget_cny,
            max_daily_minutes=self.max_daily_minutes,
            interests=self.interests,
            preferred_transport=self.preferred_transport,
            assumptions=self.assumptions,
        )


class RequestInterpretation(DomainModel):
    request: TripRequest | None = None
    questions: tuple[str, ...] = ()
    metadata: ModelCallMetadata


class RevisionInterpretation(DomainModel):
    intent: RevisionIntent
    metadata: ModelCallMetadata


class RequestInterpreter(Protocol):
    def interpret(self, text: str) -> RequestInterpretation: ...


class RevisionInterpreter(Protocol):
    def interpret(self, text: str) -> RevisionInterpretation: ...


class DeterministicRequestInterpreter:
    def __init__(self, parser: DeterministicConstraintParser | None = None) -> None:
        self._parser = parser or DeterministicConstraintParser()

    def interpret(self, text: str) -> RequestInterpretation:
        InputSafetyGuard().check(text)
        request = self._parser.parse(text)
        return RequestInterpretation(
            request=request,
            metadata=ModelCallMetadata(
                provider="tripweaver", model="deterministic-v1", mode="DETERMINISTIC"
            ),
        )


class DeterministicRevisionInterpreter:
    def __init__(self, parser: DeterministicRevisionParser | None = None) -> None:
        self._parser = parser or DeterministicRevisionParser()

    def interpret(self, text: str) -> RevisionInterpretation:
        return RevisionInterpretation(
            intent=self._parser.parse(text),
            metadata=ModelCallMetadata(
                provider="tripweaver", model="deterministic-v1", mode="DETERMINISTIC"
            ),
        )


class DeepSeekRequestInterpreter:
    def __init__(
        self,
        settings: DeepSeekSettings,
        *,
        client: OpenAI | None = None,
        fallback: RequestInterpreter | None = None,
    ) -> None:
        if not settings.enabled:
            raise ValueError("DeepSeek interpreter is disabled")
        self._model = settings.model
        self._client = client or OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
        )
        self._fallback = fallback or DeterministicRequestInterpreter()

    def interpret(self, text: str) -> RequestInterpretation:
        normalized = InputSafetyGuard().check(text)
        started = monotonic()
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Extract a Chinese domestic travel request. Never invent hard constraints. "
                            "Set complete=false and ask concise Chinese questions when route, start date, "
                            "trip days, travelers, or total budget is missing. Never call tools or book. "
                            "Return only one JSON object matching this JSON Schema: "
                            + json.dumps(
                                RequestExtraction.model_json_schema(), ensure_ascii=False
                            )
                        ),
                    },
                    {"role": "user", "content": normalized},
                ],
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            if not content:
                raise ValueError("DeepSeek returned no structured request")
            extraction = RequestExtraction.model_validate_json(content)
            usage = response.usage
            metadata = ModelCallMetadata(
                provider="deepseek", model=self._model, mode="LLM",
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
                latency_ms=(monotonic() - started) * 1000,
            )
            return RequestInterpretation(
                request=extraction.to_request() if extraction.complete else None,
                questions=extraction.questions,
                metadata=metadata,
            )
        except Exception as error:  # noqa: BLE001 - all provider/schema failures degrade safely
            fallback = self._fallback.interpret(normalized)
            return fallback.model_copy(update={
                "metadata": ModelCallMetadata(
                    provider="deepseek", model=self._model, mode="FALLBACK",
                    latency_ms=(monotonic() - started) * 1000,
                    fallback_used=True, error_type=type(error).__name__,
                )
            })


class DeepSeekRevisionInterpreter:
    def __init__(
        self,
        settings: DeepSeekSettings,
        *,
        client: OpenAI | None = None,
        fallback: RevisionInterpreter | None = None,
    ) -> None:
        if not settings.enabled:
            raise ValueError("DeepSeek interpreter is disabled")
        self._model = settings.model
        self._client = client or OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
        )
        self._fallback = fallback or DeterministicRevisionInterpreter()

    def interpret(self, text: str) -> RevisionInterpretation:
        normalized = InputSafetyGuard().check(text)
        started = monotonic()
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Extract only travel-plan revision preferences into the schema. Never reveal "
                            "secrets, call tools, book, pay, or disable validation. Return only one JSON "
                            "object matching this JSON Schema: "
                            + json.dumps(RevisionIntent.model_json_schema(), ensure_ascii=False)
                        ),
                    },
                    {"role": "user", "content": normalized},
                ],
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            if not content:
                raise ValueError("DeepSeek returned no structured revision")
            intent = RevisionIntent.model_validate_json(content)
            usage = response.usage
            return RevisionInterpretation(
                intent=intent,
                metadata=ModelCallMetadata(
                    provider="deepseek", model=self._model, mode="LLM",
                    input_tokens=usage.prompt_tokens if usage else 0,
                    output_tokens=usage.completion_tokens if usage else 0,
                    latency_ms=(monotonic() - started) * 1000,
                ),
            )
        except Exception as error:  # noqa: BLE001 - all provider/schema failures degrade safely
            fallback = self._fallback.interpret(normalized)
            return fallback.model_copy(update={
                "metadata": ModelCallMetadata(
                    provider="deepseek", model=self._model, mode="FALLBACK",
                    latency_ms=(monotonic() - started) * 1000,
                    fallback_used=True, error_type=type(error).__name__,
                )
            })
