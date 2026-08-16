# Phase 9：缓存与可观测性

## 持久化缓存

SQLite 缓存只保存已经通过 Validator 的 `HybridPlanResult`。缓存键由规范化 `TripRequest` 和 schema 版本生成，默认 TTL 为 90 秒。

命中缓存后，结果中所有原 `LIVE` 来源会改为 `CACHED`，并追加缓存声明。过期条目不会作为实时结果返回。

## 指标

每次规划只记录低基数运行事实：成功状态、缓存命中、地图/铁路/航班实时使用状态、延迟和安全错误类型。数据库不保存原始 Prompt、MCP 参数、完整响应或密钥。

默认路径为 `.tripweaver/tripweaver.db`，该目录不会提交到 Git。
