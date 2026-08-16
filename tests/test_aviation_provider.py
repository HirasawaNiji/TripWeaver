from __future__ import annotations

import json
import unittest
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date
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
from tripweaver.providers.aviation import VariflightProvider

FLIGHT_PAYLOAD = {
    "code": 200,
    "message": "Success",
    "request_id": "request-test",
    "timestamp": "2026-08-16T21:07:31+08:00",
    "data": [
        {
            "flightno": "MU5162",
            "depdate": "2026-08-20",
            "arrdate": "2026-08-20",
            "flightdeptimeplandate": 1787221800,
            "flightarrtimeplandate": 1787229900,
            "flightdepcode": "PEK",
            "flightarrcode": "SHA",
            "flightterminal": "T2",
            "flighthterminal": "T2",
            "flightcompany": "China Eastern",
            "shareflag": 0,
            "shareflightno": "",
            "stopflag": 0,
            "tax": "50",
            "oilfee": "20",
            "cabins": [
                {
                    "cabinclass": "C",
                    "classname": "Business",
                    "cabincode": "I",
                    "seatnum": 4,
                    "stprice": 8390,
                    "price": 2800,
                    "discount": 0.33,
                },
                {
                    "cabinclass": "Y",
                    "classname": "Economy",
                    "cabincode": "T",
                    "seatnum": 10,
                    "stprice": 2150,
                    "price": 560,
                    "discount": 0.26,
                },
            ],
        },
        {
            "flightno": "MU9999",
            "depdate": "2026-08-20",
            "arrdate": "2026-08-20",
            "flightdeptimeplandate": 1787221800,
            "flightarrtimeplandate": 1787229900,
            "flightdepcode": "PEK",
            "flightarrcode": "SHA",
            "cabins": [
                {
                    "cabinclass": "Y",
                    "cabincode": "T",
                    "seatnum": 0,
                    "stprice": 2150,
                    "price": 400,
                }
            ],
        },
    ],
}


class _AviationConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> tuple[ToolDefinition, ...]:
        return tuple(
            ToolDefinition(
                server_name="variflight",
                name=name,
                input_schema={"type": "object"},
            )
            for name in ("getFlightPriceByCities", "getFutureWeatherByAirport")
        )

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> RawMcpToolResult:
        self.calls.append((tool_name, arguments))
        return RawMcpToolResult(content_text=(json.dumps(FLIGHT_PAYLOAD, ensure_ascii=False),))


class _AviationFactory:
    def __init__(self, connection: McpConnection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def connect(self, config: ServerConfig) -> AsyncGenerator[McpConnection]:
        yield self.connection


class VariflightProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.connection = _AviationConnection()
        config = ServerConfig(name="variflight", transport=McpTransport.IN_MEMORY)
        gateway = McpGateway(McpRegistry((config,)), _AviationFactory(self.connection))
        self.provider = VariflightProvider(gateway)

    async def test_verifies_required_capabilities(self) -> None:
        tools = await self.provider.verify_capabilities()

        self.assertEqual(len(tools), 2)
        self.assertEqual(self.provider.health().state.value, "UP")

    async def test_normalizes_available_lowest_cabin_and_known_fees(self) -> None:
        offers = await self.provider.search_offers(
            "北京",
            "上海",
            date(2026, 8, 20),
            TransportLeg.OUTBOUND,
        )

        self.assertEqual(len(offers), 1)
        offer = offers[0]
        self.assertEqual(offer.flight_number, "MU5162")
        self.assertEqual(offer.cabin_class, "经济舱")
        self.assertEqual(offer.seats_remaining, 10)
        self.assertEqual(str(offer.base_fare_cny), "560")
        self.assertEqual(str(offer.included_fees_cny), "70")
        self.assertEqual(str(offer.price_per_person_cny), "630")
        self.assertTrue(offer.fees_complete)
        self.assertEqual(offer.depart_at.hour, 18)
        self.assertEqual(offer.source.status, DataStatus.LIVE)
        self.assertNotIn("BJS", offer.source.source_reference)
        arguments = self.connection.calls[0][1]
        self.assertEqual(arguments["dep_city"], "BJS")
        self.assertEqual(arguments["arr_city"], "SHA")

    async def test_maps_offer_to_canonical_flight_option(self) -> None:
        options = await self.provider.transport_options(
            "BJS",
            "SHA",
            date(2026, 8, 20),
            TransportLeg.OUTBOUND,
        )

        self.assertEqual(len(options), 1)
        self.assertIn("MU5162", options[0].label)
        self.assertIn("PEK T2", options[0].label)
        self.assertEqual(str(options[0].price_per_person_cny), "630")


if __name__ == "__main__":
    unittest.main()
