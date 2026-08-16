"""Generate comparable portfolio alternatives from one frozen planning catalog."""

from __future__ import annotations

from tripweaver.domain.models import (
    DataStatus,
    DomainModel,
    PlanningObjective,
    PlanningOverrides,
    PlanResult,
    TripRequest,
)
from tripweaver.fixtures.catalog import FixtureCatalog
from tripweaver.llm.constraint_parser import DeterministicConstraintParser
from tripweaver.planner.catalog import PlanningCatalog
from tripweaver.planner.engine import DeterministicPlanner
from tripweaver.validator.service import ItineraryValidator


class AlternativeSet(DomainModel):
    request: TripRequest
    alternatives: tuple[PlanResult, ...]


class AlternativeTripPlanningService:
    """Build budget, balanced, and time alternatives without repeated data fetching."""

    OBJECTIVES = (
        PlanningObjective.BUDGET,
        PlanningObjective.BALANCED,
        PlanningObjective.TIME,
    )

    def __init__(
        self,
        *,
        catalog: PlanningCatalog | None = None,
        parser: DeterministicConstraintParser | None = None,
        validator: ItineraryValidator | None = None,
    ) -> None:
        self._catalog = catalog or FixtureCatalog()
        self._parser = parser or DeterministicConstraintParser()
        self._validator = validator or ItineraryValidator()

    def plan_text(self, text: str, *, overrides: PlanningOverrides | None = None) -> AlternativeSet:
        return self.plan(self._parser.parse(text), overrides=overrides)

    def plan(
        self,
        request: TripRequest,
        *,
        overrides: PlanningOverrides | None = None,
    ) -> AlternativeSet:
        results: list[PlanResult] = []
        for objective in self.OBJECTIVES:
            itinerary, places = DeterministicPlanner(
                self._catalog,
                objective=objective,
                overrides=overrides,
            ).plan(request)
            validation = self._validator.validate(request, itinerary, places)
            results.append(
                PlanResult(
                    request=request,
                    itinerary=itinerary,
                    validation=validation,
                    data_mode=DataStatus.FIXTURE,
                    warnings=(
                        f"当前为 {objective.value} 目标的多城市 Fixture 对比方案。",
                        "三种目标共享同一数据快照；后续对话可通过 PlanningOverrides 保留用户确认项。",
                    ),
                )
            )
        return AlternativeSet(request=request, alternatives=tuple(results))
