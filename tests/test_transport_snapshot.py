from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from decimal import Decimal

from tripweaver.domain.models import (
    DataStatus,
    SourceMetadata,
    TransportLeg,
    TransportMode,
    TransportOption,
    TripRequest,
)
from tripweaver.fixtures.catalog import FixtureCatalog
from tripweaver.planner.engine import DeterministicPlanner
from tripweaver.planner.live_snapshot import FrozenPlanningCatalog
from tripweaver.planner.transport_snapshot import RailwaySnapshotEnricher
from tripweaver.providers.railway import RailwayProviderError

REQUEST = TripRequest(
    origin="北京",
    destination="上海",
    start_date=date(2026, 8, 20),
    end_date=date(2026, 8, 22),
    travelers=2,
    budget_cny=Decimal(5000),
)


def _live_option(leg: TransportLeg) -> TransportOption:
    outbound = leg == TransportLeg.OUTBOUND
    travel_date = REQUEST.start_date if outbound else REQUEST.end_date
    return TransportOption(
        id=f"live-{leg.value.lower()}",
        leg=leg,
        mode=TransportMode.RAIL,
        label="G1 二等座（有票）",
        origin=REQUEST.origin if outbound else REQUEST.destination,
        destination=REQUEST.destination if outbound else REQUEST.origin,
        depart_at=datetime.combine(travel_date, datetime.min.time()).replace(hour=7),
        arrive_at=datetime.combine(travel_date, datetime.min.time()).replace(hour=11, minute=35),
        price_per_person_cny=Decimal(553),
        source=SourceMetadata(
            provider="railway_12306",
            status=DataStatus.LIVE,
            queried_at=datetime(2026, 8, 16, tzinfo=UTC),
            source_reference="mcp://railway_12306/get-tickets?trace=test",
            confidence=0.9,
        ),
    )


class _PartialRailwayProvider:
    async def transport_options(
        self,
        origin: str,
        destination: str,
        travel_date: date,
        leg: TransportLeg,
        *,
        limit: int | None = None,
    ) -> tuple[TransportOption, ...]:
        del origin, destination, travel_date, limit
        return (_live_option(leg),) if leg == TransportLeg.OUTBOUND else ()


class _BrokenRailwayProvider(_PartialRailwayProvider):
    async def transport_options(
        self,
        origin: str,
        destination: str,
        travel_date: date,
        leg: TransportLeg,
        *,
        limit: int | None = None,
    ) -> tuple[TransportOption, ...]:
        del origin, destination, travel_date, leg, limit
        raise RailwayProviderError("private upstream detail")


def _catalog() -> FrozenPlanningCatalog:
    fixture = FixtureCatalog()
    return FrozenPlanningCatalog(
        fixture=fixture,
        destination=REQUEST.destination,
        places=fixture.places(REQUEST.destination),
        routes={},
    )


class RailwaySnapshotTests(unittest.IsolatedAsyncioTestCase):
    def test_late_arrival_is_rejected_before_generalized_cost_selection(self) -> None:
        baseline = _live_option(TransportLeg.OUTBOUND)
        late = baseline.model_copy(
            update={
                "depart_at": baseline.depart_at.replace(hour=19, minute=0),
                "arrive_at": baseline.arrive_at.replace(hour=23, minute=35),
            }
        )

        self.assertFalse(
            DeterministicPlanner.transport_window_feasible(
                late,
                REQUEST,
                TransportLeg.OUTBOUND,
            )
        )

    async def test_replaces_fixture_rail_only_for_live_leg(self) -> None:
        snapshot = await RailwaySnapshotEnricher(_PartialRailwayProvider()).enrich(
            REQUEST,
            _catalog(),
        )
        options = snapshot.catalog.transport_options(REQUEST)

        self.assertEqual(snapshot.live_legs, (TransportLeg.OUTBOUND,))
        self.assertEqual(snapshot.fallback_legs, (TransportLeg.RETURN,))
        self.assertIn("live-outbound", {option.id for option in options})
        self.assertNotIn("TW-R-OUT-01", {option.id for option in options})
        self.assertIn("TW-R-IN-01", {option.id for option in options})
        self.assertTrue(any("返程" in warning for warning in snapshot.warnings))

    async def test_failure_falls_back_without_leaking_upstream_detail(self) -> None:
        snapshot = await RailwaySnapshotEnricher(_BrokenRailwayProvider()).enrich(
            REQUEST,
            _catalog(),
        )

        self.assertEqual(snapshot.live_options, ())
        self.assertEqual(
            snapshot.fallback_legs,
            (TransportLeg.OUTBOUND, TransportLeg.RETURN),
        )
        self.assertNotIn("private upstream detail", " ".join(snapshot.warnings))


if __name__ == "__main__":
    unittest.main()
