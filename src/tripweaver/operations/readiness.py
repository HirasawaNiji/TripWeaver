"""Secret-safe environment and provider readiness diagnostics."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from collections.abc import Callable, Mapping
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from tripweaver import __version__
from tripweaver.config import (
    AmapSettings,
    ConfigurationError,
    DeepSeekSettings,
    RailwaySettings,
    RuntimeSettings,
    VariflightSettings,
)
from tripweaver.mcp_gateway.errors import McpGatewayError
from tripweaver.providers.amap import AmapProvider, AmapProviderError
from tripweaver.providers.aviation import VariflightProvider, VariflightProviderError
from tripweaver.providers.railway import RailwayProvider, RailwayProviderError


class CheckStatus(StrEnum):
    """Stable status values exposed by the CLI, API, and Web UI."""

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class ReadinessCheck(BaseModel):
    """One redacted readiness observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    label: str
    status: CheckStatus
    detail: str
    required_for: tuple[str, ...] = ()


class ReadinessReport(BaseModel):
    """Public, credential-free operational capability report."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    demo_ready: bool
    live_ready: bool
    llm_ready: bool
    provider_ready_count: int
    provider_total_count: int
    live_probed: bool = False
    checks: tuple[ReadinessCheck, ...]


ExecutableLookup = Callable[[str], str | None]


def inspect_readiness(
    *,
    environ: Mapping[str, str] | None = None,
    env_file: Path | None = None,
    executable_lookup: ExecutableLookup = shutil.which,
) -> ReadinessReport:
    """Inspect configuration and local dependencies without making network calls."""

    selected_env = _resolve_env_file(environ, env_file)
    checks: list[ReadinessCheck] = []

    python_ok = sys.version_info >= (3, 12)
    checks.append(
        ReadinessCheck(
            key="python",
            label="Python runtime",
            status=CheckStatus.PASS if python_ok else CheckStatus.FAIL,
            detail=f"Python {sys.version_info.major}.{sys.version_info.minor}",
            required_for=("DEMO", "LIVE"),
        )
    )
    checks.append(
        ReadinessCheck(
            key="env_file",
            label="Environment file",
            status=CheckStatus.PASS if selected_env.is_file() else CheckStatus.WARN,
            detail=(
                f"Loaded from {selected_env}"
                if selected_env.is_file()
                else "No .env file; process environment and DEMO mode remain available"
            ),
        )
    )

    runtime_ok = _append_settings_check(
        checks,
        key="runtime",
        label="Local runtime",
        required_for=("DEMO", "LIVE"),
        loader=lambda: RuntimeSettings.from_env(environ=environ, env_file=selected_env),
        success_detail="SQLite cache and aggregate metrics configuration is valid",
    )
    amap_ok = _append_settings_check(
        checks,
        key="amap",
        label="AMap MCP",
        required_for=("LIVE",),
        loader=lambda: AmapSettings.from_env(environ=environ, env_file=selected_env),
        success_detail="Credential and rate policy configured",
    )

    railway_ok = False
    try:
        railway = RailwaySettings.from_env(environ=environ, env_file=selected_env)
        if not railway.enabled:
            checks.append(
                ReadinessCheck(
                    key="railway",
                    label="12306 community MCP",
                    status=CheckStatus.WARN,
                    detail="Disabled; LIVE planning will use an explicit railway fallback",
                )
            )
        else:
            railway_ok = executable_lookup(railway.command) is not None
            checks.append(
                ReadinessCheck(
                    key="railway",
                    label="12306 community MCP",
                    status=CheckStatus.PASS if railway_ok else CheckStatus.FAIL,
                    detail=(
                        f"Enabled with pinned package {railway.package_spec}"
                        if railway_ok
                        else "Enabled but npx is unavailable; install Node.js 18+"
                    ),
                    required_for=("LIVE",),
                )
            )
    except ConfigurationError as error:
        checks.append(_configuration_failure("railway", "12306 community MCP", error))

    variflight_ok = False
    try:
        variflight = VariflightSettings.from_env(environ=environ, env_file=selected_env)
        if not variflight.enabled:
            checks.append(
                ReadinessCheck(
                    key="variflight",
                    label="VariFlight MCP",
                    status=CheckStatus.WARN,
                    detail="Disabled; LIVE planning will use an explicit flight fallback",
                )
            )
        else:
            variflight_ok = executable_lookup(variflight.command) is not None
            checks.append(
                ReadinessCheck(
                    key="variflight",
                    label="VariFlight MCP",
                    status=CheckStatus.PASS if variflight_ok else CheckStatus.FAIL,
                    detail=(
                        f"Credential configured with pinned package {variflight.package_spec}"
                        if variflight_ok
                        else "Enabled but npx is unavailable; install Node.js 18+"
                    ),
                    required_for=("LIVE",),
                )
            )
    except ConfigurationError as error:
        checks.append(_configuration_failure("variflight", "VariFlight MCP", error))

    llm_ok = False
    try:
        deepseek = DeepSeekSettings.from_env(environ=environ, env_file=selected_env)
        llm_ok = deepseek.enabled
        checks.append(
            ReadinessCheck(
                key="deepseek",
                label="DeepSeek interpreter",
                status=CheckStatus.PASS if llm_ok else CheckStatus.WARN,
                detail=(
                    f"Enabled with model {deepseek.model}"
                    if llm_ok
                    else "Disabled; deterministic interpretation remains available"
                ),
            )
        )
    except ConfigurationError as error:
        checks.append(_configuration_failure("deepseek", "DeepSeek interpreter", error))

    demo_ready = python_ok and runtime_ok
    live_ready = demo_ready and amap_ok and not any(
        check.status == CheckStatus.FAIL and "LIVE" in check.required_for for check in checks
    )
    provider_ready_count = sum((amap_ok, railway_ok, variflight_ok))
    return ReadinessReport(
        version=__version__,
        demo_ready=demo_ready,
        live_ready=live_ready,
        llm_ready=llm_ok,
        provider_ready_count=provider_ready_count,
        provider_total_count=3,
        checks=tuple(checks),
    )


async def inspect_live_readiness(
    *,
    environ: Mapping[str, str] | None = None,
    env_file: Path | None = None,
    executable_lookup: ExecutableLookup = shutil.which,
) -> ReadinessReport:
    """Extend local readiness with query-only MCP capability probes."""

    selected_env = _resolve_env_file(environ, env_file)
    base = inspect_readiness(
        environ=environ,
        env_file=selected_env,
        executable_lookup=executable_lookup,
    )
    probes = await asyncio.gather(
        _probe_amap(environ=environ, env_file=selected_env),
        _probe_railway(environ=environ, env_file=selected_env),
        _probe_variflight(environ=environ, env_file=selected_env),
    )

    all_checks = (*base.checks, *probes)
    live_ready = base.live_ready and not any(
        check.status == CheckStatus.FAIL for check in probes
    )
    return base.model_copy(
        update={"live_ready": live_ready, "live_probed": True, "checks": all_checks}
    )


async def _probe_amap(
    *, environ: Mapping[str, str] | None, env_file: Path
) -> ReadinessCheck:
    try:
        tools = await AmapProvider.from_settings(
            AmapSettings.from_env(environ=environ, env_file=env_file)
        ).verify_capabilities()
        return _probe_success("amap_probe", "AMap live probe", len(tools))
    except (
        AmapProviderError,
        ConfigurationError,
        McpGatewayError,
        OSError,
        TimeoutError,
        ValueError,
    ) as error:
        return _probe_failure("amap_probe", "AMap live probe", error)


async def _probe_railway(
    *, environ: Mapping[str, str] | None, env_file: Path
) -> ReadinessCheck:
    try:
        railway = RailwaySettings.from_env(environ=environ, env_file=env_file)
        if not railway.enabled:
            return _probe_skipped("railway_probe", "12306 live probe")
        tools = await RailwayProvider.from_settings(railway).verify_capabilities()
        return _probe_success("railway_probe", "12306 live probe", len(tools))
    except (
        ConfigurationError,
        McpGatewayError,
        OSError,
        RailwayProviderError,
        TimeoutError,
        ValueError,
    ) as error:
        return _probe_failure("railway_probe", "12306 live probe", error)


async def _probe_variflight(
    *, environ: Mapping[str, str] | None, env_file: Path
) -> ReadinessCheck:
    try:
        variflight = VariflightSettings.from_env(environ=environ, env_file=env_file)
        if not variflight.enabled:
            return _probe_skipped("variflight_probe", "VariFlight live probe")
        tools = await VariflightProvider.from_settings(variflight).verify_capabilities()
        return _probe_success("variflight_probe", "VariFlight live probe", len(tools))
    except (
        ConfigurationError,
        McpGatewayError,
        OSError,
        TimeoutError,
        ValueError,
        VariflightProviderError,
    ) as error:
        return _probe_failure("variflight_probe", "VariFlight live probe", error)


def _append_settings_check(
    checks: list[ReadinessCheck],
    *,
    key: str,
    label: str,
    required_for: tuple[str, ...],
    loader: Callable[[], object],
    success_detail: str,
) -> bool:
    try:
        loader()
    except ConfigurationError as error:
        checks.append(_configuration_failure(key, label, error, required_for))
        return False
    checks.append(
        ReadinessCheck(
            key=key,
            label=label,
            status=CheckStatus.PASS,
            detail=success_detail,
            required_for=required_for,
        )
    )
    return True


def _resolve_env_file(
    environ: Mapping[str, str] | None, env_file: Path | None
) -> Path:
    if env_file is not None:
        return env_file
    source = os.environ if environ is None else environ
    return Path(source.get("TRIPWEAVER_ENV_FILE", ".env"))


def _configuration_failure(
    key: str,
    label: str,
    error: Exception,
    required_for: tuple[str, ...] = ("LIVE",),
) -> ReadinessCheck:
    return ReadinessCheck(
        key=key,
        label=label,
        status=CheckStatus.FAIL,
        detail=f"Configuration invalid: {error}",
        required_for=required_for,
    )


def _probe_success(key: str, label: str, tool_count: int) -> ReadinessCheck:
    return ReadinessCheck(
        key=key,
        label=label,
        status=CheckStatus.PASS,
        detail=f"Capability discovery succeeded with {tool_count} tools",
        required_for=("LIVE",),
    )


def _probe_failure(key: str, label: str, error: Exception) -> ReadinessCheck:
    return ReadinessCheck(
        key=key,
        label=label,
        status=CheckStatus.FAIL,
        detail=f"Capability discovery failed ({type(error).__name__})",
        required_for=("LIVE",),
    )


def _probe_skipped(key: str, label: str) -> ReadinessCheck:
    return ReadinessCheck(
        key=key,
        label=label,
        status=CheckStatus.WARN,
        detail="Probe skipped because provider is disabled",
    )
