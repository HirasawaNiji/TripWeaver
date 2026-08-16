from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tripweaver.domain.models import DataStatus, GeoPoint, LodgingArea, Place, SourceMetadata
from tripweaver.fixtures.catalog import FixtureCatalog
from tripweaver.planner.live_snapshot import AmapPlanningSnapshotBuilder
from tripweaver.providers.amap import (
    AmapPlaceDetail,
    AmapPlaceSummary,
    AmapRoute,
    AmapWeather,
)


def _source() -> SourceMetadata:
    now = datetime(2026, 8, 16, tzinfo=UTC)
    return SourceMetadata(
        provider="amap",
        status=DataStatus.LIVE,
        queried_at=now,
        expires_at=now + timedelta(hours=1),
        source_reference="mcp://amap/hotel?trace=test",
        confidence=1,
    )


class _HotelProvider:
    async def search_places(
        self,
        keywords: str,
        *,
        city: str | None = None,
        city_limit: bool = True,
        limit: int = 10,
    ) -> tuple[AmapPlaceSummary, ...]:
        del city, city_limit, limit
        if keywords != "酒店":
            return ()
        return tuple(
            AmapPlaceSummary(
                id=f"hotel-{index}",
                name=f"测试酒店{index}",
                address="上海市黄浦区",
                typecode="100100",
                source=_source(),
            )
            for index in range(3)
        )

    async def place_detail(self, poi_id: str) -> AmapPlaceDetail:
        index = int(poi_id.rsplit("-", 1)[1])
        return AmapPlaceDetail(
            id=poi_id,
            name=f"测试酒店{index}",
            address="上海市黄浦区",
            location=GeoPoint(latitude=31.23 + index * 0.001, longitude=121.48),
            category="住宿服务",
            rating=4.2 + index * 0.1,
            source=_source(),
        )

    async def weather(self, city: str) -> AmapWeather:
        raise NotImplementedError(city)

    async def transit_route(
        self,
        origin: GeoPoint,
        destination: GeoPoint,
        *,
        origin_city: str,
        destination_city: str | None = None,
    ) -> AmapRoute:
        del origin, destination, origin_city, destination_city
        raise NotImplementedError


class _TestSnapshotBuilder(AmapPlanningSnapshotBuilder):
    async def build_lodgings(
        self, city: str, places: tuple[Place, ...]
    ) -> tuple[tuple[LodgingArea, ...], tuple[str, ...]]:
        return await self._build_lodging_areas(city, places)


class LodgingSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_hotel_locations_keep_price_non_live(self) -> None:
        fixture = FixtureCatalog()
        builder = _TestSnapshotBuilder(
            _HotelProvider(), fixture=fixture, nightly_price_override_cny=Decimal(499)
        )
        places = fixture.places("上海")[:4]

        areas, warnings = await builder.build_lodgings("上海", places)

        self.assertEqual(len(areas), 3)
        self.assertTrue(all(area.candidate_hotel_name for area in areas))
        self.assertTrue(all(area.price_basis == "USER_SUPPLIED" for area in areas))
        self.assertTrue(all(area.source.status == DataStatus.ESTIMATED for area in areas))
        self.assertTrue(any("OTA" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()
