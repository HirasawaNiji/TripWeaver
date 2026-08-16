"""Deterministic phase-one planner.

The planner performs hard filtering and a stable greedy schedule. It deliberately
contains no LLM calls, hidden web access, or unseeded randomness.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from hashlib import sha256
from math import ceil

from tripweaver.domain.models import (
    BudgetBreakdown,
    DayPlan,
    Itinerary,
    LodgingArea,
    Place,
    RouteLeg,
    ScheduledVisit,
    TransportLeg,
    TransportOption,
    TripRequest,
)
from tripweaver.domain.transport_policy import (
    arrival_transfer_minutes,
    departure_buffer_minutes,
    generalized_overhead_minutes,
)
from tripweaver.planner.catalog import PlanningCatalog


class NoFeasiblePlanError(RuntimeError):
    """Raised when hard constraints leave no executable itinerary."""


class DeterministicPlanner:
    """Create a stable itinerary from normalized fixture data."""

    def __init__(self, catalog: PlanningCatalog) -> None:
        self._catalog = catalog

    def plan(self, request: TripRequest) -> tuple[Itinerary, tuple[Place, ...]]:
        places = self._catalog.places(request.destination)
        options = self._catalog.transport_options(request)
        lodging_areas = self._catalog.lodging_areas(request.destination)
        outbound = self._choose_transport(options, request, TransportLeg.OUTBOUND)
        inbound = self._choose_transport(options, request, TransportLeg.RETURN)
        lodging = self._choose_lodging(lodging_areas, places)
        days = self._schedule_days(request, outbound, inbound, lodging, places)
        budget = self._calculate_budget(request, outbound, inbound, lodging, days)
        if budget.total_cny > request.budget_cny:
            raise NoFeasiblePlanError(
                f"最低当前方案预计 {budget.total_cny} 元，超过预算 {request.budget_cny} 元"
            )
        plan_id = self._stable_plan_id(request, outbound, inbound, lodging)
        itinerary = Itinerary(
            id=plan_id,
            title=f"{request.origin}到{request.destination}{request.trip_days}日可验证行程",
            outbound=outbound,
            inbound=inbound,
            lodging_area=lodging,
            days=days,
            budget=budget,
        )
        return itinerary, places

    @staticmethod
    def _choose_transport(
        options: tuple[TransportOption, ...], request: TripRequest, leg: TransportLeg
    ) -> TransportOption:
        allowed = [
            option
            for option in options
            if option.leg == leg
            and option.mode in request.preferred_transport
            and DeterministicPlanner.transport_window_feasible(option, request, leg)
        ]
        if not allowed:
            raise NoFeasiblePlanError(f"没有同时满足交通偏好和首末日活动窗口的 {leg.value} 方案")
        return min(
            allowed,
            key=lambda item: (
                item.price_per_person_cny * request.travelers
                + Decimal(item.duration_minutes + generalized_overhead_minutes(item.mode))
                * Decimal("0.35"),
                item.id,
            ),
        )

    @staticmethod
    def transport_window_feasible(
        option: TransportOption,
        request: TripRequest,
        leg: TransportLeg,
    ) -> bool:
        """Reserve at least two usable hours on the arrival/departure day."""

        minimum_activity_window = timedelta(hours=2)
        if leg == TransportLeg.OUTBOUND:
            if option.depart_at.date() != request.start_date:
                return False
            activity_start = max(
                datetime.combine(request.start_date, time(9)),
                option.arrive_at + timedelta(minutes=arrival_transfer_minutes(option.mode)),
            )
            activity_end = datetime.combine(request.start_date, time(20))
            return activity_end - activity_start >= minimum_activity_window

        if option.depart_at.date() != request.end_date:
            return False
        activity_start = datetime.combine(request.end_date, time(9))
        activity_end = min(
            datetime.combine(request.end_date, time(20)),
            option.depart_at - timedelta(minutes=departure_buffer_minutes(option.mode)),
        )
        return activity_end - activity_start >= minimum_activity_window

    def _choose_lodging(
        self, areas: tuple[LodgingArea, ...], places: tuple[Place, ...]
    ) -> LodgingArea:
        if not areas:
            raise NoFeasiblePlanError("没有可用住宿区域")

        def score(area: LodgingArea) -> Decimal:
            route_minutes = sum(
                self._catalog.route(area.id, area.location, place).minutes for place in places[:4]
            )
            return area.nightly_price_estimate_cny + Decimal(route_minutes * 2)

        return min(areas, key=lambda area: (score(area), area.id))

    def _schedule_days(
        self,
        request: TripRequest,
        outbound: TransportOption,
        inbound: TransportOption,
        lodging: LodgingArea,
        places: tuple[Place, ...],
    ) -> tuple[DayPlan, ...]:
        remaining = list(places)
        day_plans: list[DayPlan] = []
        for offset in range(request.trip_days):
            current_date = request.start_date + timedelta(days=offset)
            remaining_days = request.trip_days - offset
            daily_visit_cap = max(1, ceil(len(remaining) / remaining_days))
            day_start, day_end = self._day_window(request, current_date, outbound, inbound)
            visits: list[ScheduledVisit] = []
            current_time = day_start
            previous_id = lodging.id
            previous_point = lodging.location
            local_cost = Decimal(0)

            while remaining and len(visits) < daily_visit_cap:
                candidates: list[tuple[int, datetime, str, Place, RouteLeg]] = []
                for place in remaining:
                    if current_date.weekday() in place.closed_weekdays:
                        continue
                    route = self._catalog.route(previous_id, previous_point, place)
                    arrival = current_time + timedelta(minutes=route.minutes)
                    opens = datetime.combine(current_date, place.opens_at)
                    closes = datetime.combine(current_date, place.closes_at)
                    visit_start = max(arrival, opens)
                    visit_end = visit_start + timedelta(minutes=place.suggested_duration_minutes)
                    if visit_end > min(day_end, closes):
                        continue
                    interest_bonus = 20 * len(set(place.tags).intersection(request.interests))
                    candidates.append(
                        (
                            -(place.priority + interest_bonus),
                            visit_end,
                            place.id,
                            place,
                            route,
                        )
                    )
                if not candidates:
                    break
                _, visit_end, _, selected, route_object = min(candidates)
                route = route_object
                visit_start = visit_end - timedelta(minutes=selected.suggested_duration_minutes)
                visits.append(
                    ScheduledVisit(
                        place_id=selected.id,
                        place_name=selected.name,
                        start_at=visit_start,
                        end_at=visit_end,
                        admission_total_cny=(selected.admission_per_person_cny * request.travelers),
                        route_from_previous=route,
                        source=selected.source,
                    )
                )
                local_cost += route.cost_cny
                current_time = visit_end
                previous_id = selected.id
                previous_point = selected.location
                remaining.remove(selected)

            day_plans.append(
                DayPlan(
                    date=current_date,
                    visits=tuple(visits),
                    local_transport_cost_cny=local_cost,
                )
            )
        if remaining:
            unscheduled = "、".join(place.name for place in remaining)
            raise NoFeasiblePlanError(f"当前时间窗口无法安排全部候选景点：{unscheduled}")
        return tuple(day_plans)

    @staticmethod
    def _day_window(
        request: TripRequest,
        current_date: date,
        outbound: TransportOption,
        inbound: TransportOption,
    ) -> tuple[datetime, datetime]:
        start = datetime.combine(current_date, time(9))
        end = datetime.combine(current_date, time(20))
        if current_date == request.start_date:
            start = max(
                start,
                outbound.arrive_at + timedelta(minutes=arrival_transfer_minutes(outbound.mode)),
            )
        if current_date == request.end_date:
            end = min(
                end,
                inbound.depart_at - timedelta(minutes=departure_buffer_minutes(inbound.mode)),
            )
        end = min(end, start + timedelta(minutes=request.max_daily_minutes))
        if end <= start:
            raise NoFeasiblePlanError(f"{current_date.isoformat()} 没有可用游玩时间")
        return start, end

    def _calculate_budget(
        self,
        request: TripRequest,
        outbound: TransportOption,
        inbound: TransportOption,
        lodging: LodgingArea,
        days: tuple[DayPlan, ...],
    ) -> BudgetBreakdown:
        rooms = ceil(request.travelers / 2)
        nights = max(request.trip_days - 1, 0)
        return BudgetBreakdown(
            transport_cny=(outbound.price_per_person_cny + inbound.price_per_person_cny)
            * request.travelers,
            lodging_cny=lodging.nightly_price_estimate_cny * rooms * nights,
            admission_cny=sum(
                (visit.admission_total_cny for day in days for visit in day.visits),
                start=Decimal(0),
            ),
            local_transport_cny=sum(
                (day.local_transport_cost_cny for day in days), start=Decimal(0)
            ),
            meals_estimated_cny=Decimal(120) * request.travelers * request.trip_days,
            estimation_source=self._catalog.estimation_source(),
        )

    @staticmethod
    def _stable_plan_id(
        request: TripRequest,
        outbound: TransportOption,
        inbound: TransportOption,
        lodging: LodgingArea,
    ) -> str:
        raw = "|".join(
            (
                request.origin,
                request.destination,
                request.start_date.isoformat(),
                request.end_date.isoformat(),
                str(request.travelers),
                str(request.budget_cny),
                outbound.id,
                inbound.id,
                lodging.id,
            )
        )
        return f"tw-{sha256(raw.encode('utf-8')).hexdigest()[:12]}"
