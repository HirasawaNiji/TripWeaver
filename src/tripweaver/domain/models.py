"""Canonical domain models shared by planners, validators, and adapters."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from tripweaver.domain.cities import canonical_city_name


class DomainModel(BaseModel):
    """Strict immutable base model for deterministic planning data."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class DataStatus(StrEnum):
    LIVE = "LIVE"
    CACHED = "CACHED"
    FIXTURE = "FIXTURE"
    ESTIMATED = "ESTIMATED"
    UNAVAILABLE = "UNAVAILABLE"


class ModelCallMetadata(DomainModel):
    provider: str
    model: str
    mode: str
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0, ge=0)
    fallback_used: bool = False
    error_type: str | None = None


class Severity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"


class TransportMode(StrEnum):
    RAIL = "RAIL"
    FLIGHT = "FLIGHT"


class TransportLeg(StrEnum):
    OUTBOUND = "OUTBOUND"
    RETURN = "RETURN"


class PlanningObjective(StrEnum):
    BUDGET = "BUDGET"
    BALANCED = "BALANCED"
    TIME = "TIME"


class SourceMetadata(DomainModel):
    provider: str = Field(min_length=1)
    status: DataStatus
    queried_at: datetime
    expires_at: datetime | None = None
    source_reference: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class TripRequest(DomainModel):
    origin: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    start_date: date
    end_date: date
    travelers: int = Field(ge=1, le=8)
    budget_cny: Decimal = Field(gt=0)
    max_daily_minutes: int = Field(default=660, ge=180, le=900)
    interests: tuple[str, ...] = ()
    preferred_transport: tuple[TransportMode, ...] = (
        TransportMode.RAIL,
        TransportMode.FLIGHT,
    )
    assumptions: tuple[str, ...] = ()

    @field_validator("origin", "destination")
    @classmethod
    def normalize_city(cls, value: str) -> str:
        return canonical_city_name(value)

    @model_validator(mode="after")
    def validate_dates(self) -> TripRequest:
        if self.origin == self.destination:
            raise ValueError("origin and destination must be different")
        if self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        if self.trip_days > 7:
            raise ValueError("trips are limited to seven days")
        return self

    @computed_field
    @property
    def trip_days(self) -> int:
        return (self.end_date - self.start_date).days + 1


class PlanningOverrides(DomainModel):
    """User-confirmed choices retained across local replanning turns."""

    fixed_outbound_id: str | None = None
    fixed_inbound_id: str | None = None
    fixed_lodging_id: str | None = None
    excluded_place_ids: tuple[str, ...] = ()
    max_nightly_price_cny: Decimal | None = Field(default=None, gt=0)
    outbound_modes: tuple[TransportMode, ...] | None = None
    inbound_modes: tuple[TransportMode, ...] | None = None


class TransportOption(DomainModel):
    id: str
    leg: TransportLeg
    mode: TransportMode
    label: str
    origin: str
    destination: str
    depart_at: datetime
    arrive_at: datetime
    price_per_person_cny: Decimal = Field(ge=0)
    source: SourceMetadata

    @model_validator(mode="after")
    def validate_time_order(self) -> TransportOption:
        if self.arrive_at <= self.depart_at:
            raise ValueError("transport arrival must be after departure")
        return self

    @computed_field
    @property
    def duration_minutes(self) -> int:
        return int((self.arrive_at - self.depart_at).total_seconds() // 60)


class GeoPoint(DomainModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class Place(DomainModel):
    id: str
    name: str
    category: str
    location: GeoPoint
    suggested_duration_minutes: int = Field(gt=0)
    admission_per_person_cny: Decimal = Field(ge=0)
    opens_at: time
    closes_at: time
    closed_weekdays: tuple[int, ...] = ()
    tags: tuple[str, ...] = ()
    priority: int = Field(default=50, ge=0, le=100)
    opening_hours_note: str | None = None
    planning_assumptions: tuple[str, ...] = ()
    source: SourceMetadata


class LodgingArea(DomainModel):
    id: str
    name: str
    location: GeoPoint
    nightly_price_estimate_cny: Decimal = Field(ge=0)
    description: str
    candidate_hotel_name: str | None = None
    candidate_hotel_address: str | None = None
    candidate_hotel_rating: float | None = Field(default=None, ge=0, le=5)
    price_basis: str = "ESTIMATED_POLICY"
    source: SourceMetadata


class RouteLeg(DomainModel):
    from_id: str
    to_id: str
    mode: str
    minutes: int = Field(ge=0)
    cost_cny: Decimal = Field(ge=0)
    distance_km: Decimal = Field(ge=0)
    source: SourceMetadata


class ScheduledVisit(DomainModel):
    place_id: str
    place_name: str
    start_at: datetime
    end_at: datetime
    admission_total_cny: Decimal = Field(ge=0)
    route_from_previous: RouteLeg
    source: SourceMetadata


class DayPlan(DomainModel):
    date: date
    visits: tuple[ScheduledVisit, ...]
    local_transport_cost_cny: Decimal = Field(ge=0)


class BudgetBreakdown(DomainModel):
    transport_cny: Decimal = Field(ge=0)
    lodging_cny: Decimal = Field(ge=0)
    admission_cny: Decimal = Field(ge=0)
    local_transport_cny: Decimal = Field(ge=0)
    meals_estimated_cny: Decimal = Field(ge=0)
    estimation_source: SourceMetadata

    @computed_field
    @property
    def total_cny(self) -> Decimal:
        return (
            self.transport_cny
            + self.lodging_cny
            + self.admission_cny
            + self.local_transport_cny
            + self.meals_estimated_cny
        )


class Itinerary(DomainModel):
    id: str
    title: str
    outbound: TransportOption
    inbound: TransportOption
    lodging_area: LodgingArea
    days: tuple[DayPlan, ...]
    budget: BudgetBreakdown


class ValidationIssue(DomainModel):
    code: str
    severity: Severity
    message: str
    path: str | None = None


class ValidationReport(DomainModel):
    issues: tuple[ValidationIssue, ...] = ()

    @computed_field
    @property
    def feasible(self) -> bool:
        return not any(issue.severity == Severity.ERROR for issue in self.issues)


class PlanResult(DomainModel):
    request: TripRequest
    itinerary: Itinerary
    validation: ValidationReport
    data_mode: DataStatus
    warnings: tuple[str, ...] = ()
