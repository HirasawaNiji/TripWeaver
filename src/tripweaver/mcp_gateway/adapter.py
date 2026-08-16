"""Schema-validation boundary between raw MCP tools and TripWeaver domain data."""

from __future__ import annotations

import json
from datetime import timedelta
from time import monotonic
from typing import Any, cast

from pydantic import TypeAdapter, ValidationError

from tripweaver.domain.models import DataStatus, DomainModel, SourceMetadata
from tripweaver.mcp_gateway.errors import AdapterSchemaError, ToolExecutionError
from tripweaver.mcp_gateway.gateway import McpGateway


class NormalizedMcpResponse[T](DomainModel):
    data: T
    source: SourceMetadata
    trace_id: str


class McpAdapter:
    """Base helper for source-specific adapters.

    Concrete AMap, aviation, and railway adapters own their external schemas and
    call this method before mapping into canonical domain models.
    """

    def __init__(self, gateway: McpGateway, server_name: str) -> None:
        self._gateway = gateway
        self._server_name = server_name
        self._query_cache: dict[str, tuple[float, NormalizedMcpResponse[Any]]] = {}

    async def call_and_validate[T](
        self,
        tool_name: str,
        arguments: dict[str, object],
        response_type: type[T] | TypeAdapter[T],
        *,
        ttl: timedelta,
        confidence: float = 1.0,
        idempotent: bool = True,
        allow_text_json: bool = False,
    ) -> NormalizedMcpResponse[T]:
        cache_key = json.dumps(
            (tool_name, arguments), sort_keys=True, ensure_ascii=False, default=str
        )
        cached = self._query_cache.get(cache_key)
        if cached is not None and cached[0] > monotonic():
            response = cast(NormalizedMcpResponse[T], cached[1])
            return response.model_copy(
                update={
                    "source": response.source.model_copy(update={"status": DataStatus.CACHED})
                }
            )
        result = await self._gateway.call_tool(
            self._server_name,
            tool_name,
            arguments,
            idempotent=idempotent,
        )
        if result.is_error:
            raise ToolExecutionError(f"MCP tool returned an error: {self._server_name}/{tool_name}")
        payload = result.structured_content
        if payload is None and allow_text_json:
            payload = self._decode_text_json(result.content_text, tool_name)
        if payload is None:
            raise AdapterSchemaError(
                f"MCP tool omitted structured content: {self._server_name}/{tool_name}"
            )
        adapter = (
            response_type if isinstance(response_type, TypeAdapter) else TypeAdapter(response_type)
        )
        try:
            data = adapter.validate_python(payload)
        except ValidationError as error:
            raise AdapterSchemaError(
                f"MCP tool returned incompatible schema: {self._server_name}/{tool_name}"
            ) from error
        source = SourceMetadata(
            provider=self._server_name,
            status=DataStatus.LIVE,
            queried_at=result.completed_at,
            expires_at=result.completed_at + ttl,
            source_reference=(f"mcp://{self._server_name}/{tool_name}?trace={result.trace_id}"),
            confidence=confidence,
        )
        normalized = NormalizedMcpResponse(
            data=data,
            source=source,
            trace_id=result.trace_id,
        )
        self._query_cache[cache_key] = (
            monotonic() + max(ttl.total_seconds(), 0),
            cast(NormalizedMcpResponse[Any], normalized),
        )
        return normalized

    def _decode_text_json(self, content: tuple[str, ...], tool_name: str) -> object:
        """Decode an explicitly opted-in, single-block JSON text response.

        Some remote MCP servers, including AMap, serialize their JSON payload in
        ``TextContent`` instead of the protocol's ``structuredContent`` field.
        The raw body is never included in errors because it may contain private
        addresses or other user-supplied data.
        """

        if len(content) != 1 or not content[0].strip():
            raise AdapterSchemaError(
                f"MCP tool returned invalid JSON text blocks: {self._server_name}/{tool_name}"
            )
        try:
            return json.loads(content[0])
        except (json.JSONDecodeError, UnicodeError) as error:
            raise AdapterSchemaError(
                f"MCP tool returned malformed JSON text: {self._server_name}/{tool_name}"
            ) from error
