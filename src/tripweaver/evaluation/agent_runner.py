"""Multi-turn language-boundary and local-replanning evaluation suite."""

from __future__ import annotations

from datetime import date, timedelta
from time import monotonic

from pydantic import Field

from tripweaver.conversation import ConversationPlanningService
from tripweaver.domain.cities import CITY_REGISTRY
from tripweaver.domain.models import DomainModel, ModelCallMetadata, PlanningObjective
from tripweaver.llm.runtime import (
    DeterministicRequestInterpreter,
    DeterministicRevisionInterpreter,
    RequestInterpreter,
    RevisionInterpreter,
)


class AgentEvaluationCase(DomainModel):
    id: str
    request_text: str
    revisions: tuple[str, ...]


class AgentCaseResult(DomainModel):
    id: str
    passed: bool
    request_structured: bool
    revision_intents_correct: bool
    schema_valid: bool
    hard_constraints_satisfied: bool
    replan_preserved: bool
    snapshot_reused: bool
    fallback_count: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    latency_ms: float = Field(ge=0)
    failure_type: str | None = None


class AgentEvaluationReport(DomainModel):
    suite: str = "tripweaver-agent-v3"
    total_cases: int
    passed_cases: int
    structured_request_success_rate: float
    revision_intent_accuracy: float
    schema_validation_rate: float
    hard_constraint_satisfaction_rate: float
    replan_preservation_rate: float
    snapshot_reuse_rate: float
    fallback_rate: float
    average_latency_ms: float
    total_input_tokens: int
    total_output_tokens: int
    cases: tuple[AgentCaseResult, ...]


def default_agent_cases() -> tuple[AgentEvaluationCase, ...]:
    """Return 40 reproducible two-turn conversations across registered cities."""

    cities = tuple(city.name for city in CITY_REGISTRY)
    cases: list[AgentEvaluationCase] = []
    for index in range(40):
        origin = cities[index % len(cities)]
        destination = cities[(index + 1 + index // len(cities)) % len(cities)]
        if destination == origin:
            destination = cities[(index + 2) % len(cities)]
        start = date(2026, 10, 1) + timedelta(days=index % 7)
        request_text = (
            f"从{origin}去{destination}玩4天，{start.isoformat()}出发，2个人，"
            "预算30000元，喜欢历史文化和美食，高铁或飞机都可以"
        )
        cases.append(
            AgentEvaluationCase(
                id=f"agent-v3-{index + 1:03d}",
                request_text=request_text,
                revisions=("时间优先", "第二天第一个景点换掉"),
            )
        )
    return tuple(cases)


class AgentEvaluationRunner:
    def __init__(
        self,
        *,
        request_interpreter: RequestInterpreter | None = None,
        revision_interpreter: RevisionInterpreter | None = None,
    ) -> None:
        self._request_interpreter = request_interpreter or DeterministicRequestInterpreter()
        self._revision_interpreter = revision_interpreter or DeterministicRevisionInterpreter()

    def run(
        self, cases: tuple[AgentEvaluationCase, ...] | None = None
    ) -> AgentEvaluationReport:
        selected = cases or default_agent_cases()
        if not selected:
            raise ValueError("agent evaluation suite must not be empty")
        results = tuple(self._run_case(case) for case in selected)
        calls = len(results) * 3
        return AgentEvaluationReport(
            total_cases=len(results),
            passed_cases=sum(result.passed for result in results),
            structured_request_success_rate=(
                sum(result.request_structured for result in results) / len(results)
            ),
            revision_intent_accuracy=(
                sum(result.revision_intents_correct for result in results) / len(results)
            ),
            schema_validation_rate=sum(result.schema_valid for result in results) / len(results),
            hard_constraint_satisfaction_rate=(
                sum(result.hard_constraints_satisfied for result in results) / len(results)
            ),
            replan_preservation_rate=(
                sum(result.replan_preserved for result in results) / len(results)
            ),
            snapshot_reuse_rate=sum(result.snapshot_reused for result in results) / len(results),
            fallback_rate=sum(result.fallback_count for result in results) / calls,
            average_latency_ms=sum(result.latency_ms for result in results) / len(results),
            total_input_tokens=sum(result.input_tokens for result in results),
            total_output_tokens=sum(result.output_tokens for result in results),
            cases=results,
        )

    def _run_case(self, case: AgentEvaluationCase) -> AgentCaseResult:
        started = monotonic()
        metadata: list[ModelCallMetadata] = []
        try:
            request_result = self._request_interpreter.interpret(case.request_text)
            metadata.append(request_result.metadata)
            if request_result.request is None:
                raise ValueError("complete evaluation request unexpectedly needs clarification")
            service = ConversationPlanningService()
            session = service.select(service.create(request_result.request).id, 2)
            before_fetches = session.data_fetch_count

            first_intent = self._revision_interpreter.interpret(case.revisions[0])
            metadata.append(first_intent.metadata)
            first_correct = first_intent.intent.objective == PlanningObjective.TIME
            session = service.revise_with_intent(session.id, first_intent.intent)
            service.set_locks(session.id, ("outbound", "inbound", "lodging"))

            second_intent = self._revision_interpreter.interpret(case.revisions[1])
            metadata.append(second_intent.metadata)
            second_correct = second_intent.intent.replace_day == 2
            session = service.revise_with_intent(session.id, second_intent.intent)
            assert session.selected_plan is not None and session.last_diff is not None
            preserved = set(session.last_diff.preserved_fields) >= {
                "outbound",
                "inbound",
                "lodging",
            }
            validator_passed = session.selected_plan.validation.feasible
            snapshot_reused = (
                session.data_fetch_count == before_fetches == 1
                and session.revision_count == 2
            )
            intents_correct = first_correct and second_correct
            fallback_count = sum(call.fallback_used for call in metadata)
            passed = validator_passed and preserved and snapshot_reused and intents_correct
            return AgentCaseResult(
                id=case.id,
                passed=passed,
                request_structured=True,
                revision_intents_correct=intents_correct,
                schema_valid=True,
                hard_constraints_satisfied=validator_passed,
                replan_preserved=preserved,
                snapshot_reused=snapshot_reused,
                fallback_count=fallback_count,
                input_tokens=sum(call.input_tokens for call in metadata),
                output_tokens=sum(call.output_tokens for call in metadata),
                latency_ms=(monotonic() - started) * 1000,
            )
        except Exception as error:  # noqa: BLE001 - evaluation records failures as data
            return AgentCaseResult(
                id=case.id,
                passed=False,
                request_structured=False,
                revision_intents_correct=False,
                schema_valid=False,
                hard_constraints_satisfied=False,
                replan_preserved=False,
                snapshot_reused=False,
                fallback_count=sum(call.fallback_used for call in metadata),
                input_tokens=sum(call.input_tokens for call in metadata),
                output_tokens=sum(call.output_tokens for call in metadata),
                latency_ms=(monotonic() - started) * 1000,
                failure_type=type(error).__name__,
            )
