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
from tripweaver.planner.aviation_snapshot import AviationSnapshotEnricher
from tripweaver.planner.live_snapshot import FrozenPlanningCatalog
from tripweaver.providers.aviation import VariflightProviderError

REQUEST = TripRequest(
    origin="北京",
    destination="上海",
    start_date=date(2026, 8, 20),
    end_date=date(2026, 8, 22),
    travelers=2,
    budget_cny=Decimal(8000),
)


def _live_flight(leg: TransportLeg) -> TransportOption:
    outbound = leg == TransportLeg.OUTBOUND
    travel_date = REQUEST.start_date if outbound else REQUEST.end_date
    return TransportOption(
        id=f"live-flight-{leg.value.lower()}",
        leg=leg,
        mode=TransportMode.FLIGHT,
        label="MU5101 PEK→SHA 经济舱（余5）",
        origin=REQUEST.origin if outbound else REQUEST.destination,
        destination=REQUEST.destination if outbound else REQUEST.origin,
        depart_at=datetime.combine(travel_date, datetime.min.time()).replace(hour=8),
        arrive_at=datetime.combine(travel_date, datetime.min.time()).replace(hour=10, minute=15),
        price_per_person_cny=Decimal(650),
        source=SourceMetadata(
            provider="variflight",
            status=DataStatus.LIVE,
            queried_at=datetime(2026, 8, 16, tzinfo=UTC),
            source_reference="mcp://variflight/getFlightPriceByCities?trace=test",
            confidence=0.9,
        ),
    )


class _PartialAviationProvider:
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
        return (_live_flight(leg),) if leg == TransportLeg.RETURN else ()


class _BrokenAviationProvider(_PartialAviationProvider):
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
        raise VariflightProviderError("private upstream detail")


def _catalog() -> FrozenPlanningCatalog:
    fixture = FixtureCatalog()
    return FrozenPlanningCatalog(
        fixture=fixture,
        destination=REQUEST.destination,
        places=fixture.places(REQUEST.destination),
        routes={},
    )


class AviationSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_replaces_fixture_flight_only_for_live_leg(self) -> None:
        snapshot = await AviationSnapshotEnricher(_PartialAviationProvider()).enrich(
            REQUEST,
            _catalog(),
        )
        options = snapshot.catalog.transport_options(REQUEST)

        self.assertEqual(snapshot.live_legs, (TransportLeg.RETURN,))
        self.assertEqual(snapshot.fallback_legs, (TransportLeg.OUTBOUND,))
        self.assertIn("live-flight-return", {option.id for option in options})
        self.assertNotIn("TW-F-IN-01", {option.id for option in options})
        self.assertIn("TW-F-OUT-01", {option.id for option in options})
        self.assertTrue(any("去程" in warning for warning in snapshot.warnings))

    async def test_failure_falls_back_without_leaking_upstream_detail(self) -> None:
        snapshot = await AviationSnapshotEnricher(_BrokenAviationProvider()).enrich(
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
