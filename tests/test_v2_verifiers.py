from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from mosaic_harness.candidate import create_candidate, run_role, show_run, verify_candidate
from mosaic_harness.verifiers import select_mutation_target
from mosaic_harness.workflow import challenge, initialize_workspace, investigate


HIDDEN_TOKEN = "MOSAIC-HIDDEN-B-TOKEN-aa91"


def isolation_supported() -> bool:
    return sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file()


class MosaicPhaseBTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repository"
        (self.repository / "src").mkdir(parents=True)
        (self.repository / "tests").mkdir()
        (self.repository / ".harness" / "evaluators" / "hidden").mkdir(parents=True)
        (self.repository / "src" / "coupon.py").write_text(
            "def restore_coupon():\n    return True\n",
            encoding="utf-8",
        )
        (self.repository / "tests" / "test_coupon.py").write_text(
            "import unittest\n\n"
            "class CouponTest(unittest.TestCase):\n"
            "    def test_restore_coupon(self) -> None:\n"
            "        from coupon import restore_coupon\n"
            "        self.assertTrue(restore_coupon())\n",
            encoding="utf-8",
        )
        (self.repository / ".harness" / "evaluators" / "hidden" / "holdout.py").write_text(
            "from pathlib import Path\n"
            f"token = {HIDDEN_TOKEN!r}\n"
            "text = Path('src/coupon.py').read_text(encoding='utf-8')\n"
            "assert 'LEAK-ME' not in text, token\n",
            encoding="utf-8",
        )
        self.issue = self.root / "issue.md"
        self.issue.write_text("# hidden holdout\n\nClaim.\n", encoding="utf-8")
        initialize_workspace(self.root)
        investigate(self.root, "ISSUE-B", self.issue, self.repository)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @unittest.skipUnless(isolation_supported(), "V1 process isolation is tested on macOS sandbox-exec")
    def test_hidden_floor_fails_candidate_without_leaking_token(self) -> None:
        from mosaic_harness.candidate import build_candidate

        code = create_candidate(self.root, "ISSUE-B", self.repository, kind="code")
        script = self.root / "leak.json"
        script.write_text(
            json.dumps(
                [
                    {
                        "op": "write",
                        "path": "src/coupon.py",
                        "contents": "def restore_coupon():\n    return True  # LEAK-ME\n",
                    }
                ]
            ),
            encoding="utf-8",
        )
        build_candidate(self.root, "ISSUE-B", code["run_id"], script)
        verdict = verify_candidate(self.root, "ISSUE-B", code["run_id"])
        self.assertFalse(verdict["floor_passed"])
        self.assertIs(verdict["floors"]["hidden"], False)
        shown = show_run(self.root, "ISSUE-B", code["run_id"])
        dumped = json.dumps(shown)
        self.assertNotIn(HIDDEN_TOKEN, dumped)
        hidden_file = self.repository / ".harness" / "evaluators" / "hidden" / "holdout.py"
        denied = run_role(
            self.root,
            "ISSUE-B",
            code["run_id"],
            role="builder",
            command=[sys.executable, "-c", f"print(open({str(hidden_file)!r}).read())"],
        )
        self.assertNotEqual(denied["exit_code"], 0)
        self.assertNotIn(HIDDEN_TOKEN, denied.get("stdout", ""))

    @unittest.skipUnless(isolation_supported(), "V1 process isolation is tested on macOS sandbox-exec")
    def test_weak_public_tests_fail_mutation_floor(self) -> None:
        (self.repository / "tests" / "test_coupon.py").write_text(
            "import unittest\n\n"
            "class CouponTest(unittest.TestCase):\n"
            "    def test_always(self) -> None:\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        investigate(self.root, "ISSUE-WEAK", self.issue, self.repository)
        zero = create_candidate(self.root, "ISSUE-WEAK", self.repository, kind="zero-change")
        verdict = verify_candidate(self.root, "ISSUE-WEAK", zero["run_id"])
        self.assertIs(verdict["floors"]["mutation"], False)
        self.assertFalse(verdict["floor_passed"])

    @unittest.skipUnless(isolation_supported(), "V1 process isolation is tested on macOS sandbox-exec")
    def test_verify_does_not_mutate_candidate_and_shares_floor_definition(self) -> None:
        zero = create_candidate(self.root, "ISSUE-B", self.repository, kind="zero-change")
        code = create_candidate(self.root, "ISSUE-B", self.repository, kind="code")
        before = Path(code["workspace_root"]).joinpath("src/coupon.py").read_text(encoding="utf-8")
        zero_verdict = verify_candidate(self.root, "ISSUE-B", zero["run_id"])
        code_verdict = verify_candidate(self.root, "ISSUE-B", code["run_id"])
        after = Path(code["workspace_root"]).joinpath("src/coupon.py").read_text(encoding="utf-8")
        self.assertEqual(before, after)
        self.assertTrue(code_verdict["candidate_unmodified"])
        self.assertEqual(zero_verdict["floor_definition"], code_verdict["floor_definition"])
        self.assertTrue(zero_verdict["floor_passed"])
        self.assertTrue(code_verdict["floor_passed"])

    def test_mutation_target_prefers_nested_domain_module(self) -> None:
        app = self.root / "nested-app"
        (app / "src" / "spike").mkdir(parents=True)
        (app / "src" / "spike" / "__init__.py").write_text("", encoding="utf-8")
        (app / "src" / "spike" / "__main__.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
        (app / "src" / "spike" / "server.py").write_text("def serve():\n    return 0\n", encoding="utf-8")
        (app / "src" / "spike" / "store.py").write_text(
            "def restore_coupon():\n    return True\n", encoding="utf-8"
        )
        chosen = select_mutation_target(app)
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual(chosen.name, "store.py")
        from mosaic_harness.verifiers import select_mutation_targets

        names = [path.name for path in select_mutation_targets(app)]
        self.assertNotIn("__main__.py", names)
        self.assertNotIn("__init__.py", names)

    def test_challenge_emit_evaluators_does_not_write_hidden(self) -> None:
        hidden_before = list((self.root / ".harness" / "evaluators" / "hidden").glob("*"))
        challenge(self.root, "ISSUE-B", emit_evaluators=True)
        self.assertTrue((self.root / ".harness" / "evaluators" / "mutation" / "generated.py").is_file())
        self.assertTrue((self.root / ".harness" / "evaluators" / "property" / "generated.py").is_file())
        self.assertEqual(
            list((self.root / ".harness" / "evaluators" / "hidden").glob("*")),
            hidden_before,
        )


if __name__ == "__main__":
    unittest.main()
