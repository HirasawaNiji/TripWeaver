from __future__ import annotations

import unittest
from datetime import date, timedelta
from decimal import Decimal

from tripweaver.application.alternatives_service import AlternativeTripPlanningService
from tripweaver.domain.cities import CITY_REGISTRY, canonical_city_name
from tripweaver.domain.models import PlanningOverrides, TripRequest
from tripweaver.fixtures.catalog import FixtureCatalog
from tripweaver.providers.aviation import CITY_IATA_CODES

REQUEST = (
    "从广州市去成都玩4天，2026-10-01出发，2个人，预算10000元，"
    "喜欢历史文化和美食街区，高铁或飞机都可以"
)


class MultiCityFoundationTests(unittest.TestCase):
    def test_registry_covers_ten_cities_and_aviation_codes(self) -> None:
        self.assertEqual(len(CITY_REGISTRY), 10)
        self.assertEqual(canonical_city_name("广州市"), "广州")
        self.assertEqual(CITY_IATA_CODES["成都"], "CTU")
        self.assertEqual(CITY_IATA_CODES["重庆市"], "CKG")

    def test_each_city_has_six_fixture_seeds(self) -> None:
        catalog = FixtureCatalog()

        self.assertTrue(all(len(catalog.places(city.name)) == 6 for city in CITY_REGISTRY))

    def test_all_ninety_ordered_city_pairs_have_transport_candidates(self) -> None:
        catalog = FixtureCatalog()
        start = date(2026, 10, 1)
        checked = 0
        for origin in CITY_REGISTRY:
            for destination in CITY_REGISTRY:
                if origin == destination:
                    continue
                request = TripRequest(
                    origin=origin.name,
                    destination=destination.name,
                    start_date=start,
                    end_date=start + timedelta(days=3),
                    travelers=2,
                    budget_cny=Decimal(20000),
                )
                self.assertEqual(len(catalog.transport_options(request)), 4)
                checked += 1
        self.assertEqual(checked, 90)

    def test_generates_three_valid_objective_alternatives(self) -> None:
        result = AlternativeTripPlanningService().plan_text(REQUEST)

        self.assertEqual(result.request.origin, "广州")
        self.assertEqual(len(result.alternatives), 3)
        self.assertEqual(len({plan.itinerary.id for plan in result.alternatives}), 3)
        self.assertTrue(all(plan.validation.feasible for plan in result.alternatives))
        self.assertGreaterEqual(
            len(
                {
                    (plan.itinerary.outbound.mode, plan.itinerary.lodging_area.id)
                    for plan in result.alternatives
                }
            ),
            2,
        )

    def test_overrides_preserve_transport_and_limit_lodging(self) -> None:
        baseline = AlternativeTripPlanningService().plan_text(REQUEST)
        chosen = baseline.alternatives[1].itinerary
        overrides = PlanningOverrides(
            fixed_outbound_id=chosen.outbound.id,
            fixed_inbound_id=chosen.inbound.id,
            max_nightly_price_cny=Decimal(450),
            excluded_place_ids=("ctu-place-06",),
        )

        replanned = AlternativeTripPlanningService().plan_text(REQUEST, overrides=overrides)

        for plan in replanned.alternatives:
            self.assertEqual(plan.itinerary.outbound.id, chosen.outbound.id)
            self.assertEqual(plan.itinerary.inbound.id, chosen.inbound.id)
            self.assertLessEqual(
                plan.itinerary.lodging_area.nightly_price_estimate_cny, Decimal(450)
            )
            self.assertNotIn(
                "ctu-place-06",
                {visit.place_id for day in plan.itinerary.days for visit in day.visits},
            )


if __name__ == "__main__":
    unittest.main()
