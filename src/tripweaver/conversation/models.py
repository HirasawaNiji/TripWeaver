"""Immutable contracts for selection and conversational local replanning."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import Field

from tripweaver.application.alternatives_service import AlternativeSet
from tripweaver.domain.models import (
    DomainModel,
    ModelCallMetadata,
    Place,
    PlanningObjective,
    PlanningOverrides,
    PlanResult,
    TransportMode,
    TripRequest,
)


class SessionStatus(StrEnum):
    AWAITING_SELECTION = "AWAITING_SELECTION"
    ACTIVE = "ACTIVE"


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
