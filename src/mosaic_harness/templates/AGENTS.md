# Mosaic Agent Kernel

This file is the stable routing kernel. Full rules live in
`.harness/constitution/`.

## Mode and authority

- Default to Normal Mode and follow `.harness/constitution/normal.md`.
- Maintenance Mode requires explicit authorization and a separate worktree.
- Treat issues as claims, code as an intervention, and tests as bounded evidence.
- Never rewrite or delete raw historian events.
- In Normal Mode, do not modify the constitution or hidden evaluators.
- A Builder must not inspect hidden evaluators.
- A Verifier must not edit candidate code while evaluating it.

## Required records

- Record material claims, evidence, counterevidence, decisions, and uncertainty.
- Bind repository evidence to the revision for which it is valid.
- Define rollback and observation plans before a state-changing intervention.
- Treat non-code dispositions as valid final outcomes.

