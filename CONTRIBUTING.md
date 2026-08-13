# Contributing

Thank you for considering a change. Mosaic treats contributions the way it
treats application patches: as claims that need evidence.

## Before you write code

1. Read `README.md` and `docs/architecture.md`.
2. Treat the issue as an asserted claim, not a verified fact.
3. Prefer `NO_CHANGE` when a code patch is not justified.

## Local checks

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

There are no third-party runtime dependencies. Do not add one without a
documented reason and a reversible plan.

## Rules that do not bend

- Code, comments, and repository documentation stay in English.
- Do not rewrite or delete raw historian events.
- In Normal Mode do not edit `.harness/constitution/`, hidden evaluators, or
  memory policy. Maintenance Mode needs explicit authorization and a separate
  worktree.
- Isolation is claimed only where it is tested (today: macOS `sandbox-exec`).
  Do not document untested platforms as supported.
- Hard floors come before Pareto comparison. Do not introduce a weighted
  aggregate score that can hide a failed invariant.
- `NO_CHANGE` and the other non-code dispositions stay valid final outcomes.

## Tests

Write a failing test first for behavior changes. Violation probes (denied
reads, refused `CODE_CHANGE`, schema drift) belong before happy paths.
Production-CLI evidence for isolation or admission changes belongs in a
disposable `--root`, never in this repository's `.harness/cases`.

## Pull requests

Say what claim the patch is making, what would falsify it, and how to roll it
back. Link remaining uncertainty instead of burying it.
