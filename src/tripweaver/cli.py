"""Command-line entry point for deterministic planning and live provider probes."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from tripweaver import __version__
from tripweaver.agent import AgentRunStatus, ControlledTravelAgent
from tripweaver.application.alternatives_service import AlternativeTripPlanningService
from tripweaver.application.hybrid_service import HybridTripPlanningService
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
from tripweaver.domain.models import GeoPoint, TransportLeg
from tripweaver.evaluation import AgentEvaluationRunner, EvaluationRunner, default_agent_cases
from tripweaver.fixtures.catalog import UnsupportedFixtureRouteError
from tripweaver.llm.constraint_parser import RequestParseError
from tripweaver.llm.runtime import DeepSeekRequestInterpreter, DeepSeekRevisionInterpreter
from tripweaver.mcp_gateway.errors import McpGatewayError
from tripweaver.operations import ReadinessReport, inspect_live_readiness, inspect_readiness
from tripweaver.planner.engine import NoFeasiblePlanError
from tripweaver.providers.amap import AmapProvider, AmapProviderError
from tripweaver.providers.aviation import VariflightProvider, VariflightProviderError
from tripweaver.providers.railway import RailwayProvider, RailwayProviderError
from tripweaver.runtime import MetricsStore, SQLitePlanCache

DEMO_REQUEST = (
    "我想从北京去上海玩3天，2026-10-01出发，2个人，预算5000元，"
    "喜欢历史文化、城市夜景和美食街区，高铁或飞机都可以。"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tripweaver",
        description="TripWeaver deterministic planner and query-only MCP providers",
    )
    parser.add_argument("--version", action="version", version=f"TripWeaver {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser(
        "doctor", help="inspect demo, credential, dependency, and live MCP readiness"
    )
    doctor.add_argument("--json", action="store_true", help="emit a redacted JSON report")
    doctor.add_argument(
        "--live", action="store_true", help="perform query-only capability discovery"
    )
    doctor.add_argument(
        "--env-file",
        type=Path,
        help="environment file to inspect (defaults to TRIPWEAVER_ENV_FILE or .env)",
    )

    serve = subparsers.add_parser("serve", help="start the local API and Web demo")
    serve.add_argument(
        "--host",
        choices=("127.0.0.1", "0.0.0.0"),
        default="127.0.0.1",
        help="bind locally by default; use 0.0.0.0 only for a trusted network",
    )
    serve.add_argument("--port", type=_port_argument, default=8000)
    demo = subparsers.add_parser("demo", help="run the Beijing-to-Shanghai fixture demo")
    demo.add_argument("--json", action="store_true", help="emit the complete JSON model")
    plan = subparsers.add_parser("plan", help="plan a supported Chinese request")
    plan.add_argument("request", help="natural-language request")
    plan.add_argument("--json", action="store_true", help="emit the complete JSON model")

    alternatives = subparsers.add_parser(
        "alternatives", help="compare budget, balanced, and time-oriented fixture plans"
    )
    alternatives.add_argument("request", help="natural-language request")
    alternatives.add_argument("--json", action="store_true", help="emit all alternatives")

    live_plan = subparsers.add_parser(
        "plan-live",
        help="plan with live AMap, railway, flights, and explicit fallbacks",
    )
    live_plan.add_argument("request", help="natural-language request")
    live_plan.add_argument("--json", action="store_true", help="emit the complete JSON model")

    agent = subparsers.add_parser(
        "agent", help="run the controlled clarify-plan-validate-explain workflow"
    )
    agent.add_argument("request", help="natural-language request with all hard constraints")
    agent.add_argument("--json", action="store_true", help="emit the complete agent run")

    evaluate = subparsers.add_parser("evaluate", help="run the fixed 120-case offline suite")
    evaluate.add_argument("--json", action="store_true", help="emit the complete report")
    evaluate.add_argument("--output", type=Path, help="write the JSON report to this path")

    evaluate_agent = subparsers.add_parser(
        "evaluate-agent", help="run the fixed 40-case multi-turn Agent suite"
    )
    evaluate_agent.add_argument("--json", action="store_true", help="emit the complete report")
    evaluate_agent.add_argument("--output", type=Path, help="write the JSON report to this path")
    evaluate_agent.add_argument(
        "--live-llm",
        action="store_true",
        help="use configured DeepSeek instead of the deterministic language baseline",
    )
    evaluate_agent.add_argument(
        "--limit", type=int, default=40, choices=range(1, 41), help="number of cases"
    )

    metrics = subparsers.add_parser("metrics", help="show durable aggregate run metrics")
    metrics.add_argument("--json", action="store_true", help="emit JSON")

    cache = subparsers.add_parser("cache", help="manage the local bounded plan cache")
    cache.add_argument("action", choices=("clear",))

    amap = subparsers.add_parser(
        "amap", help="query the official AMap MCP server without booking or writes"
    )
    amap_commands = amap.add_subparsers(dest="amap_command", required=True)
    amap_health = amap_commands.add_parser("health", help="verify MCP tools and health")
    amap_health.add_argument("--json", action="store_true", help="emit JSON")

    amap_search = amap_commands.add_parser("search", help="search live AMap POIs")
    amap_search.add_argument("keywords", help="POI search keywords")
    amap_search.add_argument("--city", help="city name or adcode")
    amap_search.add_argument("--limit", type=int, default=5, choices=range(1, 21))
    amap_search.add_argument("--json", action="store_true", help="emit JSON")

    amap_detail = amap_commands.add_parser("detail", help="query a POI by AMap ID")
    amap_detail.add_argument("poi_id", help="POI ID returned by search")
    amap_detail.add_argument("--json", action="store_true", help="emit JSON")

    amap_weather = amap_commands.add_parser("weather", help="query live weather")
    amap_weather.add_argument("city", help="city name or adcode")
    amap_weather.add_argument("--json", action="store_true", help="emit JSON")

    amap_geocode = amap_commands.add_parser("geocode", help="geocode an address")
    amap_geocode.add_argument("address", help="structured address or POI name")
    amap_geocode.add_argument("--city", help="city name")
    amap_geocode.add_argument("--json", action="store_true", help="emit JSON")

    amap_route = amap_commands.add_parser("route", help="query a city route")
    amap_route.add_argument("mode", choices=("walking", "transit"))
    amap_route.add_argument("origin", type=_point_argument, help="longitude,latitude")
    amap_route.add_argument("destination", type=_point_argument, help="longitude,latitude")
    amap_route.add_argument("--city", help="required for transit; city name or adcode")
    amap_route.add_argument("--destination-city", help="destination city name or adcode")
    amap_route.add_argument("--json", action="store_true", help="emit JSON")

    railway = subparsers.add_parser(
        "railway", help="query the community 12306 MCP without login or booking"
    )
    railway_commands = railway.add_subparsers(dest="railway_command", required=True)
    railway_health = railway_commands.add_parser("health", help="verify MCP tools and health")
    railway_health.add_argument("--json", action="store_true", help="emit JSON")
    railway_search = railway_commands.add_parser("search", help="search live railway tickets")
    railway_search.add_argument("travel_date", type=date.fromisoformat, help="YYYY-MM-DD")
    railway_search.add_argument("origin", help="origin city or station in Chinese")
    railway_search.add_argument("destination", help="destination city or station in Chinese")
    railway_search.add_argument("--limit", type=int, default=10, choices=range(1, 51))
    railway_search.add_argument("--json", action="store_true", help="emit JSON")

    aviation = subparsers.add_parser(
        "aviation", help="query VariFlight MCP without booking or writes"
    )
    aviation_commands = aviation.add_subparsers(dest="aviation_command", required=True)
    aviation_health = aviation_commands.add_parser("health", help="verify MCP tools and health")
    aviation_health.add_argument("--json", action="store_true", help="emit JSON")
    aviation_search = aviation_commands.add_parser("search", help="search live flight fares")
    aviation_search.add_argument("travel_date", type=date.fromisoformat, help="YYYY-MM-DD")
    aviation_search.add_argument("origin", help="Chinese city name or IATA city code")
    aviation_search.add_argument("destination", help="Chinese city name or IATA city code")
    aviation_search.add_argument("--limit", type=int, default=10, choices=range(1, 201))
    aviation_search.add_argument("--json", action="store_true", help="emit JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_utf8_stdout()
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        return asyncio.run(_run_doctor(args))
    if args.command == "serve":
        return _run_serve(args)
    if args.command == "amap":
        return asyncio.run(_run_amap(args))
    if args.command == "railway":
        return asyncio.run(_run_railway(args))
    if args.command == "aviation":
        return asyncio.run(_run_aviation(args))
    if args.command == "plan-live":
        return asyncio.run(_run_live_plan(args))
    if args.command == "agent":
        return asyncio.run(_run_agent(args))
    if args.command == "evaluate":
        return _run_evaluation(args)
    if args.command == "evaluate-agent":
        return _run_agent_evaluation(args)
    if args.command == "metrics":
        return _run_metrics(args)
    if args.command == "cache":
        return _run_cache(args)
    if args.command == "alternatives":
        return _run_alternatives(args)
    request_text = DEMO_REQUEST if args.command == "demo" else args.request
    try:
        result = TripPlanningService().plan_text(request_text)
    except (
        RequestParseError,
        UnsupportedFixtureRouteError,
        NoFeasiblePlanError,
        ValidationError,
    ) as error:
        print(f"TripWeaver error: {error}", file=sys.stderr)
        return 2

    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        _print_summary(result.model_dump(mode="python"))
    return 0 if result.validation.feasible else 1


async def _run_doctor(args: argparse.Namespace) -> int:
    report = (
        await inspect_live_readiness(env_file=args.env_file)
        if args.live
        else inspect_readiness(env_file=args.env_file)
    )
    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        _print_readiness(report)
    return 0 if (report.live_ready if args.live else report.demo_ready) else 1


def _run_serve(args: argparse.Namespace) -> int:
    report = inspect_readiness()
    if not report.demo_ready:
        _print_readiness(report)
        print("TripWeaver cannot start until required DEMO checks pass.", file=sys.stderr)
        return 3
    import uvicorn

    print(f"TripWeaver {__version__} · http://{args.host}:{args.port}")
    uvicorn.run("tripweaver.api:app", host=args.host, port=args.port)
    return 0


def _print_readiness(report: ReadinessReport) -> None:
    print(f"TripWeaver {report.version} · PHASE 24 READINESS")
    print("=" * 52)
    for check in report.checks:
        print(f"[{check.status.value:4}] {check.label}: {check.detail}")
    print()
    print(f"DEMO ready: {'yes' if report.demo_ready else 'no'}")
    print(
        f"LIVE ready: {'yes' if report.live_ready else 'no'} "
        f"({report.provider_ready_count}/{report.provider_total_count} providers configured)"
    )
    print(f"LLM ready: {'yes' if report.llm_ready else 'no; deterministic fallback active'}")


async def _run_live_plan(args: argparse.Namespace) -> int:
    try:
        result = await HybridTripPlanningService.from_settings(
            AmapSettings.from_env(),
            RailwaySettings.from_env(),
            VariflightSettings.from_env(),
            LodgingSettings.from_env(),
            RuntimeSettings.from_env(),
        ).plan_text(args.request)
    except (
        ConfigurationError,
        NoFeasiblePlanError,
        RequestParseError,
        UnsupportedFixtureRouteError,
        ValidationError,
        ValueError,
    ) as error:
        print(f"TripWeaver live-plan error: {error}", file=sys.stderr)
        return 3

    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        _print_summary(
            result.plan.model_dump(mode="python"),
            banner="TripWeaver · PHASE 11 MULTI-CITY FOUNDATION",
        )
        cache_label = "缓存命中" if result.cache_hit else None
        print(
            f"实时地图: {cache_label or ('已使用' if result.live_map_used else '已降级到 Fixture')}"
        )
        print(
            f"实时铁路: {cache_label or ('已使用' if result.live_rail_used else '已降级到 Fixture')}"
        )
        print(
            f"实时航班: {cache_label or ('已使用' if result.live_flight_used else '已降级到 Fixture')}"
        )
        lodging_mode = result.plan.itinerary.lodging_area.price_basis
        print(f"住宿候选: {len(result.lodging_candidates)} 个（价格依据 {lodging_mode}）")
        if result.weather and result.weather.forecasts:
            first = result.weather.forecasts[0]
            print(
                f"天气: {result.weather.city} {first.date} "
                f"{first.day_weather} {first.day_temperature_c:.0f}°C"
            )
    return 0 if result.plan.validation.feasible else 1


async def _run_agent(args: argparse.Namespace) -> int:
    try:
        service = HybridTripPlanningService.from_settings(
            AmapSettings.from_env(),
            RailwaySettings.from_env(),
            VariflightSettings.from_env(),
            LodgingSettings.from_env(),
            RuntimeSettings.from_env(),
        )
        run = await ControlledTravelAgent(service).run(args.request)
    except (
        ConfigurationError,
        NoFeasiblePlanError,
        UnsupportedFixtureRouteError,
        ValidationError,
        ValueError,
    ) as error:
        print(f"TripWeaver agent error: {error}", file=sys.stderr)
        return 3
    if args.json:
        print(run.model_dump_json(indent=2))
    elif run.status == AgentRunStatus.NEEDS_INPUT:
        print("TripWeaver · NEEDS INPUT")
        for question in run.questions:
            print(f"  - {question}")
    elif run.explanation is not None:
        print("TripWeaver · VERIFIED AGENT")
        print(run.explanation.summary)
        print(run.explanation.transport_reason)
        print(run.explanation.lodging_reason)
        print(run.explanation.budget_statement)
        for outline in run.explanation.daily_outline:
            print(f"  - {outline}")
    return 0 if run.status != AgentRunStatus.REJECTED else 1


def _run_evaluation(args: argparse.Namespace) -> int:
    report = EvaluationRunner().run()
    document = report.model_dump_json(indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(document + "\n", encoding="utf-8")
    if args.json:
        print(document)
    else:
        print("TripWeaver · 120-CASE EVALUATION")
        print(f"通过: {report.passed_cases}/{report.total_cases}")
        print(f"硬约束满足率: {report.hard_constraint_satisfaction_rate:.1%}")
        print(f"来源完整率: {report.source_completeness_rate:.1%}")
        print(f"稳定性: {report.deterministic_stability_rate:.1%}")
        print(f"平均延迟: {report.average_latency_ms:.2f} ms")
        print(f"Token 成本: {report.token_cost}")
    return 0 if report.passed_cases == report.total_cases else 1


def _run_agent_evaluation(args: argparse.Namespace) -> int:
    if args.live_llm:
        settings = DeepSeekSettings.from_env()
        if not settings.enabled:
            print("TripWeaver agent evaluation error: DeepSeek is disabled", file=sys.stderr)
            return 3
        runner = AgentEvaluationRunner(
            request_interpreter=DeepSeekRequestInterpreter(settings),
            revision_interpreter=DeepSeekRevisionInterpreter(settings),
        )
    else:
        runner = AgentEvaluationRunner()
    report = runner.run(default_agent_cases()[: args.limit])
    document = report.model_dump_json(indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(document + "\n", encoding="utf-8")
    if args.json:
        print(document)
    else:
        print("TripWeaver · 40-CASE MULTI-TURN AGENT EVALUATION")
        print(f"通过: {report.passed_cases}/{report.total_cases}")
        print(f"需求结构化成功率: {report.structured_request_success_rate:.1%}")
        print(f"修改意图准确率: {report.revision_intent_accuracy:.1%}")
        print(f"硬约束满足率: {report.hard_constraint_satisfaction_rate:.1%}")
        print(f"锁定字段保持率: {report.replan_preservation_rate:.1%}")
        print(f"快照复用率: {report.snapshot_reuse_rate:.1%}")
        print(f"LLM 降级率: {report.fallback_rate:.1%}")
        print(f"Token: {report.total_input_tokens + report.total_output_tokens}")
    return 0 if report.passed_cases == report.total_cases else 1


def _run_metrics(args: argparse.Namespace) -> int:
    summary = MetricsStore(RuntimeSettings.from_env().database_path).summary()
    _print_live_payload(
        summary.model_dump(mode="json"),
        as_json=args.json,
        banner="TripWeaver · RUNTIME METRICS",
    )
    return 0


def _run_cache(args: argparse.Namespace) -> int:
    settings = RuntimeSettings.from_env()
    deleted = SQLitePlanCache(settings.database_path, settings.cache_ttl_seconds).clear()
    print(f"已清除 {deleted} 条 TripWeaver 方案缓存。")
    return 0


def _run_alternatives(args: argparse.Namespace) -> int:
    try:
        result = AlternativeTripPlanningService().plan_text(args.request)
    except (
        NoFeasiblePlanError,
        RequestParseError,
        UnsupportedFixtureRouteError,
        ValidationError,
        ValueError,
    ) as error:
        print(f"TripWeaver alternatives error: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(result.model_dump_json(indent=2))
        return 0
    print("TripWeaver · MULTI-OBJECTIVE ALTERNATIVES")
    print("=" * 44)
    for index, plan in enumerate(result.alternatives, start=1):
        itinerary = plan.itinerary
        print(
            f"{index}. {itinerary.title} | {itinerary.outbound.mode.value}/"
            f"{itinerary.inbound.mode.value} | {itinerary.lodging_area.name} | "
            f"CNY {_money(itinerary.budget.total_cny)}"
        )
    return 0 if all(plan.validation.feasible for plan in result.alternatives) else 1


async def _run_amap(args: argparse.Namespace) -> int:
    try:
        provider = AmapProvider.from_settings(AmapSettings.from_env())
        if args.amap_command == "health":
            tools = await provider.verify_capabilities()
            health = provider.health()
            payload = {
                "provider": "amap",
                "state": health.state.value,
                "tool_count": len(tools),
                "tools": [tool.name for tool in tools],
                "last_latency_ms": health.last_latency_ms,
            }
            _print_live_payload(payload, as_json=args.json)
            return 0
        if args.amap_command == "search":
            places = await provider.search_places(
                args.keywords,
                city=args.city,
                limit=args.limit,
            )
            payload = [place.model_dump(mode="json") for place in places]
            _print_live_payload(payload, as_json=args.json)
            return 0
        if args.amap_command == "detail":
            detail = await provider.place_detail(args.poi_id)
            _print_live_payload(detail.model_dump(mode="json"), as_json=args.json)
            return 0
        if args.amap_command == "weather":
            weather = await provider.weather(args.city)
            _print_live_payload(weather.model_dump(mode="json"), as_json=args.json)
            return 0
        if args.amap_command == "geocode":
            results = await provider.geocode(args.address, city=args.city)
            payload = [result.model_dump(mode="json") for result in results]
            _print_live_payload(payload, as_json=args.json)
            return 0
        if args.mode == "walking":
            route = await provider.walking_route(args.origin, args.destination)
        else:
            if not args.city:
                raise ConfigurationError("--city is required for transit routes")
            route = await provider.transit_route(
                args.origin,
                args.destination,
                origin_city=args.city,
                destination_city=args.destination_city,
            )
        _print_live_payload(route.model_dump(mode="json"), as_json=args.json)
        return 0
    except (
        AmapProviderError,
        ConfigurationError,
        McpGatewayError,
        ValidationError,
        ValueError,
    ) as error:
        print(f"TripWeaver AMap error: {error}", file=sys.stderr)
        print("实时地图不可用；行程规划仍可显式使用 Fixture 模式。", file=sys.stderr)
        return 3


async def _run_railway(args: argparse.Namespace) -> int:
    try:
        settings = RailwaySettings.from_env()
        if not settings.enabled:
            raise ConfigurationError("RAILWAY_MCP_ENABLED is false")
        provider = RailwayProvider.from_settings(settings)
        if args.railway_command == "health":
            tools = await provider.verify_capabilities()
            health = provider.health()
            payload = {
                "provider": "railway_12306_community",
                "official": False,
                "query_only": True,
                "state": health.state.value,
                "tool_count": len(tools),
                "tools": [tool.name for tool in tools],
                "last_latency_ms": health.last_latency_ms,
            }
        else:
            tickets = await provider.search_tickets(
                args.origin,
                args.destination,
                args.travel_date,
                TransportLeg.OUTBOUND,
                limit=args.limit,
            )
            payload = [ticket.model_dump(mode="json") for ticket in tickets]
        _print_live_payload(
            payload,
            as_json=args.json,
            banner="TripWeaver · 12306 COMMUNITY MCP LIVE",
        )
        return 0
    except (
        ConfigurationError,
        McpGatewayError,
        RailwayProviderError,
        ValidationError,
        ValueError,
    ) as error:
        print(f"TripWeaver railway error: {error}", file=sys.stderr)
        print("铁路实时数据不可用；规划会明确保留 Fixture 候选。", file=sys.stderr)
        return 3


async def _run_aviation(args: argparse.Namespace) -> int:
    try:
        settings = VariflightSettings.from_env()
        if not settings.enabled:
            raise ConfigurationError("VARIFLIGHT_MCP_ENABLED is false")
        provider = VariflightProvider.from_settings(settings)
        if args.aviation_command == "health":
            tools = await provider.verify_capabilities()
            health = provider.health()
            payload = {
                "provider": "variflight",
                "query_only": True,
                "state": health.state.value,
                "tool_count": len(tools),
                "tools": [tool.name for tool in tools],
                "last_latency_ms": health.last_latency_ms,
            }
        else:
            offers = await provider.search_offers(
                args.origin,
                args.destination,
                args.travel_date,
                TransportLeg.OUTBOUND,
                limit=args.limit,
            )
            payload = [offer.model_dump(mode="json") for offer in offers]
        _print_live_payload(
            payload,
            as_json=args.json,
            banner="TripWeaver · VARIFLIGHT MCP LIVE",
        )
        return 0
    except (
        ConfigurationError,
        McpGatewayError,
        ValidationError,
        ValueError,
        VariflightProviderError,
    ) as error:
        print(f"TripWeaver aviation error: {error}", file=sys.stderr)
        print("航班实时数据不可用；规划会明确保留 Fixture 候选。", file=sys.stderr)
        return 3


def _print_live_payload(
    payload: object,
    *,
    as_json: bool,
    banner: str = "TripWeaver · AMAP MCP LIVE",
) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(banner)
    print("=" * 44)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _point_argument(value: str) -> GeoPoint:
    try:
        longitude_text, latitude_text = value.split(",", 1)
        return GeoPoint(
            latitude=float(latitude_text),
            longitude=float(longitude_text),
        )
    except (ValueError, ValidationError) as error:
        raise argparse.ArgumentTypeError(
            "coordinate must use longitude,latitude within valid bounds"
        ) from error


def _port_argument(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("port must be an integer") from error
    if not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1024 and 65535")
    return port


def _print_summary(
    payload: dict[str, Any],
    *,
    banner: str = "TripWeaver · PHASE 1 FIXTURE DEMO",
) -> None:
    itinerary = payload["itinerary"]
    budget = itinerary["budget"]
    validation = payload["validation"]
    print(banner)
    print("=" * 44)
    print(itinerary["title"])
    print(f"方案 ID: {itinerary['id']}")
    print(f"交通: {itinerary['outbound']['label']} / {itinerary['inbound']['label']}")
    print(f"住宿区域: {itinerary['lodging_area']['name']}")
    print(f"预计总费用: CNY {_money(budget['total_cny'])}")
    print(f"验证通过: {'是' if validation['feasible'] else '否'}")
    print()
    for day in itinerary["days"]:
        print(f"{day['date']}")
        if not day["visits"]:
            print("  - 无可安排景点")
        for visit in day["visits"]:
            start = visit["start_at"].strftime("%H:%M")
            end = visit["end_at"].strftime("%H:%M")
            source_status = visit["source"]["status"]
            print(f"  - {start}-{end} {visit['place_name']} [{source_status}]")
    print()
    print("数据声明:")
    for warning in payload["warnings"]:
        print(f"  - {warning}")


def _money(value: Decimal | str) -> str:
    return f"{Decimal(value):.2f}"


def _configure_utf8_stdout() -> None:
    """Use UTF-8 in real Windows terminals while remaining test-capture friendly."""

    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
