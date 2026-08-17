from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta

from tripweaver.application.hybrid_service import HybridTripPlanningService
from tripweaver.conversation import HybridConversationPlanningService, SessionMode
from tripweaver.domain.models import DataStatus, GeoPoint, SourceMetadata
from tripweaver.llm.constraint_parser import DeterministicConstraintParser
from tripweaver.planner.live_snapshot import AmapPlanningSnapshotBuilder
from tripweaver.providers.amap import (
    AmapForecast,
    AmapPlaceDetail,
    AmapPlaceSummary,
    AmapProviderError,
    AmapRoute,
    AmapWeather,
)

DEMO = (
    "从北京去上海玩3天，2026-10-01出发，2个人，预算5000元，"
    "喜欢历史文化、城市夜景和美食街区，高铁或飞机都可以"
)
QUERY_TIME = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
LOCATIONS = {
    "豫园": GeoPoint(latitude=31.2271, longitude=121.4921),
    "南京路步行街": GeoPoint(latitude=31.2346, longitude=121.4750),
    "上海博物馆": GeoPoint(latitude=31.228231, longitude=121.47548),
    "外滩": GeoPoint(latitude=31.24, longitude=121.4904),
}


def _source(tool: str) -> SourceMetadata:
    return SourceMetadata(
        provider="amap",
        status=DataStatus.LIVE,
        queried_at=QUERY_TIME,
        expires_at=QUERY_TIME + timedelta(hours=1),
        source_reference=f"mcp://amap/{tool}?trace=test",
        confidence=1.0,
    )


class _StableMapProvider:
    async def search_places(
        self,
        keywords: str,
        *,
        city: str | None = None,
        city_limit: bool = True,
        limit: int = 10,
    ) -> tuple[AmapPlaceSummary, ...]:
        del city, city_limit, limit
        return (
            AmapPlaceSummary(
                id=f"poi-{keywords}",
                name=keywords,
                address=f"{keywords}测试地址",
                typecode="110000",
                source=_source("maps_text_search"),
            ),
        )

    async def place_detail(self, poi_id: str) -> AmapPlaceDetail:
        name = poi_id.removeprefix("poi-")
        return AmapPlaceDetail(
            id=poi_id,
            name=name,
            address=f"{name}测试地址",
            location=LOCATIONS[name],
            category="风景名胜",
            rating=4.8,
            opening_hours_text="09:00-17:00，节假日以现场公告为准",
            source=_source("maps_search_detail"),
        )

    async def weather(self, city: str) -> AmapWeather:
        return AmapWeather(
            city=f"{city}市",
            forecasts=(
                AmapForecast(
                    date=date(2026, 10, 1),
                    weekday=4,
                    day_weather="多云",
                    night_weather="晴",
                    day_temperature_c=26,
                    night_temperature_c=20,
                    day_wind="东",
                    night_wind="东",
                    day_wind_power="1-3",
                    night_wind_power="1-3",
                ),
            ),
            source=_source("maps_weather"),
        )

    async def transit_route(
        self,
        origin: GeoPoint,
        destination: GeoPoint,
        *,
        origin_city: str,
        destination_city: str | None = None,
    ) -> AmapRoute:
        del origin_city, destination_city
        return AmapRoute(
            mode="PUBLIC_TRANSIT",
            origin=origin,
            destination=destination,
            distance_meters=2400,
            duration_seconds=1200,
            walking_distance_meters=500,
            source=_source("maps_direction_transit_integrated"),
        )


class _UnavailableMapProvider(_StableMapProvider):
    async def search_places(
        self,
        keywords: str,
        *,
        city: str | None = None,
        city_limit: bool = True,
        limit: int = 10,
    ) -> tuple[AmapPlaceSummary, ...]:
        del keywords, city, city_limit, limit
        raise AmapProviderError("simulated outage with private details")


class _RouteUnavailableMapProvider(_StableMapProvider):
    async def transit_route(
        self,
        origin: GeoPoint,
        destination: GeoPoint,
        *,
        origin_city: str,
        destination_city: str | None = None,
    ) -> AmapRoute:
        del origin, destination, origin_city, destination_city
        raise AmapProviderError("simulated route outage")


class _OutOfRangeWeatherProvider(_StableMapProvider):
    async def weather(self, city: str) -> AmapWeather:
        weather = await super().weather(city)
        forecast = weather.forecasts[0].model_copy(update={"date": date(2026, 8, 16)})
        return weather.model_copy(update={"forecasts": (forecast,)})


class _CountingMapProvider(_StableMapProvider):
    def __init__(self) -> None:
        self.search_count = 0

    async def search_places(
        self,
        keywords: str,
        *,
        city: str | None = None,
        city_limit: bool = True,
        limit: int = 10,
    ) -> tuple[AmapPlaceSummary, ...]:
        self.search_count += 1
        return await super().search_places(
            keywords, city=city, city_limit=city_limit, limit=limit
        )


