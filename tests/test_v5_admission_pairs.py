from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from mosaic_harness.admission import AdmissionError
from mosaic_harness.executor import load_json, manifest_path
from mosaic_harness.pair import PairStateError, prepare_pair, retry_code
from mosaic_harness.pair_verifier import compare_pair, verify_pair
from mosaic_harness.util import sha256_bytes, sha256_file, utc_now
from mosaic_harness.verification_contract import VerificationContractError
from mosaic_harness.workflow import (
    add_evidence,
    decide,
    initialize_workspace,
    investigate,
    propose,
)
from mosaic_harness.domain import Outcome


class PortableCommandAdapter:
    """Portable subprocess adapter used only to test verification semantics."""

    def run(
        self,
        command: list[str],
        *,
        cwd: Path,
        profile: str,
        budget: dict[str, int],
        env: dict[str, str],
        output_dir: Path | None = None,
        output_name: str = "command",
    ) -> dict[str, object]:
        del profile
        merged = os.environ.copy()
        merged.update(env)
        started = time.monotonic()
        timed_out = False
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=merged,
                capture_output=True,
                text=True,
                timeout=int(budget["max_seconds"]),
                check=False,
            )
            exit_code: int | None = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
        except subprocess.TimeoutExpired as error:
            timed_out = True
            exit_code = None
            stdout = error.stdout or ""
            stderr = error.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
        elapsed = time.monotonic() - started
        stdout_ref = None
        stderr_ref = None
        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
            stdout_path = output_dir / f"{output_name}.stdout.txt"
            stderr_path = output_dir / f"{output_name}.stderr.txt"
            stdout_path.write_text(stdout, encoding="utf-8")
            stderr_path.write_text(stderr, encoding="utf-8")
            stdout_ref = str(stdout_path)
            stderr_ref = str(stderr_path)
        max_output = int(budget["max_output_bytes"])
        return {
            "command": list(command),
            "exit_code": exit_code,
            "stdout": stdout[:max_output],
            "stderr": stderr[:max_output],
            "stdout_sha256": sha256_bytes(stdout.encode("utf-8")),
            "stderr_sha256": sha256_bytes(stderr.encode("utf-8")),
            "stdout_ref": stdout_ref,
            "stderr_ref": stderr_ref,
            "interrupted": timed_out,
            "timed_out": timed_out,
            "denial": "portable denial" if exit_code == 71 else None,
            "budget": {
                "max_seconds": int(budget["max_seconds"]),
                "max_output_bytes": max_output,
                "used_seconds": round(elapsed, 3),
                "used_output_bytes": len(stdout.encode("utf-8"))
                + len(stderr.encode("utf-8")),
            },
            "finished_at": utc_now(),
        }


class PairHarnessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repository"
        (self.repository / "src").mkdir(parents=True)
        (self.repository / "tests").mkdir()
        (self.repository / "src" / "feature.py").write_text(
            "def is_ready():\n    return False\n\n"
            "def stable_value():\n    return 'stable'\n",
            encoding="utf-8",
        )
        (self.repository / "tests" / "test_local.py").write_text(
            "# Candidate-local tests are not an admission floor.\n",
            encoding="utf-8",
        )
        self.issue = self.root / "issue.md"
        self.issue.write_text("# Feature readiness defect\n", encoding="utf-8")
        self.contract_root = self.root / "contract"
        self.contract_root.mkdir()
        self._write_check_scripts()
        self.contract_path = self.contract_root / "verification-contract.json"
        self._write_verification_contract()
        initialize_workspace(self.root)
        investigate(
            self.root,
            "CASE-PAIR",
            self.issue,
            self.repository,
            signal="The public target fails after deployment.",
            rollback_trigger="The preservation check fails.",
        )
        self.adapter = PortableCommandAdapter()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_check_scripts(self, *, mutation_error: bool = False) -> None:
        mutation_clause = (
            "if 'mutation' in Path.cwd().parts and not is_ready():\n"
            "    raise SystemExit(71)\n"
            if mutation_error
            else ""
        )
        (self.contract_root / "target_check.py").write_text(
            "from pathlib import Path\n"
            "from feature import is_ready\n"
            f"{mutation_clause}"
            "raise SystemExit(0 if is_ready() else 1)\n",
            encoding="utf-8",
        )
        (self.contract_root / "preservation_check.py").write_text(
            "from feature import stable_value\n"
            "raise SystemExit(0 if stable_value() == 'stable' else 1)\n",
            encoding="utf-8",
        )

    def _write_verification_contract(
        self,
        *,
        required_adapters: list[str] | None = None,
        hidden: bool = False,
    ) -> None:
        checks: list[dict[str, object]] = [
            {
                "id": "target-ready",
                "role": "target",
                "visibility": "public",
                "argv": [
                    sys.executable,
                    "{frozen_public}/contract/target_check.py",
                ],
                "frozen_inputs": ["target_check.py"],
            },
            {
                "id": "preserve-stable",
                "role": "preservation",
                "visibility": "public",
                "argv": [
                    sys.executable,
                    "{frozen_public}/contract/preservation_check.py",
                ],
                "frozen_inputs": ["preservation_check.py"],
            },
        ]
        if hidden:
            checks.append(
                {
                    "id": "hidden-ready",
                    "role": "target",
                    "visibility": "hidden",
                    "evaluator_id": "holdout.py",
                }
            )
        self.contract_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "contract_id": "VC-PAIR",
                    "checks": checks,
                    "required_adapters": required_adapters or [],
                    "budgets": {"max_seconds": 5, "max_output_bytes": 65536},
                }
            ),
            encoding="utf-8",
        )

    def _prepare_and_edit(self) -> tuple[dict[str, object], Path]:
        pair = prepare_pair(
            self.root,
            "CASE-PAIR",
            self.repository,
            self.contract_path,
        )
        harness_root = self.root / ".harness"
        manifest = load_json(manifest_path(harness_root, str(pair["code_run_id"])))
        workspace = Path(manifest["workspace_root"])
        (workspace / "src" / "feature.py").write_text(
            "def is_ready():\n    return True\n\n"
            "def stable_value():\n    return 'stable'\n",
            encoding="utf-8",
        )
        return pair, workspace

    def _verify(self, pair: dict[str, object]) -> dict[str, object]:
        return verify_pair(
            self.root,
            "CASE-PAIR",
            str(pair["pair_id"]),
            str(pair["code_run_id"]),
            adapter=self.adapter,
            require_real_isolation=False,
        )

    def _support_code_hypothesis(self) -> None:
        add_evidence(
            self.root,
            "CASE-PAIR",
            "H-CODE-DEFECT",
            direction="supporting",
            strength="strong",
            summary="The frozen target reproduces the code defect.",
            source_type="pair",
            source_ref="target-fail-pass",
        )
        for claim_id in (
            "H-EXPECTED-BEHAVIOR",
            "H-OBSERVABILITY-GAP",
            "H-CONFIGURATION",
            "H-DOCUMENTATION",
            "H-OPERATIONAL",
            "H-POLICY-CONFLICT",
            "H-INVALID-REPORT",
        ):
            add_evidence(
                self.root,
                "CASE-PAIR",
                claim_id,
                direction="opposing",
                strength="strong",
                summary=f"The pair refutes {claim_id} within the tested boundary.",
                source_type="pair",
                source_ref=f"refute-{claim_id}",
            )

    def test_frozen_public_floor_proves_target_improvement_and_preservation(self) -> None:
        pair, code_workspace = self._prepare_and_edit()
        (code_workspace / "tests" / "test_override.py").write_text(
            "raise SystemExit(0)\n",
            encoding="utf-8",
        )
        (code_workspace / "tests" / "test_local.py").unlink()
        verdict = self._verify(pair)
        self.assertTrue(verdict["eligible"])
        relations = {item["id"]: item for item in verdict["checks"]}
        self.assertEqual(relations["target-ready"]["zero_status"], "FAIL")
        self.assertEqual(relations["target-ready"]["code_status"], "PASS")
        self.assertEqual(relations["preserve-stable"]["zero_status"], "PASS")
        self.assertEqual(relations["preserve-stable"]["code_status"], "PASS")
        self.assertIn("tests/test_override.py", verdict["measures"]["change_surface"]["added"])
        self.assertIn("tests/test_local.py", verdict["measures"]["change_surface"]["deleted"])
        public_result = verdict["code_results"][0]
        self.assertEqual(
            sha256_file(Path(public_result["stdout_ref"])),
            public_result["stdout_sha256"],
        )
        self.assertIn(str(pair["code_run_id"]), compare_pair(
            self.root, "CASE-PAIR", str(pair["pair_id"])
        )["frontier_run_ids"])
        self.assertIn("return False", (
            self.repository / "src" / "feature.py"
        ).read_text(encoding="utf-8"))

    def test_zero_target_pass_and_required_error_are_not_admissible(self) -> None:
        (self.repository / "src" / "feature.py").write_text(
            "def is_ready():\n    return True\n\n"
            "def stable_value():\n    return 'stable'\n",
            encoding="utf-8",
        )
        # A changed repository needs a new case because the original revision is immutable.
        investigate(
            self.root,
            "CASE-PASS",
            self.issue,
            self.repository,
            signal="Target regression.",
            rollback_trigger="Preservation failure.",
        )
        pair = prepare_pair(
            self.root,
            "CASE-PASS",
            self.repository,
            self.contract_path,
        )
        verdict = verify_pair(
            self.root,
            "CASE-PASS",
            pair["pair_id"],
            pair["code_run_id"],
            adapter=self.adapter,
            require_real_isolation=False,
        )
        self.assertFalse(verdict["eligible"])
        self.assertEqual(verdict["checks"][0]["zero_status"], "PASS")

        (self.repository / "src" / "feature.py").write_text(
            "def is_ready():\n    return False\n\n"
            "def stable_value():\n    return 'stable'\n",
            encoding="utf-8",
        )
        self._write_check_scripts(mutation_error=True)
        self._write_verification_contract(required_adapters=["mutation"])
        investigate(
            self.root,
            "CASE-ERROR",
            self.issue,
            self.repository,
            signal="Target regression.",
            rollback_trigger="Preservation failure.",
        )
        error_pair = prepare_pair(
            self.root,
            "CASE-ERROR",
            self.repository,
            self.contract_path,
        )
        manifest = load_json(
            manifest_path(self.root / ".harness", error_pair["code_run_id"])
        )
        feature = Path(manifest["workspace_root"]) / "src" / "feature.py"
        feature.write_text(
            "def is_ready():\n    return True\n\n"
            "def stable_value():\n    return 'stable'\n",
            encoding="utf-8",
        )
        error_verdict = verify_pair(
            self.root,
            "CASE-ERROR",
            error_pair["pair_id"],
            error_pair["code_run_id"],
            adapter=self.adapter,
            require_real_isolation=False,
        )
        mutation = next(item for item in error_verdict["adapters"] if item["name"] == "mutation")
        self.assertEqual(mutation["status"], "ERROR")
        self.assertFalse(error_verdict["eligible"])

    def test_mutation_runs_every_target_and_survivor_fails_floor(self) -> None:
        (self.repository / "src" / "unused.py").write_text(
            "def unused():\n    return 'unused'\n",
            encoding="utf-8",
        )
        investigate(
            self.root,
            "CASE-MUTATION",
            self.issue,
            self.repository,
            signal="Target regression.",
            rollback_trigger="Preservation failure.",
        )
        self._write_verification_contract(required_adapters=["mutation"])
        pair = prepare_pair(
            self.root,
            "CASE-MUTATION",
            self.repository,
            self.contract_path,
        )
        manifest = load_json(manifest_path(self.root / ".harness", pair["code_run_id"]))
        (Path(manifest["workspace_root"]) / "src" / "feature.py").write_text(
            "def is_ready():\n    return True\n\n"
            "def stable_value():\n    return 'stable'\n",
            encoding="utf-8",
        )
        verdict = verify_pair(
            self.root,
            "CASE-MUTATION",
            pair["pair_id"],
            pair["code_run_id"],
            adapter=self.adapter,
            require_real_isolation=False,
        )
        mutation = next(item for item in verdict["adapters"] if item["name"] == "mutation")
        self.assertEqual(len(mutation["mutants"]), 2)
        self.assertTrue(any(item["status"] == "SURVIVED" for item in mutation["mutants"]))
        self.assertEqual(mutation["status"], "FAIL")
        self.assertFalse(verdict["eligible"])

    def test_hidden_output_is_hash_only_and_not_builder_visible(self) -> None:
        hidden = self.root / ".harness" / "evaluators" / "hidden" / "holdout.py"
        hidden.write_text(
            "from feature import is_ready\n"
            "print('TOP_SECRET_HOLDOUT')\n"
            "raise SystemExit(0 if is_ready() else 1)\n",
            encoding="utf-8",
        )
        self._write_verification_contract(required_adapters=["hidden"], hidden=True)
        pair, code_workspace = self._prepare_and_edit()
        verdict = self._verify(pair)
        rendered = json.dumps(verdict)
        self.assertTrue(verdict["eligible"])
        self.assertNotIn("TOP_SECRET_HOLDOUT", rendered)
        self.assertNotIn(str(hidden), rendered)
        self.assertFalse(any(
            "TOP_SECRET_HOLDOUT" in path.read_text(encoding="utf-8", errors="ignore")
            for path in code_workspace.rglob("*")
            if path.is_file()
        ))

    def test_property_adapter_executes_its_frozen_copy(self) -> None:
        property_script = (
            self.root / ".harness" / "evaluators" / "property" / "ready_property.py"
        )
        property_script.write_text(
            "from feature import is_ready\n"
            "raise SystemExit(0 if is_ready() else 1)\n",
            encoding="utf-8",
        )
        self._write_verification_contract(required_adapters=["property"])
        pair, _ = self._prepare_and_edit()
        property_script.write_text("raise SystemExit(1)\n", encoding="utf-8")
        verdict = self._verify(pair)
        property_result = next(
            item for item in verdict["adapters"] if item["name"] == "property"
        )
        self.assertEqual(property_result["status"], "PASS")
        self.assertTrue(verdict["eligible"])

    def test_tampered_floor_and_candidate_invalidate_verification_or_decision(self) -> None:
        pair, code_workspace = self._prepare_and_edit()
        verdict = self._verify(pair)
        self.assertTrue(verdict["eligible"])
        self._support_code_hypothesis()
        proposed = propose(self.root, "CASE-PAIR")
        proposal_id = proposed["latest_proposal"]["proposal_id"]
        (code_workspace / "src" / "feature.py").write_text(
            "def is_ready():\n    return False\n",
            encoding="utf-8",
        )
        with self.assertRaises(AdmissionError):
            decide(
                self.root,
                "CASE-PAIR",
                Outcome.CODE_CHANGE,
                "Ship the verified change.",
                "human@example.com",
                proposal_id=proposal_id,
            )

        (code_workspace / "src" / "feature.py").write_text(
            "def is_ready():\n    return True\n\n"
            "def stable_value():\n    return 'stable'\n",
            encoding="utf-8",
        )
        retry = retry_code(
            self.root,
            "CASE-PAIR",
            pair["pair_id"],
            pair["code_run_id"],
        )
        frozen_input = (
            self.root
            / ".harness"
            / "experiments"
            / "pairs"
            / pair["pair_id"]
            / "floor"
            / "public"
            / "contract"
            / "target_check.py"
        )
        frozen_input.write_text("raise SystemExit(0)\n", encoding="utf-8")
        with self.assertRaises(PairStateError):
            verify_pair(
                self.root,
                "CASE-PAIR",
                pair["pair_id"],
                retry["run_id"],
                adapter=self.adapter,
                require_real_isolation=False,
            )

    def test_invalid_contract_and_incomplete_copy_fail_closed(self) -> None:
        invalid = self.root / "invalid-contract.json"
        invalid.write_text(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "contract_id": "VC-INVALID",
                    "checks": [
                        {
                            "id": "hidden-only",
                            "role": "target",
                            "visibility": "hidden",
                            "evaluator_id": "holdout.py",
                        }
                    ],
                    "required_adapters": ["hidden"],
                    "budgets": {"max_seconds": 5, "max_output_bytes": 1000},
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(VerificationContractError):
            prepare_pair(
                self.root,
                "CASE-PAIR",
                self.repository,
                invalid,
            )
        with self.assertRaises(RuntimeError):
            prepare_pair(
                self.root,
                "CASE-PAIR",
                self.repository,
                self.contract_path,
                max_files=1,
            )
        failed_pairs = [
            load_json(path)
            for path in (self.root / ".harness" / "experiments" / "pairs").glob(
                "*/pair-manifest.json"
            )
        ]
        self.assertEqual([item["state"] for item in failed_pairs], ["failed"])


if __name__ == "__main__":
    unittest.main()
