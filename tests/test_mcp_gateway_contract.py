from __future__ import annotations

import asyncio
import json
import unittest
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict

from tripweaver.domain.models import DataStatus
from tripweaver.mcp_gateway.adapter import McpAdapter
from tripweaver.mcp_gateway.client import McpConnection, McpSdkClientFactory
from tripweaver.mcp_gateway.errors import (
    AdapterSchemaError,
    DuplicateServerError,
    McpCallTimeoutError,
    ServerDisabledError,
)
from tripweaver.mcp_gateway.gateway import McpGateway
from tripweaver.mcp_gateway.models import (
    HealthState,
    McpTransport,
    RawMcpToolResult,
    ServerConfig,
    ToolDefinition,
)
from tripweaver.mcp_gateway.registry import McpRegistry


class EchoPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str


def build_contract_server() -> MCPServer[Any]:
    server = MCPServer("TripWeaver contract server")

    @server.tool(title="Echo structured data")
    def echo(message: str) -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        """Return structured content for adapter contract testing."""

        return {"message": message}

    @server.tool()
    def invalid_echo(message: str) -> dict[str, bool]:  # pyright: ignore[reportUnusedFunction]
        """Return a deliberately incompatible adapter schema."""

        return {"wrong": bool(message)}

    @server.tool()
    def fail() -> str:  # pyright: ignore[reportUnusedFunction]
        """Return an MCP tool error instead of a transport exception."""

        raise ValueError("expected contract failure")

    @server.tool()
    def text_json(message: str) -> CallToolResult:  # pyright: ignore[reportUnusedFunction]
        """Return JSON serialized in TextContent like the AMap server."""

        return CallToolResult(content=[TextContent(text=json.dumps({"message": message}))])

    @server.tool()
    def malformed_text_json() -> CallToolResult:  # pyright: ignore[reportUnusedFunction]
        """Return invalid JSON for the opt-in text decoder."""

        return CallToolResult(content=[TextContent(text="{not-json")])

    return server


class McpSdkContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        server = build_contract_server()
        config = ServerConfig(name="contract", transport=McpTransport.IN_MEMORY)
        self.gateway = McpGateway(
            McpRegistry((config,)),
            McpSdkClientFactory({"contract": server}),
        )

    async def test_discovers_and_calls_real_sdk_v2_in_memory_server(self) -> None:
        tools = await self.gateway.discover_tools("contract")
        result = await self.gateway.call_tool("contract", "echo", {"message": "hello"})

        self.assertEqual(
            {tool.name for tool in tools},
            {"echo", "fail", "invalid_echo", "malformed_text_json", "text_json"},
        )
        self.assertEqual(result.structured_content, {"message": "hello"})
        self.assertFalse(result.is_error)
        self.assertEqual(self.gateway.health("contract").state, HealthState.UP)
        self.assertEqual(len(self.gateway.traces()), 2)

    async def test_tool_failure_is_returned_without_transport_retry(self) -> None:
        result = await self.gateway.call_tool("contract", "fail", {})

        self.assertTrue(result.is_error)
        self.assertEqual(result.attempts, 1)
        self.assertTrue(any("expected contract failure" in text for text in result.content_text))
        self.assertEqual(self.gateway.health("contract").state, HealthState.UP)

    async def test_adapter_validates_structured_output_and_adds_provenance(self) -> None:
        adapter = McpAdapter(self.gateway, "contract")

        response = await adapter.call_and_validate(
            "echo",
            {"message": "normalized"},
            EchoPayload,
            ttl=timedelta(minutes=5),
        )

        self.assertEqual(response.data.message, "normalized")
        self.assertEqual(response.source.status, DataStatus.LIVE)
        self.assertEqual(response.source.provider, "contract")
        self.assertIn(response.trace_id, response.source.source_reference)

    async def test_adapter_reuses_normalized_query_until_ttl_expires(self) -> None:
        adapter = McpAdapter(self.gateway, "contract")
        first = await adapter.call_and_validate(
            "echo", {"message": "cached"}, EchoPayload, ttl=timedelta(minutes=5)
        )
        trace_count = len(self.gateway.traces())
        second = await adapter.call_and_validate(
            "echo", {"message": "cached"}, EchoPayload, ttl=timedelta(minutes=5)
        )
        self.assertEqual(first.data, second.data)
        self.assertEqual(second.source.status, DataStatus.CACHED)
        self.assertEqual(len(self.gateway.traces()), trace_count)

    async def test_adapter_rejects_incompatible_structured_output(self) -> None:
        adapter = McpAdapter(self.gateway, "contract")

        with self.assertRaises(AdapterSchemaError):
            await adapter.call_and_validate(
                "invalid_echo",
                {"message": "bad"},
                EchoPayload,
                ttl=timedelta(minutes=5),
            )

    async def test_adapter_decodes_explicit_text_json_without_weakening_default(self) -> None:
        adapter = McpAdapter(self.gateway, "contract")

        with self.assertRaises(AdapterSchemaError):
            await adapter.call_and_validate(
                "text_json",
                {"message": "strict"},
                EchoPayload,
                ttl=timedelta(minutes=5),
            )

        response = await adapter.call_and_validate(
            "text_json",
            {"message": "amap-compatible"},
            EchoPayload,
            ttl=timedelta(minutes=5),
            allow_text_json=True,
        )

        self.assertEqual(response.data.message, "amap-compatible")

    async def test_adapter_rejects_malformed_text_json_without_echoing_body(self) -> None:
        adapter = McpAdapter(self.gateway, "contract")

        with self.assertRaises(AdapterSchemaError) as context:
            await adapter.call_and_validate(
                "malformed_text_json",
                {},
                EchoPayload,
                ttl=timedelta(minutes=5),
                allow_text_json=True,
            )

        self.assertNotIn("not-json", str(context.exception))


