from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import cast

from openai import OpenAI

from tripweaver.config import DeepSeekSettings
from tripweaver.conversation.models import RevisionIntent
from tripweaver.domain.models import DomainModel, PlanningObjective, TransportMode
from tripweaver.llm.runtime import (
    DeepSeekRequestInterpreter,
    DeepSeekRevisionInterpreter,
    RequestExtraction,
)


class _FakeCompletions:
    def __init__(self, parsed: DomainModel | None, *, fail: bool = False) -> None:
        self._parsed = parsed
        self._fail = fail

    def parse(self, **_: object) -> object:
        if self._fail:
            raise ConnectionError("provider unavailable")
        assert self._parsed is not None
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(
                content=self._parsed.model_dump_json()
            ))],
            usage=SimpleNamespace(prompt_tokens=41, completion_tokens=17),
        )

    create = parse


class _FakeClient:
    def __init__(self, parsed: DomainModel | None, *, fail: bool = False) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(parsed, fail=fail))


def settings() -> DeepSeekSettings:
    return DeepSeekSettings(
        api_key="test-only",
        model="deepseek-test",
        base_url="https://api.deepseek.com",
        enabled=True,
    )


def test_deepseek_request_interpreter_returns_validated_domain_request() -> None:
    extraction = RequestExtraction(
        complete=True,
        questions=(),
        origin="广州",
        destination="成都",
        start_date=date(2026, 10, 1),
        trip_days=4,
        travelers=2,
        budget_cny=Decimal(9000),
        interests=("历史文化", "美食街区"),
    )
    client = cast(OpenAI, _FakeClient(extraction))
    result = DeepSeekRequestInterpreter(settings(), client=client).interpret(
        "国庆第一天从广州出发，去成都四天，两个人总预算九千"
    )
    assert result.request is not None
    assert result.request.destination == "成都"
    assert result.request.trip_days == 4
    assert result.metadata.mode == "LLM"
    assert result.metadata.input_tokens == 41
    assert result.metadata.provider == "deepseek"


def test_deepseek_request_interpreter_returns_clarifying_questions() -> None:
    extraction = RequestExtraction(
        complete=False,
        questions=("哪天出发？", "总预算是多少？"),
        origin="广州",
        destination="成都",
    )
    result = DeepSeekRequestInterpreter(
        settings(), client=cast(OpenAI, _FakeClient(extraction))
    ).interpret("想从广州去成都")
    assert result.request is None
    assert result.questions == ("哪天出发？", "总预算是多少？")


def test_deepseek_revision_interpreter_uses_strict_intent() -> None:
    parsed = RevisionIntent(
        objective=PlanningObjective.TIME,
        inbound_modes=(TransportMode.RAIL,),
        explanation="返程坐高铁，时间优先",
    )
    result = DeepSeekRevisionInterpreter(
        settings(), client=cast(OpenAI, _FakeClient(parsed))
    ).interpret("回来坐高铁吧，整体尽量快")
    assert result.intent.inbound_modes == (TransportMode.RAIL,)
    assert result.intent.objective == PlanningObjective.TIME
    assert result.metadata.output_tokens == 17


def test_deepseek_failure_falls_back_without_leaking_error_body() -> None:
    interpreter = DeepSeekRevisionInterpreter(
        settings(), client=cast(OpenAI, _FakeClient(None, fail=True))
    )
    result = interpreter.interpret("返程不要飞机")
    assert result.intent.inbound_modes == (TransportMode.RAIL,)
    assert result.metadata.fallback_used
    assert result.metadata.error_type == "ConnectionError"
