# Phase 1：Fixture 纵向切片

## 目标

在不依赖任何实时 API、MCP Server 或 LLM 的情况下，证明 TripWeaver 的核心链路可以端到端运行并被自动验证。

## 演示场景

- 出发地：北京
- 目的地：上海
- 日期：2026-10-01 至 2026-10-03
- 人数：2 人
- 预算：5000 元
- 偏好：历史文化、城市景观、美食街区
- 交通：高铁或飞机

所有交通标识均为 TripWeaver 自定义模拟标识，不对应真实班次。

## 组件职责

1. `DeterministicConstraintParser`：解析有限格式中文请求，无法安全识别时直接报错。
2. `FixtureCatalog`：模拟未来的航空、铁路、地图和住宿 Adapter，并输出统一领域模型。
3. `DeterministicPlanner`：硬过滤交通，选择住宿区域，按开放时间和路线耗时生成日程。
4. `ItineraryValidator`：独立重新检查交通方向、日期、营业时间、重复景点、路线缓冲、预算和来源状态。
5. `TripPlanningService`：串联上述组件并返回唯一的结构化 `PlanResult`。
6. `CLI`：以摘要或 JSON 展示结果，并明确显示 Fixture/估算声明。

## 数据可信度规则

- 模拟交通、景点和路线标记为 `FIXTURE`。
- 住宿区域价格与餐饮预算标记为 `ESTIMATED`。
- 每条来源使用 `fixture://phase-1/...` URI，便于测试来源覆盖率。
- `UNAVAILABLE` 数据会被 Validator 判定为错误。
- 估算数据不会伪装成实时数据，而是产生验证警告。

## Phase 1 完成标准

- 演示请求生成三天行程并通过所有硬约束验证。
- 六个 Fixture 景点各安排一次。
- 总预算不超过 5000 元且预算可以独立重算。
- 相同输入生成字节级一致的 JSON。
- 每个交通、住宿、景点、路线和估算费用都有来源元数据。
- 低预算和不支持的城市不会生成伪造方案。

## 下一阶段接口

后续 MCP Adapter 应继续返回当前 `TransportOption`、`Place`、`LodgingArea` 和 `RouteLeg` 模型。真实数据接入不应改变 Planner 和 Validator 的核心接口。

