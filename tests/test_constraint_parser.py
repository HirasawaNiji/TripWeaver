from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from tripweaver.domain.models import TransportMode
from tripweaver.llm.constraint_parser import (
    DeterministicConstraintParser,
    RequestParseError,
)


class DeterministicConstraintParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = DeterministicConstraintParser()

    def test_parses_supported_chinese_request(self) -> None:
        request = self.parser.parse(
            "从北京去上海玩3天，2026-10-01出发，2个人，预算5000元，喜欢历史和夜景"
        )

        self.assertEqual(request.origin, "北京")
        self.assertEqual(request.destination, "上海")
        self.assertEqual(request.start_date, date(2026, 10, 1))
        self.assertEqual(request.end_date, date(2026, 10, 3))
        self.assertEqual(request.travelers, 2)
        self.assertEqual(request.budget_cny, Decimal(5000))
        self.assertIn("历史文化", request.interests)
        self.assertIn("城市景观", request.interests)

    def test_supports_budget_units_and_transport_preference(self) -> None:
        request = self.parser.parse("从北京去上海旅游2天，1人，预算1万元，只坐高铁")

        self.assertEqual(request.budget_cny, Decimal(10000))
        self.assertEqual(request.preferred_transport, (TransportMode.RAIL,))
        self.assertTrue(request.assumptions)

    def test_rejects_ambiguous_route(self) -> None:
        with self.assertRaises(RequestParseError):
            self.parser.parse("帮我规划一次三日游")


if __name__ == "__main__":
    unittest.main()
