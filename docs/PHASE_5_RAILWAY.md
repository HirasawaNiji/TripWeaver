# Phase 5：只读铁路 MCP 与实时交通快照

## 本阶段目标

把真实铁路候选纳入确定性约束规划，同时保持数据源、时效和降级状态可验证。该阶段不登录 12306，不抢票，不预订，也不下单。

## 数据源边界

- 铁路使用 [Joooook/12306-mcp](https://github.com/Joooook/12306-mcp)。这是社区开源项目，不是中国铁路 12306 官方发布的 MCP。
- 当前固定 `12306-mcp@0.3.10`，通过 `npx -y` 启动 stdio Server；上游要求 Node.js 18+。
- TripWeaver 只调用 `get-tickets`，并强制请求 `format=json`。
- 飞常准的 [Variflight MCP](https://github.com/variflight/variflight-mcp) 需要独立 `VARIFLIGHT_API_KEY`。在没有凭证和稳定响应契约前，航空候选继续明确使用 Fixture。

## 执行链路

```text
TripRequest
  ├─ AMap snapshot：POI / 天气 / 市内路线
  └─ Railway snapshot：去程和返程并发只读查询
           ↓
     严格 JSON schema 校验
           ↓
     明确余票 + 正价格席别过滤
           ↓
     按单程替换 Fixture 铁路候选
           ↓
     冻结 PlanningCatalog
           ↓
     交通时间窗硬过滤 → 成本/耗时选择 → 行程安排 → Validator
```

Planner 的算法循环不会发网络请求。铁路和地图查询全部在快照阶段完成，随后只消费冻结数据。

## 余票归一化策略

上游每个车次可能包含多个席别。TripWeaver 当前为每个车次选择价格最低且可用性明确的席别：

- `有`、`充足`：视为可用；
- 正整数：视为明确余票并保留数量提示；
- `候补`、`无`、`--`、空值或未知文本：不视为可售；
- 价格小于等于 0：不进入候选。

每个归一化结果附带：

- `provider=railway_12306`
- `status=LIVE`
- UTC 查询时间
- 约 2 分钟 `expires_at`
- 不包含查询参数的 `mcp://...trace=...` 来源引用
- `confidence=0.9`

## 分程降级

去程和返程独立处理：

- 某一程存在实时候选：删除该程的 Fixture 铁路候选，保留实时铁路；
- 某一程调用失败、返回空集或没有可确认席别：该程保留 Fixture；
- Fixture 航空候选继续保留并明确标注，不会改写为实时数据；
- 错误输出只暴露异常类型，不回显上游响应或用户查询内容。

这避免了“返程失败导致去程实时数据也丢失”，也避免实时铁路和模拟铁路在同一程相互竞争。

## 新增硬约束

真实联调曾选中价格更低但 23:51 才到达的车次，导致首日不可执行。现在交通候选在成本比较前必须满足：

- 去程抵达、预留 60 分钟接驳后，首日仍至少有 2 小时活动窗口；
- 返程预留 90 分钟进站后，末日仍至少有 2 小时活动窗口；
- 去程出发日和返程出发日必须分别匹配旅行起止日期。

价格和时长只在通过这些硬约束的候选中参与确定性排序。

## 配置

参考 `.env.example`：

```dotenv
RAILWAY_MCP_ENABLED=true
RAILWAY_MCP_PACKAGE=12306-mcp@0.3.10
RAILWAY_MCP_TIMEOUT_SECONDS=40
RAILWAY_MCP_MAX_RETRIES=1
RAILWAY_MCP_MAX_CONCURRENCY=2
RAILWAY_MCP_CANDIDATE_LIMIT=20
```

`RAILWAY_MCP_PACKAGE` 只接受 `12306-mcp` 或固定语义版本，避免通过配置执行任意 npm 包。

## 验证命令

```powershell
uv run tripweaver railway health --json
uv run tripweaver railway search <售票窗口内日期> 北京 上海 --limit 10 --json
uv run tripweaver plan-live "从北京去上海玩3天，<有效日期>出发，2个人，预算8000元，喜欢历史文化和城市夜景，高铁。"
$env:PYTHONPATH = "src"
uv run python -m unittest discover -s tests -v
uvx ruff check src tests
uv run --with pyright pyright
```

## 当前限制

- 12306 上游接口、限流和售票窗口可能变化；实时查询失败是正常降级场景。
- 当前只接入直达 `get-tickets`，尚未把中转方案转成多段 `TransportOption`。
- 当前每个车次只保留一个最低可用席别，尚未支持用户指定席别偏好。
- stdio 模式会启动 Node.js 子进程；后续可增加常驻 HTTP Sidecar 与短 TTL 缓存来降低启动延迟。
- 航空仍为 Fixture；获得飞常准 API Key 后再实现严格 wire schema 和独立航空快照。
