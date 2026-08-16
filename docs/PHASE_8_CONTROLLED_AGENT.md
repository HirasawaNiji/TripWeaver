# Phase 8：受控旅行规划 Agent

## 状态机

`REQUIREMENTS → PARSE → FETCH → PLAN → VALIDATE → EXPLAIN`

硬约束包括路线、日期、天数、人数和总预算。缺少任何一项时返回 `NEEDS_INPUT`，不调用外部工具。

## 权限边界

- 解析层只生成 `TripRequest`。
- MCP 层只查询，不登录、不预订、不下单。
- Planner 负责关键算术、交通选择、日程与预算。
- Validator 独立重新计算硬约束。
- 解释层只能读取已验证结果，不能添加结构化结果中不存在的班次、价格或时间。

当前默认解析器是确定性的有限中文解析器。未来接入 LLM 时必须实现相同的结构化接口，并继续接受 Pydantic 与 Validator 双重约束。
