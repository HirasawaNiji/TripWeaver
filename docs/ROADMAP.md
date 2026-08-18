# Development Roadmap

TripWeaver was built as a sequence of vertical slices. Each phase kept the project demonstrable while moving one external or Agent boundary from Fixture data to controlled runtime behavior.

| Phase | Delivery |
|---|---|
| 1 | Deterministic Beijing–Shanghai vertical slice, provenance, budget and schedule validation |
| 2 | MCP Gateway with discovery, schemas, timeout, retry, concurrency and health state |
| 3–4 | Official AMap MCP and frozen hybrid planning snapshots |
| 5 | Query-only community 12306 MCP and per-leg railway fallback |
| 6 | VariFlight fares, cabins, schedule normalization and per-leg aviation fallback |
| 7 | AMap lodging POIs, commuting cost and explicit price-estimation policy |
| 8–10 | Controlled Agent state machine, SQLite metrics, FastAPI and fixed evaluation suite |
| 11 | Ten-city registry and budget/balanced/time alternatives |
| 12–15 | Stateful conversation, snapshot reuse, Web Demo, security boundaries, CI and Docker |
| 16–19 | DeepSeek JSON Output, plan explanation, detailed Web workspace and local SVG map |
| 20 | Unified DEMO/LIVE conversational sessions and explicit source refresh |
| 21 | Structured planning conflicts, locked constraints and relaxation suggestions |
| 22 | Reproducible 40-case multi-turn Agent evaluation |
| 23 | LLM/MCP/Planner/Validator execution traces and Web observability |
| 24 | Secret-safe Doctor, live capability probes, one-command serving and release readiness |

The current release intentionally stops before authentication, booking, payment, OTA inventory, and public multi-tenant hosting.

