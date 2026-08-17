"""In-memory conversation application service with network-free replanning."""

from __future__ import annotations

from datetime import UTC, datetime
from time import monotonic
from uuid import uuid4

from tripweaver.application.alternatives_service import AlternativeTripPlanningService
from tripweaver.conversation.models import (
    ConversationEvent,
    ExecutionTraceStep,
    PlanDiff,
    PlanningSession,
    PlanningSnapshot,
    ProviderSnapshot,
    RevisionIntent,
    SessionMode,
    SessionStatus,
    TraceCategory,
    TraceStatus,
    TraceSummary,
)
from tripweaver.conversation.parser import DeterministicRevisionParser
from tripweaver.domain.models import (
    DataStatus,
    ModelCallMetadata,
    PlanningObjective,
    PlanningOverrides,
    PlanResult,
    TripRequest,
)
from tripweaver.fixtures.catalog import FixtureCatalog
from tripweaver.planner.catalog import PlanningCatalog
from tripweaver.planner.conflicts import (
    ConflictAnalyzer,
    LockedConstraintError,
    PlanningConflictError,
)
from tripweaver.planner.engine import DeterministicPlanner, NoFeasiblePlanError
from tripweaver.validator.service import ItineraryValidator


class SessionNotFoundError(LookupError):
    pass


