"""Auditable models emitted by the controlled agent layer."""

from __future__ import annotations

from enum import StrEnum

from tripweaver.application.hybrid_service import HybridPlanResult
from tripweaver.domain.models import DomainModel


class AgentRunStatus(StrEnum):
    NEEDS_INPUT = "NEEDS_INPUT"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"


class AgentStep(DomainModel):
    name: str
    outcome: str
    detail: str


class GroundedExplanation(DomainModel):
    summary: str
    transport_reason: str
    lodging_reason: str
    budget_statement: str
    daily_outline: tuple[str, ...]
    caveats: tuple[str, ...]


class AgentRun(DomainModel):
    status: AgentRunStatus
    questions: tuple[str, ...] = ()
    steps: tuple[AgentStep, ...] = ()
    result: HybridPlanResult | None = None
    explanation: GroundedExplanation | None = None
