# Harness control directory

The hash-chained ledger under `historian/events/` is the runtime source of
truth. Decision Packs under `cases/` are rebuildable projections. Constitution
and schema files are installed only when absent.

Declarative `constitution/permissions.yaml` is not a security boundary.
Process isolation, when available, is enforced by the Mosaic executor (tested
on macOS `sandbox-exec` only).
