"""FastAPI delivery surface for fixture planning, controlled live runs, and metrics."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from tripweaver.agent import (
    AgentRun,
    AgentRunStatus,
    AgentStep,
    ControlledTravelAgent,
    RequirementGuard,
)
from tripweaver.application.hybrid_service import HybridPlanResult, HybridTripPlanningService
from tripweaver.application.service import TripPlanningService
from tripweaver.config import (
    AmapSettings,
    ConfigurationError,
    DeepSeekSettings,
    LodgingSettings,
    RailwaySettings,
    RuntimeSettings,
    VariflightSettings,
)
from tripweaver.conversation import (
    ConversationPlanningService,
    RevisionIntent,
    SessionNotFoundError,
    UnsafeRevisionError,
)
from tripweaver.llm.constraint_parser import RequestParseError
from tripweaver.llm.explainer import PlanExplainer
from tripweaver.llm.runtime import (
    DeepSeekRequestInterpreter,
    DeepSeekRevisionInterpreter,
    DeterministicRequestInterpreter,
    DeterministicRevisionInterpreter,
    RequestInterpreter,
    RevisionInterpreter,
)
from tripweaver.planner.engine import NoFeasiblePlanError
from tripweaver.runtime import MetricsStore


class TextPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=2000)


class PlanSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    index: int = Field(ge=1, le=3)


class PlanRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    text: str = Field(min_length=1, max_length=1000)


class SessionLockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    fields: tuple[str, ...] = ()


class PlaceReplacementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    place_id: str = Field(min_length=1, max_length=200)


def create_app(
    *,
    request_interpreter: RequestInterpreter | None = None,
    revision_interpreter: RevisionInterpreter | None = None,
    llm_settings: DeepSeekSettings | None = None,
) -> FastAPI:
    llm_settings = llm_settings or DeepSeekSettings.from_env()
    request_language = request_interpreter or (
        DeepSeekRequestInterpreter(llm_settings)
        if llm_settings.enabled
        else DeterministicRequestInterpreter()
    )
    revision_language = revision_interpreter or (
        DeepSeekRevisionInterpreter(llm_settings)
        if llm_settings.enabled
        else DeterministicRevisionInterpreter()
    )
    plan_explainer = PlanExplainer(llm_settings)
    conversations = ConversationPlanningService()
    web_dir = Path(__file__).with_name("web")
    app = FastAPI(
        title="TripWeaver API",
        version="3.0.0",
        description="Query-only, provenance-aware constrained travel planning.",
    )

    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "version": "3.0.0",
            "booking_enabled": False,
            "data_policy": "live-with-explicit-fallback",
            "llm_enabled": llm_settings.enabled,
            "llm_provider": "deepseek" if llm_settings.enabled else None,
            "llm_model": llm_settings.model if llm_settings.enabled else None,
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

    async def create_session(body: TextPlanRequest) -> object:
        try:
            interpretation = request_language.interpret(body.text)
            if interpretation.request is None:
                return {
                    "status": "NEEDS_INPUT",
                    "questions": interpretation.questions,
                    "language": interpretation.metadata,
                }
            session = conversations.create(interpretation.request)
            return conversations.record_model_call(session.id, interpretation.metadata)
        except (RequestParseError, NoFeasiblePlanError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    async def get_session(session_id: str) -> object:
        try:
            return conversations.get(session_id)
        except SessionNotFoundError as error:
            raise HTTPException(status_code=404, detail="session not found") from error

    async def select_session(session_id: str, body: PlanSelectionRequest) -> object:
        try:
            return conversations.select(session_id, body.index)
        except SessionNotFoundError as error:
            raise HTTPException(status_code=404, detail="session not found") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    async def revise_session(session_id: str, body: PlanRevisionRequest) -> object:
        try:
            interpretation = revision_language.interpret(body.text)
            conversations.revise_with_intent(session_id, interpretation.intent)
            return conversations.record_model_call(session_id, interpretation.metadata)
        except SessionNotFoundError as error:
            raise HTTPException(status_code=404, detail="session not found") from error
        except UnsafeRevisionError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except (NoFeasiblePlanError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    async def undo_session(session_id: str) -> object:
        try:
            return conversations.undo(session_id)
        except SessionNotFoundError as error:
            raise HTTPException(status_code=404, detail="session not found") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    async def lock_session(session_id: str, body: SessionLockRequest) -> object:
        try:
            return conversations.set_locks(session_id, body.fields)
        except SessionNotFoundError as error:
            raise HTTPException(status_code=404, detail="session not found") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    async def explain_session(session_id: str) -> object:
        try:
            session = conversations.get(session_id)
            if session.selected_plan is None:
                raise ValueError("请先选择一个方案")
            result = plan_explainer.explain(
                HybridPlanResult(plan=session.selected_plan, live_map_used=False)
            )
            conversations.record_model_call(session_id, result.metadata)
            return result
        except SessionNotFoundError as error:
            raise HTTPException(status_code=404, detail="session not found") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    async def replace_place(
        session_id: str, body: PlaceReplacementRequest
    ) -> object:
        try:
            return conversations.revise_with_intent(
                session_id,
                RevisionIntent(
                    replace_place_id=body.place_id,
                    explanation="用户通过行程卡片替换指定景点",
                ),
            )
        except SessionNotFoundError as error:
            raise HTTPException(status_code=404, detail="session not found") from error
        except (NoFeasiblePlanError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    async def demo_app() -> FileResponse:
        return FileResponse(web_dir / "index.html")

    app.add_api_route("/health", health, methods=["GET"])
    app.add_api_route("/v1/plans/fixture", fixture_plan, methods=["POST"])
    app.add_api_route("/v1/agent/runs", live_agent_run, methods=["POST"], response_model=AgentRun)
    app.add_api_route("/v1/metrics", metrics, methods=["GET"])
    app.add_api_route("/v2/sessions", create_session, methods=["POST"])
    app.add_api_route("/v2/sessions/{session_id}", get_session, methods=["GET"])
    app.add_api_route(
        "/v2/sessions/{session_id}/select", select_session, methods=["POST"]
    )
    app.add_api_route(
        "/v2/sessions/{session_id}/revise", revise_session, methods=["POST"]
    )
    app.add_api_route(
        "/v2/sessions/{session_id}/undo", undo_session, methods=["POST"]
    )
    app.add_api_route(
        "/v2/sessions/{session_id}/locks", lock_session, methods=["PUT"]
    )
    app.add_api_route(
        "/v2/sessions/{session_id}/explain", explain_session, methods=["POST"]
    )
    app.add_api_route(
        "/v2/sessions/{session_id}/places/replace", replace_place, methods=["POST"]
    )
    app.add_api_route("/", demo_app, methods=["GET"], include_in_schema=False)
    app.mount("/static", StaticFiles(directory=web_dir), name="static")

    return app


app = create_app()
