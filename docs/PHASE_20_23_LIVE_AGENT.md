# Phase 20–23 · Live Conversational Agent

## Phase 20 · Live session snapshots

The Web session API accepts `mode=DEMO|LIVE`. LIVE creation prepares AMap, community 12306, VariFlight, and lodging data once, records provider freshness in an immutable `PlanningSnapshot`, and generates three alternatives from that same catalog. Revisions are local and keep `data_fetch_count` unchanged. `POST /v2/sessions/{id}/refresh` is the only session operation that fetches providers again.

## Phase 21 · Constraint conflicts

`NoFeasiblePlanError` carries a stable code and safe structured details. `ConflictAnalyzer` turns budget, transport-window, lodging, schedule-capacity, and locked-field failures into `PlanningConflict` responses with deterministic relaxation suggestions. The API uses HTTP 409 for these recoverable conflicts; the LLM never decides whether a plan is feasible.

## Phase 22 · Multi-turn Agent evaluation

`tripweaver evaluate-agent` runs 40 reproducible two-turn conversations across the city registry. It reports request-structuring success, revision-intent accuracy, schema validity, hard-constraint satisfaction, locked-field preservation, snapshot reuse, fallback rate, latency, and tokens. `--live-llm` opts into configured DeepSeek calls; the default is a zero-cost deterministic baseline.

## Phase 23 · Execution traces

Every Session exposes `GET /v2/sessions/{id}/trace`. Trace steps cover LLM calls, provider snapshots, deterministic planning, independent validation, fallbacks, latency, and token counts without storing prompts, API keys, MCP parameters, or raw responses. The Web workbench renders the snapshot and trace next to the itinerary.

## Commands

```powershell
uv run tripweaver evaluate-agent --output reports/evaluation-agent-v3.json
uv run tripweaver evaluate-agent --live-llm --limit 3
uv run uvicorn tripweaver.api:app --host 127.0.0.1 --port 8000
```
