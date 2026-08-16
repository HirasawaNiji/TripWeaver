# TripWeaver

TripWeaver 是一个基于 MCP 的多源约束旅行规划 Agent。它的目标不是让大模型自由生成攻略，而是把交通、地图、住宿和用户约束转换成一份有来源、可计算、可验证的结构化行程。

> 当前版本是 Phase 10 Portfolio MVP。`agent` 会在硬约束完整后调用高德、12306 社区 MCP 和飞常准 MCP，使用确定性 Planner 与独立 Validator 生成可执行方案；住宿价格、门票及复杂营业规则仍明确标注用户输入、Fixture 或估算，绝不伪装成全实时预订系统。

## Phase 1 已实现

- 将有限格式的中文旅行需求解析为 `TripRequest`
- 使用北京往返上海的确定性 Fixture 数据代替外部 MCP
- 选择往返交通和住宿区域
- 在开放时间、交通耗时、每日时长和预算约束下安排 3 日行程
- 独立重新验证交通、开放时间、路线缓冲、预算和数据来源
- 在每个外部事实上标记 `FIXTURE` 或 `ESTIMATED` 及来源 URI
- 提供可复现的 CLI 摘要和完整 JSON 输出
- 覆盖正常、低预算、越界城市、结果稳定性和预算篡改测试

## Phase 2 已实现

- MCP Server 注册、启停状态和工具能力缓存
- MCP SDK v2 Streamable HTTP、stdio 和内存 transport
- 分页工具发现与统一结构化调用结果
- 每个 Server 独立的 Semaphore 并发上限
- 单次调用超时、指数退避和有限重试
- 只有显式幂等调用允许重试，避免重复执行有副作用的工具
- `UNKNOWN → UP / DEGRADED / DOWN / DISABLED` 健康状态
- 不记录参数、URL 和环境变量的脱敏调用 Trace
- Pydantic Adapter schema 校验和自动来源元数据
- 真实 MCP SDK 内存连接及故障注入 contract tests

## Phase 3 已实现

- 从进程环境或本地 `.env` 安全读取 `AMAP_MAPS_API_KEY`
- 高德官方 Streamable HTTP MCP 注册、能力校验和健康检查
- 显式兼容高德在 `TextContent` 中返回 JSON 的协议形态
- POI 搜索与详情、天气、地理编码、步行和公交路线归一化
- 每个实时结果附 `LIVE`、查询时间、TTL 和脱敏 Trace 来源
- Key、完整 MCP URL、查询参数和原始响应均不进入 Trace
- 实时服务失败时返回非零状态并明确提示继续使用 Fixture 模式
- 通过内存 MCP 契约测试和真实高德 MCP 端到端探针

## Phase 4 已实现

- 在网络阶段并发预取高德 POI、详情、天气和市内公交路线
- 将外部数据冻结为同步 Snapshot，Planner 算法循环中不发网络请求
- 根据用户兴趣和优先级确定性选择最多 4 个实时景点候选
- 实时名称、类别、坐标、路线距离和耗时进入约束规划
- 门票、建议时长、开放窗口和公交费用使用明确标注的本地策略
- 单个 POI 失败时从候选集移除，单条路线失败时使用估算路线
- 可用实时 POI 少于 2 个时完整回退 Fixture，并只暴露安全错误类型
- 天气预报必须覆盖旅行日期，否则不写入旅行上下文
- 输出原始营业时间提示、字段假设、来源状态、TTL 和 Trace

## Phase 5 已实现

