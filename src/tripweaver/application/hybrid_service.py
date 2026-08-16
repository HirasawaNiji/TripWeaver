"""Hybrid application service: live AMap facts plus deterministic planning."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING

from tripweaver.config import (
    AmapSettings,
    LodgingSettings,
    RailwaySettings,
    RuntimeSettings,
    VariflightSettings,
)
from tripweaver.domain.models import (
    DataStatus,
    DomainModel,
    LodgingArea,
    Place,
    PlanningObjective,
    PlanningOverrides,
    PlanResult,
    TransportLeg,
    TransportMode,
    TransportOption,
    TripRequest,
)
from tripweaver.fixtures.catalog import FixtureCatalog
from tripweaver.llm.constraint_parser import DeterministicConstraintParser
from tripweaver.mcp_gateway.errors import McpGatewayError
from tripweaver.planner.aviation_snapshot import (
    AviationPlanningSnapshot,
    AviationSnapshotEnricher,
)
from tripweaver.planner.catalog import PlanningCatalog
from tripweaver.planner.engine import DeterministicPlanner
from tripweaver.planner.live_snapshot import (
    AmapPlanningSnapshotBuilder,
    LiveSnapshotUnavailableError,
)
from tripweaver.planner.transport_snapshot import (
    RailwayPlanningSnapshot,
    RailwaySnapshotEnricher,
)
from tripweaver.providers.amap import AmapProvider, AmapProviderError, AmapWeather
from tripweaver.providers.aviation import VariflightProvider
from tripweaver.providers.railway import RailwayProvider
from tripweaver.validator.service import ItineraryValidator

from .service import TripPlanningService

if TYPE_CHECKING:
    from tripweaver.runtime import MetricsStore, SQLitePlanCache


@dataclass(frozen=True)
class HybridPlanningContext:
    """One completed provider fetch; all subsequent planning is network-free."""

    request: TripRequest
    catalog: PlanningCatalog
    live_map_used: bool
    map_places: tuple[Place, ...] = ()
    weather: AmapWeather | None = None
    live_rail_used: bool = False
    rail_options: tuple[TransportOption, ...] = ()
    rail_fallback_legs: tuple[TransportLeg, ...] = ()
    live_flight_used: bool = False
    flight_options: tuple[TransportOption, ...] = ()
    flight_fallback_legs: tuple[TransportLeg, ...] = ()
    fallback_reason: str | None = None
    lodging_candidates: tuple[LodgingArea, ...] = ()
    warnings: tuple[str, ...] = ()


class HybridPlanResult(DomainModel):
    plan: PlanResult
    live_map_used: bool
    map_places: tuple[Place, ...] = ()
    weather: AmapWeather | None = None
    live_rail_used: bool = False
    rail_options: tuple[TransportOption, ...] = ()
    rail_fallback_legs: tuple[TransportLeg, ...] = ()
    live_flight_used: bool = False
    flight_options: tuple[TransportOption, ...] = ()
    flight_fallback_legs: tuple[TransportLeg, ...] = ()
    fallback_reason: str | None = None
    lodging_candidates: tuple[LodgingArea, ...] = ()
    cache_hit: bool = False


class HybridAlternativeSet(DomainModel):
    request: TripRequest
    alternatives: tuple[HybridPlanResult, ...]
    data_fetch_count: int = 1


class HybridTripPlanningService:
    """Create a verified plan from a frozen AMap snapshot with Fixture fallback."""

    def __init__(
        self,
        snapshot_builder: AmapPlanningSnapshotBuilder,
        *,
        parser: DeterministicConstraintParser | None = None,
        fixture: FixtureCatalog | None = None,
        validator: ItineraryValidator | None = None,
        railway_enricher: RailwaySnapshotEnricher | None = None,
        aviation_enricher: AviationSnapshotEnricher | None = None,
        cache: SQLitePlanCache | None = None,
        metrics: MetricsStore | None = None,
    ) -> None:
        self._parser = parser or DeterministicConstraintParser()
        self._fixture = fixture or FixtureCatalog()
        self._builder = snapshot_builder
        self._validator = validator or ItineraryValidator()
        self._railway_enricher = railway_enricher
        self._aviation_enricher = aviation_enricher
        self._cache = cache
        self._metrics = metrics
        self._fallback = TripPlanningService(
            parser=self._parser,
            catalog=self._fixture,
            validator=self._validator,
        )

    @classmethod
    def from_settings(
        cls,
        settings: AmapSettings,
        railway_settings: RailwaySettings | None = None,
        variflight_settings: VariflightSettings | None = None,
        lodging_settings: LodgingSettings | None = None,
        runtime_settings: RuntimeSettings | None = None,
    ) -> HybridTripPlanningService:
        fixture = FixtureCatalog()
        provider = AmapProvider.from_settings(settings)
        railway_enricher = None
        if railway_settings is not None and railway_settings.enabled:
            railway_enricher = RailwaySnapshotEnricher(
                RailwayProvider.from_settings(railway_settings)
            )
        aviation_enricher = None
        if variflight_settings is not None and variflight_settings.enabled:
            aviation_enricher = AviationSnapshotEnricher(
                VariflightProvider.from_settings(variflight_settings)
            )
        lodging_policy = lodging_settings or LodgingSettings()
        cache = None
        metrics = None
        if runtime_settings is not None:
            from tripweaver.runtime import MetricsStore, SQLitePlanCache

            if runtime_settings.cache_enabled:
                cache = SQLitePlanCache(
                    runtime_settings.database_path, runtime_settings.cache_ttl_seconds
                )
            if runtime_settings.metrics_enabled:
                metrics = MetricsStore(runtime_settings.database_path)
        return cls(
            AmapPlanningSnapshotBuilder(
                provider,
                fixture=fixture,
                lodging_limit=lodging_policy.candidate_limit,
                nightly_price_override_cny=lodging_policy.nightly_price_cny,
            ),
            fixture=fixture,
            railway_enricher=railway_enricher,
            aviation_enricher=aviation_enricher,
            cache=cache,
            metrics=metrics,
        )

    async def plan_text(self, text: str) -> HybridPlanResult:
        return await self.plan(self._parser.parse(text))

    async def plan(self, request: TripRequest) -> HybridPlanResult:
        started = monotonic()
        if self._cache is not None and (cached := self._cache.get(request)) is not None:
            self._record_metrics(cached, started)
            return cached
        try:
            result = await self._plan_uncached(request)
        except Exception as error:
            if self._metrics is not None:
                self._metrics.record(
                    success=False,
                    cache_hit=False,
                    live_map=False,
                    live_rail=False,
                    live_flight=False,
                    latency_ms=(monotonic() - started) * 1000,
                    error_type=type(error).__name__,
                )
            raise
        if self._cache is not None:
            self._cache.put(request, result)
        self._record_metrics(result, started)
        return result

    async def _plan_uncached(self, request: TripRequest) -> HybridPlanResult:
        try:
            snapshot = await self._builder.build(request)
        except (
            AmapProviderError,
            LiveSnapshotUnavailableError,
            McpGatewayError,
        ) as error:
            fallback = self._fallback.plan(request)
            reason = type(error).__name__
            fallback = fallback.model_copy(
                update={
                    "warnings": fallback.warnings
                    + (f"高德实时快照不可用（{reason}），已明确降级到完整 Fixture 方案。",)
                }
            )
            return HybridPlanResult(
                plan=fallback,
                live_map_used=False,
                fallback_reason=reason,
            )

        catalog = snapshot.catalog
        rail_options: tuple[TransportOption, ...] = ()
        rail_fallback_legs: tuple[TransportLeg, ...] = ()
        flight_options: tuple[TransportOption, ...] = ()
        flight_fallback_legs: tuple[TransportLeg, ...] = ()
        transport_warnings: tuple[str, ...] = ()
        railway_snapshot: RailwayPlanningSnapshot | None = None
        aviation_snapshot: AviationPlanningSnapshot | None = None
        if self._railway_enricher is not None and self._aviation_enricher is not None:
            railway_snapshot, aviation_snapshot = await asyncio.gather(
                self._railway_enricher.enrich(request, catalog),
                self._aviation_enricher.enrich(request, catalog),
            )
        elif self._railway_enricher is not None:
            railway_snapshot = await self._railway_enricher.enrich(request, catalog)
        elif self._aviation_enricher is not None:
            aviation_snapshot = await self._aviation_enricher.enrich(request, catalog)

        replacements: set[tuple[TransportMode, TransportLeg]] = set()
        if railway_snapshot is not None:
            rail_options = railway_snapshot.live_options
            rail_fallback_legs = railway_snapshot.fallback_legs
            replacements.update((TransportMode.RAIL, leg) for leg in railway_snapshot.live_legs)
            transport_warnings += railway_snapshot.warnings
        if aviation_snapshot is not None:
            flight_options = aviation_snapshot.live_options
            flight_fallback_legs = aviation_snapshot.fallback_legs
            replacements.update((TransportMode.FLIGHT, leg) for leg in aviation_snapshot.live_legs)
            transport_warnings += aviation_snapshot.warnings
        if rail_options or flight_options:
            baseline = catalog.transport_options(request)
            merged = (
                tuple(
                    option for option in baseline if (option.mode, option.leg) not in replacements
                )
                + rail_options
                + flight_options
            )
            catalog = catalog.with_transport_options(
                tuple(
                    sorted(
                        merged,
                        key=lambda item: (
                            item.leg.value,
                            item.mode.value,
                            item.depart_at,
                            item.id,
                        ),
                    )
                )
            )

        itinerary, places = DeterministicPlanner(catalog).plan(request)
        report = self._validator.validate(request, itinerary, places)
        plan = PlanResult(
            request=request,
            itinerary=itinerary,
            validation=report,
            data_mode=DataStatus.ESTIMATED,
            warnings=(
                "当前为混合实时方案：地图事实来自高德，铁路按可用性使用 12306 社区 MCP，航班按可用性使用飞常准 MCP；住宿价格及部分规划字段仍为 Fixture/估算。",
                "结果仅用于查询与规划，不提供登录、抢票、预订或下单能力。",
                *snapshot.warnings,
                *transport_warnings,
            ),
        )
        return HybridPlanResult(
            plan=plan,
            live_map_used=True,
            map_places=places,
            weather=snapshot.weather,
            live_rail_used=bool(rail_options),
            rail_options=rail_options,
            rail_fallback_legs=rail_fallback_legs,
            live_flight_used=bool(flight_options),
            flight_options=flight_options,
            flight_fallback_legs=flight_fallback_legs,
            lodging_candidates=snapshot.lodging_areas,
        )

    async def prepare(self, request: TripRequest) -> HybridPlanningContext:
        """Fetch every enabled provider once and return a frozen planning context."""

        try:
            snapshot = await self._builder.build(request)
        except (AmapProviderError, LiveSnapshotUnavailableError, McpGatewayError) as error:
            return HybridPlanningContext(
                request=request,
                catalog=self._fixture,
                live_map_used=False,
                fallback_reason=type(error).__name__,
                warnings=(
                    f"高德实时快照不可用（{type(error).__name__}），已降级到 Fixture。",
                ),
            )

        catalog = snapshot.catalog
        railway_snapshot: RailwayPlanningSnapshot | None = None
        aviation_snapshot: AviationPlanningSnapshot | None = None
        if self._railway_enricher is not None and self._aviation_enricher is not None:
            railway_snapshot, aviation_snapshot = await asyncio.gather(
                self._railway_enricher.enrich(request, catalog),
                self._aviation_enricher.enrich(request, catalog),
            )
        elif self._railway_enricher is not None:
            railway_snapshot = await self._railway_enricher.enrich(request, catalog)
        elif self._aviation_enricher is not None:
            aviation_snapshot = await self._aviation_enricher.enrich(request, catalog)

        rail_options = railway_snapshot.live_options if railway_snapshot else ()
        rail_fallback = railway_snapshot.fallback_legs if railway_snapshot else ()
        flight_options = aviation_snapshot.live_options if aviation_snapshot else ()
        flight_fallback = aviation_snapshot.fallback_legs if aviation_snapshot else ()
        warnings = snapshot.warnings
        replacements: set[tuple[TransportMode, TransportLeg]] = set()
        if railway_snapshot is not None:
            replacements.update((TransportMode.RAIL, leg) for leg in railway_snapshot.live_legs)
            warnings += railway_snapshot.warnings
        if aviation_snapshot is not None:
            replacements.update((TransportMode.FLIGHT, leg) for leg in aviation_snapshot.live_legs)
            warnings += aviation_snapshot.warnings
        if rail_options or flight_options:
            baseline = catalog.transport_options(request)
            merged = tuple(
                option for option in baseline if (option.mode, option.leg) not in replacements
            ) + rail_options + flight_options
            catalog = catalog.with_transport_options(
                tuple(
                    sorted(
                        merged,
                        key=lambda item: (item.leg.value, item.mode.value, item.depart_at, item.id),
                    )
                )
            )
        return HybridPlanningContext(
            request=request,
            catalog=catalog,
            live_map_used=True,
            map_places=snapshot.places,
            weather=snapshot.weather,
            live_rail_used=bool(rail_options),
            rail_options=rail_options,
            rail_fallback_legs=rail_fallback,
            live_flight_used=bool(flight_options),
            flight_options=flight_options,
            flight_fallback_legs=flight_fallback,
            lodging_candidates=snapshot.lodging_areas,
            warnings=warnings,
        )

    def plan_from_context(
        self,
        context: HybridPlanningContext,
        *,
        objective: PlanningObjective = PlanningObjective.BALANCED,
        overrides: PlanningOverrides | None = None,
    ) -> HybridPlanResult:
        """Plan locally from a context without invoking an external provider."""

        itinerary, places = DeterministicPlanner(
            context.catalog, objective=objective, overrides=overrides
        ).plan(context.request)
        report = self._validator.validate(context.request, itinerary, places)
        plan = PlanResult(
            request=context.request,
            itinerary=itinerary,
            validation=report,
            data_mode=DataStatus.ESTIMATED if context.live_map_used else DataStatus.FIXTURE,
            warnings=(
                f"{objective.value} 方案来自同一冻结快照；本次规划没有重复调用 MCP。",
                "结果仅用于查询和演示，不提供预订、下单或支付能力。",
                *context.warnings,
            ),
        )
        return HybridPlanResult(
            plan=plan,
            live_map_used=context.live_map_used,
            map_places=context.map_places,
            weather=context.weather,
            live_rail_used=context.live_rail_used,
            rail_options=context.rail_options,
            rail_fallback_legs=context.rail_fallback_legs,
            live_flight_used=context.live_flight_used,
            flight_options=context.flight_options,
            flight_fallback_legs=context.flight_fallback_legs,
            fallback_reason=context.fallback_reason,
            lodging_candidates=context.lodging_candidates,
        )

    async def plan_alternatives(self, request: TripRequest) -> HybridAlternativeSet:
        context = await self.prepare(request)
        alternatives = tuple(
            self.plan_from_context(context, objective=objective)
            for objective in (
                PlanningObjective.BUDGET,
                PlanningObjective.BALANCED,
                PlanningObjective.TIME,
            )
        )
        return HybridAlternativeSet(request=request, alternatives=alternatives)

    def _record_metrics(self, result: HybridPlanResult, started: float) -> None:
        if self._metrics is None:
            return
        self._metrics.record(
            success=result.plan.validation.feasible,
            cache_hit=result.cache_hit,
            live_map=result.live_map_used and not result.cache_hit,
            live_rail=result.live_rail_used and not result.cache_hit,
            live_flight=result.live_flight_used and not result.cache_hit,
            latency_ms=(monotonic() - started) * 1000,
        )
