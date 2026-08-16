"""Run a real MCP SDK v2 in-memory contract through TripWeaver's Gateway."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from mcp.server import MCPServer

from tripweaver.mcp_gateway import (
    McpGateway,
    McpRegistry,
    McpSdkClientFactory,
    McpTransport,
    ServerConfig,
)


def build_server() -> MCPServer[Any]:
    server = MCPServer("TripWeaver Gateway Demo")

    @server.tool(title="Search demo places")
    def search_places(  # pyright: ignore[reportUnusedFunction]
        city: str,
    ) -> dict[str, object]:
        """Return deterministic demo data through the real MCP protocol layer."""

        return {
            "city": city,
            "places": ["外滩（模拟）", "上海博物馆（模拟）"],
        }

    return server


async def run() -> None:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8")
    config = ServerConfig(name="demo", transport=McpTransport.IN_MEMORY)
    gateway = McpGateway(
        McpRegistry((config,)),
        McpSdkClientFactory({"demo": build_server()}),
    )

    tools = await gateway.discover_tools("demo")
    result = await gateway.call_tool(
        "demo",
        "search_places",
        {"city": "上海"},
        idempotent=True,
    )

    print("Discovered tools:", [tool.name for tool in tools])
    print("Structured result:", result.structured_content)
    print("Health:", gateway.health("demo").state)
    print("Trace attempts:", result.attempts)


if __name__ == "__main__":
    asyncio.run(run())
