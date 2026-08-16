"""Reliable discovery, invocation, retry, throttling, health, and tracing."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from uuid import uuid4

from tripweaver.mcp_gateway.client import McpClientFactory, McpConnection
from tripweaver.mcp_gateway.errors import (
    McpCallTimeoutError,
    McpConnectionError,
    McpGatewayError,
    ServerDisabledError,
    UnknownToolError,
)
from tripweaver.mcp_gateway.models import (
    GatewayToolResult,
    HealthState,
    InvocationTrace,
    RawMcpToolResult,
    ServerConfig,
    ServerHealth,
    ToolDefinition,
    TraceOutcome,
)
from tripweaver.mcp_gateway.registry import McpRegistry


@dataclass
class _Execution[T]:
    value: T
    trace: InvocationTrace


@dataclass
class _MutableHealth:
    state: HealthState = HealthState.UNKNOWN
    consecutive_failures: int = 0
    last_checked_at: datetime | None = None
    last_latency_ms: int | None = None
    last_error_type: str | None = None


class McpGateway:
    """Single policy boundary for every external MCP operation."""

    def __init__(
        self,
        registry: McpRegistry,
        client_factory: McpClientFactory,
        *,
        trace_capacity: int = 200,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.registry = registry
        self._client_factory = client_factory
        self._sleep = sleep
        self._semaphores = {
            config.name: asyncio.Semaphore(config.max_concurrency)
            for config in registry.list_configs()
        }
        self._health = {
            config.name: _MutableHealth(
                state=HealthState.UNKNOWN if config.enabled else HealthState.DISABLED
            )
            for config in registry.list_configs()
        }
        self._traces: deque[InvocationTrace] = deque(maxlen=trace_capacity)

    async def discover_tools(
        self, server_name: str, *, force_refresh: bool = False
    ) -> tuple[ToolDefinition, ...]:
        config = self._enabled_config(server_name)
        if not force_refresh and (cached := self.registry.cached_tools(server_name)) is not None:
            return cached

        execution = await self._execute(
            config,
            operation="tools/list",
            tool_name=None,
            allow_retry=True,
            invoke=lambda connection: connection.list_tools(),
        )
        self.registry.cache_tools(server_name, execution.value)
        return execution.value

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, object],
        *,
        idempotent: bool = False,
    ) -> GatewayToolResult:
        config = self._enabled_config(server_name)
        tools = await self.discover_tools(server_name)
        if tool_name not in {tool.name for tool in tools}:
            raise UnknownToolError(f"unknown MCP tool: {server_name}/{tool_name}")

        execution = await self._execute(
            config,
            operation="tools/call",
            tool_name=tool_name,
            allow_retry=idempotent,
            invoke=lambda connection: connection.call_tool(tool_name, dict(arguments)),
        )
        raw: RawMcpToolResult = execution.value
        trace = execution.trace
        return GatewayToolResult(
            server_name=server_name,
            tool_name=tool_name,
            content_text=raw.content_text,
            structured_content=raw.structured_content,
            is_error=raw.is_error,
            trace_id=trace.trace_id,
            attempts=trace.attempts,
            started_at=trace.started_at,
            completed_at=trace.completed_at,
            duration_ms=trace.duration_ms,
        )

    async def health_check(self, server_name: str) -> ServerHealth:
        config = self.registry.get(server_name)
        if not config.enabled:
            return self.health(server_name)
        try:
            await self.discover_tools(server_name, force_refresh=True)
        except (McpGatewayError, ConnectionError, OSError, TimeoutError):
            return self.health(server_name)
        return self.health(server_name)

    async def health_check_all(self) -> tuple[ServerHealth, ...]:
        configs = self.registry.list_configs()
        return tuple(await asyncio.gather(*(self.health_check(config.name) for config in configs)))

    def health(self, server_name: str) -> ServerHealth:
        config = self.registry.get(server_name)
        state = self._health.setdefault(
            server_name,
            _MutableHealth(state=HealthState.UNKNOWN if config.enabled else HealthState.DISABLED),
        )
        return ServerHealth(
            server_name=server_name,
            state=state.state,
            consecutive_failures=state.consecutive_failures,
            last_checked_at=state.last_checked_at,
            last_latency_ms=state.last_latency_ms,
            last_error_type=state.last_error_type,
        )

    def traces(self) -> tuple[InvocationTrace, ...]:
        return tuple(self._traces)

    def _enabled_config(self, server_name: str) -> ServerConfig:
        config = self.registry.get(server_name)
        if not config.enabled:
            raise ServerDisabledError(f"MCP server is disabled: {server_name}")
        self._semaphores.setdefault(server_name, asyncio.Semaphore(config.max_concurrency))
        self._health.setdefault(server_name, _MutableHealth())
        return config

    async def _execute[T](
        self,
        config: ServerConfig,
        *,
        operation: str,
        tool_name: str | None,
        allow_retry: bool,
        invoke: Callable[[McpConnection], Awaitable[T]],
    ) -> _Execution[T]:
        trace_id = uuid4().hex
        started_at = datetime.now(UTC)
        started_tick = monotonic()
        total_attempts = config.max_retries + 1 if allow_retry else 1
        last_error: Exception | None = None

        for attempt in range(1, total_attempts + 1):
            try:
                async with self._semaphores[config.name]:
                    async with asyncio.timeout(config.timeout_seconds):
                        async with self._client_factory.connect(config) as connection:
                            value = await invoke(connection)
                trace = self._finish_trace(
                    trace_id,
                    config.name,
                    operation,
                    tool_name,
                    TraceOutcome.SUCCESS,
                    attempt,
                    started_at,
                    started_tick,
                )
                self._record_success(config.name, trace)
                return _Execution(value=value, trace=trace)
            except asyncio.CancelledError:
                trace = self._finish_trace(
                    trace_id,
                    config.name,
                    operation,
                    tool_name,
                    TraceOutcome.CANCELLED,
                    attempt,
                    started_at,
                    started_tick,
                    error_type="CancelledError",
                )
                self._traces.append(trace)
                raise
            except Exception as error:
                last_error = error
                if self._is_retryable(error) and attempt < total_attempts:
                    delay = config.retry_backoff_seconds * (2 ** (attempt - 1))
                    if delay:
                        await self._sleep(delay)
                    continue
                trace = self._finish_trace(
                    trace_id,
                    config.name,
                    operation,
                    tool_name,
                    TraceOutcome.FAILED,
                    attempt,
                    started_at,
                    started_tick,
                    error_type=type(error).__name__,
                )
                self._record_failure(config, trace)
                if isinstance(error, TimeoutError):
                    raise McpCallTimeoutError(
                        f"MCP operation timed out: {config.name}/{operation}"
                    ) from error
                raise

        raise AssertionError(f"unreachable MCP retry state: {last_error!r}")

    @staticmethod
    def _is_retryable(error: Exception) -> bool:
        return isinstance(
            error,
            (TimeoutError, ConnectionError, OSError, McpConnectionError),
        )

    def _finish_trace(
        self,
        trace_id: str,
        server_name: str,
        operation: str,
        tool_name: str | None,
        outcome: TraceOutcome,
        attempts: int,
        started_at: datetime,
        started_tick: float,
        error_type: str | None = None,
    ) -> InvocationTrace:
        completed_at = datetime.now(UTC)
        duration_ms = max(0, int((monotonic() - started_tick) * 1000))
        return InvocationTrace(
            trace_id=trace_id,
            server_name=server_name,
            operation=operation,
            tool_name=tool_name,
            outcome=outcome,
            attempts=attempts,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            error_type=error_type,
        )

    def _record_success(self, server_name: str, trace: InvocationTrace) -> None:
        state = self._health[server_name]
        state.state = HealthState.UP
        state.consecutive_failures = 0
        state.last_checked_at = trace.completed_at
        state.last_latency_ms = trace.duration_ms
        state.last_error_type = None
        self._traces.append(trace)

    def _record_failure(self, config: ServerConfig, trace: InvocationTrace) -> None:
        state = self._health[config.name]
        state.consecutive_failures += 1
        state.state = (
            HealthState.DOWN
            if state.consecutive_failures >= config.health_failure_threshold
            else HealthState.DEGRADED
        )
        state.last_checked_at = trace.completed_at
        state.last_latency_ms = trace.duration_ms
        state.last_error_type = trace.error_type
        self._traces.append(trace)
