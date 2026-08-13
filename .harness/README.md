# Harness control directory

This directory separates constitutional policy, public schemas, evaluators,
evidence, experiments, future-change scenarios, historian data, observers, and
agent adapters.

The source of truth for runtime history is the hash-chained JSONL ledger at
`historian/events/events.jsonl`. Decision Packs under `cases/` are rebuildable
projections and may change as beliefs change. Runtime data is ignored by Git by
default because it can contain issue, repository, or operational evidence.

V0 enforces event integrity in the CLI and exposes declarative role policy. V1
enforces Builder and Verifier capabilities with macOS `sandbox-exec` plus a
filtered candidate workspace. Later slices add independent verifiers, floor-then
Pareto admission, hidden future scenarios, post-change observers, memory
invalidation, and Maintenance Mode amendment. The YAML policy alone is still not
a security boundary, and isolation is not claimed on untested platforms.

