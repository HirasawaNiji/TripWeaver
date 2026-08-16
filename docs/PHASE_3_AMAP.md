# Phase 3：高德 MCP Provider

## 目标

把高德官方 MCP 接入 TripWeaver 的查询层，同时维持三条边界：

1. 只查询，不登录、不预订、不下单。
2. 外部响应必须经过 schema 校验和来源标记，不能直接进入 Planner。
3. 实时服务失败时明确报错，Fixture 规划仍可独立运行。

## 调用链路

```text
.env / process environment
        ↓ secret-safe AmapSettings
ServerConfig (URL excluded from repr)
        ↓
McpGateway (timeout / retry / semaphore / trace)
        ↓
Official AMap Streamable HTTP MCP
        ↓ TextContent containing JSON
McpAdapter (explicit JSON decode + Pydantic wire schema)
        ↓
AmapProvider normalized models + SourceMetadata
```

Planner 和 Validator 不创建 MCP Client，也看不到 Key、远程 URL 或高德原始响应。

## 配置

复制 `.env.example` 为 `.env`：

```dotenv
AMAP_MAPS_API_KEY=你的高德Web服务Key
AMAP_MCP_TIMEOUT_SECONDS=20
AMAP_MCP_MAX_RETRIES=1
AMAP_MCP_MAX_CONCURRENCY=4
AMAP_MCP_MIN_INTERVAL_SECONDS=0.5
```

进程环境变量优先于 `.env`。`.env` 已被 Git 忽略；配置对象的 `repr` 不包含 Key，Gateway Trace 也不记录完整 URL、参数或响应正文。

## 当前 Provider 能力

| Provider 方法 | 高德 MCP 工具 | TTL | 规范化结果 |
|---|---|---:|---|
| `search_places` | `maps_text_search` | 6 小时 | POI ID、名称、地址、分类、图片 |
| `place_detail` | `maps_search_detail` | 6 小时 | 坐标、评分、营业时间文本、均价 |
| `weather` | `maps_weather` | 30 分钟 | 逐日昼夜天气、温度、风力 |
| `geocode` | `maps_geo` | 7 天 | GCJ-02 坐标、行政区和 adcode |
| `walking_route` | `maps_direction_walking` | 15 分钟 | 距离与耗时 |
| `transit_route` | `maps_direction_transit_integrated` | 15 分钟 | 最短耗时方案、总距离和步行距离 |

`verify_capabilities` 会检查以上六个工具是否仍存在。高德工具发生改名或下线时，系统拒绝假装健康。

## 文本 JSON 契约

真实高德 MCP 返回一个 `TextContent`，内容为 JSON，而不是 MCP `structuredContent`。因此通用 `McpAdapter` 保持严格默认，只允许高德 Provider 显式传入 `allow_text_json=True`。

解码器要求：

- 恰好一个非空文本块；
- 内容必须是合法 JSON；
- JSON 必须通过对应 Pydantic wire schema；
- 解析异常不得包含原始载荷。

该策略避免为了兼容一个供应商而削弱所有 MCP 的 schema 边界。

## 失败与降级

- 工具发现和只读查询允许有限重试；
- 超时、连接错误和 schema 漂移会更新 Gateway 健康状态；
- 工具返回 `is_error=True` 不自动重试；
- CLI 失败返回状态码 `3`，并提示实时地图不可用；
- `tripweaver demo` 与 `tripweaver plan` 仍可使用确定性 Fixture，不依赖高德在线状态。

现阶段不会把缺失的门票、游玩时长或复杂营业日历填成“实时高德数据”。这些字段必须在下一阶段建立明确的估算和验证策略后，才能进入确定性 Planner。

## 验证

自动测试不消耗高德配额，使用内存 MCP 和与真实响应一致的文本 JSON Fixture：

```powershell
$env:PYTHONPATH = "src"
uv run python -m unittest discover -s tests -v
```

人工端到端探针：

```powershell
uv run tripweaver amap health
uv run tripweaver amap weather "上海" --json
uv run tripweaver amap search "博物馆" --city "上海" --limit 3 --json
uv run tripweaver amap detail "搜索返回的POI_ID" --json
```

输出中的 `source_reference` 只包含 Provider、工具名和随机 Trace ID，不包含 Key。
