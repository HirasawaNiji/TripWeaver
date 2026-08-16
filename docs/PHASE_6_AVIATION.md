# Phase 6：飞常准实时航班规划

## 本阶段目标

把带舱位余量和价格的真实航班候选接入确定性 Planner，并与铁路、地图数据组成可按来源和单程降级的混合快照。本阶段仍然只查询，不预订、不下单。

## 数据源与工具

- 使用飞常准维护的 [Variflight MCP](https://github.com/variflight/variflight-mcp)。
- 固定 npm 包 `@variflight-ai/variflight-mcp@1.0.3`。
- 凭证从私有环境变量 `VARIFLIGHT_API_KEY` 读取。
- 规划调用 `getFlightPriceByCities`；健康检查同时要求机场天气工具存在。
- 当前项目支持北京/上海中文名与 `BJS`/`SHA` IATA 城市代码。

## 已验证的实时响应契约

成功响应包含 `code=200` 和航班数组。TripWeaver 只依赖以下可验证字段：

- 航班号、承运人代码；
- 出发/到达机场及航站楼；
- Unix 秒级计划起降时间；
- 舱等代码、舱位代码、余位数量；
- 舱位价格、税费与燃油费字段；
- 经停与代码共享标志。

中文机场名和舱位名曾在 stdio 输出中出现编码异常，因此规划标签使用稳定机场代码，并由 `F/C/Y` 标准舱等代码映射中文名称，不依赖乱码字段。

## 归一化与过滤

每个航班只保留一个候选舱位：

- `seatnum > 0`；
- `price > 0`；
- 在满足以上条件的舱位中选择最低价格；
- 税费和燃油费只有在明确返回非负数字时才计入；
- 计划起降时间按 UTC+8 转换为项目内部的中国本地时间；
- 出发时间必须落在请求日期，抵达必须晚于出发。

每个 `FlightOffer` 和 `TransportOption` 都带：

- `provider=variflight`
- `status=LIVE`
- UTC 查询时间
- 约 5 分钟有效期
- 不含 Key 和查询参数的 MCP Trace 来源 URI
- `confidence=0.9`

## 多源并发与单程降级

地图快照完成后，铁路与航空增强器并发执行；每个增强器内部又并发查询去程和返程。

合并规则：

- 去程实时航班成功：仅删除去程航空 Fixture；
- 返程实时航班失败：返程航空 Fixture 保留；
- 铁路成功与否不影响航空结果，反之亦然；
- 所有候选冻结后，Planner 才开始进行确定性计算；
- 错误只暴露安全异常类型，不回显 Key 或上游原始响应。

## 航空时间策略

航班不能只按空中时长与铁路比较。当前确定性策略加入：

- 抵达后 90 分钟：下机、行李与进城缓冲；
- 起飞前 150 分钟：前往机场、值机与安检缓冲；
- 广义成本额外增加 180 分钟航空非飞行时间。

这些策略同时被 Planner 和 Validator 使用。机场地面接驳费用尚未加入预算，因此输出会明确说明该缺口。

## 配置

```dotenv
VARIFLIGHT_MCP_ENABLED=true
VARIFLIGHT_API_KEY=你的真实Key
VARIFLIGHT_MCP_PACKAGE=@variflight-ai/variflight-mcp@1.0.3
VARIFLIGHT_MCP_TIMEOUT_SECONDS=40
VARIFLIGHT_MCP_MAX_RETRIES=1
VARIFLIGHT_MCP_MAX_CONCURRENCY=2
VARIFLIGHT_MCP_CANDIDATE_LIMIT=80
```

真实 Key 永远不能写入 `.env.example`、README、Issue 或 Git 提交。

## 验证命令

```powershell
uv run tripweaver aviation health --json
uv run tripweaver aviation search <有效日期> 北京 上海 --limit 10 --json
uv run tripweaver plan-live "从北京去上海玩3天，<有效日期>出发，2个人，预算8000元，喜欢历史文化和城市夜景，高铁或飞机都可以。"
uv run python -m unittest discover -s tests -v
uvx ruff check src tests
uv run --with pyright pyright
```

## 当前限制

- 航班报价可能不含税费，`fees_complete=false` 时不能视为最终支付价格。
- 机场地面接驳费用尚未进入预算模型。
- 当前只消费直飞/单航班报价，没有把中转方案建模为多段交通。
- IATA 中文城市映射目前只覆盖 Fixture 支持的北京与上海。
- 价格、余位和航班计划可能快速变化，预订前必须在承运人或授权渠道复核。
