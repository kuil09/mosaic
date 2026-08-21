# Using Mosaic 0.3

Mosaic keeps the application repository, harness workspace, and Mosaic source
tree separate. The application is read to create candidates; accepted work is
never written back automatically.

## 1. Investigate a claim

```bash
mosaic --root /tmp/case init
mosaic --root /tmp/case investigate CASE-1 \
  --issue path/to/issue.md \
  --repo path/to/application \
  --signal "The frozen target fails after deployment." \
  --rollback-trigger "Dispose the candidate; do not merge."
```

The initial outcome is `INSUFFICIENT_EVIDENCE`. Record evidence with
`mosaic evidence add`; Mosaic will not infer causal support from a passing test
alone.

## 2. Define the frozen verification contract

Public checks use argv arrays, never shell strings. Their entrypoints and other
inputs are copied outside the candidate when the pair is created.

```json
{
  "schema_version": "1.0.0",
  "contract_id": "VC-CASE-1",
  "checks": [
    {
      "id": "target-defect",
      "role": "target",
      "visibility": "public",
      "argv": ["python3", "{frozen_public}/contract/target.py"],
      "frozen_inputs": ["target.py"]
    },
    {
      "id": "preserve-api",
      "role": "preservation",
      "visibility": "public",
      "argv": ["python3", "{frozen_public}/contract/preserve.py"],
      "frozen_inputs": ["preserve.py"]
    },
    {
      "id": "holdout-defect",
      "role": "target",
      "visibility": "hidden",
      "evaluator_id": "case-1.py"
    }
  ],
  "required_adapters": ["hidden", "mutation"],
  "budgets": {
    "max_seconds": 30,
    "max_output_bytes": 65536
  }
}
```

At least one public target check is required. Hidden checks expose only their
`evaluator_id`. Required adapters fail closed when absent, erroneous, denied,
or timed out.

## 3. Define the durable work contract

A work contract names the objective, constraints, acceptance criteria, and a
deterministic single-worker task graph. Every acceptance criterion references
at least one verification check.

```json
{
  "schema_version": "1.0.0",
  "contract_id": "WC-CASE-1",
  "case_id": "CASE-1",
  "objective": "Fix the defect without changing the public API.",
  "constraints": [
    {
      "id": "C-TARGET",
      "role": "target",
      "force": "hard",
      "statement": "The defect check must pass.",
      "evidence_condition": "target-defect changes from FAIL to PASS."
    },
    {
      "id": "C-PRESERVE",
      "role": "preservation",
      "force": "hard",
      "statement": "The public API must remain compatible.",
      "evidence_condition": "preserve-api passes on zero-change and code."
    },
    {
      "id": "C-BOUNDARY",
      "role": "boundary",
      "force": "hard",
      "statement": "Only CASE-1 is in scope.",
      "evidence_condition": "Every artifact references CASE-1."
    },
    {
      "id": "C-RESOURCE",
      "role": "resource",
      "force": "soft",
      "statement": "Stay within the verification budget.",
      "evidence_condition": "Actual time and output use are recorded."
    }
  ],
  "non_goals": ["Parallel workers", "A multi-agent UI"],
  "acceptance_criteria": [
    {
      "id": "AC-1",
      "statement": "The defect is fixed and the API is preserved.",
      "verification_check_ids": ["target-defect", "preserve-api"]
    }
  ],
  "tasks": [
    {
      "id": "T-INVESTIGATE",
      "kind": "investigate",
      "description": "Establish the code cause.",
      "depends_on": [],
      "read_set": ["src"],
      "write_set": [],
      "acceptance_criteria_ids": ["AC-1"]
    },
    {
      "id": "T-IMPLEMENT",
      "kind": "implement",
      "description": "Implement the smallest viable fix.",
      "depends_on": ["T-INVESTIGATE"],
      "read_set": ["src"],
      "write_set": ["src/app.py"],
      "acceptance_criteria_ids": ["AC-1"]
    }
  ],
  "completion_policy": {
    "success_outcomes": ["CODE_CHANGE", "NO_CHANGE"],
    "stop_outcomes": [
      "INSTRUMENT_FIRST",
      "POLICY_CONFLICT",
      "INSUFFICIENT_EVIDENCE",
      "ISSUE_REJECTED"
    ]
  }
}
```

