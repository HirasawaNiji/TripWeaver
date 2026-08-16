"""Freeze live VariFlight candidates before deterministic planning begins."""

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
from tripweaver.providers.aviation import VariflightProviderError


class PlanningAviationProvider(Protocol):
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
class AviationPlanningSnapshot:
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


class AviationSnapshotEnricher:
    """Replace Fixture flights per leg only when verified live fares exist."""

    def __init__(self, provider: PlanningAviationProvider) -> None:
        self._provider = provider

    async def enrich(
        self,
        request: TripRequest,
        catalog: FrozenPlanningCatalog,
    ) -> AviationPlanningSnapshot:
        if TransportMode.FLIGHT not in request.preferred_transport:
            return AviationPlanningSnapshot(
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
                if not (option.mode == TransportMode.FLIGHT and option.leg in live_legs)
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
                    f"飞常准 MCP 提供了 {len(live_options)} 个有明确舱位余量和价格的实时航班候选。",
                    "航班价格采用每班最低有座舱位；税费字段仅在上游返回明确数字时计入，空税费不会被擅自估算。",
                    "机场往返市区的地面接驳费用尚未计入预算；时间规划已预留抵达后 90 分钟和起飞前 150 分钟。",
                    "航班结果仅用于查询和规划，价格有效期约 5 分钟，预订前必须在承运人或授权渠道复核。",
                )
            )
        return AviationPlanningSnapshot(
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
            TimeoutError,
            ValidationError,
            ValueError,
            VariflightProviderError,
        ) as error:
            return _LegOutcome(
                leg=leg,
                options=(),
                warning=(
                    f"{_leg_name(leg)}航班实时查询不可用（{type(error).__name__}），"
                    "该程保留明确标注的 Fixture 候选。"
                ),
            )
        if not options:
            return _LegOutcome(
                leg=leg,
                options=(),
                warning=(
                    f"{_leg_name(leg)}未取得有明确舱位余量和价格的航班候选，该程保留 Fixture。"
                ),
            )
        return _LegOutcome(leg=leg, options=options)


def _leg_name(leg: TransportLeg) -> str:
    return "去程" if leg == TransportLeg.OUTBOUND else "返程"
