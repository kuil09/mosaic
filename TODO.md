# Mosaic Handoff and TODO

## Mission

Mosaic is not another coding agent. It is an epistemic software harness that
controls software interventions through explicit claims, evidence,
counterevidence, uncertainty, reversibility, and post-change observation.

The product thesis is:

> Mosaic updates an organization's beliefs about software with evidence and
> admits only interventions that remain viable under current and future change.

This repository now contains V0 Decision Packs, a V1 macOS Builder–Verifier
isolation slice, and the A–F control-loop slices (scripted builder, independent
verifiers, floor-then-Pareto admission, tournament runner, observers, memory,
Maintenance Mode amend, ledger-head export). Do not describe Linux/Windows
isolation, an LLM Builder, or a network trust anchor as implemented.

## Repository state at handoff

- Repository: `/Users/gun9/Documents/ChatGPT/mosaic`
- Branch: `master`
- Version: `0.1.0`
- Runtime: Python 3.11 or newer, with no third-party runtime dependencies
- License: MIT
- Isolation claimed only for macOS `/usr/bin/sandbox-exec`

A first commit is authorized only by the user. Do not push unless asked.

## Governing constraints

These are hard constraints unless the user explicitly authorizes Maintenance
Mode:

1. Follow `AGENTS.md` and `.harness/constitution/normal.md`.
2. Treat issues as claims, code as an intervention, and tests as bounded
   evidence.
3. Preserve the raw historian ledger. Never rewrite or delete raw events.
4. Do not modify the constitution, hidden evaluators, or memory policy in
   Normal Mode.
5. Keep Builder and Verifier capabilities separate through actual enforcement,
   not role prompts alone.
6. Keep `NO_CHANGE` and all other non-code dispositions as valid final outcomes.
7. Bind evidence to the repository revision for which it is valid.
8. Require rollback and observation plans before state-changing interventions.
9. Apply hard floors before Pareto comparison. Do not introduce a weighted
   aggregate score that can compensate for a failed invariant.
10. Keep code, comments, and repository documentation in English.

## Implemented

### CLI

- V0: `init`, `investigate`, `evidence add`, `challenge`, `propose`, `decide`,
  `show`, `verify`, `rebuild`
- V1/A: `candidate prepare|exec|build|verify|compare|dispose|interrupt|show`
- B: `challenge --emit-evaluators` (mutation/property stubs only; never hidden)
- C: `candidate compare` with floor elimination then Pareto
- D: `tournament run`
- E: `observe`
- F: `memory invalidate|acknowledge`, `amend`, `ledger export|compare`

`decide CODE_CHANGE` is refused unless a code candidate survives hard floors and
is not dominated within its kind, and the observation plan names `signal` and
`rollback_trigger`. `--override` records failed floors in
`unresolved_questions`. `NO_CHANGE` remains valid without a candidate.

### Isolation (tested: macOS sandbox-exec)

- Filtered candidate workspace omits constitution, hidden evaluators, historian,
  and future scenarios.
- Git worktree when `HEAD` exists; otherwise a bounded copy.
- Builder reads/writes of protected source paths are denied by seatbelt.
- Verifier writes to the candidate are denied; snapshot mismatch is recorded.
- Scripted Builder (`candidate build --script`) cannot escape the candidate
  root.

### Verifiers and admission

- Public, hidden, mutation-kills, property, and differential adapters.
- Missing property/differential suites are `not_configured`, not a pass.
- Hidden stdout stays under `verifier/hidden/` (digest only in the verdict).
- Pareto compares same-kind survivors only. A numeric `score` field cannot
  revive a floor failure.

### Observation, memory, amendment, anchor

- Opposing `observe --kind incident` can mark an accepted state-changing
  outcome `superseded` and stale its evidence.
- `memory invalidate` prevents stale support until fresher evidence arrives.
- Unacknowledged historian conflicts fail `verify`.
- `amend` without `--mode maintenance` is refused. Ratify requires a passing
  holdout file in a separate worktree. Rollback restores
  `.harness/constitution/previous/` and does not rewrite events.
- `ledger export` writes `{head_hash, event_count, exported_at, workspace}`.
  It is not a timestamp authority. Substitution is visible only if the export
  lives outside the replaced tree.

### Primary files

- `src/mosaic_harness/cli.py` — CLI contract
- `src/mosaic_harness/workflow.py` — V0 workflows and projection anchors
- `src/mosaic_harness/candidate.py` — candidate lifecycle
- `src/mosaic_harness/isolation.py` / `executor.py` / `workspace.py`
- `src/mosaic_harness/builder.py` — scripted Builder
- `src/mosaic_harness/verifiers.py` — independent floors
- `src/mosaic_harness/admission.py` — floor-then-Pareto decide gate
- `src/mosaic_harness/tournament.py` / `observers.py` / `memory.py` /
  `amendment.py` / `anchor.py` / `schema.py`
- `.harness/claims/schemas/decision-pack.schema.json` — must match the packaged
  template; `verify` fails on drift
- `tests/test_mosaic.py` plus `tests/test_v1_*.py` … `tests/test_v4_*.py`

### Automated verification

`PYTHONPATH=src python3 -m unittest discover -s tests -v` last recorded **52**
tests passing, including V0 floors, V1 isolation probes, and A–F unit/CLI
probes.

