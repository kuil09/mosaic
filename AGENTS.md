# Mosaic Agent Kernel

This file is the stable routing kernel for agents operating in this repository.
The full constitutional rules live in `.harness/constitution/`.

## Mode

- Default to **Normal Mode**.
- Maintenance Mode must be explicitly authorized and run in a separate worktree.
- Normal Mode follows `.harness/constitution/normal.md`.
- Maintenance Mode follows `.harness/constitution/maintenance.md`.

## Authority boundaries

- Treat issues as claims, code changes as interventions, and tests as bounded evidence.
- Preserve raw historian events. Never rewrite or delete the event ledger.
- In Normal Mode, do not modify the constitution, hidden evaluators, or memory policy.
- Apply the role capabilities in `.harness/constitution/permissions.yaml`.
- A Builder must not inspect or edit hidden evaluators.
- A Verifier must not edit candidate application code while evaluating it.

## Required records

- Record material claims, evidence, counterevidence, decisions, and remaining uncertainty.
- Bind repository evidence to the commit for which it is valid.
- Define rollback and observation plans before a state-changing intervention.
- Treat `NO_CHANGE` and other non-code dispositions as valid final outcomes.

## Adapter routing

- Claude-specific instructions extend this kernel through `CLAUDE.md`.
- Gemini-specific instructions extend this kernel through `GEMINI.md`.
- Codex-specific notes live under `.harness/adapters/codex/`.