- 使用固定版本 `12306-mcp@0.3.10` 的 stdio MCP，只开放余票查询能力
- 将 MCP JSON 严格归一化为带来源的铁路票和 `TransportOption`
- 只接收“有/充足/正整数余票”且价格大于 0 的席别，候补与未知状态不进入 Planner
- 去程与返程并发查询；每一程独立替换 Fixture 铁路候选
- 单程失败、超出售窗口或无可确认席别时，仅该程显式降级
- 铁路查询结果标记 `LIVE`、查询时间、2 分钟 TTL 和脱敏 Trace URI
- Planner 在比较价格与耗时前先过滤无法保留首末日活动窗口的晚班车
- CLI 明确标注其为非官方社区数据源，且不提供登录、抢票、预订或下单
- 飞常准航空 MCP 已确认需要独立 API Key，本阶段保留航空 Fixture，不伪装接通

## Phase 6 已实现

- 从私有 `.env` 安全读取 `VARIFLIGHT_API_KEY`，Key 不进入 repr、Trace 或来源 URI
- 固定官方 npm 包 `@variflight-ai/variflight-mcp@1.0.3`，限制配置只能选择该包或语义版本
- 使用 `getFlightPriceByCities` 获取带舱位余量的结构化实时航班报价
- 将 Unix 计划起降时间归一化为中国标准时间，并验证出发日期与请求一致
- 每班航班只保留价格最低且余位大于 0 的舱位
- 税费只在上游返回明确数字时计入，空税费不伪造估算
- 去返程并发查询；每一程独立替换航空 Fixture，并与铁路查询并发执行
- 为航空规划预留抵达后 90 分钟、起飞前 150 分钟及 180 分钟广义时间开销
- 输出机场、航站楼、舱等、余位、5 分钟 TTL 和脱敏来源 Trace
- 机场地面接驳费用尚未进入预算，输出中会明确提示

## Phase 7 已实现

- 高德酒店 POI、地址、评分和位置进入住宿候选
- 以候选酒店为住宿区域锚点，并预取其到景点的公交路线
- Planner 按每晚费用与通勤时间确定性选择住宿区域
- 支持 `LODGING_NIGHTLY_PRICE_CNY` 用户价格输入
- 未提供用户价格时只使用明确标注的评分档位估算
- 不把高德人均消费包装为房价，也不声称存在可售房型

## Phase 8 已实现

- `REQUIREMENTS → PARSE → FETCH → PLAN → VALIDATE → EXPLAIN` 受控状态机
- 缺少路线、日期、天数、人数或预算时先澄清，且不调用外部工具
- 外部数据获取与确定性规划保持分层
- 只有 Validator 通过的 `PlanResult` 才能生成解释
- 解释完全引用结构化字段，不允许修改班次、时间和金额
- 当前仍使用确定性中文解析器；已保留替换 LLM 解析器的边界

## Phase 9 已实现

- SQLite 持久化 TTL 方案缓存，默认有效期 90 秒
- 缓存命中时将原始 `LIVE` 来源降为 `CACHED`
- 只缓存已经通过 Validator 的方案
- 持久化成功率、缓存命中、实时数据使用率和平均延迟
- 指标库不存储用户原始请求、MCP 参数、响应正文或 API Key
- 缓存与指标目录默认加入 `.gitignore`

## Phase 10 已实现

- 40 组固定离线旅行需求评测
- 统计预期结果准确率、硬约束满足率、不可行率、来源完整率和稳定性
- 明确记录当前确定性链路 Token 成本为 0
- FastAPI 提供健康检查、Fixture 规划、受控 Agent 和聚合指标接口
- Pydantic 严格请求/响应模型与安全错误边界
- 项目版本升级为 `1.0.0`

## 快速开始

需要 Python 3.12+。推荐使用 `uv`：

```powershell
uv sync
uv run tripweaver demo
uv run tripweaver demo --json
uv run python examples/gateway_demo.py
```

验证并查询高德官方 MCP：

```powershell
uv run tripweaver amap health
uv run tripweaver amap search "博物馆" --city "上海" --limit 5
uv run tripweaver amap detail "搜索返回的POI_ID"
uv run tripweaver amap weather "上海"
uv run tripweaver amap geocode "上海博物馆" --city "上海"
uv run tripweaver amap route walking "121.475480,31.228231" "121.490400,31.240000"
uv run tripweaver amap route transit "121.475480,31.228231" "121.490400,31.240000" --city 310000
```

