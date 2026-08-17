"""Unified demo/live conversation orchestration over frozen planning snapshots."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from time import monotonic
from uuid import uuid4

from tripweaver.application.hybrid_service import (
    HybridPlanningContext,
    HybridTripPlanningService,
)
from tripweaver.conversation.models import (
    ConversationEvent,
    ExecutionTraceStep,
    PlanningSession,
    PlanningSnapshot,
    ProviderSnapshot,
    RevisionIntent,
    SessionMode,
    TraceCategory,
    TraceStatus,
    TraceSummary,
)
from tripweaver.conversation.service import (
    ConversationPlanningService,
    SessionNotFoundError,
)
from tripweaver.domain.models import DataStatus, SourceMetadata, TripRequest


class HybridConversationPlanningService:
    """Fetch once for LIVE sessions, then keep every revision network-free."""

    def __init__(
        self,
        hybrid: HybridTripPlanningService | None = None,
        *,
        hybrid_factory: Callable[[], HybridTripPlanningService] | None = None,
    ) -> None:
        self._hybrid = hybrid
        self._hybrid_factory = hybrid_factory
        self._locals: dict[str, ConversationPlanningService] = {}
        self._contexts: dict[str, HybridPlanningContext] = {}

    async def create(
        self,
        request: TripRequest,
        *,
        mode: SessionMode = SessionMode.LIVE,
    ) -> PlanningSession:
        if mode == SessionMode.DEMO:
            local = ConversationPlanningService()
            session = local.create(request, mode=mode)
            self._locals[session.id] = local
            return session
        return await self._create_live(request)

    async def _create_live(self, request: TripRequest) -> PlanningSession:
        started_at = datetime.now(UTC)
        started = monotonic()
        context = await self._get_hybrid().prepare(request)
        duration_ms = (monotonic() - started) * 1000
        snapshot = self._snapshot_from_context(context, refresh_count=0)
        local = self._local_for_context(context)
        session = local.create(
            request,
            mode=SessionMode.LIVE,
            snapshot=snapshot,
        )
        traces = self._provider_traces(snapshot, started_at, duration_ms)
        session = local.append_traces(session.id, traces)
        self._locals[session.id] = local
        self._contexts[session.id] = context
        return session

    async def refresh(self, session_id: str) -> PlanningSession:
        current = self.get(session_id)
        if current.mode != SessionMode.LIVE:
            raise ValueError("演示模式使用固定 Fixture，不需要刷新实时数据")
        started_at = datetime.now(UTC)
        started = monotonic()
        context = await self._get_hybrid().prepare(current.request)
        duration_ms = (monotonic() - started) * 1000
        refresh_count = (current.snapshot.refresh_count if current.snapshot else 0) + 1
        snapshot = self._snapshot_from_context(context, refresh_count=refresh_count)
        local = self._local_for_context(context)
        session = local.create(
            current.request,
            session_id=session_id,
            mode=SessionMode.LIVE,
            snapshot=snapshot,
            data_fetch_count=current.data_fetch_count + 1,
            prior_events=current.events
            + (ConversationEvent(kind="SNAPSHOT_REFRESHED", message="已主动刷新实时数据快照"),),
            model_calls=current.model_calls,
            prior_traces=current.traces,
        )
        session = local.append_traces(
            session_id, self._provider_traces(snapshot, started_at, duration_ms)
        )
        self._locals[session_id] = local
        self._contexts[session_id] = context
        return session

    def get(self, session_id: str) -> PlanningSession:
        return self._local(session_id).get(session_id)

    def select(self, session_id: str, index: int) -> PlanningSession:
        return self._local(session_id).select(session_id, index)

    def revise(self, session_id: str, text: str) -> PlanningSession:
        return self._local(session_id).revise(session_id, text)

    def revise_with_intent(
        self, session_id: str, intent: RevisionIntent
    ) -> PlanningSession:
        return self._local(session_id).revise_with_intent(session_id, intent)

    def record_model_call(self, session_id: str, metadata: object) -> PlanningSession:
        return self._local(session_id).record_model_call(session_id, metadata)

    def undo(self, session_id: str) -> PlanningSession:
        return self._local(session_id).undo(session_id)

    def set_locks(self, session_id: str, fields: tuple[str, ...]) -> PlanningSession:
        return self._local(session_id).set_locks(session_id, fields)

    def trace_summary(self, session_id: str) -> TraceSummary:
        return self._local(session_id).trace_summary(session_id)

    def context(self, session_id: str) -> HybridPlanningContext:
        try:
            return self._contexts[session_id]
        except KeyError as error:
            raise SessionNotFoundError(session_id) from error

    def _get_hybrid(self) -> HybridTripPlanningService:
        if self._hybrid is None:
            if self._hybrid_factory is None:
                raise ValueError("实时会话服务尚未配置")
            self._hybrid = self._hybrid_factory()
        return self._hybrid

    @staticmethod
    def _local_for_context(context: HybridPlanningContext) -> ConversationPlanningService:
        return ConversationPlanningService(
            catalog=context.catalog,
            data_mode=(DataStatus.ESTIMATED if context.live_map_used else DataStatus.FIXTURE),
            snapshot_warnings=context.warnings,
        )

    def _local(self, session_id: str) -> ConversationPlanningService:
        try:
            return self._locals[session_id]
        except KeyError as error:
            raise SessionNotFoundError(session_id) from error

    @staticmethod
    def _snapshot_from_context(
        context: HybridPlanningContext, *, refresh_count: int
    ) -> PlanningSnapshot:
        sources: list[SourceMetadata] = [place.source for place in context.map_places]
        sources.extend(option.source for option in context.rail_options)
        sources.extend(option.source for option in context.flight_options)
        sources.extend(area.source for area in context.lodging_candidates)
        grouped: dict[tuple[str, DataStatus], list[SourceMetadata]] = {}
        for source in sources:
            grouped.setdefault((source.provider, source.status), []).append(source)
        providers: list[ProviderSnapshot] = []
        for (provider, status), items in sorted(grouped.items(), key=lambda item: item[0][0]):
            expiries = tuple(item.expires_at for item in items if item.expires_at is not None)
            providers.append(
                ProviderSnapshot(
                    provider=provider,
                    status=status,
                    item_count=len(items),
                    queried_at=max(item.queried_at for item in items),
                    expires_at=min(expiries) if expiries else None,
                    detail="冻结到当前 Session，修改时不会重复查询",
                )
            )
        if not providers:
            providers.append(
                ProviderSnapshot(
                    provider="tripweaver_fixture_v2",
                    status=DataStatus.FIXTURE,
                    item_count=len(context.catalog.places(context.request.destination)),
                    queried_at=datetime.now(UTC),
                    detail=f"实时快照降级：{context.fallback_reason or 'provider unavailable'}",
                )
            )
        expiries = tuple(item.expires_at for item in providers if item.expires_at is not None)
        return PlanningSnapshot(
            id=f"snap-{uuid4().hex[:12]}",
            mode=SessionMode.LIVE,
            expires_at=min(expiries) if expiries else None,
            refresh_count=refresh_count,
            providers=tuple(providers),
            fallback_reason=context.fallback_reason,
        )

    @staticmethod
    def _provider_traces(
        snapshot: PlanningSnapshot,
        started_at: datetime,
        duration_ms: float,
    ) -> tuple[ExecutionTraceStep, ...]:
        aggregate = ExecutionTraceStep(
            id=f"trace-{uuid4().hex[:10]}",
            category=TraceCategory.MCP,
            name="FETCH_SNAPSHOT",
            provider="multi-source-gateway",
            status=(
                TraceStatus.FALLBACK if snapshot.fallback_reason else TraceStatus.SUCCESS
            ),
            started_at=started_at,
            duration_ms=duration_ms,
            fallback_used=bool(snapshot.fallback_reason),
            detail=f"providers={len(snapshot.providers)} · snapshot={snapshot.id}",
        )
        provider_steps = tuple(
            ExecutionTraceStep(
                id=f"trace-{uuid4().hex[:10]}",
                category=TraceCategory.MCP,
                name="PROVIDER_SNAPSHOT",
                provider=provider.provider,
                status=(
                    TraceStatus.SUCCESS
                    if provider.status in {DataStatus.LIVE, DataStatus.CACHED}
                    else TraceStatus.FALLBACK
                ),
                started_at=provider.queried_at,
                fallback_used=provider.status not in {DataStatus.LIVE, DataStatus.CACHED},
                detail=f"{provider.status.value} · {provider.item_count} items",
            )
            for provider in snapshot.providers
        )
        return (aggregate, *provider_steps)
