"""Secret-safe runtime configuration loaded from environment variables."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlencode


class ConfigurationError(RuntimeError):
    """Raised when required runtime configuration is absent or invalid."""


@dataclass(frozen=True)
class DeepSeekSettings:
    """Optional DeepSeek interpreter; secrets are never logged or persisted."""

    api_key: str = field(default="", repr=False)
    model: str = "deepseek-v4-flash"
    base_url: str = "https://api.deepseek.com"
    enabled: bool = False

    @classmethod
    def from_env(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        env_file: Path | None = None,
    ) -> DeepSeekSettings:
        source = os.environ if environ is None else environ
        configured_path = source.get("TRIPWEAVER_ENV_FILE")
        file_values = _read_dotenv(env_file or Path(configured_path or ".env"))

        def value(name: str, default: str | None = None) -> str | None:
            return source.get(name, file_values.get(name, default))

        api_key = (value("DEEPSEEK_API_KEY") or "").strip()
        enabled = _read_bool("DEEPSEEK_ENABLED", value("DEEPSEEK_ENABLED", "false"))
        if enabled and not api_key:
            raise ConfigurationError(
                "DEEPSEEK_API_KEY is required when DEEPSEEK_ENABLED=true"
            )
        model = (value("DEEPSEEK_MODEL", "deepseek-v4-flash") or "").strip()
        if not re.fullmatch(r"[a-zA-Z0-9._-]{2,80}", model):
            raise ConfigurationError("DEEPSEEK_MODEL contains invalid characters")
        base_url = (value("DEEPSEEK_BASE_URL", "https://api.deepseek.com") or "").strip()
        if not re.fullmatch(r"https://[a-zA-Z0-9.-]+(?::\d{1,5})?(?:/[a-zA-Z0-9._/-]*)?", base_url):
            raise ConfigurationError("DEEPSEEK_BASE_URL must be a valid HTTPS URL")
        return cls(api_key=api_key, model=model, base_url=base_url.rstrip("/"), enabled=enabled)


@dataclass(frozen=True)
class AmapSettings:
    """Runtime policy for the official AMap Streamable HTTP MCP server."""

    api_key: str = field(repr=False)
    timeout_seconds: float = 20.0
    max_retries: int = 1
    max_concurrency: int = 4
    min_interval_seconds: float = 0.5

    @property
    def endpoint_url(self) -> str:
        return "https://mcp.amap.com/mcp?" + urlencode({"key": self.api_key})

    @classmethod
    def from_env(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        env_file: Path | None = None,
    ) -> AmapSettings:
        source = os.environ if environ is None else environ
        configured_path = source.get("TRIPWEAVER_ENV_FILE")
        path = env_file or Path(configured_path or ".env")
        file_values = _read_dotenv(path)

        def value(name: str, default: str | None = None) -> str | None:
            return source.get(name, file_values.get(name, default))

        api_key = (value("AMAP_MAPS_API_KEY") or "").strip()
        if not api_key or api_key == "your_amap_web_service_key_here":
            raise ConfigurationError(
                "AMAP_MAPS_API_KEY is missing; configure it in the environment or .env"
            )
        timeout_seconds = _read_float(
            "AMAP_MCP_TIMEOUT_SECONDS", value("AMAP_MCP_TIMEOUT_SECONDS", "20")
        )
        max_retries = _read_int("AMAP_MCP_MAX_RETRIES", value("AMAP_MCP_MAX_RETRIES", "1"))
        max_concurrency = _read_int(
            "AMAP_MCP_MAX_CONCURRENCY", value("AMAP_MCP_MAX_CONCURRENCY", "4")
        )
        min_interval_seconds = _read_float(
            "AMAP_MCP_MIN_INTERVAL_SECONDS",
            value("AMAP_MCP_MIN_INTERVAL_SECONDS", "0.5"),
        )
        if not 1 <= timeout_seconds <= 300:
            raise ConfigurationError("AMAP_MCP_TIMEOUT_SECONDS must be between 1 and 300")
        if not 0 <= max_retries <= 5:
            raise ConfigurationError("AMAP_MCP_MAX_RETRIES must be between 0 and 5")
        if not 1 <= max_concurrency <= 20:
            raise ConfigurationError("AMAP_MCP_MAX_CONCURRENCY must be between 1 and 20")
        if not 0 <= min_interval_seconds <= 5:
            raise ConfigurationError("AMAP_MCP_MIN_INTERVAL_SECONDS must be between 0 and 5")
        return cls(
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            max_concurrency=max_concurrency,
            min_interval_seconds=min_interval_seconds,
        )


@dataclass(frozen=True)
class RailwaySettings:
    """Runtime policy for the query-only community 12306 MCP server."""

    enabled: bool = True
    package_spec: str = "12306-mcp@0.3.10"
    timeout_seconds: float = 40.0
    max_retries: int = 1
    max_concurrency: int = 2
    candidate_limit: int = 20

    @property
    def command(self) -> str:
        return "npx.cmd" if os.name == "nt" else "npx"

    @property
    def args(self) -> tuple[str, ...]:
        return ("-y", self.package_spec)

    @classmethod
    def from_env(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        env_file: Path | None = None,
    ) -> RailwaySettings:
        source = os.environ if environ is None else environ
        configured_path = source.get("TRIPWEAVER_ENV_FILE")
        path = env_file or Path(configured_path or ".env")
        file_values = _read_dotenv(path)

        def value(name: str, default: str | None = None) -> str | None:
            return source.get(name, file_values.get(name, default))

        enabled = _read_bool(
            "RAILWAY_MCP_ENABLED",
            value("RAILWAY_MCP_ENABLED", "true"),
        )
        package_spec = (value("RAILWAY_MCP_PACKAGE", "12306-mcp@0.3.10") or "").strip()
        if not re.fullmatch(r"12306-mcp(?:@\d+\.\d+\.\d+)?", package_spec):
            raise ConfigurationError(
                "RAILWAY_MCP_PACKAGE must be 12306-mcp or a pinned semantic version"
            )
        timeout_seconds = _read_float(
            "RAILWAY_MCP_TIMEOUT_SECONDS",
            value("RAILWAY_MCP_TIMEOUT_SECONDS", "40"),
        )
        max_retries = _read_int(
            "RAILWAY_MCP_MAX_RETRIES",
            value("RAILWAY_MCP_MAX_RETRIES", "1"),
        )
        max_concurrency = _read_int(
            "RAILWAY_MCP_MAX_CONCURRENCY",
            value("RAILWAY_MCP_MAX_CONCURRENCY", "2"),
        )
        candidate_limit = _read_int(
            "RAILWAY_MCP_CANDIDATE_LIMIT",
            value("RAILWAY_MCP_CANDIDATE_LIMIT", "20"),
        )
        if not 5 <= timeout_seconds <= 300:
            raise ConfigurationError("RAILWAY_MCP_TIMEOUT_SECONDS must be between 5 and 300")
        if not 0 <= max_retries <= 3:
            raise ConfigurationError("RAILWAY_MCP_MAX_RETRIES must be between 0 and 3")
        if not 1 <= max_concurrency <= 4:
            raise ConfigurationError("RAILWAY_MCP_MAX_CONCURRENCY must be between 1 and 4")
        if not 1 <= candidate_limit <= 50:
            raise ConfigurationError("RAILWAY_MCP_CANDIDATE_LIMIT must be between 1 and 50")
        return cls(
            enabled=enabled,
            package_spec=package_spec,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            max_concurrency=max_concurrency,
            candidate_limit=candidate_limit,
        )


@dataclass(frozen=True)
class VariflightSettings:
    """Secret-safe runtime policy for the official VariFlight MCP package."""

    api_key: str = field(default="", repr=False)
    enabled: bool = True
    package_spec: str = "@variflight-ai/variflight-mcp@1.0.3"
    timeout_seconds: float = 40.0
    max_retries: int = 1
    max_concurrency: int = 2
    candidate_limit: int = 80

    @property
    def command(self) -> str:
        return "npx.cmd" if os.name == "nt" else "npx"

    @property
    def args(self) -> tuple[str, ...]:
        return ("-y", self.package_spec)

    @classmethod
    def from_env(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        env_file: Path | None = None,
    ) -> VariflightSettings:
        source = os.environ if environ is None else environ
        configured_path = source.get("TRIPWEAVER_ENV_FILE")
        path = env_file or Path(configured_path or ".env")
        file_values = _read_dotenv(path)

        def value(name: str, default: str | None = None) -> str | None:
            return source.get(name, file_values.get(name, default))

        api_key = (value("VARIFLIGHT_API_KEY") or "").strip()
        key_is_valid = bool(api_key) and api_key != "your_variflight_api_key_here"
        enabled_value = value("VARIFLIGHT_MCP_ENABLED")
        enabled = (
            key_is_valid
            if enabled_value is None
            else _read_bool("VARIFLIGHT_MCP_ENABLED", enabled_value)
        )
        if enabled and not key_is_valid:
            raise ConfigurationError(
                "VARIFLIGHT_API_KEY is missing; configure it in the environment or .env"
            )
        package_spec = (
            value(
                "VARIFLIGHT_MCP_PACKAGE",
                "@variflight-ai/variflight-mcp@1.0.3",
            )
            or ""
        ).strip()
        if not re.fullmatch(
            r"@variflight-ai/variflight-mcp(?:@\d+\.\d+\.\d+)?",
            package_spec,
        ):
            raise ConfigurationError(
                "VARIFLIGHT_MCP_PACKAGE must be the official package or a pinned semantic version"
            )
        timeout_seconds = _read_float(
            "VARIFLIGHT_MCP_TIMEOUT_SECONDS",
            value("VARIFLIGHT_MCP_TIMEOUT_SECONDS", "40"),
        )
        max_retries = _read_int(
            "VARIFLIGHT_MCP_MAX_RETRIES",
            value("VARIFLIGHT_MCP_MAX_RETRIES", "1"),
        )
        max_concurrency = _read_int(
            "VARIFLIGHT_MCP_MAX_CONCURRENCY",
            value("VARIFLIGHT_MCP_MAX_CONCURRENCY", "2"),
        )
        candidate_limit = _read_int(
            "VARIFLIGHT_MCP_CANDIDATE_LIMIT",
            value("VARIFLIGHT_MCP_CANDIDATE_LIMIT", "80"),
        )
        if not 5 <= timeout_seconds <= 300:
            raise ConfigurationError("VARIFLIGHT_MCP_TIMEOUT_SECONDS must be between 5 and 300")
        if not 0 <= max_retries <= 3:
            raise ConfigurationError("VARIFLIGHT_MCP_MAX_RETRIES must be between 0 and 3")
        if not 1 <= max_concurrency <= 4:
            raise ConfigurationError("VARIFLIGHT_MCP_MAX_CONCURRENCY must be between 1 and 4")
        if not 1 <= candidate_limit <= 200:
            raise ConfigurationError("VARIFLIGHT_MCP_CANDIDATE_LIMIT must be between 1 and 200")
        return cls(
            api_key=api_key if key_is_valid else "",
            enabled=enabled,
            package_spec=package_spec,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            max_concurrency=max_concurrency,
            candidate_limit=candidate_limit,
        )


@dataclass(frozen=True)
class LodgingSettings:
    """Honest lodging policy: AMap location facts plus optional user price input."""

    nightly_price_cny: Decimal | None = None
    candidate_limit: int = 3

    @classmethod
    def from_env(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        env_file: Path | None = None,
    ) -> LodgingSettings:
        source = os.environ if environ is None else environ
        configured_path = source.get("TRIPWEAVER_ENV_FILE")
        path = env_file or Path(configured_path or ".env")
        file_values = _read_dotenv(path)

        def value(name: str, default: str | None = None) -> str | None:
            return source.get(name, file_values.get(name, default))

        raw_price = (value("LODGING_NIGHTLY_PRICE_CNY") or "").strip()
        nightly_price: Decimal | None = None
        if raw_price:
            try:
                nightly_price = Decimal(raw_price)
            except InvalidOperation as error:
                raise ConfigurationError("LODGING_NIGHTLY_PRICE_CNY must be numeric") from error
            if not Decimal(50) <= nightly_price <= Decimal(10000):
                raise ConfigurationError("LODGING_NIGHTLY_PRICE_CNY must be between 50 and 10000")
        candidate_limit = _read_int(
            "LODGING_CANDIDATE_LIMIT", value("LODGING_CANDIDATE_LIMIT", "3")
        )
        if not 1 <= candidate_limit <= 5:
            raise ConfigurationError("LODGING_CANDIDATE_LIMIT must be between 1 and 5")
        return cls(nightly_price_cny=nightly_price, candidate_limit=candidate_limit)


@dataclass(frozen=True)
class RuntimeSettings:
    """Local persistence policy; the database never stores prompts or credentials."""

    database_path: Path = Path(".tripweaver/tripweaver.db")
    cache_enabled: bool = True
    cache_ttl_seconds: int = 90
    metrics_enabled: bool = True

    @classmethod
    def from_env(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        env_file: Path | None = None,
    ) -> RuntimeSettings:
        source = os.environ if environ is None else environ
        configured_path = source.get("TRIPWEAVER_ENV_FILE")
        file_values = _read_dotenv(env_file or Path(configured_path or ".env"))

        def value(name: str, default: str | None = None) -> str | None:
            return source.get(name, file_values.get(name, default))

        raw_path = (value("TRIPWEAVER_DATABASE_PATH", ".tripweaver/tripweaver.db") or "").strip()
        if not raw_path:
            raise ConfigurationError("TRIPWEAVER_DATABASE_PATH must not be empty")
        ttl = _read_int("TRIPWEAVER_CACHE_TTL_SECONDS", value("TRIPWEAVER_CACHE_TTL_SECONDS", "90"))
        if not 10 <= ttl <= 3600:
            raise ConfigurationError("TRIPWEAVER_CACHE_TTL_SECONDS must be between 10 and 3600")
        return cls(
            database_path=Path(raw_path),
            cache_enabled=_read_bool(
                "TRIPWEAVER_CACHE_ENABLED", value("TRIPWEAVER_CACHE_ENABLED", "true")
            ),
            cache_ttl_seconds=ttl,
            metrics_enabled=_read_bool(
                "TRIPWEAVER_METRICS_ENABLED", value("TRIPWEAVER_METRICS_ENABLED", "true")
            ),
        )


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, raw_value = line.split("=", 1)
        name = name.strip()
        if not name or name.startswith("export "):
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[name] = value
    return values


def _read_int(name: str, value: str | None) -> int:
    try:
        return int(value or "")
    except ValueError as error:
        raise ConfigurationError(f"{name} must be an integer") from error


def _read_float(name: str, value: str | None) -> float:
    try:
        return float(value or "")
    except ValueError as error:
        raise ConfigurationError(f"{name} must be numeric") from error


def _read_bool(name: str, value: str | None) -> bool:
    normalized = (value or "").strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be a boolean")