class HybridPlanningTests(unittest.IsolatedAsyncioTestCase):
    async def test_hybrid_conversation_revisions_do_not_refetch_providers(self) -> None:
        provider = _CountingMapProvider()
        hybrid = HybridTripPlanningService(AmapPlanningSnapshotBuilder(provider))
        request = DeterministicConstraintParser().parse(DEMO)
        conversations = HybridConversationPlanningService(hybrid)

        created = await conversations.create(request)
        self.assertEqual(created.mode, SessionMode.LIVE)
        self.assertIsNotNone(created.snapshot)
        self.assertTrue(created.snapshot.providers if created.snapshot else ())
        fetch_count = provider.search_count
        conversations.select(created.id, 2)
        revised = conversations.revise(created.id, "第二天第一个景点换掉")

        self.assertEqual(provider.search_count, fetch_count)
        self.assertEqual(revised.data_fetch_count, 1)
        self.assertEqual(revised.revision_count, 1)
        self.assertGreater(conversations.trace_summary(created.id).tool_calls, 0)

    async def test_three_alternatives_share_one_provider_snapshot(self) -> None:
        provider = _CountingMapProvider()
        service = HybridTripPlanningService(AmapPlanningSnapshotBuilder(provider))
        request = DeterministicConstraintParser().parse(DEMO)

        result = await service.plan_alternatives(request)

        self.assertEqual(len(result.alternatives), 3)
        self.assertEqual(result.data_fetch_count, 1)
        # Four attraction searches plus one hotel search, once—not once per plan.
        self.assertEqual(provider.search_count, 5)
        self.assertEqual(len({item.plan.itinerary.id for item in result.alternatives}), 3)

    async def test_live_snapshot_drives_deterministic_planner(self) -> None:
        service = HybridTripPlanningService(AmapPlanningSnapshotBuilder(_StableMapProvider()))

        result = await service.plan_text(DEMO)

        self.assertTrue(result.live_map_used)
        self.assertTrue(result.plan.validation.feasible)
        self.assertEqual(result.plan.data_mode, DataStatus.ESTIMATED)
        self.assertEqual(len(result.map_places), 4)
        self.assertEqual(
            sum(len(day.visits) for day in result.plan.itinerary.days),
            4,
        )
        self.assertTrue(all(place.id.startswith("amap-") for place in result.map_places))
        self.assertTrue(
            all(
                visit.route_from_previous.mode == "PUBLIC_TRANSIT_LIVE"
                for day in result.plan.itinerary.days
                for visit in day.visits
            )
        )
        self.assertEqual(result.weather.city if result.weather else None, "上海市")

    async def test_live_snapshot_is_reproducible_with_fixed_provider_data(self) -> None:
        service = HybridTripPlanningService(AmapPlanningSnapshotBuilder(_StableMapProvider()))

        first = await service.plan_text(DEMO)
        second = await service.plan_text(DEMO)

        self.assertEqual(first.model_dump_json(), second.model_dump_json())

    async def test_unavailable_map_explicitly_falls_back_without_leaking_error(self) -> None:
        service = HybridTripPlanningService(AmapPlanningSnapshotBuilder(_UnavailableMapProvider()))

        result = await service.plan_text(DEMO)

        self.assertFalse(result.live_map_used)
        self.assertEqual(result.plan.data_mode, DataStatus.FIXTURE)
        self.assertEqual(result.fallback_reason, "LiveSnapshotUnavailableError")
        self.assertNotIn("private details", result.model_dump_json())
        self.assertTrue(any("降级" in warning for warning in result.plan.warnings))

    async def test_route_failure_uses_estimator_but_keeps_live_pois(self) -> None:
        service = HybridTripPlanningService(
            AmapPlanningSnapshotBuilder(_RouteUnavailableMapProvider())
        )

        result = await service.plan_text(DEMO)

        self.assertTrue(result.live_map_used)
        self.assertTrue(result.plan.validation.feasible)
        self.assertTrue(
            all(
                visit.route_from_previous.mode == "PUBLIC_TRANSIT_ESTIMATED"
                for day in result.plan.itinerary.days
                for visit in day.visits
            )
        )
        self.assertTrue(any("市内路线" in warning for warning in result.plan.warnings))

    async def test_weather_outside_trip_dates_is_not_presented_as_trip_weather(self) -> None:
        service = HybridTripPlanningService(
            AmapPlanningSnapshotBuilder(_OutOfRangeWeatherProvider())
        )

        result = await service.plan_text(DEMO)

        self.assertTrue(result.live_map_used)
        self.assertIsNone(result.weather)
        self.assertTrue(
            any("天气预报未覆盖旅行日期" in warning for warning in result.plan.warnings)
        )


if __name__ == "__main__":
    unittest.main()