## Known limitations (do not overclaim)

- Process isolation is implemented and tested only on macOS `sandbox-exec`.
- There is no LLM or multi-provider Builder. The scripted adapter is the only
  Builder.
- Tournament scenario *generation* is not automated; the runner consumes a
  hand-written opaque scenario JSON.
- Observers are a CLI ingest, not a live CI/deploy/incident integration.
- Maintenance Mode does not yet automate historical replay, shadow, or canary.
  It enforces propose/holdout/ratify/rollback and refuses Normal Mode edits.
- Hash chaining detects mutation; an actor with filesystem write access can
  still replace the entire ledger. Export is an external witness, not a
  notary.
- File locking uses `fcntl` on Unix. Windows has no equivalent writer lock.
- The repository fingerprint is bounded (default 500 files, 2 MiB each).
- Evidence strength and provenance are recorded but not authenticated.
- Rebuild uses the latest materialized snapshot, not a semantic event reducer.
- `permissions.yaml` is declarative and is not a security boundary.
- Using this repository itself as `--repo` makes the public floor equal to
  Mosaic's own `tests/` suite. That is a harsh, often failing, floor for a
  product smoke — not proof that application tests are the intended floor.

## Next useful work

1. Run real cases against *application* repositories (not Mosaic-as-repo)
   whose public tests match the issue under investigation.
2. Add one LLM Builder behind `builder.py`'s instruction boundary, still
   sandboxed.
3. Add a Linux isolation adapter only after the same denial probes pass.
4. Wire `observe` to real deploy/incident sources if an organization wants
   post-change belief updates without a human typing the CLI.
5. Deepen Maintenance Mode (replay, shadow, canary) only with explicit
   Maintenance Mode authorization in a separate worktree.

Do not start a multi-agent UI, marketplace, or untested cross-platform
isolation claim.

## First commands for the next agent

```bash
cd /Users/gun9/Documents/ChatGPT/mosaic
git status --short
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m mosaic_harness --help
```

For a disposable smoke test, use a temporary workspace rather than writing
runtime events into this repository:

```bash
mkdir -p /tmp/mosaic-handoff-smoke
PYTHONPATH=src python3 -m mosaic_harness --root /tmp/mosaic-handoff-smoke init
PYTHONPATH=src python3 -m mosaic_harness --root /tmp/mosaic-handoff-smoke \
  investigate HANDOFF-001 --issue examples/issue.md --repo .
PYTHONPATH=src python3 -m mosaic_harness --root /tmp/mosaic-handoff-smoke \
  verify HANDOFF-001
```

Treat `/tmp/mosaic-real-case` observations (REAL-001) as one production-CLI
probe of Mosaic-as-repo, not as a release.

## Real-case observation (REAL-001)

A subagent ran the production CLI against this source tree as `--repo`, with
all historian/cases written only under `/tmp/mosaic-real-case`. The mosaic
worktree was not modified by that run.

Issue (asserted, not assumed true): Mosaic can refuse `CODE_CHANGE` without a
surviving floor-passing candidate, and an opposing incident can supersede an
accepted decision.

Observed facts:

- Investigate opened `REAL-001` as `INSUFFICIENT_EVIDENCE` with 0 evidence.
- Zero-change `R-097e32101ef6` and code `R-fd83c49c20ca` prepared with
  `bounded-copy` and `sandbox-exec`.
- Live constitution, hidden evaluators, historian, and future/scenarios were
  absent from both candidate `workspace/` trees. Packaged templates under
  `src/mosaic_harness/templates/` were present (they are application source).
- Builder `PermissionError: Operation not permitted` on mosaic hidden dir,
  mosaic constitution, temp-workspace constitution, and the raw ledger.
  Mosaic's `denial` field stayed `null` because the probe process exited 0
  after catching exceptions.
- Zero-change public floor failed (`unittest discover` exit 1) using Mosaic's
  own `tests/` as the floor. Hidden/mutation/property/differential were
  unconfigured. Code candidate was not verified in this run.
- `decide CODE_CHANGE` first refused for a missing observation `signal` and
  `rollback_trigger`. Use `mosaic observation-plan CASE --signal ...
  --rollback-trigger ...` (or `investigate --signal ...`) so the pack stays
  ledger-anchored. After those fields existed, REAL-001 refused: `no
  undominated code candidate survives the floors`.
- `decide NO_CHANGE` was accepted (`admission.status: ungated`).
- `verify` / `rebuild` succeeded (22 events at that point).
- `--override` accepted `CODE_CHANGE` and recorded the failed run in
  `unresolved_questions`. Opposing `observe --kind incident` set outcome
  status to `superseded` (29 events). Observer did not call `decide`.
- `ledger export` / `compare` reported `equal`; a mutated head hash compared
  as `ledger head mismatch`.

Remaining uncertainty from that run: why the public unittest floor returned 1
was not stored in the verdict; a floor-admitted (non-override) `CODE_CHANGE`
was not observed; `observe` without `--claim` bound the incident to
`H-CODE-DEFECT` and marked it `refuted`.

Do not treat Mosaic-as-`--repo` as the intended application floor. Use an
application repository whose tests match the issue.
