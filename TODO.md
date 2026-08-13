# Mosaic Handoff and TODO

## Mission

Mosaic is an epistemic software harness. It updates beliefs about software
with evidence and admits only interventions that remain viable under current
and future change.

## Repository state

- Path: this tree (`mosaic`)
- Branch: `master`
- Runtime: Python 3.11+, no third-party dependencies
- Isolation claimed only for macOS `/usr/bin/sandbox-exec`
- Example application target: sibling `spike` (kitchen-ticket todos)

Do not push unless asked. Normal Mode must not edit constitution, hidden
evaluators, or memory policy.

## What is implemented

CLI: `init`, `investigate`, `evidence add`, `challenge`, `propose`, `decide`,
`show`, `verify`, `rebuild`, `observation-plan`, `candidate
prepare|exec|build|verify|compare|dispose|interrupt|show`, `tournament run`,
`observe`, `memory invalidate|acknowledge`, `amend`, `ledger export|compare`.

`decide CODE_CHANGE` requires a surviving undominated code candidate and an
observation plan with `signal` and `rollback_trigger`. `NO_CHANGE` does not.
`--override` records floor failures instead of hiding them.

Isolation: filtered or git-worktree candidate; Builder denied live
constitution, hidden evaluators, historian, and future scenarios; Verifier
cannot write the candidate.

Floors: public, hidden, mutation (`src/**/*.py`, skipping `__init__.py` and
`__main__.py`), property, differential. Unconfigured is not a pass.

## Automated tests

`PYTHONPATH=src python3 -m unittest discover -s tests -v`

## Do not overclaim

- No Linux/Windows isolation
- No LLM Builder
- Tournament runner exists; scenario generation does not
- `observe` is CLI ingest, not a live deploy hook
- Maintenance Mode is propose/holdout/ratify/rollback, not replay/shadow/canary
- Ledger export is a witness file, not a notary
- `permissions.yaml` is not a security boundary
- Mosaic-as-`--repo` makes Mosaic's own tests the public floor. Use `spike`
  or another application whose tests encode the issue.

## Observed production-CLI cases

- **REAL-001** (`/tmp/mosaic-real-case`, Mosaic-as-repo): isolation denials
  held; public floor failed on Mosaic's suite; `CODE_CHANGE` without a
  survivor was refused; override plus opposing incident superseded.
- **SPIKE-002** (`/tmp/mosaic-spike-recheck`, `--repo` = sibling Spike):
  `investigate --signal/--rollback-trigger` needed no JSON edit; zero-change
  failed the defect test; code candidate passed public+mutation; `decide
  CODE_CHANGE` accepted without override; Spike source stayed defective.

Public docs for an open repository: `README.md`, `CONTRIBUTING.md`,
`SECURITY.md`, `CODE_OF_CONDUCT.md`, `docs/architecture.md`, `docs/using.md`,
and `docs/brand/`.

## Next (only if still building Mosaic)

1. Persist public-floor stdout/stderr on the verdict so a failed floor is
   interpretable.
2. Classify sandbox `PermissionError` as `denial` even when the Builder
   process exits 0 after catching it.
3. An LLM Builder only behind the existing sandboxed instruction boundary.
4. Linux isolation only after the same denial probes pass.

Do not start a multi-agent UI, marketplace, or untested isolation claim.

## Commands for the next agent

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m mosaic_harness --help
```

Smoke against Spike, never against this tree as `--repo`:

```bash
mkdir -p /tmp/mosaic-handoff-smoke
PYTHONPATH=src python3 -m mosaic_harness --root /tmp/mosaic-handoff-smoke init
PYTHONPATH=src python3 -m mosaic_harness --root /tmp/mosaic-handoff-smoke \
  investigate SPIKE-SMOKE \
  --issue ../spike/issue.md --repo ../spike \
  --signal "test_completed_tickets_leave_the_active_rail passes" \
  --rollback-trigger "Dispose the candidate; do not merge."
PYTHONPATH=src python3 -m mosaic_harness --root /tmp/mosaic-handoff-smoke \
  verify SPIKE-SMOKE
```
