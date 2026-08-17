# Phase 12–15 Release

- Phase 12: conversation sessions, strict revision intents, selection, preservation, local replanning, and diffs.
- Phase 13: single-fetch hybrid planning contexts and three alternatives from identical provider facts.
- Phase 14: v2 session API, built-in Web Demo, progress events, and normalized MCP query caching.
- Phase 15: 120-case offline evaluation, CI, Docker delivery, release documentation, and version 2.0.0.

The optional structured interpreter produces a Pydantic `RevisionIntent`. The deterministic parser remains the no-key default and all resulting plans still pass through the same planner and Validator.
