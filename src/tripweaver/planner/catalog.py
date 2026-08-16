"""Synchronous planning-data contract consumed by the deterministic planner."""

from __future__ import annotations

from typing import Protocol

from tripweaver.domain.models import (
    GeoPoint,
    LodgingArea,
    Place,
    RouteLeg,
    SourceMetadata,
    TransportOption,
    TripRequest,
)


class PlanningCatalog(Protocol):
    """A frozen data snapshot; implementations must not perform network I/O."""

    def transport_options(self, request: TripRequest) -> tuple[TransportOption, ...]: ...

    def places(self, destination: str) -> tuple[Place, ...]: ...

    def lodging_areas(self, destination: str) -> tuple[LodgingArea, ...]: ...

    def route(self, from_id: str, from_point: GeoPoint, to_place: Place) -> RouteLeg: ...

    def estimation_source(self) -> SourceMetadata: ...