Create work and finish the investigation task with a Decision Pack evidence
reference:

```bash
mosaic --root /tmp/case work create CASE-1 \
  --contract work-contract.json --actor planner@example.com
mosaic --root /tmp/case work next CASE-1
mosaic --root /tmp/case work task start CASE-1 T-INVESTIGATE \
  --actor worker@example.com
mosaic --root /tmp/case work task complete CASE-1 T-INVESTIGATE \
  --evidence-ref E-0001 --actor worker@example.com
```

## 4. Prepare and implement one experiment pair

```bash
mosaic --root /tmp/case candidate prepare-pair CASE-1 \
  --repo path/to/application \
  --verification-contract verification-contract.json
mosaic --root /tmp/case work attach-pair CASE-1 PAIR_ID \
  --code-run CODE_RUN --actor worker@example.com
mosaic --root /tmp/case work task start CASE-1 T-IMPLEMENT \
  --actor worker@example.com
mosaic --root /tmp/case candidate build CASE-1 CODE_RUN --script fix.json
mosaic --root /tmp/case work task complete CASE-1 T-IMPLEMENT \
  --evidence-ref run:CODE_RUN --actor worker@example.com
mosaic --root /tmp/case work implementation-complete CASE-1 \
  --actor worker@example.com
```

A scripted build is a JSON array of writes confined to the candidate:

```json
[
  {
    "op": "write",
    "path": "src/app.py",
    "contents": "..."
  }
]
```

`implementation-complete` freezes the candidate. A failed frozen attempt is
never reopened:

```bash
mosaic --root /tmp/case candidate retry-code CASE-1 PAIR_ID \
  --from FAILED_RUN
mosaic --root /tmp/case work attach-pair CASE-1 PAIR_ID \
  --code-run NEW_RUN --actor worker@example.com
```

## 5. Verify, propose, decide, and finish

```bash
mosaic --root /tmp/case candidate verify-pair CASE-1 PAIR_ID \
  --code-run CODE_RUN
mosaic --root /tmp/case candidate compare CASE-1 PAIR_ID
mosaic --root /tmp/case propose CASE-1
mosaic --root /tmp/case show CASE-1
mosaic --root /tmp/case decide CASE-1 CODE_CHANGE \
  --proposal PROPOSAL_ID \
  --actor human@example.com \
  --rationale "The frozen pair proves the target improvement."
mosaic --root /tmp/case work finish CASE-1 --actor worker@example.com
mosaic --root /tmp/case verify CASE-1
```

`decide` requires the exact latest proposal. Evidence, replan, candidate,
revision, floor, snapshot, or ledger changes make that proposal stale. There is
no override path.

`CONFIGURATION_CHANGE`, `DOCUMENTATION_CHANGE`, and `OPERATIONAL_ACTION` remain
inadmissible until typed verification adapters are implemented.

## 6. Resume and replan

```bash
mosaic --root /tmp/case work status CASE-1
mosaic --root /tmp/case work resume CASE-1 --format json
mosaic --root /tmp/case work resume CASE-1 --format markdown
mosaic --root /tmp/case work replan CASE-1 \
  --contract work-contract-r2.json \
  --reason "The investigation found a new implementation dependency." \
  --actor planner@example.com
```

The ResumePacket contains the objective, hard constraints, task state,
evidence references, active pair/run/snapshot, stale artifacts, and allowed
next commands. It contains neither hidden evaluator details nor chain-of-thought.

Replan may add or replace unfinished tasks and adjust dependencies or read/write
sets. It cannot change the objective, constraints, non-goals, acceptance
criteria, completion policy, or completed task definitions. Such a change needs
a new case.

## 7. Legacy cases and isolation

Decision Pack 1.0 cases are read-only. Only `show`, `verify`, `candidate show`,
and ledger export/compare remain available. Start 0.2/0.3 work with a new case
ID; Mosaic does not rewrite the legacy ledger.

Real Builder/Verifier execution requires a successful macOS
`sandbox-exec` capability probe. Linux and Windows are not claimed as isolated
platforms. Portable fake-runner tests validate state and verdict semantics, not
OS isolation.
