# Three-minute Demo Script

1. Run `uv run tripweaver doctor --live`, then `uv run tripweaver serve`. Open the home page and point out LLM mode plus LIVE Provider readiness.
2. Choose Guangzhou → Chengdu and generate three alternatives. Explain that all alternatives share one frozen source snapshot.
3. Select the time-oriented plan. Show exact transport, lodging, daily attractions, local route time, budget, provenance, and the SVG map.
4. Lock the outbound journey. Ask: “返程不要飞机，酒店每晚控制在 500 元”. Show the structured diff and unchanged outbound journey.
5. Replace one attraction directly, then undo. Show that provider fetch count remains one while local replan and version counts change.
6. Request AI interpretation and show model/Token metadata. Disable the API key if needed to demonstrate deterministic fallback.
7. Finish with the 120-case planning report and 40-case multi-turn Agent report. Clarify the query-only boundary: no login, booking, payment, or invented availability.
