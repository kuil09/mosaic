# Harness control directory

The raw hash-chained event ledger under `historian/events/` is the runtime source
of truth. Decision Packs under `cases/` are mutable, integrity-checked
projections. Constitution and schema files are installed only when absent.

The V0 permission policy is declarative. It does not replace OS, container,
worktree, or hidden-evaluator isolation.

