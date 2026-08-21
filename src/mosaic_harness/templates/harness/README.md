# Harness control directory

The hash-chained ledger under `historian/events/` is the runtime source of
truth. Decision Packs and Work State files under `cases/` are rebuildable
projections. Experiment pairs under `experiments/pairs/` bind a repository
snapshot to frozen external verification inputs. Constitution and schema files
are installed only when absent.

Declarative `constitution/permissions.yaml` is not a security boundary.
Process isolation, when available, is enforced by the Mosaic executor (tested
on macOS `sandbox-exec` only). A failed admission gate has no override path.
