from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from tripweaver.agent import AgentRunStatus, ControlledTravelAgent
from tripweaver.api import create_app
from tripweaver.application.hybrid_service import HybridPlanResult
from tripweaver.application.service import TripPlanningService
from tripweaver.evaluation import EvaluationRunner, default_cases
from tripweaver.runtime import MetricsStore, SQLitePlanCache

COMPLETE_REQUEST = (
    "从北京去上海玩3天，2026-10-01出发，2个人，预算8000元，喜欢历史文化和城市夜景，高铁或飞机都可以"
)


class _FixtureHybridPlanner:
    async def plan(self, request: object) -> HybridPlanResult:
        result = TripPlanningService().plan(request)  # type: ignore[arg-type]
        return HybridPlanResult(plan=result, live_map_used=False)


class ControlledAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_constraints_do_not_call_planner(self) -> None:
        agent = ControlledTravelAgent(_FixtureHybridPlanner())

        run = await agent.run("帮我规划从北京去上海玩")

        self.assertEqual(run.status, AgentRunStatus.NEEDS_INPUT)
        self.assertGreaterEqual(len(run.questions), 4)
        self.assertIsNone(run.result)

    async def test_explanation_is_derived_from_validated_plan(self) -> None:
        run = await ControlledTravelAgent(_FixtureHybridPlanner()).run(COMPLETE_REQUEST)

        self.assertEqual(run.status, AgentRunStatus.COMPLETED)
        self.assertIsNotNone(run.explanation)
        assert run.result is not None and run.explanation is not None
        self.assertIn(
            str(run.result.plan.itinerary.budget.total_cny), run.explanation.budget_statement
        )
        self.assertTrue(run.result.plan.validation.feasible)


class RuntimeAndEvaluationTests(unittest.TestCase):
    def test_cache_marks_live_sources_and_metrics_are_aggregate_only(self) -> None:
        result = HybridPlanResult(
            plan=TripPlanningService().plan_text(COMPLETE_REQUEST), live_map_used=False
        )
        request = result.plan.request
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.db"
            cache = SQLitePlanCache(path, ttl_seconds=30)
            cache.put(request, result)

            cached = cache.get(request)

            self.assertIsNotNone(cached)
            assert cached is not None
            self.assertTrue(cached.cache_hit)
            metrics = MetricsStore(path)
            metrics.record(
                success=True,
                cache_hit=True,
                live_map=False,
                live_rail=False,
                live_flight=False,
                latency_ms=1.5,
            )
            self.assertEqual(metrics.summary().cache_hits, 1)

    def test_fixed_suite_has_40_cases_and_is_reproducible(self) -> None:
        cases = default_cases()
        report = EvaluationRunner().run(cases)

        self.assertEqual(len(cases), 40)
        self.assertEqual(report.total_cases, 40)
        self.assertEqual(report.deterministic_stability_rate, 1)
        self.assertEqual(report.source_completeness_rate, 1)
        self.assertEqual(report.passed_cases, 40)


class ApiTests(unittest.TestCase):
    def test_health_and_fixture_plan(self) -> None:
        client = TestClient(create_app())

        health = client.get("/health")
        plan = client.post("/v1/plans/fixture", json={"text": COMPLETE_REQUEST})

        self.assertEqual(health.status_code, 200)
        self.assertFalse(health.json()["booking_enabled"])
        self.assertEqual(plan.status_code, 200)
        self.assertTrue(plan.json()["validation"]["feasible"])

    def test_agent_clarification_does_not_require_credentials(self) -> None:
        client = TestClient(create_app())

        response = client.post("/v1/agent/runs", json={"text": "从北京去上海玩"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "NEEDS_INPUT")


if __name__ == "__main__":
    unittest.main()
