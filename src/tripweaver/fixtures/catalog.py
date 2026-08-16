"""Deterministic multi-city replacement for transport, map, and lodging adapters."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from math import atan2, cos, radians, sin, sqrt

from tripweaver.domain.cities import CityInfo, city_info, supported_city_names
from tripweaver.domain.models import (
    DataStatus,
    GeoPoint,
    LodgingArea,
    Place,
    RouteLeg,
    SourceMetadata,
    TransportLeg,
    TransportMode,
    TransportOption,
    TripRequest,
)
from tripweaver.fixtures.city_profiles import CITY_ATTRACTIONS


class UnsupportedFixtureRouteError(LookupError):
    """Raised when the deterministic demo catalog does not cover a city."""


class FixtureCatalog:
    """Stable 10-city data source that mimics normalized MCP adapter output."""

    SUPPORTED_CITIES = supported_city_names()
    QUERY_TIME = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)

    def _source(self, reference: str, *, estimated: bool = False) -> SourceMetadata:
        return SourceMetadata(
            provider="tripweaver_fixture_v2",
            status=DataStatus.ESTIMATED if estimated else DataStatus.FIXTURE,
            queried_at=self.QUERY_TIME,
            expires_at=None,
            source_reference=f"fixture://v2/{reference}",
            confidence=0.65 if estimated else 1.0,
        )

    def transport_options(self, request: TripRequest) -> tuple[TransportOption, ...]:
        origin = self._require_city(request.origin)
        destination = self._require_city(request.destination)
        if origin.name == destination.name:
            raise UnsupportedFixtureRouteError("出发城市和目的城市不能相同")
        distance = self._haversine_km(
            GeoPoint(latitude=origin.latitude, longitude=origin.longitude),
            GeoPoint(latitude=destination.latitude, longitude=destination.longitude),
        )
        rail_minutes = max(150, min(600, round(distance / 220 * 60 + 60)))
        flight_minutes = max(100, min(240, round(distance / 750 * 60 + 80)))
        rail_price = max(Decimal(120), Decimal(str(distance * 0.45))).quantize(Decimal(1))
        flight_price = max(Decimal(450), Decimal(str(distance * 0.7 + 300))).quantize(Decimal(1))
        specs = (
            (
                "rail",
                TransportMode.RAIL,
                "模拟高铁候选",
                time(7),
                rail_minutes,
                rail_price,
            ),
            (
                "flight",
                TransportMode.FLIGHT,
                "模拟航班候选",
                time(8, 10),
                flight_minutes,
                flight_price,
            ),
        )
        options: list[TransportOption] = []
        for code, mode, label, depart_time, duration, price in specs:
            outbound_depart = datetime.combine(request.start_date, depart_time)
            return_depart_time = time(16) if mode == TransportMode.RAIL else time(17)
            return_depart = datetime.combine(request.end_date, return_depart_time)
            options.extend(
                (
                    TransportOption(
                        id=self._transport_id(
                            code, TransportLeg.OUTBOUND, origin.name, destination.name
                        ),
                        leg=TransportLeg.OUTBOUND,
                        mode=mode,
                        label=f"{origin.name}→{destination.name} {label}",
                        origin=origin.name,
                        destination=destination.name,
                        depart_at=outbound_depart,
                        arrive_at=outbound_depart + timedelta(minutes=duration),
                        price_per_person_cny=price,
                        source=self._source(
                            f"transport/{origin.name}/{destination.name}/{code}/outbound"
                        ),
                    ),
                    TransportOption(
                        id=self._transport_id(
                            code, TransportLeg.RETURN, destination.name, origin.name
                        ),
                        leg=TransportLeg.RETURN,
                        mode=mode,
                        label=f"{destination.name}→{origin.name} {label}",
                        origin=destination.name,
                        destination=origin.name,
                        depart_at=return_depart,
                        arrive_at=return_depart + timedelta(minutes=duration),
                        price_per_person_cny=price,
                        source=self._source(
                            f"transport/{destination.name}/{origin.name}/{code}/return"
                        ),
                    ),
                )
            )
        return tuple(options)

    def places(self, destination: str) -> tuple[Place, ...]:
        city = self._require_city(destination)
        seeds = CITY_ATTRACTIONS[city.name]
        return tuple(
            Place(
                id=f"{city.iata_code.lower()}-place-{index:02d}",
                name=f"{seed.name}（模拟）",
                category=seed.category,
                location=GeoPoint(latitude=seed.latitude, longitude=seed.longitude),
                suggested_duration_minutes=seed.duration_minutes,
                admission_per_person_cny=seed.admission_cny,
                opens_at=seed.opens_at,
                closes_at=seed.closes_at,
                closed_weekdays=seed.closed_weekdays,
                tags=seed.tags,
                priority=seed.priority,
                planning_assumptions=("多城市 Fixture 基线；不代表实时开放或票价。",),
                source=self._source(f"places/{city.name}/{index:02d}"),
            )
            for index, seed in enumerate(seeds, start=1)
        )

    def lodging_areas(self, destination: str) -> tuple[LodgingArea, ...]:
        city = self._require_city(destination)
        return (
            LodgingArea(
                id=f"{city.iata_code.lower()}-central",
                name=f"{city.name}中心城区（模拟）",
                location=GeoPoint(latitude=city.latitude, longitude=city.longitude),
                nightly_price_estimate_cny=Decimal(480),
                description="靠近核心景点的多城市 Fixture 住宿基线。",
                price_basis="ESTIMATED_POLICY",
                source=self._source(f"lodging/{city.name}/central", estimated=True),
            ),
            LodgingArea(
                id=f"{city.iata_code.lower()}-transit",
                name=f"{city.name}交通便利区域（模拟）",
                location=GeoPoint(
                    latitude=city.latitude + 0.015,
                    longitude=city.longitude - 0.015,
                ),
                nightly_price_estimate_cny=Decimal(420),
                description="价格较低、通勤稍长的多城市 Fixture 住宿基线。",
                price_basis="ESTIMATED_POLICY",
                source=self._source(f"lodging/{city.name}/transit", estimated=True),
            ),
        )

    def route(self, from_id: str, from_point: GeoPoint, to_place: Place) -> RouteLeg:
        distance = self._haversine_km(from_point, to_place.location)
        minutes = max(12, int((distance / 18 * 60) + 10))
        cost = (Decimal(minutes) * Decimal("0.18")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        return RouteLeg(
            from_id=from_id,
            to_id=to_place.id,
            mode="PUBLIC_TRANSIT_FIXTURE",
            minutes=minutes,
            cost_cny=cost,
            distance_km=Decimal(str(round(distance, 2))),
            source=self._source(f"routes/{from_id}/{to_place.id}"),
        )

    def estimation_source(self) -> SourceMetadata:
        return self._source("budget/meals", estimated=True)

    @staticmethod
    def _require_city(value: str) -> CityInfo:
        info = city_info(value)
        if info is None:
            supported = "、".join(supported_city_names())
            raise UnsupportedFixtureRouteError(f"Fixture 当前支持城市：{supported}")
        return info

    @staticmethod
    def _transport_id(code: str, leg: TransportLeg, origin: str, destination: str) -> str:
        if {origin, destination} == {"北京", "上海"}:
            legacy_code = "R" if code == "rail" else "F"
            legacy_leg = "OUT" if leg == TransportLeg.OUTBOUND else "IN"
            return f"TW-{legacy_code}-{legacy_leg}-01"
        leg_name = "out" if leg == TransportLeg.OUTBOUND else "return"
        return f"fixture-{code}-{leg_name}-{origin}-{destination}"

    @staticmethod
    def _haversine_km(first: GeoPoint, second: GeoPoint) -> float:
        radius = 6371.0
        lat1, lat2 = radians(first.latitude), radians(second.latitude)
        delta_lat = radians(second.latitude - first.latitude)
        delta_lon = radians(second.longitude - first.longitude)
        value = sin(delta_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
        return radius * 2 * atan2(sqrt(value), sqrt(1 - value))
