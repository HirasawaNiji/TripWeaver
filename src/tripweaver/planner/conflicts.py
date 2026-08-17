"""Deterministic, actionable explanations for infeasible planning requests."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import Field

from tripweaver.domain.models import DomainModel, TripRequest
from tripweaver.planner.engine import NoFeasiblePlanError


class ConflictCode(StrEnum):
    BUDGET_SHORTFALL = "BUDGET_SHORTFALL"
    TRANSPORT_WINDOW_CONFLICT = "TRANSPORT_WINDOW_CONFLICT"
    FIXED_TRANSPORT_UNAVAILABLE = "FIXED_TRANSPORT_UNAVAILABLE"
    LODGING_UNAVAILABLE = "LODGING_UNAVAILABLE"
    LODGING_CONSTRAINT_CONFLICT = "LODGING_CONSTRAINT_CONFLICT"
    SCHEDULE_CAPACITY_CONFLICT = "SCHEDULE_CAPACITY_CONFLICT"
    DAY_WINDOW_CONFLICT = "DAY_WINDOW_CONFLICT"
    LOCKED_CONSTRAINT = "LOCKED_CONSTRAINT"
    NO_FEASIBLE_PLAN = "NO_FEASIBLE_PLAN"


class RelaxationSuggestion(DomainModel):
    id: str
    label: str
    description: str
    revision_text: str


class PlanningConflict(DomainModel):
    code: ConflictCode
    title: str
    message: str
    blocking_constraints: tuple[str, ...]
    suggestions: tuple[RelaxationSuggestion, ...]
    shortfall_cny: Decimal | None = Field(default=None, ge=0)
    recoverable: bool = True


class PlanningConflictError(RuntimeError):
    def __init__(self, conflict: PlanningConflict) -> None:
        super().__init__(conflict.message)
        self.conflict = conflict


class LockedConstraintError(ValueError):
    def __init__(self, fields: tuple[str, ...]) -> None:
        self.fields = fields
        super().__init__("修改涉及已锁定项目，请先解除锁定")


class ConflictAnalyzer:
    """Convert planner failures into stable UI and API contracts without an LLM."""

    @staticmethod
    def analyze(error: Exception, request: TripRequest) -> PlanningConflict:
        if isinstance(error, LockedConstraintError):
            labels = {"outbound": "去程", "inbound": "返程", "lodging": "住宿"}
            names = tuple(labels.get(field, field) for field in error.fields)
            return PlanningConflict(
                code=ConflictCode.LOCKED_CONSTRAINT,
                title="修改与锁定内容冲突",
                message=f"请先解除锁定：{'、'.join(names)}。",
                blocking_constraints=error.fields,
                suggestions=(
                    RelaxationSuggestion(
                        id="unlock-fields",
                        label="解除相关锁定",
                        description="在 Agent 工作台取消对应勾选后重新提交修改。",
                        revision_text="",
                    ),
                ),
            )

        code_value = getattr(error, "code", ConflictCode.NO_FEASIBLE_PLAN.value)
        try:
            code = ConflictCode(code_value)
        except ValueError:
            code = ConflictCode.NO_FEASIBLE_PLAN
        details = error.details if isinstance(error, NoFeasiblePlanError) else {}

        if code == ConflictCode.BUDGET_SHORTFALL:
            shortfall = Decimal(details.get("shortfall_cny", 0))
            minimum = Decimal(details.get("minimum_cny", request.budget_cny))
            return PlanningConflict(
                code=code,
                title="当前预算无法覆盖最低可执行方案",
                message=f"至少还需要增加约 {shortfall} 元；当前最低方案为 {minimum} 元。",
                shortfall_cny=shortfall,
                blocking_constraints=("budget_cny",),
                suggestions=(
                    RelaxationSuggestion(
                        id="prefer-budget",
                        label="切换预算优先",
                        description="优先选择更低交通与住宿成本。",
                        revision_text="整体预算优先，酒店每晚控制在500元",
                    ),
                    RelaxationSuggestion(
                        id="increase-budget",
                        label=f"预算提高到 {minimum} 元",
                        description="使用最低可执行预算重新提交需求。",
                        revision_text=f"总预算调整为{minimum}元",
                    ),
                ),
            )
        if code in {
            ConflictCode.TRANSPORT_WINDOW_CONFLICT,
            ConflictCode.FIXED_TRANSPORT_UNAVAILABLE,
            ConflictCode.DAY_WINDOW_CONFLICT,
        }:
            return PlanningConflict(
                code=code,
                title="交通时间与游玩窗口冲突",
                message=str(error),
                blocking_constraints=("transport_mode", "time_window"),
                suggestions=(
                    RelaxationSuggestion(
                        id="allow-both-modes",
                        label="允许高铁或飞机",
                        description="扩大交通候选范围并重新计算首末日时间。",
                        revision_text="去程和返程高铁或飞机都可以，时间优先",
                    ),
                ),
            )
        if code in {
            ConflictCode.LODGING_UNAVAILABLE,
            ConflictCode.LODGING_CONSTRAINT_CONFLICT,
        }:
            return PlanningConflict(
                code=code,
                title="住宿限制没有可用候选",
                message=str(error),
                blocking_constraints=("lodging", "max_nightly_price_cny"),
                suggestions=(
                    RelaxationSuggestion(
                        id="relax-lodging",
                        label="放宽至每晚 800 元",
                        description="保留交通方案，只扩大住宿候选范围。",
                        revision_text="酒店每晚控制在800元",
                    ),
                ),
            )
        if code == ConflictCode.SCHEDULE_CAPACITY_CONFLICT:
            return PlanningConflict(
                code=code,
                title="当前游玩窗口无法安排全部景点",
                message=str(error),
                blocking_constraints=("max_daily_minutes", "places"),
                suggestions=(
                    RelaxationSuggestion(
                        id="replace-place",
                        label="替换冲突景点",
                        description="移除一个当前无法安排的景点并局部重规划。",
                        revision_text="第二天第一个景点换掉",
                    ),
                ),
            )
        return PlanningConflict(
            code=ConflictCode.NO_FEASIBLE_PLAN,
            title="没有找到满足全部硬约束的方案",
            message=str(error),
            blocking_constraints=("unknown",),
            suggestions=(
                RelaxationSuggestion(
                    id="balanced-retry",
                    label="使用均衡策略重试",
                    description="保留原始需求，重新比较交通和住宿组合。",
                    revision_text="整体使用均衡方案",
                ),
            ),
        )
