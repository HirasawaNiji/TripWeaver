"""Immutable contracts for selection and conversational local replanning."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import Field, computed_field

from tripweaver.application.alternatives_service import AlternativeSet
from tripweaver.domain.models import (
    DataStatus,
    DomainModel,
    ModelCallMetadata,
    Place,
    PlanningObjective,
    PlanningOverrides,
    PlanResult,
    TransportMode,
    TripRequest,
)
from tripweaver.planner.conflicts import PlanningConflict


class SessionStatus(StrEnum):
    AWAITING_SELECTION = "AWAITING_SELECTION"
    ACTIVE = "ACTIVE"


class SessionMode(StrEnum):
    DEMO = "DEMO"
    LIVE = "LIVE"


class TraceCategory(StrEnum):
    LLM = "LLM"
    MCP = "MCP"
    PLANNER = "PLANNER"
    VALIDATOR = "VALIDATOR"
    SESSION = "SESSION"


class TraceStatus(StrEnum):
    SUCCESS = "SUCCESS"
    FALLBACK = "FALLBACK"
    FAILED = "FAILED"


class ProviderSnapshot(DomainModel):
    provider: str
    status: DataStatus
    item_count: int = Field(default=0, ge=0)
    queried_at: datetime
    expires_at: datetime | None = None
    detail: str = ""

    @computed_field
    @property
    def expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= datetime.now(UTC)


class PlanningSnapshot(DomainModel):
    id: str
    mode: SessionMode
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    refresh_count: int = Field(default=0, ge=0)
    providers: tuple[ProviderSnapshot, ...] = ()
    fallback_reason: str | None = None

    @computed_field
    @property
    def expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= datetime.now(UTC)


class ExecutionTraceStep(DomainModel):
    id: str
    category: TraceCategory
    name: str
    provider: str
    status: TraceStatus
    started_at: datetime
    duration_ms: float = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    fallback_used: bool = False
    detail: str = ""


class TraceSummary(DomainModel):
    total_steps: int = Field(ge=0)
    llm_calls: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    fallback_count: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    total_latency_ms: float = Field(ge=0)
    steps: tuple[ExecutionTraceStep, ...]


class RevisionIntent(DomainModel):
    """Strict allow-list of changes an interpreter may request."""

    select_alternative: int | None = Field(default=None, ge=1, le=3)
    objective: PlanningObjective | None = None
    outbound_modes: tuple[TransportMode, ...] | None = None
    inbound_modes: tuple[TransportMode, ...] | None = None
    max_nightly_price_cny: Decimal | None = Field(default=None, gt=0, le=10000)
    replace_day: int | None = Field(default=None, ge=1, le=7)
    replace_place_id: str | None = None
    preserve_outbound: bool = False
    preserve_inbound: bool = False
    preserve_lodging: bool = False
    explanation: str = Field(default="", max_length=300)


class PlanDiff(DomainModel):
    changed_fields: tuple[str, ...]
    summary: tuple[str, ...]
    preserved_fields: tuple[str, ...]


class ConversationEvent(DomainModel):
    kind: str
    message: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PlanningSession(DomainModel):
    id: str
    request: TripRequest
    alternatives: AlternativeSet
    mode: SessionMode = SessionMode.DEMO
    snapshot: PlanningSnapshot | None = None
    selected_index: int | None = None
    selected_plan: PlanResult | None = None
    overrides: PlanningOverrides = Field(default_factory=PlanningOverrides)
    status: SessionStatus = SessionStatus.AWAITING_SELECTION
    revision_count: int = 0
    data_fetch_count: int = 1
    last_diff: PlanDiff | None = None
    events: tuple[ConversationEvent, ...] = ()
    model_calls: tuple[ModelCallMetadata, ...] = ()
    locked_fields: tuple[str, ...] = ()
    history: tuple[PlanResult, ...] = ()
    places: tuple[Place, ...] = ()
    last_conflict: PlanningConflict | None = None
    traces: tuple[ExecutionTraceStep, ...] = ()
