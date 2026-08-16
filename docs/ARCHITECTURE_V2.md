# TripWeaver 2.0 Architecture

TripWeaver separates uncertain language and provider data from deterministic execution:

```text
User text
  -> Requirement / Revision Interpreter (strict schemas)
  -> Provider Fetch (AMap + 12306 community MCP + VariFlight MCP)
  -> Frozen Planning Context + TTL query cache
  -> Budget / Balanced / Time deterministic planners
  -> Independent Validator
  -> User selection
  -> Local Replanner + PlanDiff (no provider call)
  -> Grounded API / Web presentation
```

## Trust boundaries

- Interpreters may only emit `TripRequest` or `RevisionIntent`; they cannot choose arbitrary tools.
- MCP responses cross strict provider-specific wire schemas before entering domain models.
- Every external fact carries provenance, freshness, confidence, and a live/cached/estimated status.
- The planner owns time, price, distance, opening-window, transfer, and budget arithmetic.
- The Validator independently checks executable output before presentation.
- Sessions store normalized plans and preferences, never credentials or raw provider responses.

## Replanning invariant

A session obtains one planning context. Selecting a candidate or revising a preference reuses that context. Unaffected transport and lodging IDs become fixed overrides, while only the requested dimension is unlocked. `PlanDiff` makes changed and preserved fields observable.

## Demo boundary

This repository is a query-only portfolio system. It deliberately excludes login, ticket grabbing, reservation, payment, and claims of OTA availability.
