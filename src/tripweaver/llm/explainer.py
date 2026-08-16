"""Grounded LLM narration over an already validated PlanResult."""

from __future__ import annotations

import json
from time import monotonic

from openai import OpenAI

from tripweaver.agent import GroundedExplanation, explain_deterministically
from tripweaver.application.hybrid_service import HybridPlanResult
from tripweaver.config import DeepSeekSettings
from tripweaver.domain.models import DomainModel, ModelCallMetadata


class ExplanationResult(DomainModel):
    explanation: GroundedExplanation
    metadata: ModelCallMetadata


class PlanExplainer:
    def __init__(
        self, settings: DeepSeekSettings, *, client: OpenAI | None = None
    ) -> None:
        self._settings = settings
        self._client = client or (
            OpenAI(api_key=settings.api_key, base_url=settings.base_url)
            if settings.enabled
            else None
        )

    def explain(self, result: HybridPlanResult) -> ExplanationResult:
        fallback = explain_deterministically(result)
        if not self._settings.enabled or self._client is None:
            return ExplanationResult(
                explanation=fallback,
                metadata=ModelCallMetadata(
                    provider="tripweaver", model="deterministic-v1", mode="DETERMINISTIC"
                ),
            )
        started = monotonic()
        try:
            response = self._client.chat.completions.create(
                model=self._settings.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Explain this validated travel plan in concise Chinese. Use only supplied "
                            "facts. Preserve exact transport labels, lodging name, prices, dates and place "
                            "names. Do not add bookings, availability or recommendations not in the JSON. "
                            "Return only one JSON object matching this JSON Schema: "
                            + json.dumps(
                                GroundedExplanation.model_json_schema(), ensure_ascii=False
                            )
                        ),
                    },
                    {"role": "user", "content": result.model_dump_json()},
                ],
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            if not content:
                raise ValueError("DeepSeek returned no structured explanation")
            explanation = GroundedExplanation.model_validate_json(content)
            if not self._is_grounded(result, explanation):
                raise ValueError("explanation failed grounding checks")
            usage = response.usage
            return ExplanationResult(
                explanation=explanation,
                metadata=ModelCallMetadata(
                    provider="deepseek", model=self._settings.model, mode="LLM",
                    input_tokens=usage.prompt_tokens if usage else 0,
                    output_tokens=usage.completion_tokens if usage else 0,
                    latency_ms=(monotonic() - started) * 1000,
                ),
            )
        except Exception as error:  # noqa: BLE001 - narration must always have safe fallback
            return ExplanationResult(
                explanation=fallback,
                metadata=ModelCallMetadata(
                    provider="deepseek", model=self._settings.model, mode="FALLBACK",
                    latency_ms=(monotonic() - started) * 1000,
                    fallback_used=True, error_type=type(error).__name__,
                ),
            )

    @staticmethod
    def _is_grounded(
        result: HybridPlanResult, explanation: GroundedExplanation
    ) -> bool:
        itinerary = result.plan.itinerary
        if str(itinerary.budget.total_cny) not in explanation.budget_statement:
            return False
        if itinerary.outbound.label not in explanation.transport_reason:
            return False
        if itinerary.inbound.label not in explanation.transport_reason:
            return False
        if itinerary.lodging_area.name not in explanation.lodging_reason:
            return False
        outline = " ".join(explanation.daily_outline)
        return all(
            visit.place_name in outline for day in itinerary.days for visit in day.visits
        )
