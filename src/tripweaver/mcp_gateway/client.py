"""MCP SDK v2 transport adapter and testable client protocols."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol

from mcp import Client, MCPError, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

from tripweaver.mcp_gateway.errors import (
    McpConnectionError,
    McpProtocolError,
    UnknownServerError,
)
from tripweaver.mcp_gateway.models import (
    McpTransport,
    RawMcpToolResult,
    ServerConfig,
    ToolDefinition,
)


class McpConnection(Protocol):
    async def list_tools(self) -> tuple[ToolDefinition, ...]: ...

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> RawMcpToolResult: ...


class McpClientFactory(Protocol):
    def connect(self, config: ServerConfig) -> AbstractAsyncContextManager[McpConnection]: ...


class _SdkMcpConnection:
    def __init__(self, server_name: str, client: Any) -> None:
        self._server_name = server_name
        self._client = client

    async def list_tools(self) -> tuple[ToolDefinition, ...]:
        tools: list[ToolDefinition] = []
        cursor: str | None = None
        try:
            while True:
                result = await self._client.list_tools(cursor=cursor)
                tools.extend(
                    ToolDefinition(
                        server_name=self._server_name,
                        name=tool.name,
                        title=tool.title,
                        description=tool.description,
                        input_schema=dict(tool.input_schema),
                    )
                    for tool in result.tools
                )
                cursor = result.next_cursor
                if cursor is None:
                    break
        except MCPError as error:
            raise McpProtocolError(
                f"MCP protocol error while listing tools for {self._server_name}"
            ) from error
        return tuple(sorted(tools, key=lambda tool: tool.name))

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> RawMcpToolResult:
        try:
            result = await self._client.call_tool(tool_name, arguments)
        except MCPError as error:
            raise McpProtocolError(
                f"MCP protocol error calling {self._server_name}/{tool_name}"
            ) from error
        content_text = tuple(
            block.text for block in result.content if isinstance(block, TextContent)
        )
        return RawMcpToolResult(
            content_text=content_text,
            structured_content=result.structured_content,
            is_error=result.is_error,
        )


class McpSdkClientFactory:
    """Create MCP SDK v2 clients for HTTP, stdio, or in-memory servers."""

    def __init__(self, in_memory_servers: Mapping[str, Any] | None = None) -> None:
        self._in_memory_servers = dict(in_memory_servers or {})

    @asynccontextmanager
    async def connect(self, config: ServerConfig) -> AsyncGenerator[McpConnection]:
        source = self._resolve_source(config)
        try:
            async with Client(source) as client:
                yield _SdkMcpConnection(config.name, client)
        except MCPError as error:
            raise McpProtocolError(f"MCP protocol connection failed for {config.name}") from error
        except (OSError, ConnectionError) as error:
            raise McpConnectionError(
                f"MCP transport connection failed for {config.name}"
            ) from error

    def _resolve_source(self, config: ServerConfig) -> Any:
        if config.transport == McpTransport.STREAMABLE_HTTP:
            return config.url
        if config.transport == McpTransport.STDIO:
            return stdio_client(
                StdioServerParameters(
                    command=config.command or "",
                    args=list(config.args),
                    env=dict(config.env) or None,
                )
            )
        try:
            return self._in_memory_servers[config.name]
        except KeyError as error:
            raise UnknownServerError(
                f"no in-memory MCP server object registered for {config.name}"
            ) from error
