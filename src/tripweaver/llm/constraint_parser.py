"""Deterministic Chinese request parser used by the fixture vertical slice."""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from typing import ClassVar

from tripweaver.domain.models import TransportMode, TripRequest


class RequestParseError(ValueError):
    """Raised when phase-one parsing cannot safely infer required constraints."""


class DeterministicConstraintParser:
    """Parse a deliberately small, documented subset of Chinese travel requests."""

    _route_patterns = (
        re.compile(
            r"从(?P<origin>[\u4e00-\u9fff]{2,12})去(?P<destination>[\u4e00-\u9fff]{2,12}?)(?:玩|旅游|旅行|游玩)"
        ),
        re.compile(
            r"(?P<origin>[\u4e00-\u9fff]{2,12})到(?P<destination>[\u4e00-\u9fff]{2,12}?)(?:玩|旅游|旅行|游玩)"
        ),
    )
    _date_pattern = re.compile(
        r"(?P<year>20\d{2})[-年/](?P<month>\d{1,2})[-月/](?P<day>\d{1,2})日?"
    )
    _days_pattern = re.compile(r"(?P<days>[1-7])\s*天")
    _travelers_pattern = re.compile(r"(?P<travelers>[1-8])\s*(?:个人|人)")
    _budget_pattern = re.compile(
        r"预算(?:为|是|大约|约)?\s*(?P<budget>\d+(?:\.\d+)?)\s*(?P<unit>万|千)?元?"
    )

    _interest_keywords: ClassVar[dict[str, tuple[str, ...]]] = {
        "历史文化": ("历史", "文化", "博物馆", "古迹"),
        "城市景观": ("夜景", "地标", "高楼", "城市景观"),
        "美食街区": ("美食", "小吃", "街区", "逛街"),
    }

    def parse(self, text: str) -> TripRequest:
        normalized = " ".join(text.strip().split())
        if not normalized:
            raise RequestParseError("request must not be empty")

        origin, destination = self._parse_route(normalized)
        start_date, date_assumption = self._parse_start_date(normalized)
        days = self._parse_int(self._days_pattern, normalized, "days", default=3)
        travelers = self._parse_int(self._travelers_pattern, normalized, "travelers", default=2)
        budget = self._parse_budget(normalized, default=Decimal(5000))
        interests = tuple(
            label
            for label, keywords in self._interest_keywords.items()
            if any(keyword in normalized for keyword in keywords)
        )
        assumptions = [date_assumption] if date_assumption else []
        if not self._days_pattern.search(normalized):
            assumptions.append("未识别到旅行天数，演示模式默认按 3 天规划")
        if not self._travelers_pattern.search(normalized):
            assumptions.append("未识别到出行人数，演示模式默认按 2 人规划")
        if not self._budget_pattern.search(normalized):
            assumptions.append("未识别到预算，演示模式默认预算为 5000 元")
        if not interests:
            interests = ("历史文化", "城市景观", "美食街区")
            assumptions.append("未识别到兴趣偏好，使用演示模式默认综合偏好")

        preferred = self._parse_transport_preferences(normalized)
        return TripRequest(
            origin=origin,
            destination=destination,
            start_date=start_date,
            end_date=start_date + timedelta(days=days - 1),
            travelers=travelers,
            budget_cny=budget,
            interests=interests,
            preferred_transport=preferred,
            assumptions=tuple(assumptions),
        )

    def _parse_route(self, text: str) -> tuple[str, str]:
        for pattern in self._route_patterns:
            if match := pattern.search(text):
                return match.group("origin"), match.group("destination")
        raise RequestParseError("无法识别出发地和目的地，请使用“从北京去上海玩3天”格式")

    def _parse_start_date(self, text: str) -> tuple[date, str | None]:
        if match := self._date_pattern.search(text):
            return (
                date(
                    int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")),
                ),
                None,
            )
        return date(2026, 10, 1), "未识别到出发日期，Fixture 演示默认使用 2026-10-01"

    @staticmethod
    def _parse_int(pattern: re.Pattern[str], text: str, group: str, *, default: int) -> int:
        match = pattern.search(text)
        return int(match.group(group)) if match else default

    def _parse_budget(self, text: str, *, default: Decimal) -> Decimal:
        match = self._budget_pattern.search(text)
        if not match:
            return default
        multiplier = {None: Decimal(1), "千": Decimal(1000), "万": Decimal(10000)}
        return Decimal(match.group("budget")) * multiplier[match.group("unit")]

    @staticmethod
    def _parse_transport_preferences(text: str) -> tuple[TransportMode, ...]:
        wants_rail = any(keyword in text for keyword in ("高铁", "动车", "火车", "铁路"))
        wants_flight = any(keyword in text for keyword in ("飞机", "航班", "飞行"))
        if wants_rail and not wants_flight:
            return (TransportMode.RAIL,)
        if wants_flight and not wants_rail:
            return (TransportMode.FLIGHT,)
        return (TransportMode.RAIL, TransportMode.FLIGHT)
