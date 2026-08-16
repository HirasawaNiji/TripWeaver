"""In-process MCP server and capability registry."""

from __future__ import annotations

from tripweaver.mcp_gateway.errors import DuplicateServerError, UnknownServerError
from tripweaver.mcp_gateway.models import ServerConfig, ToolDefinition


class McpRegistry:
    """Register servers and cache discovered tools without storing credentials in traces."""

    def __init__(self, configs: tuple[ServerConfig, ...] = ()) -> None:
        self._configs: dict[str, ServerConfig] = {}
        self._tools: dict[str, tuple[ToolDefinition, ...]] = {}
        for config in configs:
            self.register(config)

    def register(self, config: ServerConfig, *, replace: bool = False) -> None:
        if config.name in self._configs and not replace:
            raise DuplicateServerError(f"MCP server already registered: {config.name}")
        self._configs[config.name] = config
        self._tools.pop(config.name, None)

    def unregister(self, server_name: str) -> None:
        self.get(server_name)
        del self._configs[server_name]
        self._tools.pop(server_name, None)

    def get(self, server_name: str) -> ServerConfig:
        try:
            return self._configs[server_name]
        except KeyError as error:
            raise UnknownServerError(f"unknown MCP server: {server_name}") from error

    def list_configs(self, *, enabled_only: bool = False) -> tuple[ServerConfig, ...]:
        configs = self._configs.values()
        if enabled_only:
            configs = (config for config in configs if config.enabled)
        return tuple(sorted(configs, key=lambda config: config.name))

    def cache_tools(self, server_name: str, tools: tuple[ToolDefinition, ...]) -> None:
        self.get(server_name)
        self._tools[server_name] = tools

    def cached_tools(self, server_name: str) -> tuple[ToolDefinition, ...] | None:
        self.get(server_name)
        return self._tools.get(server_name)

    def invalidate_tools(self, server_name: str) -> None:
        self.get(server_name)
        self._tools.pop(server_name, None)