class _FakeConnection:
    def __init__(self, *, delay_seconds: float = 0) -> None:
        self.delay_seconds = delay_seconds

    async def list_tools(self) -> tuple[ToolDefinition, ...]:
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        return (
            ToolDefinition(
                server_name="fake",
                name="echo",
                input_schema={"type": "object"},
            ),
        )

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> RawMcpToolResult:
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        return RawMcpToolResult(structured_content={"arguments": arguments})


class _FakeFactory:
    def __init__(
        self,
        connection: McpConnection,
        *,
        connection_failures: int = 0,
        always_fail: bool = False,
    ) -> None:
        self.connection = connection
        self.connection_failures = connection_failures
        self.always_fail = always_fail
        self.connect_calls = 0
        self.active = 0
        self.max_active = 0

    @asynccontextmanager
    async def connect(self, config: ServerConfig) -> AsyncGenerator[McpConnection]:
        self.connect_calls += 1
        if self.always_fail or self.connection_failures > 0:
            self.connection_failures = max(0, self.connection_failures - 1)
            raise ConnectionError(f"simulated connection failure for {config.name}")
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            yield self.connection
        finally:
            self.active -= 1


async def _no_sleep(_: float) -> None:
    return None


class McpGatewayReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_transient_connection_failures(self) -> None:
        factory = _FakeFactory(_FakeConnection(), connection_failures=2)
        config = ServerConfig(
            name="fake",
            transport=McpTransport.IN_MEMORY,
            max_retries=2,
            retry_backoff_seconds=0,
        )
        gateway = McpGateway(McpRegistry((config,)), factory, sleep=_no_sleep)

        tools = await gateway.discover_tools("fake")

        self.assertEqual(tools[0].name, "echo")
        self.assertEqual(factory.connect_calls, 3)
        self.assertEqual(gateway.traces()[-1].attempts, 3)
        self.assertEqual(gateway.health("fake").state, HealthState.UP)

    async def test_non_idempotent_tool_call_is_never_retried(self) -> None:
        factory = _FakeFactory(_FakeConnection())
        config = ServerConfig(
            name="fake",
            transport=McpTransport.IN_MEMORY,
            max_retries=3,
            retry_backoff_seconds=0,
        )
        gateway = McpGateway(McpRegistry((config,)), factory, sleep=_no_sleep)
        await gateway.discover_tools("fake")
        factory.connection_failures = 3
        baseline_calls = factory.connect_calls

        with self.assertRaises(ConnectionError):
            await gateway.call_tool("fake", "echo", {"value": 1})

        self.assertEqual(factory.connect_calls - baseline_calls, 1)

    async def test_timeout_marks_server_down_at_configured_threshold(self) -> None:
        factory = _FakeFactory(_FakeConnection(delay_seconds=0.05))
        config = ServerConfig(
            name="fake",
            transport=McpTransport.IN_MEMORY,
            timeout_seconds=0.01,
            max_retries=0,
            health_failure_threshold=1,
        )
        gateway = McpGateway(McpRegistry((config,)), factory)

        with self.assertRaises(McpCallTimeoutError):
            await gateway.discover_tools("fake")

        health = gateway.health("fake")
        self.assertEqual(health.state, HealthState.DOWN)
        self.assertEqual(health.last_error_type, "TimeoutError")

    async def test_semaphore_limits_parallel_connections(self) -> None:
        factory = _FakeFactory(_FakeConnection(delay_seconds=0.02))
        config = ServerConfig(
            name="fake",
            transport=McpTransport.IN_MEMORY,
            max_concurrency=2,
            max_retries=0,
        )
        gateway = McpGateway(McpRegistry((config,)), factory)
        await gateway.discover_tools("fake")
        factory.max_active = 0

        await asyncio.gather(
            *(gateway.call_tool("fake", "echo", {"index": index}) for index in range(6))
        )

        self.assertEqual(factory.max_active, 2)

    async def test_health_check_transitions_from_degraded_to_down(self) -> None:
        factory = _FakeFactory(_FakeConnection(), always_fail=True)
        config = ServerConfig(
            name="fake",
            transport=McpTransport.IN_MEMORY,
            max_retries=0,
            health_failure_threshold=2,
        )
        gateway = McpGateway(McpRegistry((config,)), factory)

        first = await gateway.health_check("fake")
        second = await gateway.health_check("fake")

        self.assertEqual(first.state, HealthState.DEGRADED)
        self.assertEqual(second.state, HealthState.DOWN)
        self.assertEqual(second.consecutive_failures, 2)

    async def test_disabled_server_is_not_called(self) -> None:
        factory = _FakeFactory(_FakeConnection())
        config = ServerConfig(name="fake", transport=McpTransport.IN_MEMORY, enabled=False)
        gateway = McpGateway(McpRegistry((config,)), factory)

        with self.assertRaises(ServerDisabledError):
            await gateway.discover_tools("fake")

        self.assertEqual(gateway.health("fake").state, HealthState.DISABLED)
        self.assertEqual(factory.connect_calls, 0)


class McpRegistryTests(unittest.TestCase):
    def test_rejects_duplicate_server_names(self) -> None:
        config = ServerConfig(name="fake", transport=McpTransport.IN_MEMORY)
        registry = McpRegistry((config,))

        with self.assertRaises(DuplicateServerError):
            registry.register(config)


if __name__ == "__main__":
    unittest.main()
