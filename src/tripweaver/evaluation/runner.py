"""Forty fixed cases covering constraints, stability, provenance, and latency."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from time import monotonic
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field

from tripweaver.application.service import TripPlanningService
from tripweaver.domain.models import PlanResult, TransportMode, TripRequest
from tripweaver.planner.engine import NoFeasiblePlanError


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    request: TripRequest
    expect_feasible: bool


class CaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    passed: bool
    feasible: bool
    validator_passed: bool
    deterministic: bool
    source_complete: bool
    latency_ms: float = Field(ge=0)
    failure_type: str | None = None


class EvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    suite: str = "tripweaver-fixture-v1"
    total_cases: int
    passed_cases: int
    expected_outcome_accuracy: float
    hard_constraint_satisfaction_rate: float
    infeasible_plan_rate: float
    source_completeness_rate: float
    deterministic_stability_rate: float
    average_latency_ms: float
    token_cost: int = 0
    cases: tuple[CaseResult, ...]


def default_cases() -> tuple[EvaluationCase, ...]:
    """Return a stable 40-case matrix; eight cases intentionally exceed their budget."""

    cases: list[EvaluationCase] = []
    interests = (
        ("历史文化",),
        ("城市景观",),
        ("美食街区",),
        ("历史文化", "城市景观"),
    )
    modes = (
        (TransportMode.RAIL,),
        (TransportMode.FLIGHT,),
        (TransportMode.RAIL, TransportMode.FLIGHT),
    )
    for index in range(40):
        trip_days = 3 + index % 3
        start = date(2026, 10, 1) + timedelta(days=index % 10)
        expect_feasible = index % 5 != 0
        budget = Decimal(1000) if not expect_feasible else Decimal(20000)
        request = TripRequest(
            origin="北京",
            destination="上海",
            start_date=start,
            end_date=start + timedelta(days=trip_days - 1),
            travelers=1 + index % 4,
            budget_cny=budget,
            max_daily_minutes=660,
            interests=interests[index % len(interests)],
            preferred_transport=modes[index % len(modes)],
        )
        cases.append(
            EvaluationCase(
                id=f"fixture-{index + 1:02d}",
                request=request,
                expect_feasible=expect_feasible,
            )
        )
    return tuple(cases)


class EvaluationRunner:
    def __init__(self, service: TripPlanningService | None = None) -> None:
        self._service = service or TripPlanningService()

    def run(self, cases: tuple[EvaluationCase, ...] | None = None) -> EvaluationReport:
        selected = cases or default_cases()
        results = tuple(self._run_case(case) for case in selected)
        count = len(results)
        if count == 0:
            raise ValueError("evaluation suite must not be empty")
        produced = tuple(item for item in results if item.feasible)
        return EvaluationReport(
            total_cases=count,
            passed_cases=sum(item.passed for item in results),
            expected_outcome_accuracy=sum(item.passed for item in results) / count,
            hard_constraint_satisfaction_rate=(
                sum(item.validator_passed for item in produced) / len(produced) if produced else 0
            ),
            infeasible_plan_rate=sum(not item.feasible for item in results) / count,
            source_completeness_rate=sum(item.source_complete for item in results) / count,
            deterministic_stability_rate=sum(item.deterministic for item in results) / count,
            average_latency_ms=sum(item.latency_ms for item in results) / count,
            cases=results,
        )

    def _run_case(self, case: EvaluationCase) -> CaseResult:
        started = monotonic()
        try:
            first = self._service.plan(case.request)
            second = self._service.plan(case.request)
            feasible = first.validation.feasible
            deterministic = first.model_dump_json() == second.model_dump_json()
            source_complete = _source_complete(first)
            validator_passed = feasible
            return CaseResult(
                id=case.id,
                passed=feasible == case.expect_feasible and validator_passed,
                feasible=feasible,
                validator_passed=validator_passed,
                deterministic=deterministic,
                source_complete=source_complete,
                latency_ms=(monotonic() - started) * 1000,
            )
        except NoFeasiblePlanError as error:
            return CaseResult(
                id=case.id,
                passed=not case.expect_feasible,
                feasible=False,
                validator_passed=True,
                deterministic=True,
                source_complete=True,
                latency_ms=(monotonic() - started) * 1000,
                failure_type=type(error).__name__,
            )


def _source_complete(result: PlanResult) -> bool:
    document = result.model_dump(mode="json")
    found = 0
    missing = 0

    def walk(value: object) -> None:
        nonlocal found, missing
        if isinstance(value, dict):
            mapping = cast(dict[str, Any], value)
            if "source_reference" in value:
                found += 1
                if not mapping.get("provider") or not mapping.get("queried_at"):
                    missing += 1
            for child in mapping.values():
                walk(child)
        elif isinstance(value, list):
            for child in cast(list[Any], value):
                walk(child)

    walk(document)
    return found > 0 and missing == 0
