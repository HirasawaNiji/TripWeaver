"""Build a frozen hybrid planning snapshot from AMap and deterministic policies."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from math import ceil
from typing import Protocol

from pydantic import ValidationError

from tripweaver.domain.models import (
    DataStatus,
    GeoPoint,
    LodgingArea,
    Place,
    RouteLeg,
    SourceMetadata,
    TransportOption,
    TripRequest,
)
from tripweaver.fixtures.catalog import FixtureCatalog
from tripweaver.mcp_gateway.errors import McpGatewayError
from tripweaver.providers.amap import (
    AmapPlaceDetail,
    AmapPlaceSummary,
    AmapProviderError,
    AmapRoute,
    AmapWeather,
)

DEFAULT_LIVE_PLACE_LIMIT = 4
DEFAULT_TRANSIT_COST_ESTIMATE_CNY = Decimal("5.00")


class LiveSnapshotUnavailableError(RuntimeError):
    """Raised when too little verified live data exists to build a snapshot."""


class PlanningMapProvider(Protocol):
    async def search_places(
        self,
        keywords: str,
        *,
        city: str | None = None,
        city_limit: bool = True,
        limit: int = 10,
    ) -> tuple[AmapPlaceSummary, ...]: ...

    async def place_detail(self, poi_id: str) -> AmapPlaceDetail: ...

    async def weather(self, city: str) -> AmapWeather: ...

    async def transit_route(
        self,
        origin: GeoPoint,
        destination: GeoPoint,
        *,
        origin_city: str,
        destination_city: str | None = None,
    ) -> AmapRoute: ...


class FrozenPlanningCatalog:
    """Network-free catalog backed by a completed live-data snapshot."""

    def __init__(
        self,
        *,
        fixture: FixtureCatalog,
        destination: str,
        places: tuple[Place, ...],
        routes: dict[tuple[str, str], RouteLeg],
        lodging_areas: tuple[LodgingArea, ...] | None = None,
        transport_options: tuple[TransportOption, ...] | None = None,
    ) -> None:
        self._fixture = fixture
        self._destination = destination
        self._places = places
        self._routes = dict(routes)
        self._lodging_areas = lodging_areas
        self._transport_options = transport_options

    def transport_options(self, request: TripRequest) -> tuple[TransportOption, ...]:
        if self._transport_options is not None:
            return self._transport_options
        return self._fixture.transport_options(request)

    def with_transport_options(self, options: tuple[TransportOption, ...]) -> FrozenPlanningCatalog:
        """Create a new frozen snapshot while preserving map facts and routes."""

        return FrozenPlanningCatalog(
            fixture=self._fixture,
            destination=self._destination,
            places=self._places,
            routes=self._routes,
            lodging_areas=self._lodging_areas,
            transport_options=options,
        )

    def places(self, destination: str) -> tuple[Place, ...]:
        if destination != self._destination:
            raise LookupError(f"live snapshot does not cover destination: {destination}")
        return self._places

    def lodging_areas(self, destination: str) -> tuple[LodgingArea, ...]:
        if destination != self._destination:
            raise LookupError(f"live snapshot does not cover destination: {destination}")
        if self._lodging_areas is not None:
            return self._lodging_areas
        return self._fixture.lodging_areas(destination)

    def route(self, from_id: str, from_point: GeoPoint, to_place: Place) -> RouteLeg:
        del from_point
        try:
            return self._routes[(from_id, to_place.id)]
        except KeyError as error:
            raise LookupError(f"snapshot route missing: {from_id}/{to_place.id}") from error

    def estimation_source(self) -> SourceMetadata:
        return self._fixture.estimation_source()


@dataclass(frozen=True)
class HybridPlanningSnapshot:
    catalog: FrozenPlanningCatalog
    places: tuple[Place, ...]
    weather: AmapWeather | None
    lodging_areas: tuple[LodgingArea, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class _HydrationOutcome:
    place: Place | None
    warning: str | None = None


@dataclass(frozen=True)
class _RouteSpec:
    from_id: str
    from_point: GeoPoint
    to_place: Place


@dataclass(frozen=True)
class _RouteOutcome:
    route: RouteLeg
    used_fallback: bool


class AmapPlanningSnapshotBuilder:
    """Prefetch AMap facts concurrently, then freeze them for deterministic use."""

    def __init__(
        self,
        provider: PlanningMapProvider,
        *,
        fixture: FixtureCatalog | None = None,
        place_limit: int = DEFAULT_LIVE_PLACE_LIMIT,
        lodging_limit: int = 3,
        nightly_price_override_cny: Decimal | None = None,
    ) -> None:
        if not 2 <= place_limit <= 6:
            raise ValueError("place_limit must be between 2 and 6")
        self._provider = provider
        self._fixture = fixture or FixtureCatalog()
        self._place_limit = place_limit
        if not 1 <= lodging_limit <= 5:
            raise ValueError("lodging_limit must be between 1 and 5")
        self._lodging_limit = lodging_limit
        self._nightly_price_override_cny = nightly_price_override_cny

    async def build(self, request: TripRequest) -> HybridPlanningSnapshot:
        seeds = self._select_seeds(request)
        hydration = await asyncio.gather(
            *(self._safe_hydrate(seed, request.destination) for seed in seeds)
        )
        places = tuple(outcome.place for outcome in hydration if outcome.place is not None)
        warnings = [outcome.warning for outcome in hydration if outcome.warning is not None]
        if len(places) < 2:
            raise LiveSnapshotUnavailableError("fewer than two AMap POIs could be normalized")

        weather, weather_warning = await self._safe_weather(
            request.destination,
            request.start_date,
            request.end_date,
        )
        if weather_warning:
            warnings.append(weather_warning)

        lodging_areas, lodging_warnings = await self._build_lodging_areas(
            request.destination, places
        )
        warnings.extend(lodging_warnings)
        routes, fallback_count = await self._build_routes(request, places, lodging_areas)
        if fallback_count:
            warnings.append(
                f"{fallback_count} 条市内路线未取得高德结果，已使用明确标注的确定性估算。"
            )
        warnings.extend(
            (
                "景点名称、类别与坐标来自高德；门票、建议时长和可计算开放窗口来自本地规划基线。",
                "高德营业时间原文仅作为复核提示，复杂节假日规则尚未自动转成硬约束。",
                "市内路线距离与耗时来自高德；公交费用按每段 5 元估算。",
                "酒店 POI、位置与评分来自高德；房价不是 OTA 实时报价，必须按 price_basis 复核。",
            )
        )
        catalog = FrozenPlanningCatalog(
            fixture=self._fixture,
            destination=request.destination,
            places=places,
            routes=routes,
            lodging_areas=lodging_areas,
        )
        return HybridPlanningSnapshot(
            catalog=catalog,
            places=places,
            weather=weather,
            lodging_areas=lodging_areas,
            warnings=tuple(warnings),
        )

    def _select_seeds(self, request: TripRequest) -> tuple[Place, ...]:
        seeds = self._fixture.places(request.destination)

        def score(place: Place) -> tuple[int, str]:
            interest_bonus = 20 * len(set(place.tags).intersection(request.interests))
            return (-(place.priority + interest_bonus), place.id)

        return tuple(sorted(seeds, key=score)[: self._place_limit])

    async def _safe_hydrate(self, seed: Place, city: str) -> _HydrationOutcome:
        query = _seed_query(seed.name)
        try:
            candidates = await self._provider.search_places(
                query,
                city=city,
                city_limit=True,
                limit=5,
            )
            selected = _select_candidate(query, candidates)
            if selected is None:
                raise LiveSnapshotUnavailableError("no matching POI")
            detail = await self._provider.place_detail(selected.id)
            return _HydrationOutcome(place=_merge_live_place(seed, detail))
        except (
            AmapProviderError,
            LiveSnapshotUnavailableError,
            McpGatewayError,
            ValidationError,
            ValueError,
            LookupError,
        ) as error:
            return _HydrationOutcome(
                place=None,
                warning=f"{query} 实时 POI 不可用（{type(error).__name__}），未进入候选集。",
            )

    async def _safe_weather(
        self,
        city: str,
        start_date: date,
        end_date: date,
    ) -> tuple[AmapWeather | None, str | None]:
        try:
            weather = await self._provider.weather(city)
            applicable = tuple(
                forecast
                for forecast in weather.forecasts
                if start_date <= forecast.date <= end_date
            )
            if not applicable:
                return (
                    None,
                    "高德天气预报未覆盖旅行日期，未将当前短期预报写入行程。",
                )
            return weather.model_copy(update={"forecasts": applicable}), None
        except (
            AmapProviderError,
            LiveSnapshotUnavailableError,
            McpGatewayError,
            ValidationError,
            ValueError,
        ) as error:
            return None, f"实时天气不可用（{type(error).__name__}），未生成天气事实。"

    async def _build_routes(
        self,
        request: TripRequest,
        places: tuple[Place, ...],
        lodging_areas: tuple[LodgingArea, ...],
    ) -> tuple[dict[tuple[str, str], RouteLeg], int]:
        origins = [(area.id, area.location) for area in lodging_areas]
        origins.extend((place.id, place.location) for place in places)
        specs = tuple(
            _RouteSpec(from_id=from_id, from_point=from_point, to_place=place)
            for from_id, from_point in origins
            for place in places
            if from_id != place.id
        )
        outcomes = await asyncio.gather(
            *(self._safe_route(spec, request.destination) for spec in specs)
        )
        routes = {
            (route.from_id, route.to_id): route for route in (outcome.route for outcome in outcomes)
        }
        return routes, sum(outcome.used_fallback for outcome in outcomes)

    async def _build_lodging_areas(
        self,
        city: str,
        places: tuple[Place, ...],
    ) -> tuple[tuple[LodgingArea, ...], tuple[str, ...]]:
        """Recommend live hotel locations while keeping room prices explicitly non-live."""

        try:
            summaries = await self._provider.search_places(
                "酒店", city=city, city_limit=True, limit=min(10, self._lodging_limit * 3)
            )
            details = await asyncio.gather(
                *(self._safe_hotel_detail(item.id) for item in summaries)
            )
            candidates = tuple(item for item in details if item is not None)
            if not candidates:
                raise LiveSnapshotUnavailableError("no hotel POI could be normalized")
            centroid_lat = sum(place.location.latitude for place in places) / len(places)
            centroid_lon = sum(place.location.longitude for place in places) / len(places)
            ranked = sorted(
                candidates,
                key=lambda item: (
                    (item.location.latitude - centroid_lat) ** 2
                    + (item.location.longitude - centroid_lon) ** 2,
                    -(item.rating or 0),
                    item.id,
                ),
            )[: self._lodging_limit]
            areas = tuple(self._hotel_to_lodging(item) for item in ranked)
            price_warning = (
                "住宿每晚价格采用用户提供值；该值没有经过 OTA 可售房型验证。"
                if self._nightly_price_override_cny is not None
                else "住宿每晚价格采用 TripWeaver 评分档位估算，不代表实时房价或可订状态。"
            )
            return areas, (price_warning,)
        except (
            AmapProviderError,
            LiveSnapshotUnavailableError,
            McpGatewayError,
            ValidationError,
            ValueError,
        ) as error:
            return (
                self._fixture.lodging_areas(city),
                (f"酒店 POI 不可用（{type(error).__name__}），住宿区域已降级为 Fixture。",),
            )

    async def _safe_hotel_detail(self, poi_id: str) -> AmapPlaceDetail | None:
        try:
            return await self._provider.place_detail(poi_id)
        except (AmapProviderError, McpGatewayError, ValidationError, ValueError, LookupError):
            return None

    def _hotel_to_lodging(self, hotel: AmapPlaceDetail) -> LodgingArea:
        if self._nightly_price_override_cny is not None:
            price = self._nightly_price_override_cny
            price_basis = "USER_SUPPLIED"
            provider = "amap+user_price"
            confidence = 0.82
        else:
            rating = hotel.rating or 0
            price = Decimal(680) if rating >= 4.5 else Decimal(520) if rating >= 4 else Decimal(380)
            price_basis = "ESTIMATED_POLICY"
            provider = "amap+tripweaver_lodging_policy"
            confidence = 0.6
        source = SourceMetadata(
            provider=provider,
            status=DataStatus.ESTIMATED,
            queried_at=hotel.source.queried_at,
            expires_at=hotel.source.expires_at,
            source_reference=hotel.source.source_reference + "#lodging-policy-v1",
            confidence=confidence,
        )
        return LodgingArea(
            id=f"amap-lodging-{hotel.id}",
            name=f"{hotel.name}周边住宿区域",
            location=hotel.location,
            nightly_price_estimate_cny=price,
            description="以真实酒店 POI 为通勤锚点选择住宿区域；价格与可订状态需另行复核。",
            candidate_hotel_name=hotel.name,
            candidate_hotel_address=hotel.address or None,
            candidate_hotel_rating=hotel.rating,
            price_basis=price_basis,
            source=source,
        )

    async def _safe_route(self, spec: _RouteSpec, city: str) -> _RouteOutcome:
        try:
            live = await self._provider.transit_route(
                spec.from_point,
                spec.to_place.location,
                origin_city=city,
            )
            return _RouteOutcome(
                route=_live_route_leg(spec, live),
                used_fallback=False,
            )
        except (AmapProviderError, McpGatewayError, ValidationError, ValueError):
            estimated = self._fixture.route(
                spec.from_id,
                spec.from_point,
                spec.to_place,
            )
            return _RouteOutcome(
                route=estimated.model_copy(
                    update={
                        "mode": "PUBLIC_TRANSIT_ESTIMATED",
                        "source": SourceMetadata(
                            provider="tripweaver_route_estimator",
                            status=DataStatus.ESTIMATED,
                            queried_at=datetime.now(UTC),
                            expires_at=None,
                            source_reference=(
                                f"estimate://route/{spec.from_id}/{spec.to_place.id}"
                            ),
                            confidence=0.45,
                        ),
                    }
                ),
                used_fallback=True,
            )


def _seed_query(name: str) -> str:
    return re.sub(r"[（(]模拟[）)]", "", name).strip()


def _canonical_name(name: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]", "", name).lower()


def _select_candidate(
    query: str, candidates: tuple[AmapPlaceSummary, ...]
) -> AmapPlaceSummary | None:
    normalized_query = _canonical_name(query)
    ranked: list[tuple[int, str, AmapPlaceSummary]] = []
    for candidate in candidates:
        normalized_name = _canonical_name(candidate.name)
        if normalized_name == normalized_query:
            rank = 0
        elif normalized_query in normalized_name:
            rank = 1
        elif normalized_name in normalized_query:
            rank = 2
        else:
            continue
        ranked.append((rank, candidate.id, candidate))
    if not ranked:
        return None
    return min(ranked, key=lambda item: (item[0], item[1]))[2]


def _merge_live_place(seed: Place, detail: AmapPlaceDetail) -> Place:
    note = detail.opening_hours_text
    if note and len(note) > 1000:
        note = note[:997] + "..."
    mixed_source = SourceMetadata(
        provider="amap+tripweaver_policy",
        status=DataStatus.ESTIMATED,
        queried_at=detail.source.queried_at,
        expires_at=detail.source.expires_at,
        source_reference=detail.source.source_reference + "#planning-policy-v1",
        confidence=0.75,
    )
    return Place(
        id=f"amap-{detail.id}",
        name=detail.name,
        category=detail.category,
        location=detail.location,
        suggested_duration_minutes=seed.suggested_duration_minutes,
        admission_per_person_cny=seed.admission_per_person_cny,
        opens_at=seed.opens_at,
        closes_at=seed.closes_at,
        closed_weekdays=seed.closed_weekdays,
        tags=seed.tags,
        priority=seed.priority,
        opening_hours_note=note,
        planning_assumptions=(
            "名称、类别和坐标来自高德实时查询。",
            "门票、建议时长和规划开放窗口来自本地基线，出行前必须复核。",
        ),
        source=mixed_source,
    )


def _live_route_leg(spec: _RouteSpec, route: AmapRoute) -> RouteLeg:
    source = SourceMetadata(
        provider="amap+tripweaver_cost_policy",
        status=DataStatus.ESTIMATED,
        queried_at=route.source.queried_at,
        expires_at=route.source.expires_at,
        source_reference=route.source.source_reference + "#cost-policy-v1",
        confidence=0.8,
    )
    return RouteLeg(
        from_id=spec.from_id,
        to_id=spec.to_place.id,
        mode="PUBLIC_TRANSIT_LIVE",
        minutes=max(1, ceil(route.duration_seconds / 60)),
        cost_cny=DEFAULT_TRANSIT_COST_ESTIMATE_CNY,
        distance_km=(Decimal(route.distance_meters) / Decimal(1000)).quantize(Decimal("0.01")),
        source=source,
    )
