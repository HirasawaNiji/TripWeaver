"""Shared deterministic buffers for intercity transport feasibility."""

from __future__ import annotations

from tripweaver.domain.models import TransportMode


def arrival_transfer_minutes(mode: TransportMode) -> int:
    """Minutes reserved after arrival before city activities can begin."""

    return 90 if mode == TransportMode.FLIGHT else 60


def departure_buffer_minutes(mode: TransportMode) -> int:
    """Minutes reserved before departure after city activities must end."""

    return 150 if mode == TransportMode.FLIGHT else 90


def generalized_overhead_minutes(mode: TransportMode) -> int:
    """Non-vehicle time used when comparing rail and flight candidates."""

    return 180 if mode == TransportMode.FLIGHT else 0
