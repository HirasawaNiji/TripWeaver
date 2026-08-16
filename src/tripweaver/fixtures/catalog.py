"""Fixture-backed replacement for transport, map, and lodging MCP adapters."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from math import atan2, cos, radians, sin, sqrt

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


class UnsupportedFixtureRouteError(LookupError):
    """Raised when the phase-one fixture does not cover a requested city pair."""


class FixtureCatalog:
    """Deterministic data source that mimics normalized MCP adapter output."""

    SUPPORTED_ROUTE = ("北京", "上海")
    QUERY_TIME = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)

    def _source(self, reference: str, *, estimated: bool = False) -> SourceMetadata:
        return SourceMetadata(
            provider="tripweaver_fixture",
            status=DataStatus.ESTIMATED if estimated else DataStatus.FIXTURE,
            queried_at=self.QUERY_TIME,
            expires_at=None,
            source_reference=f"fixture://phase-1/{reference}",
            confidence=0.65 if estimated else 1.0,
        )

    def transport_options(self, request: TripRequest) -> tuple[TransportOption, ...]:
        if (request.origin, request.destination) != self.SUPPORTED_ROUTE:
            raise UnsupportedFixtureRouteError("阶段一 Fixture 仅支持从北京到上海的往返行程")

        start = request.start_date
        end = request.end_date
        outbound_specs = (
            ("TW-R-OUT-01", TransportMode.RAIL, "模拟高铁方案 A", 7, 0, 11, 35, "553"),
            ("TW-F-OUT-01", TransportMode.FLIGHT, "模拟航班方案 A", 8, 10, 10, 25, "780"),
        )
        return_specs = (
            ("TW-R-IN-01", TransportMode.RAIL, "模拟高铁方案 B", 17, 0, 21, 35, "553"),
            ("TW-F-IN-01", TransportMode.FLIGHT, "模拟航班方案 B", 18, 0, 20, 15, "820"),
        )
        options: list[TransportOption] = []
        for option_id, mode, label, dh, dm, ah, am, price in outbound_specs:
            options.append(
                TransportOption(
                    id=option_id,
                    leg=TransportLeg.OUTBOUND,
                    mode=mode,
                    label=label,
                    origin=request.origin,
                    destination=request.destination,
                    depart_at=datetime.combine(start, time(dh, dm)),
                    arrive_at=datetime.combine(start, time(ah, am)),
                    price_per_person_cny=Decimal(price),
                    source=self._source(f"transport/{option_id}"),
                )
            )
        for option_id, mode, label, dh, dm, ah, am, price in return_specs:
            arrival_date = end + timedelta(days=1) if ah < dh else end
            options.append(
                TransportOption(
                    id=option_id,
                    leg=TransportLeg.RETURN,
                    mode=mode,
                    label=label,
                    origin=request.destination,
                    destination=request.origin,
                    depart_at=datetime.combine(end, time(dh, dm)),
                    arrive_at=datetime.combine(arrival_date, time(ah, am)),
                    price_per_person_cny=Decimal(price),
                    source=self._source(f"transport/{option_id}"),
                )
            )
        return tuple(options)

    def places(self, destination: str) -> tuple[Place, ...]:
        if destination != "上海":
            raise UnsupportedFixtureRouteError("阶段一 Fixture 仅包含上海景点")
        specs = (
            (
                "shanghai_museum",
                "上海博物馆（模拟）",
                "博物馆",
                31.2303,
                121.4700,
                120,
                "0",
                time(9),
                time(17),
                (0,),
                ("历史文化",),
                95,
            ),
            (
                "yu_garden",
                "豫园（模拟）",
                "历史景点",
                31.2271,
                121.4921,
                120,
                "40",
                time(9),
                time(16, 30),
                (),
                ("历史文化", "美食街区"),
                92,
            ),
            (
                "the_bund",
                "外滩（模拟）",
                "城市景观",
                31.2400,
                121.4904,
                90,
                "0",
                time(6),
                time(23),
                (),
                ("城市景观",),
                90,
            ),
            (
                "shanghai_tower",
                "上海中心观景区（模拟）",
                "城市景观",
                31.2335,
                121.5055,
                120,
                "180",
                time(9),
                time(21),
                (),
                ("城市景观",),
                86,
            ),
            (
                "tianzifang",
                "田子坊（模拟）",
                "特色街区",
                31.2101,
                121.4687,
                90,
                "0",
                time(10),
                time(21),
                (),
                ("美食街区",),
                78,
            ),
            (
                "nanjing_road",
                "南京路步行街（模拟）",
                "商业街区",
                31.2346,
                121.4750,
                90,
                "0",
                time(10),
                time(22),
                (),
                ("美食街区", "城市景观"),
                75,
            ),
        )
        return tuple(
            Place(
                id=place_id,
                name=name,
                category=category,
                location=GeoPoint(latitude=lat, longitude=lon),
                suggested_duration_minutes=duration,
                admission_per_person_cny=Decimal(admission),
                opens_at=opens,
                closes_at=closes,
                closed_weekdays=closed,
                tags=tags,
                priority=priority,
                source=self._source(f"places/{place_id}"),
            )
            for place_id, name, category, lat, lon, duration, admission, opens, closes, closed, tags, priority in specs
        )

    def lodging_areas(self, destination: str) -> tuple[LodgingArea, ...]:
        if destination != "上海":
            raise UnsupportedFixtureRouteError("阶段一 Fixture 仅包含上海住宿区域")
        return (
            LodgingArea(
                id="peoples_square",
                name="人民广场区域（模拟）",
                location=GeoPoint(latitude=31.2328, longitude=121.4677),
                nightly_price_estimate_cny=Decimal(480),
                description="靠近核心景点与公共交通，适合作为阶段一演示住宿区域。",
                source=self._source("lodging/peoples-square", estimated=True),
            ),
            LodgingArea(
                id="jingan",
                name="静安寺区域（模拟）",
                location=GeoPoint(latitude=31.2235, longitude=121.4450),
                nightly_price_estimate_cny=Decimal(560),
                description="餐饮便利，但前往浦东景点的模拟通勤时间更长。",
                source=self._source("lodging/jingan", estimated=True),
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
    def _haversine_km(first: GeoPoint, second: GeoPoint) -> float:
        radius = 6371.0
        lat1, lat2 = radians(first.latitude), radians(second.latitude)
        delta_lat = radians(second.latitude - first.latitude)
        delta_lon = radians(second.longitude - first.longitude)
        value = sin(delta_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
        return radius * 2 * atan2(sqrt(value), sqrt(1 - value))
