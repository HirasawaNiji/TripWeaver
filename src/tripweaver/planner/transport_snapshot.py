"""Freeze live railway candidates before deterministic planning begins."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from pydantic import ValidationError

from tripweaver.domain.models import (
    TransportLeg,
    TransportMode,
    TransportOption,
    TripRequest,
)
from tripweaver.mcp_gateway.errors import McpGatewayError
from tripweaver.planner.live_snapshot import FrozenPlanningCatalog
from tripweaver.providers.railway import RailwayProviderError


class PlanningRailwayProvider(Protocol):
    async def transport_options(
        self,
        origin: str,
        destination: str,
        travel_date: date,
        leg: TransportLeg,
        *,
        limit: int | None = None,
    ) -> tuple[TransportOption, ...]: ...


@dataclass(frozen=True)
class RailwayPlanningSnapshot:
    catalog: FrozenPlanningCatalog
    live_options: tuple[TransportOption, ...]
    live_legs: tuple[TransportLeg, ...]
    fallback_legs: tuple[TransportLeg, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class _LegOutcome:
    leg: TransportLeg
    options: tuple[TransportOption, ...]
    warning: str | None = None


class RailwaySnapshotEnricher:
    """Replace Fixture rail per leg only when verified live candidates exist."""

    def __init__(self, provider: PlanningRailwayProvider) -> None:
        self._provider = provider

    async def enrich(
        self,
        request: TripRequest,
        catalog: FrozenPlanningCatalog,
    ) -> RailwayPlanningSnapshot:
        if TransportMode.RAIL not in request.preferred_transport:
            return RailwayPlanningSnapshot(
                catalog=catalog,
                live_options=(),
                live_legs=(),
                fallback_legs=(),
                warnings=(),
            )

        outcomes = await asyncio.gather(
            self._safe_leg(
                request.origin,
                request.destination,
                request.start_date,
                TransportLeg.OUTBOUND,
            ),
            self._safe_leg(
                request.destination,
                request.origin,
                request.end_date,
                TransportLeg.RETURN,
            ),
        )
        fixture_options = catalog.transport_options(request)
        live_options = tuple(option for outcome in outcomes for option in outcome.options)
        live_legs = tuple(outcome.leg for outcome in outcomes if outcome.options)
        fallback_legs = tuple(outcome.leg for outcome in outcomes if not outcome.options)
        merged = (
            tuple(
                option
                for option in fixture_options
                if not (option.mode == TransportMode.RAIL and option.leg in live_legs)
            )
            + live_options
        )
        merged = tuple(
            sorted(
                merged,
                key=lambda item: (item.leg.value, item.mode.value, item.depart_at, item.id),
            )
        )
        warnings = [outcome.warning for outcome in outcomes if outcome.warning]
        if live_options:
            warnings.extend(
                (
                    f"12306 社区 MCP 提供了 {len(live_options)} 个有明确可用席别的实时铁路候选。",
                    "铁路结果来自非官方社区 MCP，仅用于查询；余票与价格具有约 2 分钟有效期，出行前必须在官方渠道复核。",
                )
            )
        return RailwayPlanningSnapshot(
            catalog=catalog.with_transport_options(merged),
            live_options=live_options,
            live_legs=live_legs,
            fallback_legs=fallback_legs,
            warnings=tuple(warnings),
        )

    async def _safe_leg(
        self,
        origin: str,
        destination: str,
        travel_date: date,
        leg: TransportLeg,
    ) -> _LegOutcome:
        try:
            options = await self._provider.transport_options(
                origin,
                destination,
                travel_date,
                leg,
            )
        except (
            McpGatewayError,
            OSError,
            RailwayProviderError,
            TimeoutError,
            ValidationError,
            ValueError,
        ) as error:
            return _LegOutcome(
                leg=leg,
                options=(),
                warning=(
                    f"{_leg_name(leg)}铁路实时查询不可用（{type(error).__name__}），"
                    "该程保留明确标注的 Fixture 候选。"
                ),
            )
        if not options:
            return _LegOutcome(
                leg=leg,
                options=(),
                warning=(
                    f"{_leg_name(leg)}未取得有明确余票与价格的铁路候选，"
                    "可能超出售窗口或暂无可售席别；该程保留 Fixture。"
                ),
            )
        return _LegOutcome(leg=leg, options=options)


def _leg_name(leg: TransportLeg) -> str:
    return "去程" if leg == TransportLeg.OUTBOUND else "返程"
