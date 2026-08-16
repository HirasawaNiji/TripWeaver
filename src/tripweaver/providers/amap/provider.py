"""Official AMap MCP provider with strict normalization and provenance."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from time import monotonic

from tripweaver.config import AmapSettings
from tripweaver.domain.models import GeoPoint
from tripweaver.mcp_gateway import (
    McpAdapter,
    McpGateway,
    McpRegistry,
    McpSdkClientFactory,
    McpTransport,
    ServerConfig,
    ServerHealth,
    ToolDefinition,
)
from tripweaver.providers.amap.models import (
    AmapForecast,
    AmapGeocodePayloadWire,
    AmapGeocodeResult,
    AmapPlaceDetail,
    AmapPlaceSummary,
    AmapPoiDetailWire,
    AmapPoiSearchWire,
    AmapRoute,
    AmapTransitPayloadWire,
    AmapWalkingPayloadWire,
    AmapWeather,
    AmapWeatherWire,
)

AMAP_SERVER_NAME = "amap"
AMAP_REQUIRED_TOOLS = frozenset(
    {
        "maps_direction_transit_integrated",
        "maps_direction_walking",
        "maps_geo",
        "maps_search_detail",
        "maps_text_search",
        "maps_weather",
    }
)


class AmapProviderError(RuntimeError):
    """Safe base error for AMap schema and capability failures."""


class AmapCapabilityError(AmapProviderError):
    pass


class AmapEmptyResultError(AmapProviderError):
    pass


class AmapProvider:
    """Typed query-only facade over the official AMap MCP server."""

    def __init__(
        self,
        gateway: McpGateway,
        server_name: str = AMAP_SERVER_NAME,
        *,
        min_interval_seconds: float = 0,
    ) -> None:
        self._gateway = gateway
        self._server_name = server_name
        self._adapter = McpAdapter(gateway, server_name)
        self._min_interval_seconds = min_interval_seconds
        self._next_start_tick = 0.0
        self._rate_lock = asyncio.Lock()

    @classmethod
    def from_settings(cls, settings: AmapSettings) -> AmapProvider:
        config = ServerConfig(
            name=AMAP_SERVER_NAME,
            transport=McpTransport.STREAMABLE_HTTP,
            url=settings.endpoint_url,
            timeout_seconds=settings.timeout_seconds,
            max_retries=settings.max_retries,
            max_concurrency=settings.max_concurrency,
            health_failure_threshold=2,
        )
        gateway = McpGateway(McpRegistry((config,)), McpSdkClientFactory())
        return cls(
            gateway,
            min_interval_seconds=settings.min_interval_seconds,
        )

    async def verify_capabilities(self) -> tuple[ToolDefinition, ...]:
        tools = await self._gateway.discover_tools(self._server_name, force_refresh=True)
        names = {tool.name for tool in tools}
        missing = sorted(AMAP_REQUIRED_TOOLS - names)
        if missing:
            raise AmapCapabilityError("AMap MCP is missing required tools: " + ", ".join(missing))
        return tools

    async def health_check(self) -> ServerHealth:
        return await self._gateway.health_check(self._server_name)

    def health(self) -> ServerHealth:
        return self._gateway.health(self._server_name)

    async def search_places(
        self,
        keywords: str,
        *,
        city: str | None = None,
        city_limit: bool = True,
        limit: int = 10,
    ) -> tuple[AmapPlaceSummary, ...]:
        if not keywords.strip():
            raise ValueError("keywords must not be empty")
        if not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        arguments: dict[str, object] = {
            "keywords": keywords.strip(),
            "citylimit": city_limit,
        }
        if city and city.strip():
            arguments["city"] = city.strip()
        await self._throttle()
        response = await self._adapter.call_and_validate(
            "maps_text_search",
            arguments,
            AmapPoiSearchWire,
            ttl=timedelta(hours=6),
            allow_text_json=True,
        )
        return tuple(
            AmapPlaceSummary(
                id=poi.id,
                name=poi.name,
                address=poi.address,
                typecode=poi.typecode,
                photo_url=poi.photo or None,
                source=response.source,
            )
            for poi in response.data.pois[:limit]
        )

    async def place_detail(self, poi_id: str) -> AmapPlaceDetail:
        await self._throttle()
        response = await self._adapter.call_and_validate(
            "maps_search_detail",
            {"id": poi_id},
            AmapPoiDetailWire,
            ttl=timedelta(hours=6),
            allow_text_json=True,
        )
        item = response.data
        return AmapPlaceDetail(
            id=item.id,
            name=item.name,
            address=item.address,
            location=_parse_point(item.location),
            category=item.type,
            rating=_optional_float(item.rating),
            opening_hours_text=item.opentime2 or item.open_time or None,
            average_cost_cny=_optional_float(item.cost),
            photo_url=item.photo or None,
            source=response.source,
        )

    async def weather(self, city: str) -> AmapWeather:
        await self._throttle()
        response = await self._adapter.call_and_validate(
            "maps_weather",
            {"city": city},
            AmapWeatherWire,
            ttl=timedelta(minutes=30),
            allow_text_json=True,
        )
        return AmapWeather(
            city=response.data.city,
            forecasts=tuple(
                AmapForecast(
                    date=item.date,
                    weekday=int(item.week),
                    day_weather=item.dayweather,
                    night_weather=item.nightweather,
                    day_temperature_c=item.daytemp_float,
                    night_temperature_c=item.nighttemp_float,
                    day_wind=item.daywind,
                    night_wind=item.nightwind,
                    day_wind_power=item.daypower,
                    night_wind_power=item.nightpower,
                )
                for item in response.data.forecasts
            ),
            source=response.source,
        )

    async def geocode(
        self, address: str, *, city: str | None = None
    ) -> tuple[AmapGeocodeResult, ...]:
        arguments: dict[str, object] = {"address": address}
        if city:
            arguments["city"] = city
        await self._throttle()
        response = await self._adapter.call_and_validate(
            "maps_geo",
            arguments,
            AmapGeocodePayloadWire,
            ttl=timedelta(days=7),
            allow_text_json=True,
        )
        return tuple(
            AmapGeocodeResult(
                country=item.country,
                province=item.province,
                city=item.city,
                district=item.district,
                adcode=item.adcode,
                level=item.level,
                location=_parse_point(item.location),
                source=response.source,
            )
            for item in response.data.results
        )

    async def walking_route(self, origin: GeoPoint, destination: GeoPoint) -> AmapRoute:
        await self._throttle()
        response = await self._adapter.call_and_validate(
            "maps_direction_walking",
            {
                "origin": _format_point(origin),
                "destination": _format_point(destination),
            },
            AmapWalkingPayloadWire,
            ttl=timedelta(minutes=15),
            allow_text_json=True,
        )
        if not response.data.route.paths:
            raise AmapEmptyResultError("AMap returned no walking route")
        best = min(
            response.data.route.paths,
            key=lambda item: (item.duration, item.distance),
        )
        return AmapRoute(
            mode="WALKING",
            origin=_parse_point(response.data.route.origin),
            destination=_parse_point(response.data.route.destination),
            distance_meters=best.distance,
            duration_seconds=best.duration,
            source=response.source,
        )

    async def transit_route(
        self,
        origin: GeoPoint,
        destination: GeoPoint,
        *,
        origin_city: str,
        destination_city: str | None = None,
    ) -> AmapRoute:
        await self._throttle()
        response = await self._adapter.call_and_validate(
            "maps_direction_transit_integrated",
            {
                "origin": _format_point(origin),
                "destination": _format_point(destination),
                "city": origin_city,
                "cityd": destination_city or origin_city,
            },
            AmapTransitPayloadWire,
            ttl=timedelta(minutes=15),
            allow_text_json=True,
        )
        if not response.data.transits:
            raise AmapEmptyResultError("AMap returned no transit route")
        best = min(
            response.data.transits,
            key=lambda item: (item.duration, item.walking_distance),
        )
        return AmapRoute(
            mode="PUBLIC_TRANSIT",
            origin=_parse_point(response.data.origin),
            destination=_parse_point(response.data.destination),
            distance_meters=response.data.distance,
            duration_seconds=best.duration,
            walking_distance_meters=best.walking_distance,
            source=response.source,
        )

    async def _throttle(self) -> None:
        if not self._min_interval_seconds:
            return
        async with self._rate_lock:
            now = monotonic()
            delay = self._next_start_tick - now
            if delay > 0:
                await asyncio.sleep(delay)
                now = monotonic()
            self._next_start_tick = now + self._min_interval_seconds


def _parse_point(value: str) -> GeoPoint:
    try:
        longitude_text, latitude_text = value.split(",", 1)
        return GeoPoint(
            latitude=float(latitude_text),
            longitude=float(longitude_text),
        )
    except (TypeError, ValueError) as error:
        raise AmapProviderError("AMap returned an invalid coordinate") from error


def _format_point(point: GeoPoint) -> str:
    return f"{point.longitude:.6f},{point.latitude:.6f}"


def _optional_float(value: str) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError as error:
        raise AmapProviderError("AMap returned an invalid numeric field") from error
