# Changelog

## 3.5.0 - 2026-08-17

- Added secret-safe offline and live readiness diagnostics through `tripweaver doctor`.
- Added a one-command local Web/API launcher through `tripweaver serve`.
- Added the `/readiness` capability endpoint and Web readiness badge.
- Added query-only capability probes for AMap, 12306, and VariFlight.
- Extended CI with Phase 24 Doctor and multi-turn Agent evaluation gates.
- Added a container health check and unified Docker startup command.

## 3.4.0 - 2026-08-17

- Unified demo and live MCP planning inside the conversational Web session flow.
- Added immutable snapshot metadata, provider freshness, explicit refresh, and network-free replanning.
- Added structured constraint conflicts with deterministic relaxation suggestions.
- Added a reproducible 40-case multi-turn Agent evaluation and DeepSeek opt-in mode.
- Added per-session execution traces for LLM, MCP, planner, Validator, fallbacks, latency, and tokens.
- Added Web snapshot, conflict, refresh, and observability panels.

## 3.0.0 - 2026-08-16

- Wired DeepSeek JSON Output into request, revision, clarification, and explanation flows.
- Added model usage metadata and deterministic fallback for every language-model boundary.
- Rebuilt the Web Demo as a detailed itinerary and conversational planning workbench.
- Added place replacement, field locks, undo, history, diffs, runtime events, and grounded explanations.
- Added an offline SVG itinerary map, budget chart, provenance dashboard, and demo scenarios.
- Added browser-oriented UI checks, frontend syntax validation, demo script, and release checklist.

## 2.0.0 - 2026-08-16

- Expanded deterministic fixtures and provider mappings to 10 Chinese cities.
- Added budget, balanced, and time alternatives from one frozen snapshot.
- Added stateful selection, bounded natural-language revisions, local replanning, and plan diffs.
- Added an optional structured LLM revision interpreter behind a strict allow-list.
- Added prompt-injection boundary checks and normalized MCP query caching.
- Added FastAPI v2 session endpoints and a responsive local demo UI.
- Expanded the offline evaluation suite from 40 to 120 cases.
- Added GitHub Actions, Docker, Compose, architecture, and contribution documentation.
