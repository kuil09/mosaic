# Using Mosaic

## Investigate a claim

```bash
mosaic --root /tmp/case init
mosaic --root /tmp/case investigate CASE-1 \
  --issue path/to/issue.md \
  --repo path/to/application \
  --signal "The test that encodes the defect passes." \
  --rollback-trigger "Dispose the candidate; do not merge."
```

The first outcome is `INSUFFICIENT_EVIDENCE`. That is intentional.

## Record evidence and propose

```bash
mosaic --root /tmp/case evidence add CASE-1 \
  --claim H-OBSERVABILITY-GAP \
  --direction supporting \
  --strength strong \
  --summary "Logs share no correlation identifier." \
  --source-type log-inspection \
  --source-ref incident-42
mosaic --root /tmp/case challenge CASE-1
mosaic --root /tmp/case propose CASE-1
mosaic --root /tmp/case show CASE-1
```

## Build and compare candidates

```bash
mosaic --root /tmp/case candidate prepare CASE-1 --repo path/to/application --kind zero-change
mosaic --root /tmp/case candidate prepare CASE-1 --repo path/to/application --kind code
mosaic --root /tmp/case candidate build CASE-1 CODE_RUN --script fix.json
mosaic --root /tmp/case candidate verify CASE-1 ZERO_RUN
mosaic --root /tmp/case candidate verify CASE-1 CODE_RUN
mosaic --root /tmp/case candidate compare CASE-1 ZERO_RUN CODE_RUN
```

A scripted build is a JSON array of writes. Paths must stay inside the
candidate workspace:

```json
[{"op": "write", "path": "src/app.py", "contents": "..."}]
```

## Decide

```bash
mosaic --root /tmp/case decide CASE-1 CODE_CHANGE \
  --actor you@example.com \
  --rationale "The code candidate survives the floor that encodes the defect."
mosaic --root /tmp/case verify CASE-1
```

`NO_CHANGE` does not need a code candidate. State-changing outcomes do, plus
an observation plan. If `investigate` omitted `--signal`, run
`mosaic observation-plan` before `decide`.

`--override` accepts a refused state-changing outcome and records the failed
floors as unresolved questions.

## After a change

```bash
mosaic --root /tmp/case observe CASE-1 \
  --kind incident --direction opposing \
  --summary "The signal fired in production." \
  --source-ref pager-1
```

An opposing incident can mark an accepted state-changing outcome
`superseded` without deleting the decision event.

## Maintenance Mode

`mosaic amend` refuses unless `--mode maintenance` and a separate
`--worktree` are supplied. Ratify requires a passing holdout file. Rollback
restores `.harness/constitution/previous/` and does not rewrite history.