验证并查询 12306 社区 MCP（需要 Node.js 18+ 与 `npx`）：

```powershell
uv run tripweaver railway health
uv run tripweaver railway search 2026-08-20 北京 上海 --limit 10
uv run tripweaver railway search 2026-08-20 北京 上海 --limit 10 --json
```

日期必须处于 12306 当前允许查询/售票的范围内；示例日期仅展示命令格式，运行时请替换为有效日期。

验证并查询飞常准 MCP：

```powershell
uv run tripweaver aviation health
uv run tripweaver aviation search 2026-08-20 北京 上海 --limit 10
uv run tripweaver aviation search 2026-08-20 BJS SHA --limit 10 --json
```

真实 Key 只放在 `.env` 的 `VARIFLIGHT_API_KEY` 中。复制新版 `.env.example` 时还需要将 `VARIFLIGHT_MCP_ENABLED` 改为 `true`。

真实 Key 只写入不提交的 `.env`，变量名和运行策略参考 `.env.example`。

也可以规划阶段一支持的中文请求：

```powershell
uv run tripweaver plan "从北京去上海玩3天，2026-10-01出发，2个人，预算5000元，喜欢历史文化和城市夜景"
```

使用高德实时地图与只读铁路数据生成混合可验证行程：

```powershell
uv run tripweaver plan-live "从北京去上海玩3天，2026-10-01出发，2个人，预算5000元，喜欢历史文化和城市夜景"
uv run tripweaver plan-live "从北京去上海玩3天，2026-10-01出发，2个人，预算5000元" --json
```

运行受控 Agent、评测与指标：

```powershell
uv run tripweaver agent "从北京去上海玩3天，2026-10-01出发，2个人，预算8000元，喜欢历史文化和城市夜景，高铁或飞机都可以"
uv run tripweaver evaluate
uv run tripweaver evaluate --json --output reports/evaluation-fixture-v1.json
uv run tripweaver metrics
```

启动 API：

```powershell
uv run uvicorn tripweaver.api:app --host 127.0.0.1 --port 8000
```

接口文档启动后位于 `http://127.0.0.1:8000/docs`。

运行测试：

```powershell
$env:PYTHONPATH = "src"
uv run python -m unittest discover -s tests -v
```

## 当前边界

- 仅支持北京到上海、1–7 天的 Fixture 往返行程
- 解析器不是 LLM，只覆盖 README 中演示的有限中文格式
- `plan` 和 `demo` 不访问实时服务；`amap`、`railway`、`aviation` 与 `plan-live` 才访问外部 MCP
- 12306 数据来自社区项目而非铁路官方发布，接口变化、限流和可用性风险必须显式处理
- 飞常准最低舱位价可能不含税费，且机场往返市区费用尚未进入预算
- 酒店位置与评分来自高德，但尚未接入任何 OTA 实时房型和房价服务
- 不登录、不抢票、不预订、不下单
- 已提供 FastAPI，但尚未提供 Web UI、身份认证和公网部署配置
- 高德复杂营业日历尚未自动转成硬约束，当前保留原文并使用规划基线
- 高德不提供可靠门票和推荐游玩时长，相关字段保持估算
- 方案缓存和聚合运行指标已持久化；MCP 工具发现缓存与健康状态仍为进程内数据

## 架构原则

`Requirement Guard → TripRequest → MCP Gateway / Fixture → Snapshot → Deterministic Planner → Independent Validator → Grounded Explanation`

结构化 `PlanResult` 是事实来源。后续接入 LLM 时，LLM 只能解析请求和解释已验证结果，不负责关键算术与可行性判断。

更多说明见 `docs/` 下的 Phase 1–10 文档。

MCP Gateway 基于官方 [MCP Python SDK v2](https://github.com/modelcontextprotocol/python-sdk)。
