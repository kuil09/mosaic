# Architecture

Mosaic is a local Python CLI. It keeps beliefs in a Decision Pack projection
and truth in an append-only hash-chained ledger.

## Control loop

```mermaid
flowchart LR
  claim["Claim"] --> pack["Decision Pack"]
  pack --> builder["Isolated Builder"]
  pack --> zero["Zero-change"]
  builder --> candidate["Candidate workspace"]
  zero --> floors["Verifier floors"]
  candidate --> floors
  floors --> gate["Admission gate"]
  gate -->|"eligible"| decide["decide CODE_CHANGE"]
  gate -->|"fail or only zero-change"| refuse["NO_CHANGE or refuse"]
  decide --> ledger["Append-only ledger"]
  refuse --> ledger
  ledger --> observe["observe"]
  observe -->|"opposing incident"| super["Outcome superseded"]
```

## Three trees

```mermaid
flowchart TB
  subgraph tool["Mosaic source"]
    cli["mosaic CLI"]
  end
  subgraph app["Application --repo"]
    src["Application code and tests"]
  end
  subgraph root["Disposable --root"]
    cases["cases / Decision Pack"]
    events["historian/events"]
    runs["experiments/candidates"]
  end
  cli -->|"investigate, verify, decide"| root
  cli -->|"read-only scan and copy/worktree"| app
  runs -.->|"must not write back"| app
```

| Tree | Role |
|------|------|
| Mosaic source | The tool |
| Application `--repo` | The software under investigation |
| Disposable `--root` | Cases, ledger, candidates |

Never point `--repo` at the Mosaic source tree. Its `tests/` become the public
floor.

## Roles and isolation

```mermaid
flowchart TD
  orch["Orchestrator: unsandboxed"] --> prep["candidate prepare"]
  prep --> ws["Candidate workspace"]
  orch --> builder["Builder process"]
  orch --> verifier["Verifier process"]
  builder --> ws
  verifier -->|"read only"| ws
  subgraph deny["Denied to Builder"]
    hidden["hidden evaluators"]
    const["constitution"]
    hist["raw historian"]
    scen["future scenarios"]
  end
  builder -.->|"sandbox-exec deny"| deny
  verifier -.->|"write deny"| ws
```

- **Builder** writes only inside an isolated candidate. It cannot read hidden
  evaluators, constitution, the raw historian, or future scenarios.
- **Verifier** runs floors against a frozen candidate. It cannot edit that
  candidate while evaluation is active.
- **Historian** appends events. It does not rewrite them.
- **Observer** records post-change evidence. It cannot `decide`.
- **Amender** exists only in Maintenance Mode, in a separate worktree.

`permissions.yaml` names these roles. Enforcement, where it exists, is the
executor plus `sandbox-exec` on macOS.

## Admission

```mermaid
flowchart TD
  v["Verdicts"] --> hard{"Any configured hard floor failed?"}
  hard -->|yes| drop["eliminated_by_floor"]
  hard -->|no| kind["Group by kind"]
  kind --> pareto["Pareto on change surface, budget, reversible, human intervention"]
  pareto --> front["frontier"]
  front --> obs{"Observation plan has signal and rollback_trigger?"}
  obs -->|no| refuse["Refuse CODE_CHANGE"]
  obs -->|yes| code{"A code candidate is on the frontier?"}
  code -->|yes| ok["CODE_CHANGE eligible"]
  code -->|no| refuse2["NO_CHANGE or refuse"]
```

1. Apply hard floors. Unconfigured hidden/property suites are not passes.
2. Drop floor failures.
3. Compare remaining same-kind candidates by Pareto dominance (smaller change
   surface, smaller budget, reversible, no human intervention).
4. `CODE_CHANGE` is eligible only if a code candidate remains on the frontier
   and the observation plan names `signal` and `rollback_trigger`.

There is no weighted score.

## Modules

| Module | Job |
|--------|-----|
| `workflow` | Investigate, evidence, propose, decide, rebuild |
| `candidate` | Prepare, build, verify, compare candidates |
| `isolation` / `executor` / `workspace` | Sandbox, run, materialize |
| `builder` | Scripted writes confined to the candidate |
| `verifiers` | Public, hidden, mutation, property, differential |
| `admission` | Floor-then-Pareto gate |
| `tournament` | Equal-budget hidden scenarios |
| `observers` / `memory` / `amendment` / `anchor` | After-change and self-change |

## Trust

```mermaid
flowchart LR
  ev["Event"] --> hash["SHA-256 of unsigned payload"]
  hash --> chain["previous_hash link"]
  chain --> jsonl["events.jsonl"]
  jsonl --> snap["projection_materialized snapshot"]
  snap --> pack["Decision Pack"]
  pack -->|"tamper"| verifyFail["verify fails"]
  jsonl -->|"replace whole file"| blind["hash chain cannot see it"]
  jsonl -->|"ledger export"| witness["External head witness"]
  witness --> compare["ledger compare"]
```

The ledger detects tampering. It does not stop someone with filesystem write
access from replacing the file. `mosaic ledger export` writes a head witness
for comparison outside the tree. That is not a timestamp authority.
