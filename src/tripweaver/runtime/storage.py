"""Small SQLite stores for bounded cache entries and aggregate run metrics."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field

from tripweaver.application.hybrid_service import HybridPlanResult
from tripweaver.domain.models import DataStatus, TripRequest


class MetricsSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    total_runs: int = Field(ge=0)
    successful_runs: int = Field(ge=0)
    cache_hits: int = Field(ge=0)
    map_live_runs: int = Field(ge=0)
    rail_live_runs: int = Field(ge=0)
    flight_live_runs: int = Field(ge=0)
    average_latency_ms: float = Field(ge=0)


class _SQLiteStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path, timeout=5)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            yield connection
            connection.commit()
        finally:
            connection.close()


class SQLitePlanCache(_SQLiteStore):
    """A schema-versioned TTL cache for normalized, already-validated plans."""

    SCHEMA_VERSION = "hybrid-plan-v2"

    def __init__(self, path: Path, ttl_seconds: int = 90) -> None:
        super().__init__(path)
        if not 10 <= ttl_seconds <= 3600:
            raise ValueError("cache ttl must be between 10 and 3600 seconds")
        self._ttl_seconds = ttl_seconds
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS plan_cache (
                    cache_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )

    @classmethod
    def key_for(cls, request: TripRequest) -> str:
        canonical = request.model_dump_json(exclude={"assumptions"})
        raw = f"{cls.SCHEMA_VERSION}|{canonical}".encode()
        return hashlib.sha256(raw).hexdigest()

    def get(self, request: TripRequest) -> HybridPlanResult | None:
        now = time.time()
        key = self.key_for(request)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload, expires_at FROM plan_cache WHERE cache_key = ?", (key,)
            ).fetchone()
            if row is None:
                return None
            payload, expires_at = row
            if float(expires_at) <= now:
                connection.execute("DELETE FROM plan_cache WHERE cache_key = ?", (key,))
                return None
        document: Any = json.loads(str(payload))
        _mark_live_sources_cached(document)
        result = HybridPlanResult.model_validate(document)
        plan = result.plan.model_copy(
            update={
                "warnings": result.plan.warnings
                + ("本次结果命中未过期的持久化缓存；LIVE 来源已标记为 CACHED。",)
            }
        )
        return result.model_copy(update={"plan": plan, "cache_hit": True})

    def put(self, request: TripRequest, result: HybridPlanResult) -> None:
        if not result.plan.validation.feasible:
            return
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO plan_cache(cache_key, payload, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    payload = excluded.payload,
                    created_at = excluded.created_at,
                    expires_at = excluded.expires_at
                """,
                (
                    self.key_for(request),
                    result.model_dump_json(exclude_computed_fields=True),
                    now,
                    now + self._ttl_seconds,
                ),
            )

    def clear(self) -> int:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM plan_cache")
            return max(cursor.rowcount, 0)


class MetricsStore(_SQLiteStore):
    """Durable low-cardinality metrics; raw prompts and secrets are never stored."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS plan_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    success INTEGER NOT NULL,
                    cache_hit INTEGER NOT NULL,
                    live_map INTEGER NOT NULL,
                    live_rail INTEGER NOT NULL,
                    live_flight INTEGER NOT NULL,
                    latency_ms REAL NOT NULL,
                    error_type TEXT
                )
                """
            )

    def record(
        self,
        *,
        success: bool,
        cache_hit: bool,
        live_map: bool,
        live_rail: bool,
        live_flight: bool,
        latency_ms: float,
        error_type: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO plan_runs(
                    created_at, success, cache_hit, live_map, live_rail,
                    live_flight, latency_ms, error_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    time.time(),
                    int(success),
                    int(cache_hit),
                    int(live_map),
                    int(live_rail),
                    int(live_flight),
                    max(latency_ms, 0),
                    error_type,
                ),
            )

    def summary(self) -> MetricsSummary:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*), COALESCE(SUM(success), 0), COALESCE(SUM(cache_hit), 0),
                    COALESCE(SUM(live_map), 0), COALESCE(SUM(live_rail), 0),
                    COALESCE(SUM(live_flight), 0), COALESCE(AVG(latency_ms), 0)
                FROM plan_runs
                """
            ).fetchone()
        assert row is not None
        return MetricsSummary(
            total_runs=int(row[0]),
            successful_runs=int(row[1]),
            cache_hits=int(row[2]),
            map_live_runs=int(row[3]),
            rail_live_runs=int(row[4]),
            flight_live_runs=int(row[5]),
            average_latency_ms=float(row[6]),
        )


def _mark_live_sources_cached(value: Any) -> None:
    if isinstance(value, dict):
        mapping = cast(dict[str, Any], value)
        if mapping.get("status") == DataStatus.LIVE.value and "source_reference" in mapping:
            mapping["status"] = DataStatus.CACHED.value
        for child in mapping.values():
            _mark_live_sources_cached(child)
    elif isinstance(value, list):
        for child in cast(list[Any], value):
            _mark_live_sources_cached(child)
