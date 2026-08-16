# Contributing

TripWeaver requires Python 3.12+ and `uv`. Create a branch, run `uv sync`, and keep external integrations query-only.

Before opening a pull request, run:

```bash
uv run pytest -q
uv run ruff check src tests
uv run pyright
```

New provider facts must use strict Pydantic wire schemas and include provider, status, query time, expiry, source reference, and confidence. Never commit `.env`, credentials, raw provider responses, booking flows, or invented live facts. Planner arithmetic and validation must remain deterministic and network-free.
