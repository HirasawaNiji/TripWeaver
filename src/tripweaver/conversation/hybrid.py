"""Conversation orchestration over one live-or-fallback hybrid context."""

from __future__ import annotations

from tripweaver.application.hybrid_service import HybridPlanningContext, HybridTripPlanningService
from tripweaver.conversation.models import PlanningSession
from tripweaver.conversation.service import ConversationPlanningService, SessionNotFoundError
from tripweaver.domain.models import TripRequest


class HybridConversationPlanningService:
    """Fetch once on creation, then delegate every turn to a network-free local service."""

    def __init__(self, hybrid: HybridTripPlanningService) -> None:
        self._hybrid = hybrid
        self._locals: dict[str, ConversationPlanningService] = {}
        self._contexts: dict[str, HybridPlanningContext] = {}

    async def create(self, request: TripRequest) -> PlanningSession:
        context = await self._hybrid.prepare(request)
        local = ConversationPlanningService(catalog=context.catalog)
        session = local.create(request)
        self._locals[session.id] = local
        self._contexts[session.id] = context
        return session

    def get(self, session_id: str) -> PlanningSession:
        return self._local(session_id).get(session_id)

    def select(self, session_id: str, index: int) -> PlanningSession:
        return self._local(session_id).select(session_id, index)

    def revise(self, session_id: str, text: str) -> PlanningSession:
        return self._local(session_id).revise(session_id, text)

    def context(self, session_id: str) -> HybridPlanningContext:
        try:
            return self._contexts[session_id]
        except KeyError as error:
            raise SessionNotFoundError(session_id) from error

    def _local(self, session_id: str) -> ConversationPlanningService:
        try:
            return self._locals[session_id]
        except KeyError as error:
            raise SessionNotFoundError(session_id) from error
