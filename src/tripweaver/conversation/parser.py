"""Bounded deterministic revision parsing with a prompt-injection guard."""

from __future__ import annotations

import re
from decimal import Decimal

from tripweaver.conversation.models import RevisionIntent
from tripweaver.domain.models import PlanningObjective, TransportMode


class UnsafeRevisionError(ValueError):
    """Raised when a revision attempts to cross the planning control boundary."""


class InputSafetyGuard:
    MAX_INPUT_LENGTH = 2000
    UNSAFE = (
        "system prompt", "系统提示词", "忽略之前", "ignore previous", "读取.env",
        "读取环境变量", "api key", "绕过validator", "关闭validator", "调用任意工具",
    )

    def check(self, text: str) -> str:
        normalized = text.strip()
        if not normalized:
            raise ValueError("输入不能为空")
        if len(normalized) > self.MAX_INPUT_LENGTH:
            raise ValueError("输入不能超过 2000 个字符")
        lowered = normalized.lower().replace(" ", "")
        if any(token.replace(" ", "") in lowered for token in self.UNSAFE):
            raise UnsafeRevisionError("输入越过了旅行规划的安全边界")
        return normalized


class DeterministicRevisionParser:
    MAX_INPUT_LENGTH = 1000
    _UNSAFE = (
        "system prompt", "系统提示词", "忽略之前", "ignore previous", "读取.env",
        "读取环境变量", "api key", "绕过validator", "关闭validator", "调用任意工具",
    )

    def parse(self, text: str) -> RevisionIntent:
        normalized = InputSafetyGuard().check(text)
        if len(normalized) > self.MAX_INPUT_LENGTH:
            raise ValueError("修改要求不能超过 1000 个字符")

        selected = self._match_int(normalized, r"(?:选择|选|采用)第\s*([123一二三])\s*个")
        replace_day = self._match_int(normalized, r"第\s*([1-7一二三四五六七])\s*天.*(?:换|替换|不要)")
        price_match = re.search(r"(?:每晚|一晚|酒店).*?(\d{2,5})(?:元|块)?", normalized)
        if price_match is None:
            price_match = re.search(r"(\d{2,5})(?:元|块).*?(?:每晚|一晚)", normalized)
        outbound_modes = self._leg_modes(normalized, "去程")
        inbound_modes = self._leg_modes(normalized, "返程")
        objective = None
        if any(word in normalized for word in ("最省钱", "便宜优先", "预算优先")):
            objective = PlanningObjective.BUDGET
        elif any(word in normalized for word in ("最快", "时间优先", "少花时间")):
            objective = PlanningObjective.TIME
        elif any(word in normalized for word in ("均衡", "平衡方案")):
            objective = PlanningObjective.BALANCED

        intent = RevisionIntent(
            select_alternative=selected, objective=objective,
            outbound_modes=outbound_modes, inbound_modes=inbound_modes,
            max_nightly_price_cny=Decimal(price_match.group(1)) if price_match else None,
            replace_day=replace_day,
            preserve_outbound="保留去程" in normalized or "去程不变" in normalized,
            preserve_inbound="保留返程" in normalized or "返程不变" in normalized,
            preserve_lodging="保留酒店" in normalized or "酒店不变" in normalized,
            explanation=normalized,
        )
        if intent == RevisionIntent(explanation=normalized):
            raise ValueError("未识别到可执行的修改，请说明交通、酒店、方案目标或替换日期")
        return intent

    @staticmethod
    def _match_int(text: str, pattern: str) -> int | None:
        match = re.search(pattern, text)
        if match is None:
            return None
        token = match.group(1)
        return int(token) if token.isdigit() else {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7}[token]

    @staticmethod
    def _leg_modes(text: str, leg: str) -> tuple[TransportMode, ...] | None:
        fragment_match = re.search(rf"{leg}.{{0,12}}", text)
        if fragment_match is None:
            return None
        fragment = fragment_match.group(0)
        if "高铁" in fragment or "动车" in fragment or "火车" in fragment:
            return (TransportMode.RAIL,)
        if "飞机" in fragment or "航班" in fragment:
            if "不要" in fragment or "不坐" in fragment:
                return (TransportMode.RAIL,)
            return (TransportMode.FLIGHT,)
        return None
