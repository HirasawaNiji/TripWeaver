from __future__ import annotations

import json
import unittest
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from tripweaver.domain.models import DataStatus, GeoPoint
from tripweaver.mcp_gateway.client import McpConnection
from tripweaver.mcp_gateway.gateway import McpGateway
from tripweaver.mcp_gateway.models import (
    McpTransport,
    RawMcpToolResult,
    ServerConfig,
    ToolDefinition,
)
from tripweaver.mcp_gateway.registry import McpRegistry
from tripweaver.providers.amap import AMAP_REQUIRED_TOOLS, AmapProvider

PAYLOADS: dict[str, object] = {
    "maps_text_search": {
        "suggestion": {},
        "pois": [
            {
                "id": "B001",
                "name": "上海博物馆",
                "address": "人民大道201号",
                "typecode": "140100",
                "photo": "https://example.test/photo.jpg",
            },
            {
                "id": "B002",
                "name": "上海历史博物馆",
                "address": "南京西路325号",
                "typecode": "140100",
            },
        ],
    },
    "maps_search_detail": {
        "id": "B001",
        "name": "上海博物馆",
        "address": "人民大道201号",
        "location": "121.475480,31.228231",
        "type": "科教文化服务;博物馆;博物馆",
        "rating": "4.7",
        "open_time": "",
        "opentime2": "周二至周日 09:00-17:00",
        "cost": "",
        "photo": "https://example.test/photo.jpg",
    },
    "maps_weather": {
        "city": "上海市",
        "forecasts": [
            {
                "date": "2026-08-16",
                "week": "7",
                "dayweather": "小雨",
                "nightweather": "多云",
                "daytemp_float": "30.0",
                "nighttemp_float": "26.0",
                "daywind": "北",
                "nightwind": "北",
                "daypower": "1-3",
                "nightpower": "1-3",
            }
        ],
    },
    "maps_geo": {
        "results": [
            {
                "country": "中国",
                "province": "上海市",
                "city": "上海市",
                "district": "黄浦区",
                "adcode": "310101",
                "location": "121.476211,31.226732",
                "level": "兴趣点",
            }
        ]
    },
    "maps_direction_walking": {
        "route": {
            "origin": "121.475480,31.228231",
            "destination": "121.490400,31.240000",
            "paths": [{"distance": 2495, "duration": 1996, "steps": []}],
        }
    },
    "maps_direction_transit_integrated": {
        "origin": "121.475480,31.228231",
        "destination": "121.490400,31.240000",
        "distance": "2675",
        "transits": [
            {"duration": "1790", "walking_distance": "1809", "segments": []},
            {"duration": "1787", "walking_distance": "1280", "segments": []},
        ],
    },
}


class _AmapConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> tuple[ToolDefinition, ...]:
        return tuple(
            ToolDefinition(
                server_name="amap",
                name=name,
                input_schema={"type": "object"},
            )
            for name in sorted(AMAP_REQUIRED_TOOLS)
        )

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> RawMcpToolResult:
        self.calls.append((tool_name, arguments))
        return RawMcpToolResult(content_text=(json.dumps(PAYLOADS[tool_name], ensure_ascii=False),))


class _AmapFactory:
    def __init__(self, connection: McpConnection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def connect(self, config: ServerConfig) -> AsyncGenerator[McpConnection]:
        yield self.connection


class AmapProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.connection = _AmapConnection()
        config = ServerConfig(name="amap", transport=McpTransport.IN_MEMORY)
        gateway = McpGateway(McpRegistry((config,)), _AmapFactory(self.connection))
        self.provider = AmapProvider(gateway)

    async def test_verifies_required_capabilities(self) -> None:
        tools = await self.provider.verify_capabilities()

        self.assertEqual({tool.name for tool in tools}, AMAP_REQUIRED_TOOLS)
        self.assertEqual(self.provider.health().state.value, "UP")

    async def test_normalizes_search_and_detail_with_provenance(self) -> None:
        places = await self.provider.search_places("博物馆", city="上海", limit=1)
        detail = await self.provider.place_detail(places[0].id)

        self.assertEqual(len(places), 1)
        self.assertEqual(places[0].name, "上海博物馆")
        self.assertEqual(places[0].source.status, DataStatus.LIVE)
        self.assertTrue(places[0].source.source_reference.startswith("mcp://amap/"))
        self.assertEqual(detail.location, GeoPoint(latitude=31.228231, longitude=121.47548))
        self.assertEqual(detail.rating, 4.7)
        self.assertIn("09:00", detail.opening_hours_text or "")

    async def test_normalizes_weather_and_geocode(self) -> None:
        weather = await self.provider.weather("上海")
        geocodes = await self.provider.geocode("上海博物馆", city="上海")

        self.assertEqual(weather.forecasts[0].day_temperature_c, 30.0)
        self.assertEqual(weather.forecasts[0].weekday, 7)
        self.assertEqual(geocodes[0].adcode, "310101")
        self.assertEqual(geocodes[0].location.longitude, 121.476211)

    async def test_normalizes_walking_and_selects_fastest_transit(self) -> None:
        origin = GeoPoint(latitude=31.228231, longitude=121.47548)
        destination = GeoPoint(latitude=31.24, longitude=121.4904)

        walking = await self.provider.walking_route(origin, destination)
        transit = await self.provider.transit_route(
            origin,
            destination,
            origin_city="310000",
        )

        self.assertEqual(walking.distance_meters, 2495)
        self.assertEqual(walking.duration_seconds, 1996)
        self.assertEqual(transit.duration_seconds, 1787)
        self.assertEqual(transit.walking_distance_meters, 1280)


if __name__ == "__main__":
    unittest.main()
