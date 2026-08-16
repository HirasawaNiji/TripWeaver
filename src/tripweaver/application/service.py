"""End-to-end application service for the phase-one vertical slice."""

from __future__ import annotations

from tripweaver.domain.models import DataStatus, PlanResult, TripRequest
from tripweaver.fixtures.catalog import FixtureCatalog
from tripweaver.llm.constraint_parser import DeterministicConstraintParser
from tripweaver.planner.catalog import PlanningCatalog
from tripweaver.planner.engine import DeterministicPlanner
from tripweaver.validator.service import ItineraryValidator


class TripPlanningService:
    """Orchestrate parsing, fixture queries, planning, and independent validation."""

    def __init__(
        self,
        *,
        parser: DeterministicConstraintParser | None = None,
        catalog: PlanningCatalog | None = None,
        validator: ItineraryValidator | None = None,
    ) -> None:
        self._parser = parser or DeterministicConstraintParser()
        self._catalog = catalog or FixtureCatalog()
        self._planner = DeterministicPlanner(self._catalog)
        self._validator = validator or ItineraryValidator()

    @property
    def catalog(self) -> PlanningCatalog:
        """Expose the read-only fixture catalog for independent verification."""

        return self._catalog

    def plan_text(self, text: str) -> PlanResult:
        return self.plan(self._parser.parse(text))

    def plan(self, request: TripRequest) -> PlanResult:
        itinerary, places = self._planner.plan(request)
        report = self._validator.validate(request, itinerary, places)
        warnings = (
            "当前结果全部来自 Fixture/估算数据，不代表实时班次、票价、营业时间或房价。",
            "阶段一仅用于架构演示和自动化测试，不提供登录、抢票、预订或下单能力。",
        )
        return PlanResult(
            request=request,
            itinerary=itinerary,
            validation=report,
            data_mode=DataStatus.FIXTURE,
            warnings=warnings,
        )
