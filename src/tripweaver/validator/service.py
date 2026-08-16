"""Independent feasibility checks for structured plans."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal
from math import ceil

from tripweaver.domain.models import (
    DataStatus,
    Itinerary,
    Place,
    Severity,
    SourceMetadata,
    TransportLeg,
    TripRequest,
    ValidationIssue,
    ValidationReport,
)
from tripweaver.domain.transport_policy import (
    arrival_transfer_minutes,
    departure_buffer_minutes,
)


class ItineraryValidator:
    """Recompute critical invariants without trusting planner decisions."""

    def validate(
        self,
        request: TripRequest,
        itinerary: Itinerary,
        places: tuple[Place, ...],
    ) -> ValidationReport:
        issues: list[ValidationIssue] = []
        place_index = {place.id: place for place in places}
        self._validate_transport(request, itinerary, issues)
        self._validate_schedule(request, itinerary, place_index, issues)
        self._validate_budget(request, itinerary, issues)
        self._validate_sources(itinerary, issues)
        return ValidationReport(issues=tuple(issues))

    @staticmethod
    def _validate_transport(
        request: TripRequest,
        itinerary: Itinerary,
        issues: list[ValidationIssue],
    ) -> None:
        outbound = itinerary.outbound
        inbound = itinerary.inbound
        if (
            outbound.leg != TransportLeg.OUTBOUND
            or outbound.origin != request.origin
            or outbound.destination != request.destination
            or outbound.depart_at.date() != request.start_date
        ):
            issues.append(
                ValidationIssue(
                    code="TRANSPORT_OUTBOUND_MISMATCH",
                    severity=Severity.ERROR,
                    message="去程交通与请求不匹配",
                    path="itinerary.outbound",
                )
            )
        if (
            inbound.leg != TransportLeg.RETURN
            or inbound.origin != request.destination
            or inbound.destination != request.origin
            or inbound.depart_at.date() != request.end_date
        ):
            issues.append(
                ValidationIssue(
                    code="TRANSPORT_RETURN_MISMATCH",
                    severity=Severity.ERROR,
                    message="返程交通与请求不匹配",
                    path="itinerary.inbound",
                )
            )

    @staticmethod
    def _validate_schedule(
        request: TripRequest,
        itinerary: Itinerary,
        place_index: dict[str, Place],
        issues: list[ValidationIssue],
    ) -> None:
        seen_places: set[str] = set()
        expected_dates = {
            request.start_date + timedelta(days=offset) for offset in range(request.trip_days)
        }
        actual_dates = {day.date for day in itinerary.days}
        if actual_dates != expected_dates:
            issues.append(
                ValidationIssue(
                    code="SCHEDULE_DATE_COVERAGE",
                    severity=Severity.ERROR,
                    message="日程日期未完整覆盖旅行日期",
                    path="itinerary.days",
                )
            )
        for day_index, day in enumerate(itinerary.days):
            window_start = datetime.combine(day.date, time(9))
            window_end = datetime.combine(day.date, time(20))
            if day.date == request.start_date:
                window_start = max(
                    window_start,
                    itinerary.outbound.arrive_at
                    + timedelta(minutes=arrival_transfer_minutes(itinerary.outbound.mode)),
                )
            if day.date == request.end_date:
                window_end = min(
                    window_end,
                    itinerary.inbound.depart_at
                    - timedelta(minutes=departure_buffer_minutes(itinerary.inbound.mode)),
                )
            window_end = min(
                window_end, window_start + timedelta(minutes=request.max_daily_minutes)
            )
            previous_end = window_start
            previous_id = itinerary.lodging_area.id
            recomputed_day_transport = Decimal(0)
            for visit_index, visit in enumerate(day.visits):
                path = f"itinerary.days[{day_index}].visits[{visit_index}]"
                place = place_index.get(visit.place_id)
                if place is None:
                    issues.append(
                        ValidationIssue(
                            code="UNKNOWN_PLACE",
                            severity=Severity.ERROR,
                            message=f"未知景点 {visit.place_id}",
                            path=path,
                        )
                    )
                    continue
                if visit.place_id in seen_places:
                    issues.append(
                        ValidationIssue(
                            code="DUPLICATE_PLACE",
                            severity=Severity.ERROR,
                            message=f"景点 {place.name} 被重复安排",
                            path=path,
                        )
                    )
                seen_places.add(visit.place_id)
                if (
                    visit.route_from_previous.from_id != previous_id
                    or visit.route_from_previous.to_id != visit.place_id
                ):
                    issues.append(
                        ValidationIssue(
                            code="ROUTE_ENDPOINT_MISMATCH",
                            severity=Severity.ERROR,
                            message=f"前往 {place.name} 的路线端点与日程顺序不一致",
                            path=f"{path}.route_from_previous",
                        )
                    )
                if visit.start_at.date() != day.date or visit.end_at.date() != day.date:
                    issues.append(
                        ValidationIssue(
                            code="VISIT_WRONG_DATE",
                            severity=Severity.ERROR,
                            message=f"{place.name} 不在所属日程日期内",
                            path=path,
                        )
                    )
                if (
                    visit.start_at.time() < place.opens_at
                    or visit.end_at.time() > place.closes_at
                    or day.date.weekday() in place.closed_weekdays
                ):
                    issues.append(
                        ValidationIssue(
                            code="PLACE_CLOSED",
                            severity=Severity.ERROR,
                            message=f"{place.name} 的访问时间不在开放窗口内",
                            path=path,
                        )
                    )
                minimum_start = previous_end + visit.route_from_previous.minutes * _ONE_MINUTE
                if visit.start_at < minimum_start:
                    issues.append(
                        ValidationIssue(
                            code="ROUTE_BUFFER_MISSING",
                            severity=Severity.ERROR,
                            message=f"前往 {place.name} 的交通时间不足",
                            path=path,
                        )
                    )
                if visit.end_at > window_end:
                    issues.append(
                        ValidationIssue(
                            code="DAY_WINDOW_EXCEEDED",
                            severity=Severity.ERROR,
                            message=f"{place.name} 超出当日可用时间或返程缓冲窗口",
                            path=path,
                        )
                    )
                expected_duration = timedelta(minutes=place.suggested_duration_minutes)
                if visit.end_at - visit.start_at != expected_duration:
                    issues.append(
                        ValidationIssue(
                            code="VISIT_DURATION_MISMATCH",
                            severity=Severity.ERROR,
                            message=f"{place.name} 的游玩时长与规范化数据不一致",
                            path=path,
                        )
                    )
                expected_admission = place.admission_per_person_cny * request.travelers
                if visit.admission_total_cny != expected_admission:
                    issues.append(
                        ValidationIssue(
                            code="ADMISSION_COST_MISMATCH",
                            severity=Severity.ERROR,
                            message=f"{place.name} 的门票金额计算错误",
                            path=f"{path}.admission_total_cny",
                        )
                    )
                previous_end = visit.end_at
                previous_id = visit.place_id
                recomputed_day_transport += visit.route_from_previous.cost_cny
            if recomputed_day_transport != day.local_transport_cost_cny:
                issues.append(
                    ValidationIssue(
                        code="DAY_TRANSPORT_COST_MISMATCH",
                        severity=Severity.ERROR,
                        message=f"{day.date.isoformat()} 的市内交通费与路线明细不一致",
                        path=f"itinerary.days[{day_index}].local_transport_cost_cny",
                    )
                )
            if day.visits:
                active_minutes = int(
                    (day.visits[-1].end_at - day.visits[0].start_at).total_seconds() // 60
                )
                if active_minutes > request.max_daily_minutes:
                    issues.append(
                        ValidationIssue(
                            code="DAILY_TIME_LIMIT",
                            severity=Severity.ERROR,
                            message=f"{day.date.isoformat()} 超过每日游玩时长限制",
                            path=f"itinerary.days[{day_index}]",
                        )
                    )

    @staticmethod
    def _validate_budget(
        request: TripRequest,
        itinerary: Itinerary,
        issues: list[ValidationIssue],
    ) -> None:
        budget = itinerary.budget
        expected_transport = (
            itinerary.outbound.price_per_person_cny + itinerary.inbound.price_per_person_cny
        ) * request.travelers
        expected_lodging = (
            itinerary.lodging_area.nightly_price_estimate_cny
            * ceil(request.travelers / 2)
            * max(request.trip_days - 1, 0)
        )
        recomputed_admission = sum(
            (visit.admission_total_cny for day in itinerary.days for visit in day.visits),
            start=Decimal(0),
        )
        recomputed_local = sum(
            (day.local_transport_cost_cny for day in itinerary.days), start=Decimal(0)
        )
        expected_meals = Decimal(120) * request.travelers * request.trip_days
        if (
            expected_transport != budget.transport_cny
            or expected_lodging != budget.lodging_cny
            or recomputed_admission != budget.admission_cny
            or recomputed_local != budget.local_transport_cny
            or expected_meals != budget.meals_estimated_cny
        ):
            issues.append(
                ValidationIssue(
                    code="BUDGET_RECOMPUTE_MISMATCH",
                    severity=Severity.ERROR,
                    message="预算分项与日程明细重新计算结果不一致",
                    path="itinerary.budget",
                )
            )
        if budget.total_cny > request.budget_cny:
            issues.append(
                ValidationIssue(
                    code="BUDGET_EXCEEDED",
                    severity=Severity.ERROR,
                    message=f"预计总费用 {budget.total_cny} 元超过预算 {request.budget_cny} 元",
                    path="itinerary.budget.total_cny",
                )
            )

    @staticmethod
    def _validate_sources(itinerary: Itinerary, issues: list[ValidationIssue]) -> None:
        sources: list[tuple[str, SourceMetadata]] = [
            ("itinerary.outbound", itinerary.outbound.source),
            ("itinerary.inbound", itinerary.inbound.source),
            ("itinerary.lodging_area", itinerary.lodging_area.source),
            ("itinerary.budget.meals", itinerary.budget.estimation_source),
        ]
        for day_index, day in enumerate(itinerary.days):
            for visit_index, visit in enumerate(day.visits):
                base = f"itinerary.days[{day_index}].visits[{visit_index}]"
                sources.append((base, visit.source))
                sources.append((f"{base}.route_from_previous", visit.route_from_previous.source))
        for path, source in sources:
            if source.status == DataStatus.UNAVAILABLE:
                issues.append(
                    ValidationIssue(
                        code="SOURCE_UNAVAILABLE",
                        severity=Severity.ERROR,
                        message=f"{path} 的数据来源不可用",
                        path=path,
                    )
                )
            elif source.status == DataStatus.ESTIMATED:
                issues.append(
                    ValidationIssue(
                        code="ESTIMATED_DATA",
                        severity=Severity.WARNING,
                        message=f"{path} 使用明确标注的估算数据",
                        path=path,
                    )
                )


_ONE_MINUTE = timedelta(minutes=1)
