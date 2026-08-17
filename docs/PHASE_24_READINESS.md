# Phase 24 · Demo Readiness

Phase 24 turns the Phase 23 feature set into a repeatable local demonstration. It does not add public hosting, authentication, booking, payment, or portfolio publishing.

## Delivery boundary

- `tripweaver doctor` performs local, network-free checks and is safe to paste into a screen recording.
- `tripweaver doctor --live` performs query-only MCP capability discovery. It does not search tickets, reserve inventory, log in, or place orders.
- `tripweaver serve` starts the FastAPI application and built-in Web client at `http://127.0.0.1:8000` by default.
- `GET /readiness` exposes only redacted capability state. API keys, full provider URLs, request parameters, prompts, and response bodies are never returned.

## Readiness semantics

`DEMO ready` requires Python 3.12+ and valid local runtime settings. It does not require any credential or Node.js.

`LIVE ready` additionally requires a valid AMap configuration and valid dependencies for every enabled stdio Provider. Disabled railway or flight Providers remain explicit fallbacks. When `--live` is supplied, an enabled Provider must also pass MCP capability discovery.

`LLM ready` indicates that DeepSeek is enabled and configured. It is never required for DEMO readiness because every language boundary has a deterministic fallback.

## Recommended demonstration startup

```powershell
uv sync --frozen
uv run tripweaver doctor --live
uv run tripweaver serve
```

If an external Provider is unstable, switch the Web selector to `演示 Fixture`. The same multi-option, revision, conflict, snapshot, and Trace flow remains available without network access.

## Exit codes

- `doctor`: exits `0` when DEMO mode is ready.
- `doctor --live`: exits `0` only when the configured live stack and capability probes pass.
- `serve`: exits `3` before binding a port if required DEMO settings are invalid.

Warnings represent an intentional optional fallback, such as disabled DeepSeek or VariFlight. Failures represent a configuration or dependency problem that blocks the relevant mode.
