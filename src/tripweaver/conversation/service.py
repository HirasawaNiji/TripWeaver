"""In-memory conversation application service with network-free replanning."""

from __future__ import annotations

from uuid import uuid4

from tripweaver.application.alternatives_service import AlternativeTripPlanningService
from tripweaver.conversation.models import (
    ConversationEvent,
    PlanDiff,
    PlanningSession,
    RevisionIntent,
    SessionStatus,
)
from tripweaver.conversation.parser import DeterministicRevisionParser
from tripweaver.domain.models import (
    DataStatus,
    PlanningObjective,
    PlanningOverrides,
    PlanResult,
    TripRequest,
)
from tripweaver.fixtures.catalog import FixtureCatalog
from tripweaver.planner.catalog import PlanningCatalog
from tripweaver.planner.engine import DeterministicPlanner
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
    ) -> None:
        self.catalog = catalog or FixtureCatalog()
        self.parser = parser or DeterministicRevisionParser()
        self.validator = validator or ItineraryValidator()
        self._sessions: dict[str, PlanningSession] = {}

    def create(self, request: TripRequest) -> PlanningSession:
        alternatives = AlternativeTripPlanningService(catalog=self.catalog, validator=self.validator).plan(request)
        session = PlanningSession(
            id=f"tws-{uuid4().hex[:12]}", request=request, alternatives=alternatives,
            places=self.catalog.places(request.destination),
            events=(ConversationEvent(kind="SNAPSHOT_READY", message="数据快照与三个方案已生成"),),
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
        blocked = (
            ("outbound" in session.locked_fields and intent.outbound_modes is not None)
            or ("inbound" in session.locked_fields and intent.inbound_modes is not None)
            or ("lodging" in session.locked_fields and intent.max_nightly_price_cny is not None)
        )
        if blocked:
            raise ValueError("修改涉及已锁定项目，请先解除锁定")
        if intent.select_alternative is not None:
            session = self.select(session_id, intent.select_alternative)
        if session.selected_plan is None:
            session = self.select(session_id, 2)
        assert session.selected_plan is not None
        before = session.selected_plan
        overrides = self._merge_overrides(session.overrides, before, intent)
        objective = intent.objective or self._objective_for_index(session.selected_index or 2)
        itinerary, places = DeterministicPlanner(self.catalog, objective=objective, overrides=overrides).plan(session.request)
        validation = self.validator.validate(session.request, itinerary, places)
        result = PlanResult(
            request=session.request, itinerary=itinerary, validation=validation, data_mode=DataStatus.FIXTURE,
            warnings=("本次为基于已冻结数据快照的局部重规划，未重复调用外部 MCP。",),
        )
        diff = self._diff(before, result)
        updated = session.model_copy(update={
            "selected_plan": result, "overrides": overrides, "status": SessionStatus.ACTIVE,
            "revision_count": session.revision_count + 1, "last_diff": diff,
            "history": session.history + (before,),
            "events": session.events + (ConversationEvent(kind="PLAN_REVISED", message="局部重规划完成"),),
        })
        self._sessions[session_id] = updated
        return updated

    def record_model_call(self, session_id: str, metadata: object) -> PlanningSession:
        from tripweaver.domain.models import ModelCallMetadata

        session = self.get(session_id)
        call = ModelCallMetadata.model_validate(metadata)
        updated = session.model_copy(update={"model_calls": session.model_calls + (call,)})
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
