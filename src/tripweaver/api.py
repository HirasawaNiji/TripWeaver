"""FastAPI delivery surface for fixture planning, controlled live runs, and metrics."""

from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from tripweaver.agent import (
    AgentRun,
    AgentRunStatus,
    AgentStep,
    ControlledTravelAgent,
    RequirementGuard,
)
from tripweaver.application.hybrid_service import HybridTripPlanningService
from tripweaver.application.service import TripPlanningService
from tripweaver.config import (
    AmapSettings,
    ConfigurationError,
    LodgingSettings,
    RailwaySettings,
    RuntimeSettings,
    VariflightSettings,
)
from tripweaver.llm.constraint_parser import RequestParseError
from tripweaver.planner.engine import NoFeasiblePlanError
from tripweaver.runtime import MetricsStore


class TextPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=2000)


def create_app() -> FastAPI:
    app = FastAPI(
        title="TripWeaver API",
        version="1.0.0",
        description="Query-only, provenance-aware constrained travel planning.",
    )

    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "version": "1.0.0",
            "booking_enabled": False,
            "data_policy": "live-with-explicit-fallback",
        }

    async def fixture_plan(body: TextPlanRequest) -> object:
        try:
            return TripPlanningService().plan_text(body.text)
        except (RequestParseError, NoFeasiblePlanError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    async def live_agent_run(body: TextPlanRequest) -> AgentRun:
        questions = RequirementGuard().questions(body.text)
        if questions:
            return AgentRun(
                status=AgentRunStatus.NEEDS_INPUT,
                questions=questions,
                steps=(
                    AgentStep(
                        name="REQUIREMENTS",
                        outcome="NEEDS_INPUT",
                        detail="缺少硬约束，未加载凭证或调用外部工具。",
                    ),
                ),
            )
        try:
            runtime = RuntimeSettings.from_env()
            service = HybridTripPlanningService.from_settings(
                AmapSettings.from_env(),
                RailwaySettings.from_env(),
                VariflightSettings.from_env(),
                LodgingSettings.from_env(),
                runtime,
            )
            return await ControlledTravelAgent(service).run(body.text)
        except ConfigurationError as error:
            raise HTTPException(status_code=503, detail=type(error).__name__) from error
        except (NoFeasiblePlanError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    async def metrics(
        include_zero: Annotated[bool, Query(description="Return an empty summary too")] = True,
    ) -> object:
        del include_zero
        settings = RuntimeSettings.from_env()
        return MetricsStore(settings.database_path).summary()

    app.add_api_route("/health", health, methods=["GET"])
    app.add_api_route("/v1/plans/fixture", fixture_plan, methods=["POST"])
    app.add_api_route("/v1/agent/runs", live_agent_run, methods=["POST"], response_model=AgentRun)
    app.add_api_route("/v1/metrics", metrics, methods=["GET"])

    return app


app = create_app()
