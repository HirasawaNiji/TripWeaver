from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tripweaver.config import (
    AmapSettings,
    ConfigurationError,
    DeepSeekSettings,
    RailwaySettings,
    VariflightSettings,
)


class DeepSeekSettingsTests(unittest.TestCase):
    def test_loads_enabled_provider_without_exposing_key(self) -> None:
        settings = DeepSeekSettings.from_env(
            environ={
                "DEEPSEEK_ENABLED": "true",
                "DEEPSEEK_API_KEY": "private-deepseek-key",
            },
            env_file=Path("missing.env"),
        )

        self.assertTrue(settings.enabled)
        self.assertEqual(settings.model, "deepseek-v4-flash")
        self.assertEqual(settings.base_url, "https://api.deepseek.com")
        self.assertNotIn("private-deepseek-key", repr(settings))

    def test_enabled_provider_requires_key(self) -> None:
        with self.assertRaises(ConfigurationError):
            DeepSeekSettings.from_env(
                environ={"DEEPSEEK_ENABLED": "true"},
                env_file=Path("missing.env"),
            )


class AmapSettingsTests(unittest.TestCase):
    def test_loads_dotenv_without_exposing_key_in_repr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "AMAP_MAPS_API_KEY=secret-value\n"
                "AMAP_MCP_TIMEOUT_SECONDS=12\n"
                "AMAP_MCP_MAX_RETRIES=2\n",
                encoding="utf-8",
            )

            settings = AmapSettings.from_env(environ={}, env_file=env_file)

        self.assertEqual(settings.api_key, "secret-value")
        self.assertEqual(settings.timeout_seconds, 12)
        self.assertEqual(settings.max_retries, 2)
        self.assertEqual(settings.min_interval_seconds, 0.5)
        self.assertNotIn("secret-value", repr(settings))
        self.assertIn("key=secret-value", settings.endpoint_url)

    def test_process_environment_overrides_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("AMAP_MAPS_API_KEY=file-key\n", encoding="utf-8")

            settings = AmapSettings.from_env(
                environ={"AMAP_MAPS_API_KEY": "process-key"},
                env_file=env_file,
            )

        self.assertEqual(settings.api_key, "process-key")

    def test_rejects_missing_or_placeholder_key(self) -> None:
        with self.assertRaises(ConfigurationError):
            AmapSettings.from_env(environ={}, env_file=Path("missing.env"))
        with self.assertRaises(ConfigurationError):
            AmapSettings.from_env(
                environ={"AMAP_MAPS_API_KEY": "your_amap_web_service_key_here"},
                env_file=Path("missing.env"),
            )

    def test_rejects_invalid_runtime_policy_without_echoing_value(self) -> None:
        with self.assertRaises(ConfigurationError) as context:
            AmapSettings.from_env(
                environ={
                    "AMAP_MAPS_API_KEY": "secret",
                    "AMAP_MCP_MAX_RETRIES": "many",
                },
                env_file=Path("missing.env"),
            )

        self.assertNotIn("many", str(context.exception))


class RailwaySettingsTests(unittest.TestCase):
    def test_uses_pinned_query_only_defaults(self) -> None:
        settings = RailwaySettings.from_env(environ={}, env_file=Path("missing.env"))

        self.assertTrue(settings.enabled)
        self.assertEqual(settings.package_spec, "12306-mcp@0.3.10")
        self.assertEqual(settings.args, ("-y", "12306-mcp@0.3.10"))
        self.assertEqual(settings.candidate_limit, 20)

    def test_can_disable_and_rejects_arbitrary_package(self) -> None:
        settings = RailwaySettings.from_env(
            environ={"RAILWAY_MCP_ENABLED": "false"},
            env_file=Path("missing.env"),
        )
        self.assertFalse(settings.enabled)

        with self.assertRaises(ConfigurationError):
            RailwaySettings.from_env(
                environ={"RAILWAY_MCP_PACKAGE": "malicious-package"},
                env_file=Path("missing.env"),
            )


class VariflightSettingsTests(unittest.TestCase):
    def test_key_enables_provider_without_exposing_secret(self) -> None:
        settings = VariflightSettings.from_env(
            environ={"VARIFLIGHT_API_KEY": "private-flight-key"},
            env_file=Path("missing.env"),
        )

        self.assertTrue(settings.enabled)
        self.assertEqual(settings.api_key, "private-flight-key")
        self.assertEqual(
            settings.args,
            ("-y", "@variflight-ai/variflight-mcp@1.0.3"),
        )
        self.assertNotIn("private-flight-key", repr(settings))

    def test_explicit_disable_accepts_placeholder_but_rejects_package_injection(self) -> None:
        settings = VariflightSettings.from_env(
            environ={
                "VARIFLIGHT_MCP_ENABLED": "false",
                "VARIFLIGHT_API_KEY": "your_variflight_api_key_here",
            },
            env_file=Path("missing.env"),
        )
        self.assertFalse(settings.enabled)
        self.assertEqual(settings.api_key, "")

        with self.assertRaises(ConfigurationError):
            VariflightSettings.from_env(
                environ={
                    "VARIFLIGHT_MCP_ENABLED": "false",
                    "VARIFLIGHT_MCP_PACKAGE": "untrusted-package",
                },
                env_file=Path("missing.env"),
            )


if __name__ == "__main__":
    unittest.main()
