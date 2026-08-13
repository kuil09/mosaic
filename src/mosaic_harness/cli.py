"""Command-line entrypoint for the Mosaic Decision Pack and V1 isolation slice."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from mosaic_harness import __version__
from mosaic_harness.admission import AdmissionError
from mosaic_harness.builder import BuilderPathError
from mosaic_harness.candidate import (
    CandidateNotFoundError,
    IsolationUnavailableError,
    RunStateError,
    build_candidate,
    compare_candidates,
    create_candidate,
    dispose_candidate,
    interrupt_run,
    run_role,
    show_run,
    verify_candidate,
)
from mosaic_harness.schema import SchemaDriftError
from mosaic_harness.amendment import AmendmentError, propose_amendment, ratify_amendment, rollback_amendment
from mosaic_harness.anchor import AnchorError, compare_head, export_head
from mosaic_harness.memory import MemoryError, acknowledge_conflict, invalidate_claim
from mosaic_harness.observers import ObserverError, observe
from mosaic_harness.tournament import TournamentError, run_tournament
from mosaic_harness.domain import Outcome
from mosaic_harness.historian import LedgerIntegrityError
from mosaic_harness.storage import CaseNotFoundError, CaseStore
from mosaic_harness.validation import DecisionPackValidationError
from mosaic_harness.workflow import (
    add_evidence,
    challenge,
    decide,
    initialize_workspace,
    investigate,
    propose,
    rebuild_case,
    render_pack,
    set_observation_plan,
    verify_workspace,
)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mosaic",
        description="Evidence-first software change assurance harness",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="workspace containing .harness (default: current directory)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="initialize the append-only harness workspace")

    investigate_parser = subparsers.add_parser(
        "investigate", help="create an evidence-first Decision Pack from an issue"
    )
    investigate_parser.add_argument("case_id")
    investigate_parser.add_argument("--issue", type=Path, required=True)
    investigate_parser.add_argument("--repo", type=Path, default=Path.cwd())
    investigate_parser.add_argument("--max-files", type=int, default=500)
    investigate_parser.add_argument("--replace", action="store_true")
    investigate_parser.add_argument("--signal", help="observation signal that would show the intervention is wrong")
    investigate_parser.add_argument(
        "--rollback-trigger",
        help="rollback trigger required before a state-changing disposition",
    )

    evidence_parser = subparsers.add_parser(
        "evidence", help="record immutable evidence and update its linked claim projection"
    )
    evidence_subparsers = evidence_parser.add_subparsers(dest="evidence_command", required=True)
    evidence_add_parser = evidence_subparsers.add_parser("add")
    evidence_add_parser.add_argument("case_id")
    evidence_add_parser.add_argument("--claim", required=True)
    evidence_add_parser.add_argument(
        "--direction", choices=("supporting", "opposing", "neutral"), required=True
    )
    evidence_add_parser.add_argument(
        "--strength", choices=("weak", "moderate", "strong"), required=True
    )
    evidence_add_parser.add_argument("--summary", required=True)
    evidence_add_parser.add_argument("--source-type", required=True)
    evidence_add_parser.add_argument("--source-ref", required=True)
    evidence_add_parser.add_argument("--artifact", type=Path)
    evidence_add_parser.add_argument("--valid-for-revision")
    evidence_add_parser.add_argument("--actor", default="mosaic")

    propose_parser = subparsers.add_parser(
        "propose", help="derive a conservative disposition from current fresh evidence"
    )
    propose_parser.add_argument("case_id")
    propose_parser.add_argument("--actor", default="mosaic")

    challenge_parser = subparsers.add_parser(
        "challenge", help="generate falsifiers and admissibility-gated verification plans"
    )
    challenge_parser.add_argument("case_id")
    challenge_parser.add_argument("--actor", default="challenger")
    challenge_parser.add_argument(
        "--emit-evaluators",
        action="store_true",
        help="write mutation and property evaluator stubs; never writes hidden evaluators",
    )

    decide_parser = subparsers.add_parser(
        "decide", help="record an explicit human disposition without hiding uncertainty"
    )
    decide_parser.add_argument("case_id")
    decide_parser.add_argument("outcome", choices=tuple(member.value for member in Outcome))
    decide_parser.add_argument("--rationale", required=True)
    decide_parser.add_argument("--actor", required=True)
    decide_parser.add_argument("--condition", action="append", default=[])
    decide_parser.add_argument(
        "--override",
        action="store_true",
        help="accept a state-changing disposition even when floors failed; keep failures visible",
    )

    observation_plan_parser = subparsers.add_parser(
        "observation-plan",
        help="record the signal and rollback trigger required before CODE_CHANGE",
    )
    observation_plan_parser.add_argument("case_id")
    observation_plan_parser.add_argument("--signal", required=True)
    observation_plan_parser.add_argument("--rollback-trigger", required=True)
    observation_plan_parser.add_argument("--observation")
    observation_plan_parser.add_argument("--actor", default="mosaic")

    show_parser = subparsers.add_parser("show", help="print the current Decision Pack projection")
    show_parser.add_argument("case_id")

    verify_parser = subparsers.add_parser(
        "verify", help="verify ledger integrity and an optional Decision Pack contract"
    )
    verify_parser.add_argument("case_id", nargs="?")

    rebuild_parser = subparsers.add_parser(
        "rebuild", help="rebuild a Decision Pack from the latest verified ledger snapshot"
    )
    rebuild_parser.add_argument("case_id")

    candidate_parser = subparsers.add_parser(
        "candidate",
        help="create, execute, and verify isolated Builder–Verifier candidates",
    )
    candidate_subparsers = candidate_parser.add_subparsers(
        dest="candidate_command", required=True
    )

    prepare_parser = candidate_subparsers.add_parser(
        "prepare", help="materialize an isolated candidate workspace"
    )
    prepare_parser.add_argument("case_id")
    prepare_parser.add_argument("--repo", type=Path, default=Path.cwd())
    prepare_parser.add_argument(
        "--kind", choices=("code", "zero-change"), default="code"
    )
    prepare_parser.add_argument("--max-seconds", type=int, default=30)
    prepare_parser.add_argument("--max-output-bytes", type=int, default=65536)
    prepare_parser.add_argument("--max-files", type=int, default=500)

    exec_parser = candidate_subparsers.add_parser(
        "exec",
        help="run a sandboxed Builder or Verifier command",
        epilog="Put the role command after -- so flags such as -c are not parsed by mosaic.",
    )
    exec_parser.add_argument("case_id")
    exec_parser.add_argument("run_id")
    exec_parser.add_argument("--role", choices=("builder", "verifier"), default="builder")
    exec_parser.add_argument("--max-seconds", type=int)
    exec_parser.add_argument("--max-output-bytes", type=int)
    exec_parser.add_argument("role_command", nargs="*")

    build_parser = candidate_subparsers.add_parser(
        "build", help="apply a confined scripted Builder instruction list"
    )
    build_parser.add_argument("case_id")
    build_parser.add_argument("run_id")
    build_parser.add_argument("--script", type=Path, required=True)

    verify_candidate_parser = candidate_subparsers.add_parser(
        "verify", help="evaluate a candidate against the public floor"
    )
    verify_candidate_parser.add_argument("case_id")
    verify_candidate_parser.add_argument("run_id")

    dispose_parser = candidate_subparsers.add_parser(
        "dispose", help="remove a candidate workspace while keeping the run record"
    )
    dispose_parser.add_argument("case_id")
    dispose_parser.add_argument("run_id")

    interrupt_parser = candidate_subparsers.add_parser(
        "interrupt", help="mark a run interrupted without mutating the source repository"
    )
    interrupt_parser.add_argument("case_id")
    interrupt_parser.add_argument("run_id")

    candidate_show_parser = candidate_subparsers.add_parser(
        "show", help="print a candidate run manifest and verdict"
    )
    candidate_show_parser.add_argument("case_id")
    candidate_show_parser.add_argument("run_id")

    compare_parser = candidate_subparsers.add_parser(
        "compare", help="compare two candidates under the same public floor"
    )
    compare_parser.add_argument("case_id")
    compare_parser.add_argument("run_ids", nargs="+")

    tournament_parser = subparsers.add_parser(
        "tournament", help="run an equal-budget Future Maintainer Tournament"
    )
    tournament_sub = tournament_parser.add_subparsers(dest="tournament_command", required=True)
    tournament_run = tournament_sub.add_parser("run")
    tournament_run.add_argument("case_id")
    tournament_run.add_argument("--scenario", required=True)
    tournament_run.add_argument("--runs", required=True, help="comma-separated run ids")

    observe_parser = subparsers.add_parser(
        "observe", help="record a post-change observation without deciding"
    )
    observe_parser.add_argument("case_id")
    observe_parser.add_argument(
        "--kind",
        required=True,
        choices=("deployment", "incident", "rollback", "human-override"),
    )
    observe_parser.add_argument("--summary", required=True)
    observe_parser.add_argument("--source-ref", required=True)
    observe_parser.add_argument(
        "--direction",
        required=True,
        choices=("supporting", "opposing", "neutral"),
    )
    observe_parser.add_argument("--claim")
    observe_parser.add_argument("--actor", default="observer")

    memory_parser = subparsers.add_parser("memory", help="invalidate or acknowledge memory projections")
    memory_sub = memory_parser.add_subparsers(dest="memory_command", required=True)
    invalidate_parser = memory_sub.add_parser("invalidate")
    invalidate_parser.add_argument("case_id")
    invalidate_parser.add_argument("--claim", required=True)
    invalidate_parser.add_argument("--reason", required=True)
    invalidate_parser.add_argument("--actor", required=True)
    ack_parser = memory_sub.add_parser("acknowledge")
    ack_parser.add_argument("case_id")

    amend_parser = subparsers.add_parser("amend", help="Maintenance Mode constitution amendment")
    amend_parser.add_argument("action", choices=("propose", "ratify", "rollback"))
    amend_parser.add_argument("--mode", default="normal")
    amend_parser.add_argument("--worktree", type=Path)
    amend_parser.add_argument("--id", dest="amendment_id", required=True)
    amend_parser.add_argument("--rationale", default="")
    amend_parser.add_argument("--falsifier", default="")

    ledger_parser = subparsers.add_parser("ledger", help="export or compare the ledger head")
    ledger_sub = ledger_parser.add_subparsers(dest="ledger_command", required=True)
    export_parser = ledger_sub.add_parser("export")
    export_parser.add_argument("--out", type=Path, required=True)
    compare_ledger = ledger_sub.add_parser("compare")
    compare_ledger.add_argument("--against", type=Path, required=True)
    return parser


def _summary(pack: dict[str, object], path: Path | None = None) -> dict[str, object]:
    result: dict[str, object] = {
        "case_id": pack["case_id"],
        "outcome": pack["outcome"],
        "claim_count": len(pack["claims"]),
        "evidence_count": len(pack["evidence"]),
        "updated_at": pack["updated_at"],
    }
    if path is not None:
        result["decision_pack"] = str(path)
    return result


def run(args: argparse.Namespace) -> object:
    root = args.root.resolve()
    if args.command == "init":
        return initialize_workspace(root)
    if args.command == "investigate":
        if args.max_files < 1:
            raise ValueError("--max-files must be at least 1")
        pack, path = investigate(
            root,
            args.case_id,
            args.issue,
            args.repo,
            max_files=args.max_files,
            replace=args.replace,
            signal=args.signal,
            rollback_trigger=args.rollback_trigger,
        )
        return _summary(pack, path)
    if args.command == "observation-plan":
        pack = set_observation_plan(
            root,
            args.case_id,
            signal=args.signal,
            rollback_trigger=args.rollback_trigger,
            observation=args.observation,
            actor=args.actor,
        )
        return {
            **_summary(pack),
            "observation_plan": pack["observation_plan"],
        }
    if args.command == "evidence" and args.evidence_command == "add":
        pack, evidence = add_evidence(
            root,
            args.case_id,
            args.claim,
            direction=args.direction,
            strength=args.strength,
            summary=args.summary,
            source_type=args.source_type,
            source_ref=args.source_ref,
            artifact_path=args.artifact,
            valid_for_revision=args.valid_for_revision,
            actor=args.actor,
        )
        return {**_summary(pack), "recorded_evidence": evidence}
    if args.command == "propose":
        return _summary(propose(root, args.case_id, actor=args.actor))
    if args.command == "challenge":
        pack = challenge(
            root,
            args.case_id,
            actor=args.actor,
            emit_evaluators=args.emit_evaluators,
        )
        return {
            **_summary(pack),
            "counterexample_count": len(pack["counterexamples"]),
            "verification_step_count": len(pack["verification_plan"]),
        }
    if args.command == "decide":
        pack = decide(
            root,
            args.case_id,
            Outcome(args.outcome),
            args.rationale,
            args.actor,
            args.condition,
            override=args.override,
        )
        return _summary(pack)
    if args.command == "show":
        return CaseStore(root / ".harness").load(args.case_id)
    if args.command == "verify":
        return verify_workspace(root, args.case_id)
    if args.command == "rebuild":
        pack, path = rebuild_case(root, args.case_id)
        return _summary(pack, path)
    if args.command == "candidate":
        return _run_candidate(root, args)
    if args.command == "tournament":
        if args.tournament_command == "run":
            run_ids = [item.strip() for item in args.runs.split(",") if item.strip()]
            return run_tournament(root, args.case_id, args.scenario, run_ids)
        raise ValueError(f"unsupported tournament command: {args.tournament_command}")
    if args.command == "memory":
        if args.memory_command == "invalidate":
            pack = invalidate_claim(
                root,
                args.case_id,
                args.claim,
                reason=args.reason,
                actor=args.actor,
            )
            return _summary(pack)
        if args.memory_command == "acknowledge":
            return acknowledge_conflict(root, args.case_id)
        raise ValueError(f"unsupported memory command: {args.memory_command}")
    if args.command == "amend":
        if args.mode != "maintenance":
            raise AmendmentError("Maintenance Mode required")
        if args.action == "propose":
            if args.worktree is None:
                raise AmendmentError("separate worktree required")
            return propose_amendment(
                root,
                mode=args.mode,
                worktree=args.worktree,
                amendment_id=args.amendment_id,
                rationale=args.rationale,
                falsifier=args.falsifier,
            )
        if args.action == "ratify":
            if args.worktree is None:
                raise AmendmentError("separate worktree required")
            return ratify_amendment(
                root,
                mode=args.mode,
                worktree=args.worktree,
                amendment_id=args.amendment_id,
            )
        if args.action == "rollback":
            return rollback_amendment(root, mode=args.mode, amendment_id=args.amendment_id)
        raise ValueError(f"unsupported amend action: {args.action}")
    if args.command == "ledger":
        if args.ledger_command == "export":
            return export_head(root, args.out)
        if args.ledger_command == "compare":
            return compare_head(root, args.against)
        raise ValueError(f"unsupported ledger command: {args.ledger_command}")
    if args.command == "observe":
        return observe(
            root,
            args.case_id,
            kind=args.kind,
            summary=args.summary,
            source_ref=args.source_ref,
            direction=args.direction,
            claim_id=args.claim,
            actor=args.actor,
        )
    raise ValueError(f"unsupported command: {args.command}")


def _run_candidate(root: Path, args: argparse.Namespace) -> object:
    if args.candidate_command == "prepare":
        if args.max_files < 1:
            raise ValueError("--max-files must be at least 1")
        return create_candidate(
            root,
            args.case_id,
            args.repo,
            kind=args.kind,
            budget={
                "max_seconds": args.max_seconds,
                "max_output_bytes": args.max_output_bytes,
            },
            max_files=args.max_files,
        )
    if args.candidate_command == "exec":
        command = list(args.role_command)
        if command and command[0] == "--":
            command = command[1:]
        budget = None
        if args.max_seconds is not None or args.max_output_bytes is not None:
            budget = {}
            if args.max_seconds is not None:
                budget["max_seconds"] = args.max_seconds
            if args.max_output_bytes is not None:
                budget["max_output_bytes"] = args.max_output_bytes
        return run_role(
            root,
            args.case_id,
            args.run_id,
            role=args.role,
            command=command,
            budget=budget,
        )
    if args.candidate_command == "build":
        return build_candidate(root, args.case_id, args.run_id, args.script)
    if args.candidate_command == "verify":
        return verify_candidate(root, args.case_id, args.run_id)
    if args.candidate_command == "dispose":
        return dispose_candidate(root, args.case_id, args.run_id)
    if args.candidate_command == "interrupt":
        return interrupt_run(root, args.case_id, args.run_id)
    if args.candidate_command == "show":
        return show_run(root, args.case_id, args.run_id)
    if args.candidate_command == "compare":
        return compare_candidates(root, args.case_id, args.run_ids)
    raise ValueError(f"unsupported candidate command: {args.candidate_command}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = run(args)
    except (
        ValueError,
        CaseNotFoundError,
        CandidateNotFoundError,
        DecisionPackValidationError,
        IsolationUnavailableError,
        LedgerIntegrityError,
        RunStateError,
        AdmissionError,
        BuilderPathError,
        SchemaDriftError,
        TournamentError,
        ObserverError,
        MemoryError,
        AmendmentError,
        AnchorError,
        json.JSONDecodeError,
    ) as error:
        print(f"mosaic: error: {error}", file=sys.stderr)
        return 2
    if args.command == "show":
        sys.stdout.write(render_pack(result))
    else:
        sys.stdout.write(_json(result))
    return 0
