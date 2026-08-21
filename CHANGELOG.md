# Changelog

All notable changes to Mosaic are documented in this file.

## 0.3.0 - 2026-08-21

Mosaic 0.3 introduces a durable single-worker execution plane on top of the
frozen admission plane. The package remains an external harness rather than a
coding agent.

### Added

- Frozen `ExperimentPair` preparation with one zero-change control and one or
  more code attempts derived from the same source snapshot and verification
  contract.
- Public target and preservation checks with `PASS`, `FAIL`, `ABSENT`, and
  `ERROR` results, plus explicit hidden, mutation, and property adapters.
- Full mutation traversal, syntax validation, verifier-only raw output, output
  hashes, deletion-aware change surfaces, actual execution cost, retry lineage,
  and code-only Pareto comparison.
- Proposal-bound admission that rechecks the pair, run, candidate snapshot,
  repository revision, frozen floor, and ledger head before a human decision.
- Event-sourced `WorkContract` and `WorkState` support for deterministic
  single-worker task execution, blocking, replanning, verification, admission,
  and final `DONE` or `STOPPED` transitions.
- JSON and Markdown `ResumePacket` projections that expose durable work state
  without hidden evaluator details or chain-of-thought.
- A sibling Spike example with reusable work and verification contracts.

### Changed

- Package version is now `0.3.0`.
- Decision Pack, run manifest, and verdict schemas are now `2.0.0`.
- `candidate prepare` is replaced by `candidate prepare-pair`; pair verification
  uses `candidate verify-pair`.
- `decide` now requires the exact proposal ID and matching outcome. Failed gates
  cannot be approved through an override.
- Candidate snapshots are immutable after implementation completion. Further
  work uses `candidate retry-code` and explicit pair attachment.

### Compatibility

- Decision Pack `1.0.0` cases are read-only. Mosaic permits inspection,
  integrity verification, candidate display, and ledger export or comparison,
  but does not migrate or rewrite legacy ledgers.

### Evidence and limits

- The automated suite covers frozen-input integrity, target and preservation
  relations, required-adapter failure, mutation traversal, hidden-output
  redaction, snapshot invalidation, proposal binding, state replay, replan and
  retry staleness, human finish, and legacy read-only behavior.
- Real process isolation is supported only on macOS when the
  `/usr/bin/sandbox-exec` capability probe succeeds. Portable fake-runner tests
  establish state and verdict semantics, not operating-system isolation.
- Parallel workers, leases, task-specific worktrees, an LLM Builder, typed
  non-code admission adapters, and broad fresh-session reliability claims remain
  outside this release.
