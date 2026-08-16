"""Canonical MCP Gateway configuration and result models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, JsonValue, model_validator

from tripweaver.domain.models import DomainModel


class McpTransport(StrEnum):
    STREAMABLE_HTTP = "STREAMABLE_HTTP"
    STDIO = "STDIO"
    IN_MEMORY = "IN_MEMORY"


class HealthState(StrEnum):
    UNKNOWN = "UNKNOWN"
    UP = "UP"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"
    DISABLED = "DISABLED"


class TraceOutcome(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ServerConfig(DomainModel):
    """Safe runtime policy for one MCP server.

    URLs and subprocess environment values are excluded from repr so traces and
    logs do not accidentally expose credentials.
    """

    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    transport: McpTransport
    enabled: bool = True
    url: str | None = Field(default=None, repr=False)
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = Field(default_factory=dict, repr=False)
    timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    max_retries: int = Field(default=2, ge=0, le=5)
    retry_backoff_seconds: float = Field(default=0.05, ge=0, le=30)
    max_concurrency: int = Field(default=4, ge=1, le=100)
    health_failure_threshold: int = Field(default=3, ge=1, le=20)

    @model_validator(mode="after")
    def validate_transport_fields(self) -> ServerConfig:
        if self.transport == McpTransport.STREAMABLE_HTTP and not self.url:
            raise ValueError("Streamable HTTP servers require url")
        if self.transport == McpTransport.STDIO and not self.command:
            raise ValueError("stdio servers require command")
        if self.transport != McpTransport.STREAMABLE_HTTP and self.url is not None:
            raise ValueError("url is only valid for Streamable HTTP servers")
        if self.transport != McpTransport.STDIO and (
            self.command is not None or self.args or self.env
        ):
            raise ValueError("command, args, and env are only valid for stdio servers")
        return self


class ToolDefinition(DomainModel):
    server_name: str
    name: str
    title: str | None = None
    description: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)


class RawMcpToolResult(DomainModel):
    content_text: tuple[str, ...] = ()
    structured_content: JsonValue | None = None
    is_error: bool = False


class GatewayToolResult(DomainModel):
    server_name: str
    tool_name: str
    content_text: tuple[str, ...] = ()
    structured_content: JsonValue | None = None
    is_error: bool
    trace_id: str
    attempts: int = Field(ge=1)
    started_at: datetime
    completed_at: datetime
    duration_ms: int = Field(ge=0)


class InvocationTrace(DomainModel):
    trace_id: str
    server_name: str
    operation: str
    tool_name: str | None = None
    outcome: TraceOutcome
    attempts: int = Field(ge=1)
    started_at: datetime
    completed_at: datetime
    duration_ms: int = Field(ge=0)
    error_type: str | None = None


class ServerHealth(DomainModel):
    server_name: str
    state: HealthState
    consecutive_failures: int = Field(ge=0)
    last_checked_at: datetime | None = None
    last_latency_ms: int | None = Field(default=None, ge=0)
    last_error_type: str | None = None
