# Mosaic Handoff and TODO

## Mission

Mosaic is a generic execution and admission harness for external agents. It
stores epistemic claims, durable work state, frozen verification contracts,
and proposal-bound human decisions without treating tests as the objective.

## Current release surface

- Package version: `0.3.0`
- Decision Pack, run manifest, and verdict schema: `2.0.0`
- Work Contract and ResumePacket schema: `1.0.0`
- Runtime: Python 3.11+, no third-party dependencies
- Real isolation claim: macOS only, and only after the `sandbox-exec` probe
  succeeds
- Mode: Normal Mode; do not edit constitution, hidden evaluator contents, raw
  historian events, or memory policy

## Implemented

Admission Plane:

- `candidate prepare-pair` freezes an external verification contract and
  creates paired zero-change/code candidates from one source snapshot.
- Public checks use argv arrays and frozen inputs outside the candidate.
- Results are `PASS | FAIL | ABSENT | ERROR`; target and preservation relations
  fail closed.
- Hidden output is verifier-only; general verdicts retain output hashes.
- Mutation runs all deterministic Python mutants; property and hidden adapters
  are explicit requirements.
- Candidate freezing, retry lineage, deletion-aware change surface, actual
  execution cost, and code-only Pareto comparison are recorded.
- Proposal and human decision bind pair, run, snapshot, revision, floor digest,
  evidence, claims, and ledger head. There is no override.
- Configuration, documentation, and operational state changes remain
  inadmissible without typed adapters.

Execution Plane:

- `work create|status|next|attach-pair|implementation-complete|replan|resume|finish`
  and `work task start|block|unblock|complete`.
- Event-sourced single-worker Work State with deterministic task selection.
- Versioned replan preserves goal fields and completed task definitions.
- ResumePacket exposes durable external state without hidden evaluator details
  or chain-of-thought.
- `work finish` requires an exact accepted human decision and emits `DONE` or
  `STOPPED` from the completion policy.

Compatibility:

- Decision Pack 1.0 cases are read-only.
- Allowed legacy commands: `show`, integrity `verify`, `candidate show`, and
  ledger export/compare.
- No automatic migration or ledger rewrite.

## Automated evidence

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Portable tests cover frozen-input tamper resistance, target/preservation
relations, required `ABSENT/ERROR`, full mutation traversal, hidden-output
redaction, candidate snapshot invalidation, proposal binding, deterministic
replay, replan/retry staleness, human finish, and legacy read-only behavior.

The full suite also passes outside the nested development sandbox with the real
macOS isolation probe enabled. The sibling Spike smoke reached `DONE` through
the real CLI and isolated Builder/Verifier: zero target `FAIL`, code target
`PASS`, preservation `PASS` on both, ledger verification valid, and the source
Spike repository clean.

A separate ephemeral Codex session received only a non-terminal Spike
ResumePacket. It exactly restored the objective, hard-constraint IDs,
`PLANNED` state, next task, active pair, active run, and candidate snapshot
without reading files or using tools. This is evidence for that packet and
session boundary, not a universal long-running reliability claim.

Isolation tests skip when the capability probe cannot run. A passing portable
fake-runner suite alone is not an isolation claim.

## Remaining release evidence

1. Repeat the fresh-session experiment across blocked, replanned, verifying,
   and stale-artifact states before making a broad reliability claim.
2. Add typed admission adapters before enabling configuration, documentation,
   or operational actions.
3. Claim Linux or Windows isolation only after platform-specific denial probes
   pass.

## Explicitly out of scope for 0.3

- parallel workers, leases, task-specific worktrees, and merge policy;
- a multi-agent UI or marketplace;
- an LLM Builder;
- a network notary or timestamp authority;
- automatic migration of legacy cases.

Do not push or publish unless explicitly requested.
