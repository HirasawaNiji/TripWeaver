from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

from tripweaver.cli import main


class CliTests(unittest.TestCase):
    def test_demo_summary_is_explicitly_marked_as_fixture(self) -> None:
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = main(["demo"])

        self.assertEqual(exit_code, 0)
        self.assertIn("PHASE 1 FIXTURE DEMO", output.getvalue())
        self.assertIn("验证通过: 是", output.getvalue())
        self.assertIn("预计总费用: CNY", output.getvalue())
        self.assertIn("不代表实时班次", output.getvalue())


if __name__ == "__main__":
    unittest.main()
