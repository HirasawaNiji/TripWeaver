# Phase 4：混合实时约束规划

## 目标

让确定性 Planner 真正消费高德数据，同时不把高德没有提供或无法可靠解析的字段包装成实时事实。

`plan-live` 当前组合：

| 规划事实 | 来源 | 状态 |
|---|---|---|
| 景点名称、类别、地址、坐标 | 高德 MCP | 实时事实 |
| 市内公交距离、耗时 | 高德 MCP | 实时事实 |
| 高德营业时间原文 | 高德 MCP | 复核提示 |
| 可计算开放窗口、闭馆日 | 本地规划基线 | 估算 |
| 建议游玩时长、门票 | 本地规划基线 | 估算 |
| 公交费用 | 每段 5 元策略 | 估算 |
| 往返交通班次与价格 | Fixture | Fixture |
| 住宿区域与价格 | Fixture | Fixture/估算 |
| 短期天气 | 高德 MCP，且必须覆盖旅行日期 | 实时事实或缺失 |

因为一次行程仍包含混合数据，顶层 `data_mode` 为 `ESTIMATED`，不会标记为 `LIVE`。

## 两阶段执行

```text
Natural-language request
        ↓ deterministic parser
Concurrent AMap POI / detail / weather queries
        ↓
Concurrent route matrix queries (Gateway Semaphore)
        ↓
FrozenPlanningCatalog
        ↓ no network I/O
DeterministicPlanner
        ↓
Independent ItineraryValidator
        ↓
HybridPlanResult + provenance + warnings
```

网络查询与规划算法严格分离。`FrozenPlanningCatalog` 实现 `PlanningCatalog` 协议；Planner 在路线排序、开放时间计算和预算计算过程中只读取内存快照。

## 景点选择

现阶段仍以北京到上海 Fixture 提供的景点基线作为查询种子：

1. 使用 `priority + 用户兴趣加权` 稳定排序；
2. 最多选择 4 个种子，控制 POI 和路线调用量；
3. 高德关键词搜索后，只接受名称相等或互相包含的候选；
4. 用 POI ID 查询详情并获取 GCJ-02 坐标；
5. 单个景点失败时移除并产生 warning；
6. 少于 2 个实时景点时完整回退 Fixture。

景点 ID 使用 `amap-<POI_ID>`，避免与 Fixture ID 混淆。

## 路线矩阵

Snapshot Builder 会为候选住宿区域和候选景点预取有向公交路线。默认 4 个景点时最多约 20 条路线，实际并发受 `AMAP_MCP_MAX_CONCURRENCY` 控制，请求启动速率受 `AMAP_MCP_MIN_INTERVAL_SECONDS` 节流。默认每 500ms 最多启动一个高德调用，以降低个人配额下的随机限流。

每条路线分别处理：

- 高德成功：距离和耗时取实时结果，费用按 5 元估算；
- 高德失败：使用 Haversine 派生的确定性估算，并标记 `tripweaver_route_estimator`；
- Planner 看到的是完整、冻结的路线表，不会在排序中触发额外调用。

## 天气时间有效性

高德通常只返回短期天气。Builder 会将预报日期与 `TripRequest.start_date/end_date` 求交集：

- 有重叠：只保留覆盖旅行日期的预报；
- 无重叠：天气设为 `None`，输出“预报未覆盖旅行日期”提示；
- 调用失败：天气设为 `None`，不阻断地图规划。

因此当前天气不会被错误地当作未来数周或数月后的旅行天气。

## 降级规则

| 故障 | 行为 |
|---|---|
| 单个 POI 搜索或详情失败 | 移除该候选，继续规划 |
| 单条公交路线失败 | 使用明确标注的估算路线 |
| 天气失败或日期不覆盖 | 不写入天气事实 |
| 可用实时 POI 少于 2 个 | 完整回退 Phase 1 Fixture |
| Planner 判定预算或时间不可行 | 返回不可行，不用 Fixture 掩盖约束冲突 |

降级结果只保存安全异常类型，不保存远程错误正文、查询参数或 Key。

## 使用

```powershell
uv run tripweaver plan-live "从北京去上海玩3天，2026-10-01出发，2个人，预算5000元，喜欢历史文化、城市夜景和美食街区"
```

完整 JSON 会包含：

- `plan`：结构化行程、预算和 Validator 结果；
- `live_map_used`：是否成功使用实时地图快照；
- `map_places`：原始营业时间提示和字段假设；
- `weather`：仅包含覆盖旅行日期的短期预报；
- `fallback_reason`：完整降级时的安全异常类型。

## 当前边界

- 往返交通和住宿仍限制为北京到上海 Fixture。
- 未解析复杂的季节、节假日、预约和临时闭馆规则。
- 未把高德商户均价当作景点门票。
- 未登录、抢票、预订、下单或支付。
- 进程退出后不持久化实时快照和路线缓存。
