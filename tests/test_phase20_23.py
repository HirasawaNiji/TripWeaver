from __future__ import annotations

import unittest
from typing import cast

from fastapi.testclient import TestClient

from tripweaver.api import create_app
from tripweaver.application.hybrid_service import (
    HybridPlanningContext,
    HybridTripPlanningService,
)
from tripweaver.config import DeepSeekSettings
from tripweaver.conversation import HybridConversationPlanningService, SessionMode
from tripweaver.evaluation import AgentEvaluationRunner, default_agent_cases
from tripweaver.fixtures.catalog import FixtureCatalog
from tripweaver.llm.constraint_parser import DeterministicConstraintParser

REQUEST = (
    "我想从广州去成都玩4天，2026-10-01出发，2个人，预算10000元，"
    "喜欢历史文化和美食，高铁或飞机都可以。"
)


class _CountingHybrid:
    def __init__(self) -> None:
        self.prepare_count = 0

    async def prepare(self, request: object) -> HybridPlanningContext:
        self.prepare_count += 1
        parsed = DeterministicConstraintParser().parse(REQUEST)
        assert request == parsed
        return HybridPlanningContext(
            request=parsed,
            catalog=FixtureCatalog(),
            live_map_used=False,
            fallback_reason="TEST_FALLBACK",
            warnings=("测试快照",),
        )


class LiveSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_session_reuses_then_explicitly_refreshes_snapshot(self) -> None:
        hybrid = _CountingHybrid()
        service = HybridConversationPlanningService(
            cast(HybridTripPlanningService, hybrid)
        )
        request = DeterministicConstraintParser().parse(REQUEST)

        created = await service.create(request, mode=SessionMode.LIVE)
        first_snapshot_id = created.snapshot.id if created.snapshot else None
        service.select(created.id, 2)
        revised = service.revise(created.id, "第二天第一个景点换掉")

        self.assertEqual(hybrid.prepare_count, 1)
        self.assertEqual(revised.data_fetch_count, 1)
        refreshed = await service.refresh(created.id)
        self.assertEqual(hybrid.prepare_count, 2)
        self.assertEqual(refreshed.data_fetch_count, 2)
        self.assertNotEqual(refreshed.snapshot.id if refreshed.snapshot else None, first_snapshot_id)
        self.assertEqual(refreshed.snapshot.refresh_count if refreshed.snapshot else None, 1)
        self.assertGreater(service.trace_summary(created.id).tool_calls, 0)


def test_demo_api_exposes_snapshot_trace_and_structured_conflict() -> None:
    client = TestClient(create_app(llm_settings=DeepSeekSettings()))
    created = client.post("/v2/sessions", json={"text": REQUEST, "mode": "DEMO"})
    assert created.status_code == 200
    document = created.json()
    assert document["mode"] == "DEMO"
    assert document["snapshot"]["providers"][0]["status"] == "FIXTURE"

    trace = client.get(f"/v2/sessions/{document['id']}/trace")
    assert trace.status_code == 200
    assert trace.json()["total_steps"] >= 3

    conflict = client.post(
        "/v2/sessions",
        json={
            "text": "从广州去成都玩4天，2026-10-01出发，2个人，预算500元，高铁或飞机都可以",
            "mode": "DEMO",
        },
    )
    assert conflict.status_code == 409
    detail = conflict.json()["detail"]
    assert detail["code"] == "BUDGET_SHORTFALL"
    assert float(detail["shortfall_cny"]) > 0
    assert detail["suggestions"]


def test_multi_turn_agent_evaluation_reports_reliable_baseline() -> None:
    report = AgentEvaluationRunner().run(default_agent_cases()[:4])

    assert report.total_cases == 4
    assert report.passed_cases == 4
    assert report.structured_request_success_rate == 1
    assert report.revision_intent_accuracy == 1
    assert report.hard_constraint_satisfaction_rate == 1
    assert report.replan_preservation_rate == 1
    assert report.snapshot_reuse_rate == 1


if __name__ == "__main__":
    unittest.main()
