from __future__ import annotations

import json
import unittest
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from tripweaver.domain.models import DataStatus, TransportLeg
from tripweaver.mcp_gateway.client import McpConnection
from tripweaver.mcp_gateway.gateway import McpGateway
from tripweaver.mcp_gateway.models import (
    McpTransport,
    RawMcpToolResult,
    ServerConfig,
    ToolDefinition,
)
from tripweaver.mcp_gateway.registry import McpRegistry
from tripweaver.providers.railway import RailwayProvider

TICKETS = [
    {
        "train_no": "2400000G010A",
        "start_train_code": "G1",
        "start_date": "2026-08-20",
        "start_time": "07:00",
        "arrive_date": "2026-08-20",
        "arrive_time": "11:35",
        "lishi": "04:35",
        "from_station": "北京南",
        "to_station": "上海虹桥",
        "from_station_telecode": "VNP",
        "to_station_telecode": "AOH",
        "prices": [
            {
                "seat_name": "一等座",
                "short": "zy",
                "seat_type_code": "M",
                "num": "有",
                "price": 933,
                "discount": None,
            },
            {
                "seat_name": "二等座",
                "short": "ze",
                "seat_type_code": "O",
                "num": "3",
                "price": 553,
                "discount": None,
            },
        ],
        "dw_flag": ["复兴号"],
    },
    {
        "train_no": "2400000G030A",
        "start_train_code": "G3",
        "start_date": "2026-08-20",
        "start_time": "08:00",
        "arrive_date": "2026-08-20",
        "arrive_time": "12:35",
        "lishi": "04:35",
        "from_station": "北京南",
        "to_station": "上海虹桥",
        "from_station_telecode": "VNP",
        "to_station_telecode": "AOH",
        "prices": [
            {
                "seat_name": "二等座",
                "short": "ze",
                "seat_type_code": "O",
                "num": "候补",
                "price": 553,
                "discount": None,
            }
        ],
        "dw_flag": [],
    },
]


class _RailwayConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> tuple[ToolDefinition, ...]:
        return (
            ToolDefinition(
                server_name="railway_12306",
                name="get-tickets",
                input_schema={"type": "object"},
            ),
        )

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> RawMcpToolResult:
        self.calls.append((tool_name, arguments))
        return RawMcpToolResult(content_text=(json.dumps(TICKETS, ensure_ascii=False),))


class _RailwayFactory:
    def __init__(self, connection: McpConnection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def connect(self, config: ServerConfig) -> AsyncGenerator[McpConnection]:
        yield self.connection


class RailwayProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.connection = _RailwayConnection()
        config = ServerConfig(
            name="railway_12306",
            transport=McpTransport.IN_MEMORY,
        )
        gateway = McpGateway(McpRegistry((config,)), _RailwayFactory(self.connection))
        self.provider = RailwayProvider(gateway)

    async def test_verifies_query_capability(self) -> None:
        tools = await self.provider.verify_capabilities()

        self.assertEqual([tool.name for tool in tools], ["get-tickets"])
        self.assertEqual(self.provider.health().state.value, "UP")

    async def test_normalizes_only_explicitly_available_cheapest_seat(self) -> None:
        from datetime import date

        tickets = await self.provider.search_tickets(
            "北京",
            "上海",
            date(2026, 8, 20),
            TransportLeg.OUTBOUND,
            limit=10,
        )

        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0].train_code, "G1")
        self.assertEqual(tickets[0].seat_name, "二等座")
        self.assertEqual(str(tickets[0].price_per_person_cny), "553")
        self.assertEqual(tickets[0].availability, "余3张")
        self.assertEqual(tickets[0].source.status, DataStatus.LIVE)
        self.assertNotIn("北京", tickets[0].source.source_reference)
        arguments = self.connection.calls[0][1]
        self.assertEqual(arguments["format"], "json")
        self.assertEqual(arguments["limitedNum"], 10)

    async def test_maps_ticket_to_canonical_transport_option(self) -> None:
        from datetime import date

        options = await self.provider.transport_options(
            "北京",
            "上海",
            date(2026, 8, 20),
            TransportLeg.OUTBOUND,
        )

        self.assertEqual(len(options), 1)
        self.assertEqual(options[0].origin, "北京")
        self.assertIn("G1", options[0].label)
        self.assertIn("二等座", options[0].label)


if __name__ == "__main__":
    unittest.main()
