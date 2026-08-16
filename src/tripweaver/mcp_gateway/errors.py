"""Safe, typed errors exposed by the MCP Gateway."""


class McpGatewayError(RuntimeError):
    """Base error for Gateway configuration or execution failures."""


class DuplicateServerError(McpGatewayError):
    pass


class UnknownServerError(McpGatewayError):
    pass


class ServerDisabledError(McpGatewayError):
    pass


class UnknownToolError(McpGatewayError):
    pass


class McpConnectionError(McpGatewayError):
    pass


class McpProtocolError(McpGatewayError):
    pass


class McpCallTimeoutError(McpGatewayError):
    pass


class ToolExecutionError(McpGatewayError):
    pass


class AdapterSchemaError(McpGatewayError):
    pass
