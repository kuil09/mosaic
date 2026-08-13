# Mosaic

Mosaic is an epistemic software harness: it manages what a software organization
knows, does not know, and must verify before accepting an intervention. It does
not assume that every issue needs code or that passing tests prove the issue is
resolved.

This repository contains the V0 **Decision Pack**. It turns an issue and a bounded
repository inventory into a claim–evidence graph, records tamper-evident events,
generates falsification plans, and supports code and non-code dispositions as
first-class outcomes.

## Current product boundary

Implemented in V0:

- issue intake as an asserted claim;
- target, preservation, boundary, and resource constraints;
- eight rival causal or interpretive hypotheses;
- commit-scoped supporting and opposing evidence;
- conservative, explicit outcome proposals;
- `CODE_CHANGE`, `NO_CHANGE`, `INSTRUMENT_FIRST`, `CONFIGURATION_CHANGE`,
  `DOCUMENTATION_CHANGE`, `OPERATIONAL_ACTION`, `POLICY_CONFLICT`,
  `INSUFFICIENT_EVIDENCE`, and `ISSUE_REJECTED` dispositions;
- adversarial counterexample and verification-plan generation;
- hash-chained append-only JSONL events;
- a public Decision Pack JSON Schema; and
- constitutional role and mode contracts.

Implemented in the V1 Builder–Verifier slice:

- versioned run manifests for isolated candidates;
- bounded candidate workspaces that omit constitution, hidden evaluators, and
  the raw historian ledger;
- a local command adapter that enforces Builder and Verifier capabilities with
  macOS `sandbox-exec`;
- a zero-change candidate evaluated under the same public floor; and
- `candidate_created`, `builder_started`, `builder_finished`,
  `verification_started`, `verdict_recorded`, `run_interrupted`, and
  `candidate_disposed` historian events.

Implemented in the Phase A control loop:

- a single packaged Decision Pack schema, with drift detection on `verify`;
- git worktree materialization when `HEAD` exists, otherwise the bounded copy;
- a scripted Builder that can write only inside the candidate workspace;
- verdicts rebound into the Decision Pack `experiments` list; and
- floor-gated `decide` for state-changing outcomes, with an explicit
  `--override` that keeps failed floors visible.

Later slices now present in this tree:

- independent public, hidden, mutation, property, and differential verifiers;
- hard-floor then Pareto admission, with no weighted score;
- equal-budget Future Maintainer Tournament runs that hide scenario text from Builders;
- post-change `observe` that can stale-date an accepted decision;
- memory invalidation and Maintenance Mode `amend` (refused in Normal Mode); and
- `ledger export` / `ledger compare` for an external copy of the chain head.

Not claimed:

- Linux or Windows process isolation;
- multi-provider agent execution;
- a timestamp authority or network ledger anchor.

The declarative permission file documents the intended boundary but is not a
security control. V1 process isolation is implemented and tested only with
macOS `/usr/bin/sandbox-exec`. Mosaic does not claim isolation on other
platforms.

## Requirements

- Python 3.11 or newer
- Git is optional; when available, evidence is scoped to the repository commit

The runtime has no third-party Python dependencies.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
mosaic --version
```

For source-tree development, replace `mosaic` with:

```bash
PYTHONPATH=src python3 -m mosaic_harness
```

## End-to-end workflow

Initialize local runtime storage:

```bash
mosaic init
```

Create a Decision Pack. The repository scan is bounded, read-only, excludes
generated and harness directories, and stores hashes rather than file contents.

```bash
mosaic investigate ISSUE-123 \
  --issue examples/issue.md \
  --repo .
```

The initial disposition is intentionally `INSUFFICIENT_EVIDENCE`. Generate
falsification plans:

```bash
mosaic challenge ISSUE-123
```

Record evidence against a claim:

```bash
mosaic evidence add ISSUE-123 \
  --claim H-OBSERVABILITY-GAP \
  --direction supporting \
  --strength strong \
  --summary "Cancellation and coupon events have no shared correlation identifier." \
  --source-type log-inspection \
  --source-ref incident-2026-08-14
```

Derive a new provisional disposition:

```bash
mosaic propose ISSUE-123
```

Inspect and verify the projection and raw ledger:

```bash
mosaic show ISSUE-123
mosaic verify ISSUE-123
mosaic rebuild ISSUE-123
```

An authorized human can accept any disposition while keeping the evidence and
remaining uncertainty visible:

```bash
mosaic decide ISSUE-123 INSTRUMENT_FIRST \
  --actor engineer@example.com \
  --rationale "A correlation identifier is required before causal attribution." \
  --condition "Review telemetry privacy before deployment."
```

On macOS, create an isolated candidate after a case exists. The Builder process
can read the copied repository, Decision Pack, and public tests. It cannot read
hidden evaluators or write constitution and historian paths.

```bash
mosaic candidate prepare ISSUE-123 --repo . --kind zero-change
mosaic candidate prepare ISSUE-123 --repo . --kind code
mosaic candidate exec ISSUE-123 RUN_ID --role builder -- python3 -c 'print("ok")'
mosaic candidate build ISSUE-123 RUN_ID --script instruction.json
mosaic candidate verify ISSUE-123 RUN_ID
mosaic candidate compare ISSUE-123 ZERO_RUN_ID CODE_RUN_ID
mosaic observation-plan ISSUE-123 \
  --signal "The public test that encoded the defect now passes." \
  --rollback-trigger "Dispose the candidate; do not merge."
mosaic decide ISSUE-123 CODE_CHANGE \
  --actor engineer@example.com \
  --rationale "A surviving candidate passed the public floor."
mosaic candidate dispose ISSUE-123 RUN_ID
```

## Evidence semantics

Evidence is directional, provenance-bearing, and revision-scoped. A revision
combines the Git commit when available with a bounded working-tree fingerprint,
so uncommitted changes do not silently inherit older evidence. Stale evidence
remains in history but does not update the current claim status. A
single severe falsification attempt can support or refute a claim at low
empirical confidence; two or three independent sources raise confidence without
turning empirical survival into proof.

Automatic proposals are deliberately conservative. A supported hypothesis does
not select a state-changing disposition until its material rivals are refuted.
An independently supported observability gap selects `INSTRUMENT_FIRST`, and a
supported policy conflict stops normal intervention planning.

## Storage and trust boundary

```text
.harness/
├── constitution/       ratified modes and declarative permissions
├── claims/schemas/     public Decision Pack contract
├── cases/              mutable projections (ignored by Git)
├── evidence/           local evidence artifacts (ignored by Git)
├── evaluators/         public, hidden, mutation, and property boundaries
├── future/             scenario and tournament contracts
├── historian/events/   append-only hash-chained source events
├── observers/          CI, deployment, and incident boundaries
└── adapters/           agent-specific routing notes
```

The event ledger is the runtime source of truth. Each materialization stores a
hash-verified projection snapshot, so `mosaic rebuild` can restore a missing or
damaged Decision Pack from the latest valid event. `mosaic verify` detects edits, removals,
reordering, and broken links in the event chain. Hash chaining detects tampering;
it does not prevent a process with filesystem write access from replacing the
entire ledger. External anchoring and restricted writer processes are later
hardening work.

## Development

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The next implementation boundary is V1 Builder–Verifier isolation. Future
Maintainer Tournament work must use the equal-budget, hidden-scenario contract
in `.harness/future/scenarios/SCHEMA.md` and hard-floor/Pareto selection rather
than a weighted aggregate score.
