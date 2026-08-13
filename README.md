# Mosaic

Mosaic is an epistemic software harness. It records what an organization
believes about software, what remains uncertain, and which interventions are
admissible. It does not assume every issue needs code, or that a passing test
proves the issue is resolved.

## Product boundary

Mosaic admits a state-changing code intervention only when:

- a Builder produced the change in an isolated candidate;
- a Verifier ran the same public floor against that candidate and against
  zero-change;
- hard floors pass (a missing hidden/property suite is not a pass);
- the observation plan names a `signal` and a `rollback_trigger`;
- the code candidate is not floor-eliminated and is on the same-kind Pareto
  frontier.

`NO_CHANGE` and the other non-code dispositions remain valid final outcomes.
`--override` can accept a refused state-changing disposition but must leave
failed floors visible.

Process isolation is implemented and tested only with macOS
`/usr/bin/sandbox-exec`. Mosaic does not claim Linux or Windows isolation, an
LLM Builder, or a network trust anchor. `permissions.yaml` is not a security
control.

## Requirements

- Python 3.11 or newer
- Git is optional; when present, evidence is scoped to the commit plus a tree
  fingerprint

No third-party runtime dependencies.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
mosaic --version
```

From this source tree:

```bash
PYTHONPATH=src python3 -m mosaic_harness
```

## Workflow

Keep three trees separate: the Mosaic tool, the application `--repo`, and a
disposable `--root` for ledger and cases. Do not use the Mosaic source tree as
`--repo`; its `tests/` become the public floor.

An example application target is the sibling **Spike** kitchen-ticket app
(`../spike` when both live under the same parent).

```bash
mosaic --root /tmp/case init
mosaic --root /tmp/case investigate SPIKE-001 \
  --issue /path/to/spike/issue.md \
  --repo /path/to/spike \
  --signal "test_completed_tickets_leave_the_active_rail passes" \
  --rollback-trigger "Dispose the candidate; do not merge."

mosaic --root /tmp/case candidate prepare SPIKE-001 --repo /path/to/spike --kind zero-change
mosaic --root /tmp/case candidate prepare SPIKE-001 --repo /path/to/spike --kind code
mosaic --root /tmp/case candidate build SPIKE-001 CODE_RUN --script fix.json
mosaic --root /tmp/case candidate verify SPIKE-001 ZERO_RUN
mosaic --root /tmp/case candidate verify SPIKE-001 CODE_RUN
mosaic --root /tmp/case candidate compare SPIKE-001 ZERO_RUN CODE_RUN
mosaic --root /tmp/case decide SPIKE-001 CODE_CHANGE \
  --actor engineer@example.com \
  --rationale "The code candidate survives the floor that encodes the defect."
mosaic --root /tmp/case verify SPIKE-001
```

If `investigate` omitted the signal, record it later without editing the pack
file:

```bash
mosaic --root /tmp/case observation-plan SPIKE-001 \
  --signal "..." --rollback-trigger "..."
```

Other commands: `evidence add`, `challenge`, `propose`, `show`, `rebuild`,
`observe`, `tournament run`, `memory invalidate`, `amend` (Maintenance Mode
only), `ledger export`, `ledger compare`.

## Evidence

Evidence is directional, provenance-bearing, and revision-scoped. A revision
combines the Git commit when available with a bounded working-tree fingerprint.
Stale evidence stays in history but does not update the current claim.
Automatic `propose` stays conservative: a supported hypothesis does not select a
state-changing disposition while material rivals remain.

## Storage

```text
.harness/
├── constitution/       ratified modes and declarative permissions
├── claims/schemas/     public Decision Pack contract
├── cases/              mutable projections (gitignored)
├── evidence/           local evidence artifacts (gitignored)
├── evaluators/         public, hidden, mutation, and property roots
├── experiments/        candidate workspaces and verdicts (gitignored)
├── future/             scenario contracts and tournament projections
├── historian/events/   append-only hash-chained ledger (gitignored)
├── observers/          ingest boundaries
└── adapters/           agent-specific notes
```

The ledger is the runtime source of truth. `rebuild` restores a Decision Pack
from the latest verified snapshot. Hash chaining detects tampering; it does not
stop a process with write access from replacing the file. `ledger export`
writes a head witness for comparison outside the tree. It is not a timestamp
authority.

## Development

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Admission uses hard floors, then Pareto dominance. Do not add a weighted
aggregate score. Future scenarios must stay hidden from Builders and use equal
budgets. See `.harness/future/scenarios/SCHEMA.md`.
