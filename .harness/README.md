# Harness control directory

This directory separates constitutional policy, public schemas, evaluators,
evidence, experiments, future-change scenarios, historian data, observers, and
agent adapters.

The source of truth for runtime history is the hash-chained JSONL ledger at
`historian/events/events.jsonl`. Decision Packs under `cases/` are rebuildable
projections and may change as beliefs change. Runtime data is ignored by Git by
default because it can contain issue, repository, or operational evidence.

The CLI verifies ledger integrity. Builder and Verifier processes are isolated
with macOS `sandbox-exec` and a filtered candidate workspace. Independent
verifiers, floor-then-Pareto admission, hidden future scenarios, post-change
observers, memory invalidation, and Maintenance Mode amendment sit on that
boundary. The YAML policy is not a security control. Isolation is not claimed
on untested platforms.

