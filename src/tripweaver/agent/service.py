"""A small state machine that keeps language handling outside critical arithmetic."""

from __future__ import annotations

import re
from typing import Protocol

from tripweaver.agent.models import AgentRun, AgentRunStatus, AgentStep, GroundedExplanation
from tripweaver.application.hybrid_service import HybridPlanResult
from tripweaver.domain.models import TripRequest
from tripweaver.llm.constraint_parser import DeterministicConstraintParser, RequestParseError


class AsyncPlanner(Protocol):
    async def plan(self, request: TripRequest) -> HybridPlanResult: ...


class RequirementGuard:
    """Detect missing hard constraints before defaults can silently become facts."""

    _checks = (
        (
            re.compile(
                r"(?:从.+?(?:去|到).+?(?:玩|旅游|旅行|游玩)|"
                r".+?到.+?(?:玩|旅游|旅行|游玩))"
            ),
            "请明确出发地和目的地，例如“从北京去上海玩”。",
        ),
        (
            re.compile(r"20\d{2}[-年/]\d{1,2}[-月/]\d{1,2}日?"),
            "请提供出发日期（YYYY-MM-DD）。",
        ),
        (re.compile(r"[1-7]\s*天"), "请提供旅行天数（当前支持 1–7 天）。"),
        (re.compile(r"[1-8]\s*(?:个人|人)"), "请提供出行人数（当前支持 1–8 人）。"),
        (
            re.compile(r"预算(?:为|是|大约|约)?\s*\d+(?:\.\d+)?\s*(?:万|千)?元?"),
            "请提供总预算，例如“预算 8000 元”。",
        ),
    )

    def questions(self, text: str) -> tuple[str, ...]:
        normalized = " ".join(text.strip().split())
        if not normalized:
            return ("请描述出发地、目的地、日期、天数、人数和预算。",)
        return tuple(
            question for pattern, question in self._checks if not pattern.search(normalized)
        )


class ControlledTravelAgent:
    """Parse, plan, validate, and explain without granting a model arithmetic authority."""

    def __init__(
        self,
        planner: AsyncPlanner,
        *,
        parser: DeterministicConstraintParser | None = None,
        guard: RequirementGuard | None = None,
    ) -> None:
        self._planner = planner
        self._parser = parser or DeterministicConstraintParser()
        self._guard = guard or RequirementGuard()

    async def run(self, text: str) -> AgentRun:
        questions = self._guard.questions(text)
        if questions:
            return AgentRun(
                status=AgentRunStatus.NEEDS_INPUT,
                questions=questions,
                steps=(
                    AgentStep(
                        name="REQUIREMENTS",
                        outcome="NEEDS_INPUT",
                        detail="缺少硬约束，未调用任何外部工具。",
                    ),
                ),
            )
        try:
            request = self._parser.parse(text)
        except RequestParseError:
            return AgentRun(
                status=AgentRunStatus.REJECTED,
                questions=("无法安全解析路线，请使用“从北京去上海玩3天”格式。",),
                steps=(
                    AgentStep(
                        name="PARSE",
                        outcome="REJECTED",
                        detail="结构化解析失败，未调用规划工具。",
                    ),
                ),
            )
        result = await self._planner.plan(request)
        validation_outcome = "PASSED" if result.plan.validation.feasible else "FAILED"
        steps = (
            AgentStep(name="PARSE", outcome="PASSED", detail="硬约束已结构化。"),
            AgentStep(
                name="FETCH",
                outcome="COMPLETED",
                detail="数据源按配置并发查询，并保留逐源降级状态。",
            ),
            AgentStep(name="PLAN", outcome="COMPLETED", detail="确定性规划器已生成候选方案。"),
            AgentStep(
                name="VALIDATE",
                outcome=validation_outcome,
                detail="独立 Validator 已重新计算时间、路线与预算。",
            ),
        )
        if not result.plan.validation.feasible:
            return AgentRun(status=AgentRunStatus.REJECTED, steps=steps, result=result)
        return AgentRun(
            status=AgentRunStatus.COMPLETED,
            steps=steps
            + (
                AgentStep(
                    name="EXPLAIN",
                    outcome="GROUNDED",
                    detail="解释仅由已验证 PlanResult 字段生成。",
                ),
            ),
            result=result,
            explanation=explain_deterministically(result),
        )


def explain_deterministically(result: HybridPlanResult) -> GroundedExplanation:
    plan = result.plan
    itinerary = plan.itinerary
    request = plan.request
    daily = tuple(
        f"{day.date.isoformat()}："
        + ("、".join(visit.place_name for visit in day.visits) if day.visits else "无已安排景点")
        for day in itinerary.days
    )
    return GroundedExplanation(
        summary=(
            f"已生成 {request.origin}到{request.destination}的 {request.trip_days} 日行程，"
            f"方案 {itinerary.id} 已通过可行性验证。"
        ),
        transport_reason=(
            f"去程选择 {itinerary.outbound.label}，返程选择 {itinerary.inbound.label}；"
            "选择由程序综合价格、行程耗时和机场/车站缓冲后确定。"
        ),
        lodging_reason=(
            f"住宿选择 {itinerary.lodging_area.name}，每晚按 "
            f"{itinerary.lodging_area.nightly_price_estimate_cny} 元计算，"
            f"价格依据为 {itinerary.lodging_area.price_basis}。"
        ),
        budget_statement=(
            f"预计总费用 {itinerary.budget.total_cny} 元，用户预算 {request.budget_cny} 元。"
        ),
        daily_outline=daily,
        caveats=plan.warnings,
    )
