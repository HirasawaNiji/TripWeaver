from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from tripweaver.api import create_app
from tripweaver.cli import build_parser, main
from tripweaver.config import DeepSeekSettings
from tripweaver.operations import CheckStatus, inspect_live_readiness, inspect_readiness

READY_ENV = {
    "AMAP_MAPS_API_KEY": "private-amap-key",
    "RAILWAY_MCP_ENABLED": "true",
    "VARIFLIGHT_MCP_ENABLED": "true",
    "VARIFLIGHT_API_KEY": "private-flight-key",
    "DEEPSEEK_ENABLED": "true",
    "DEEPSEEK_API_KEY": "private-llm-key",
}


class _HealthyProvider:
    async def verify_capabilities(self) -> tuple[object, ...]:
        return (object(), object())


def test_offline_readiness_is_complete_and_never_exposes_credentials() -> None:
    report = inspect_readiness(
        environ=READY_ENV,
        env_file=Path("missing.env"),
        executable_lookup=lambda _: "available",
    )

    assert report.demo_ready
    assert report.live_ready
    assert report.llm_ready
    assert report.provider_ready_count == 3
    document = report.model_dump_json()
    assert "private-amap-key" not in document
    assert "private-flight-key" not in document
    assert "private-llm-key" not in document


def test_demo_stays_ready_when_live_credentials_are_absent() -> None:
    report = inspect_readiness(
        environ={
            "RAILWAY_MCP_ENABLED": "false",
            "VARIFLIGHT_MCP_ENABLED": "false",
            "DEEPSEEK_ENABLED": "false",
        },
        env_file=Path("missing.env"),
        executable_lookup=lambda _: None,
    )

    assert report.demo_ready
    assert not report.live_ready
    assert not report.llm_ready
    assert next(check for check in report.checks if check.key == "amap").status == CheckStatus.FAIL


def test_live_doctor_adds_query_only_capability_probes() -> None:
    with (
        patch("tripweaver.operations.readiness.AmapProvider.from_settings", return_value=_HealthyProvider()),
        patch("tripweaver.operations.readiness.RailwayProvider.from_settings", return_value=_HealthyProvider()),
        patch("tripweaver.operations.readiness.VariflightProvider.from_settings", return_value=_HealthyProvider()),
    ):
        report = asyncio.run(
            inspect_live_readiness(
                environ=READY_ENV,
                env_file=Path("missing.env"),
                executable_lookup=lambda _: "available",
            )
        )

    assert report.live_ready
    assert report.live_probed
    probes = [check for check in report.checks if check.key.endswith("_probe")]
    assert len(probes) == 3
    assert all(check.status == CheckStatus.PASS for check in probes)


def test_readiness_api_and_phase24_commands_are_exposed() -> None:
    client = TestClient(create_app(llm_settings=DeepSeekSettings()))
    response = client.get("/readiness")

    assert response.status_code == 200
    assert response.json()["demo_ready"] is True
    assert build_parser().parse_args(["serve"]).port == 8000
    assert build_parser().parse_args(["doctor", "--json"]).command == "doctor"


def test_doctor_command_returns_success_for_demo_ready_report() -> None:
    report = inspect_readiness(
        environ={
            "RAILWAY_MCP_ENABLED": "false",
            "VARIFLIGHT_MCP_ENABLED": "false",
            "DEEPSEEK_ENABLED": "false",
        },
        env_file=Path("missing.env"),
        executable_lookup=lambda _: None,
    )
    with patch("tripweaver.cli.inspect_readiness", return_value=report):
        assert main(["doctor", "--json"]) == 0
