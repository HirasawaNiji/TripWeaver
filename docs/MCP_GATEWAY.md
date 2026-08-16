# Phase 2：MCP Gateway

## 目标

把所有外部 MCP 调用收敛到一个可靠性边界中。Planner、Validator 和业务 Adapter 不直接创建 MCP Client，也不处理 transport、重试、并发或协议错误。

## 调用链路

```text
Source-specific Adapter
        ↓ schema validation
McpGateway.call_tool(..., idempotent=True)
        ↓ capability allow-list
Per-server Semaphore
        ↓ timeout / retry policy
McpSdkClientFactory
        ↓
Streamable HTTP | stdio | in-memory MCP SDK transport
```

## 核心组件

- `ServerConfig`：transport、超时、重试、并发和健康阈值。
- `McpRegistry`：服务注册、启停配置和工具缓存。
- `McpSdkClientFactory`：隔离 MCP SDK v2，创建 HTTP、stdio 或内存 Client。
- `McpGateway`：工具发现、调用、Semaphore、超时、重试、健康和 Trace。
- `McpAdapter`：检查 `is_error`，用 Pydantic 验证外部 schema；只有来源 Adapter 显式选择时才解析单块文本 JSON。

## Transport 配置

Streamable HTTP：

```python
ServerConfig(
    name="amap",
    transport=McpTransport.STREAMABLE_HTTP,
    url=amap_mcp_url,
    timeout_seconds=10,
    max_retries=2,
    max_concurrency=4,
)
```

stdio：

```python
ServerConfig(
    name="railway",
    transport=McpTransport.STDIO,
    command="npx",
    args=("-y", "12306-mcp"),
    env={},
)
```

URL 和 subprocess 环境变量不会进入调用 Trace，也从 `ServerConfig` 的 repr 中隐藏。后续真实配置必须来自环境变量或 Secret Manager，不能提交到仓库。

## 重试语义

- 工具发现始终视为幂等操作，可对超时和连接异常重试。
- 工具调用默认不重试。
- 旅行查询 Adapter 可以显式设置 `idempotent=True`。
- MCP `is_error=True` 是工具执行结果，不是 transport 故障，不进行自动重试。
- MCP 协议错误不假定可恢复，不进行自动重试。
- 重试采用有限次数的指数退避。

该规则避免在连接中断时重复执行未来可能存在的预订、写入或支付工具。

## 健康状态

- `UNKNOWN`：尚未调用。
- `UP`：最近一次操作成功。
- `DEGRADED`：存在连续失败，但未达到阈值。
- `DOWN`：连续失败达到阈值。
- `DISABLED`：配置明确禁用，不发起连接。

`health_check()` 强制刷新工具列表，以协议级发现作为健康探针。

## Trace

Trace 仅保存：

- 随机 Trace ID
- Server 和操作名
- Tool 名
- 开始/完成时间与耗时
- 尝试次数
- 结果状态和异常类型

Trace 不保存 Tool 参数、响应内容、URL、API Key 或 subprocess 环境变量。

## Adapter 约束

Adapter 必须：

1. 检查 `is_error`。
2. 默认拒绝缺失 `structured_content` 的响应；已确认只返回文本 JSON 的服务可以显式启用安全解码。
3. 使用 Pydantic schema 校验外部数据。
4. 将外部 schema 映射到 TripWeaver 领域模型。
5. 为规范化结果生成查询时间、TTL、来源 URI 和置信度。

文本 JSON 兼容模式只接受一个非空文本块，解析失败时不会在异常或 Trace 中回显原始载荷。它不会改变其他 MCP Server 的严格默认行为。

## Contract tests

Phase 2 使用 MCP SDK v2 的 `Client(server)` 内存 transport，协议发现、输入校验、Tool 调用和结构化输出均经过真实 MCP 协议层。同时通过故障注入测试：

- 两次连接失败后的成功重试
- 超时与 DOWN 状态
- 并发连接上限
- DEGRADED → DOWN 状态转换
- 禁用服务零调用
- 非幂等 Tool 零重试
- Tool error 与 transport error 的区分
- Adapter schema 不兼容拒绝

## 当前限制

- 每次操作创建独立 Client；尚未实现长期连接池。
- 工具缓存、健康和 Trace 只保存在当前进程内。
- 尚未加入分布式限流、持久化指标和 circuit breaker 半开探测。
- 高德 schema 已在 Phase 3 归一化；铁路和航空仍留给后续 Adapter。

官方 SDK 参考：[MCP Python SDK v2](https://github.com/modelcontextprotocol/python-sdk)、[Client 文档](https://py.sdk.modelcontextprotocol.io/client/)。
