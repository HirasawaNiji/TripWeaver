from __future__ import annotations

import unittest
from datetime import timedelta
from decimal import Decimal

from tripweaver.application.service import TripPlanningService
from tripweaver.domain.models import DataStatus, Severity
from tripweaver.fixtures.catalog import UnsupportedFixtureRouteError
from tripweaver.planner.engine import NoFeasiblePlanError
from tripweaver.validator.service import ItineraryValidator

DEMO = (
    "从北京去上海玩3天，2026-10-01出发，2个人，预算5000元，"
    "喜欢历史文化、城市夜景和美食街区，高铁或飞机都可以"
)
_MINUTE = timedelta(minutes=1)


class FixtureVerticalSliceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = TripPlanningService()

    def test_generates_a_feasible_three_day_plan(self) -> None:
        result = self.service.plan_text(DEMO)

        self.assertTrue(result.validation.feasible)
        self.assertEqual(result.data_mode, DataStatus.FIXTURE)
        self.assertEqual(len(result.itinerary.days), 3)
        self.assertEqual(
            sum(len(day.visits) for day in result.itinerary.days),
            6,
        )
        self.assertTrue(all(day.visits for day in result.itinerary.days))
        self.assertLessEqual(result.itinerary.budget.total_cny, result.request.budget_cny)
        self.assertTrue(
            all(issue.severity == Severity.WARNING for issue in result.validation.issues)
        )

    def test_output_is_deterministic(self) -> None:
        first = self.service.plan_text(DEMO).model_dump_json()
        second = self.service.plan_text(DEMO).model_dump_json()

        self.assertEqual(first, second)

    def test_every_external_fact_has_provenance(self) -> None:
        result = self.service.plan_text(DEMO)
        itinerary = result.itinerary
        sources = [
            itinerary.outbound.source,
            itinerary.inbound.source,
            itinerary.lodging_area.source,
            itinerary.budget.estimation_source,
        ]
        for day in itinerary.days:
            for visit in day.visits:
                sources.extend((visit.source, visit.route_from_previous.source))

        self.assertTrue(all(source.provider for source in sources))
        self.assertTrue(all(source.source_reference.startswith("fixture://") for source in sources))
        self.assertTrue(
            all(source.status in {DataStatus.FIXTURE, DataStatus.ESTIMATED} for source in sources)
        )

    def test_rejects_a_budget_that_cannot_cover_the_fixture_plan(self) -> None:
        with self.assertRaises(NoFeasiblePlanError):
            self.service.plan_text("从北京去上海玩3天，2026-10-01出发，2个人，预算1000元")

    def test_rejects_unsupported_city(self) -> None:
        with self.assertRaises(UnsupportedFixtureRouteError):
            self.service.plan_text("从北京去拉萨玩3天，2个人，预算5000元")

    def test_supports_a_second_city_pair(self) -> None:
        result = self.service.plan_text("从广州去成都玩4天，2026-10-01出发，2个人，预算10000元")

        self.assertTrue(result.validation.feasible)
        self.assertEqual(result.request.origin, "广州")
        self.assertEqual(result.request.destination, "成都")
        self.assertEqual(len(result.itinerary.days), 4)

    def test_validator_detects_tampered_budget(self) -> None:
        result = self.service.plan_text(DEMO)
        bad_budget = result.itinerary.budget.model_copy(update={"admission_cny": Decimal(0)})
        bad_itinerary = result.itinerary.model_copy(update={"budget": bad_budget})
        places = self.service.catalog.places(result.request.destination)

        report = ItineraryValidator().validate(result.request, bad_itinerary, places)

        self.assertFalse(report.feasible)
        self.assertIn("BUDGET_RECOMPUTE_MISMATCH", {issue.code for issue in report.issues})

    def test_validator_detects_missing_first_route_buffer(self) -> None:
        result = self.service.plan_text(DEMO)
        first_day = result.itinerary.days[0]
        first_visit = first_day.visits[0]
        bad_visit = first_visit.model_copy(
            update={
                "start_at": first_visit.start_at - first_visit.route_from_previous.minutes * _MINUTE
            }
        )
        bad_day = first_day.model_copy(update={"visits": (bad_visit, *first_day.visits[1:])})
        bad_itinerary = result.itinerary.model_copy(
            update={"days": (bad_day, *result.itinerary.days[1:])}
        )
        places = self.service.catalog.places(result.request.destination)

        report = ItineraryValidator().validate(result.request, bad_itinerary, places)

        self.assertFalse(report.feasible)
        self.assertIn("ROUTE_BUFFER_MISSING", {issue.code for issue in report.issues})


if __name__ == "__main__":
    unittest.main()