class ConversationPlanningService:
    def __init__(
        self,
        *,
        catalog: PlanningCatalog | None = None,
        parser: DeterministicRevisionParser | None = None,
        validator: ItineraryValidator | None = None,
        data_mode: DataStatus = DataStatus.FIXTURE,
        snapshot_warnings: tuple[str, ...] = (),
    ) -> None:
        self.catalog = catalog or FixtureCatalog()
        self.parser = parser or DeterministicRevisionParser()
        self.validator = validator or ItineraryValidator()
        self.data_mode = data_mode
        self.snapshot_warnings = snapshot_warnings
        self._sessions: dict[str, PlanningSession] = {}

    def create(
        self,
        request: TripRequest,
        *,
        session_id: str | None = None,
        mode: SessionMode = SessionMode.DEMO,
        snapshot: PlanningSnapshot | None = None,
        data_fetch_count: int = 1,
        prior_events: tuple[ConversationEvent, ...] = (),
        model_calls: tuple[ModelCallMetadata, ...] = (),
        prior_traces: tuple[ExecutionTraceStep, ...] = (),
    ) -> PlanningSession:
        started_at = datetime.now(UTC)
        started = monotonic()
        alternatives = AlternativeTripPlanningService(
            catalog=self.catalog,
            validator=self.validator,
            data_mode=self.data_mode,
            warnings=self.snapshot_warnings,
        ).plan(request)
        duration_ms = (monotonic() - started) * 1000
        places = self.catalog.places(request.destination)
        snapshot = snapshot or PlanningSnapshot(
            id=f"snap-{uuid4().hex[:12]}",
            mode=mode,
            providers=(
                ProviderSnapshot(
                    provider="tripweaver_fixture_v2",
                    status=DataStatus.FIXTURE,
                    item_count=len(places),
                    queried_at=started_at,
                    detail="内置可复现演示快照",
                ),
            ),
        )
        session = PlanningSession(
            id=session_id or f"tws-{uuid4().hex[:12]}",
            request=request,
            alternatives=alternatives,
            mode=mode,
            snapshot=snapshot,
            data_fetch_count=data_fetch_count,
            places=places,
            model_calls=model_calls,
            events=prior_events
            + (ConversationEvent(kind="SNAPSHOT_READY", message="数据快照与三个方案已生成"),),
            traces=prior_traces
            + (
                ExecutionTraceStep(
                    id=f"trace-{uuid4().hex[:10]}",
                    category=TraceCategory.PLANNER,
                    name="GENERATE_ALTERNATIVES",
                    provider="tripweaver",
                    status=TraceStatus.SUCCESS,
                    started_at=started_at,
                    duration_ms=duration_ms,
                    detail="从同一冻结快照生成预算、均衡、时间三套方案",
                ),
                ExecutionTraceStep(
                    id=f"trace-{uuid4().hex[:10]}",
                    category=TraceCategory.VALIDATOR,
                    name="VALIDATE_ALTERNATIVES",
                    provider="tripweaver",
                    status=TraceStatus.SUCCESS,
                    started_at=started_at,
                    detail="三套方案均经过独立硬约束校验",
                ),
            ),
        )
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> PlanningSession:
        try:
            return self._sessions[session_id]
        except KeyError as error:
            raise SessionNotFoundError(session_id) from error

    def select(self, session_id: str, index: int) -> PlanningSession:
        session = self.get(session_id)
        if not 1 <= index <= len(session.alternatives.alternatives):
            raise ValueError("方案序号必须是 1、2 或 3")
        selected = session.alternatives.alternatives[index - 1]
        updated = session.model_copy(update={
            "selected_index": index, "selected_plan": selected, "status": SessionStatus.ACTIVE,
            "events": session.events + (ConversationEvent(kind="PLAN_SELECTED", message=f"已选择方案 {index}"),),
        })
        self._sessions[session_id] = updated
        return updated

    def revise(self, session_id: str, text: str) -> PlanningSession:
        intent = self.parser.parse(text)
        return self.revise_with_intent(session_id, intent)

    def revise_with_intent(
        self, session_id: str, intent: RevisionIntent
    ) -> PlanningSession:
        session = self.get(session_id)
        blocked_fields = tuple(
            field
            for field, changed in (
                ("outbound", intent.outbound_modes is not None),
                ("inbound", intent.inbound_modes is not None),
                ("lodging", intent.max_nightly_price_cny is not None),
            )
            if field in session.locked_fields and changed
        )
        if blocked_fields:
            error = LockedConstraintError(blocked_fields)
            conflict = ConflictAnalyzer.analyze(error, session.request)
            self._sessions[session_id] = session.model_copy(update={"last_conflict": conflict})
            raise PlanningConflictError(conflict) from error
        if intent.select_alternative is not None:
            session = self.select(session_id, intent.select_alternative)
        if session.selected_plan is None:
            session = self.select(session_id, 2)
        assert session.selected_plan is not None
        before = session.selected_plan
        overrides = self._merge_overrides(session.overrides, before, intent)
        objective = intent.objective or self._objective_for_index(session.selected_index or 2)
        started_at = datetime.now(UTC)
        started = monotonic()
        try:
            itinerary, places = DeterministicPlanner(
                self.catalog, objective=objective, overrides=overrides
            ).plan(session.request)
        except NoFeasiblePlanError as error:
            conflict = ConflictAnalyzer.analyze(error, session.request)
            self._sessions[session_id] = session.model_copy(
                update={
                    "last_conflict": conflict,
                    "events": session.events
                    + (ConversationEvent(kind="PLAN_CONFLICT", message=conflict.title),),
                    "traces": session.traces
                    + (
                        ExecutionTraceStep(
                            id=f"trace-{uuid4().hex[:10]}",
                            category=TraceCategory.PLANNER,
                            name="LOCAL_REPLAN",
                            provider="tripweaver",
                            status=TraceStatus.FAILED,
                            started_at=started_at,
                            duration_ms=(monotonic() - started) * 1000,
                            detail=conflict.code.value,
                        ),
                    ),
                }
            )
            raise PlanningConflictError(conflict) from error
        validation = self.validator.validate(session.request, itinerary, places)
        result = PlanResult(
            request=session.request,
            itinerary=itinerary,
            validation=validation,
            data_mode=self.data_mode,
            warnings=(
                "本次为基于已冻结数据快照的局部重规划，未重复调用外部 MCP。",
                *self.snapshot_warnings,
            ),
        )
        diff = self._diff(before, result)
        updated = session.model_copy(update={
            "selected_plan": result, "overrides": overrides, "status": SessionStatus.ACTIVE,
            "revision_count": session.revision_count + 1, "last_diff": diff,
            "history": session.history + (before,),
            "last_conflict": None,
            "events": session.events + (ConversationEvent(kind="PLAN_REVISED", message="局部重规划完成"),),
            "traces": session.traces
            + (
                ExecutionTraceStep(
                    id=f"trace-{uuid4().hex[:10]}",
                    category=TraceCategory.PLANNER,
                    name="LOCAL_REPLAN",
                    provider="tripweaver",
                    status=TraceStatus.SUCCESS,
                    started_at=started_at,
                    duration_ms=(monotonic() - started) * 1000,
                    detail="复用冻结快照，未调用外部 MCP",
                ),
                ExecutionTraceStep(
                    id=f"trace-{uuid4().hex[:10]}",
                    category=TraceCategory.VALIDATOR,
                    name="VALIDATE_REPLAN",
                    provider="tripweaver",
                    status=(
                        TraceStatus.SUCCESS if validation.feasible else TraceStatus.FAILED
                    ),
                    started_at=started_at,
                    detail=f"issues={len(validation.issues)}",
                ),
            ),
        })
        self._sessions[session_id] = updated
        return updated

    def record_model_call(self, session_id: str, metadata: object) -> PlanningSession:
        session = self.get(session_id)
        call = ModelCallMetadata.model_validate(metadata)
        trace = ExecutionTraceStep(
            id=f"trace-{uuid4().hex[:10]}",
            category=TraceCategory.LLM,
            name="MODEL_CALL",
            provider=call.provider,
            status=(TraceStatus.FALLBACK if call.fallback_used else TraceStatus.SUCCESS),
            started_at=datetime.now(UTC),
            duration_ms=call.latency_ms,
            input_tokens=call.input_tokens,
            output_tokens=call.output_tokens,
            fallback_used=call.fallback_used,
            detail=f"{call.mode} · {call.model}",
        )
        updated = session.model_copy(
            update={
                "model_calls": session.model_calls + (call,),
                "traces": session.traces + (trace,),
            }
        )
        self._sessions[session_id] = updated
        return updated

    def trace_summary(self, session_id: str) -> TraceSummary:
        steps = self.get(session_id).traces
        return TraceSummary(
            total_steps=len(steps),
            llm_calls=sum(step.category == TraceCategory.LLM for step in steps),
            tool_calls=sum(step.category == TraceCategory.MCP for step in steps),
            fallback_count=sum(step.status == TraceStatus.FALLBACK for step in steps),
            total_tokens=sum(step.input_tokens + step.output_tokens for step in steps),
            total_latency_ms=sum(step.duration_ms for step in steps),
            steps=steps,
        )

    def append_traces(
        self, session_id: str, traces: tuple[ExecutionTraceStep, ...]
    ) -> PlanningSession:
        session = self.get(session_id)
        updated = session.model_copy(update={"traces": session.traces + traces})
        self._sessions[session_id] = updated
        return updated

    def undo(self, session_id: str) -> PlanningSession:
        session = self.get(session_id)
        if not session.history:
            raise ValueError("没有可撤销的规划版本")
        previous = session.history[-1]
        updated = session.model_copy(update={
            "selected_plan": previous,
            "history": session.history[:-1],
            "revision_count": max(session.revision_count - 1, 0),
            "last_diff": None,
            "events": session.events
            + (ConversationEvent(kind="PLAN_UNDONE", message="已撤销上一轮修改"),),
        })
        self._sessions[session_id] = updated
        return updated

    def set_locks(self, session_id: str, fields: tuple[str, ...]) -> PlanningSession:
        allowed = {"outbound", "inbound", "lodging"}
        if not set(fields) <= allowed:
            raise ValueError("只能锁定 outbound、inbound 或 lodging")
        session = self.get(session_id)
        updated = session.model_copy(update={"locked_fields": tuple(sorted(set(fields)))})
        self._sessions[session_id] = updated
        return updated

    @staticmethod
    def _objective_for_index(index: int) -> PlanningObjective:
        return (PlanningObjective.BUDGET, PlanningObjective.BALANCED, PlanningObjective.TIME)[index - 1]

    @staticmethod
    def _merge_overrides(current: PlanningOverrides, before: PlanResult, intent: RevisionIntent) -> PlanningOverrides:
        itinerary = before.itinerary
        excluded = list(current.excluded_place_ids)
        if intent.replace_place_id is not None and intent.replace_place_id not in excluded:
            excluded.append(intent.replace_place_id)
        if intent.replace_day is not None and intent.replace_day <= len(itinerary.days):
            visits = itinerary.days[intent.replace_day - 1].visits
            if visits and visits[0].place_id not in excluded:
                excluded.append(visits[0].place_id)
        return PlanningOverrides(
            fixed_outbound_id=None if intent.outbound_modes is not None else itinerary.outbound.id,
            fixed_inbound_id=None if intent.inbound_modes is not None else itinerary.inbound.id,
            fixed_lodging_id=None if intent.max_nightly_price_cny is not None else itinerary.lodging_area.id,
            excluded_place_ids=tuple(excluded),
            max_nightly_price_cny=intent.max_nightly_price_cny or current.max_nightly_price_cny,
            outbound_modes=intent.outbound_modes or current.outbound_modes,
            inbound_modes=intent.inbound_modes or current.inbound_modes,
        )

    @staticmethod
    def _diff(before: PlanResult, after: PlanResult) -> PlanDiff:
        old, new = before.itinerary, after.itinerary
        changed: list[str] = []
        summary: list[str] = []
        preserved: list[str] = []
        for field_name, old_value, new_value, label in (
            ("outbound", old.outbound.id, new.outbound.id, "去程"),
            ("inbound", old.inbound.id, new.inbound.id, "返程"),
            ("lodging", old.lodging_area.id, new.lodging_area.id, "住宿"),
        ):
            if old_value == new_value:
                preserved.append(field_name)
            else:
                changed.append(field_name)
                summary.append(f"{label}：{old_value} → {new_value}")
        old_visits = tuple(v.place_id for d in old.days for v in d.visits)
        new_visits = tuple(v.place_id for d in new.days for v in d.visits)
        if old_visits != new_visits:
            changed.append("visits")
            summary.append("每日景点安排已重新计算")
        if old.budget.total_cny != new.budget.total_cny:
            changed.append("budget")
            summary.append(f"总预算：{old.budget.total_cny} → {new.budget.total_cny}")
        return PlanDiff(changed_fields=tuple(changed), summary=tuple(summary) or ("方案内容未发生变化",), preserved_fields=tuple(preserved))
