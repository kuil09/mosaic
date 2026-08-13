# Mosaic

<p align="center">
  <img src="docs/brand/mosaic-icon.svg" alt="Mosaic mark: a tessera not yet seated" width="160" height="160">
</p>

<p align="center">
  <strong>An epistemic harness for software change.</strong><br>
  Mosaic records what you believe, what you do not know, and which interventions
  are still admissible.
</p>

[![License: MIT](https://img.shields.io/badge/license-MIT-1a2b24)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-1a2b24)](pyproject.toml)

Mosaic is not a coding agent. It does not assume every issue needs a patch, or
that a passing test proves the issue is resolved. A code change is one possible
intervention. `NO_CHANGE` is a valid final answer.

The mark is a tile that has not been set. Missing sockets are uncertainty. The
gold grout is a verification thread, not a score.

## Status

Usable as a local CLI on macOS with `/usr/bin/sandbox-exec`. Isolation is not
claimed on Linux or Windows. There is no LLM Builder and no network ledger
anchor. `permissions.yaml` names roles; it is not a security control.

See [docs/architecture.md](docs/architecture.md) for the control loop and
[docs/using.md](docs/using.md) for the full command sequence.

## Install

Python 3.11 or newer. No third-party runtime dependencies. Git is optional.

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
mosaic --version
```

From a source checkout:

```bash
PYTHONPATH=src python3 -m mosaic_harness --help
```

## How it decides

A state-changing `CODE_CHANGE` is admitted only when all of these hold:

1. A Builder produced the change in an isolated candidate.
2. A Verifier ran the same public floor on that candidate and on zero-change.
3. Configured hard floors pass. A missing hidden or property suite is not a pass.
4. The observation plan names a `signal` and a `rollback_trigger`.
5. The code candidate is not floor-eliminated and sits on the same-kind Pareto
   frontier.

`--override` can force a refused state-changing outcome. Failed floors stay
visible. There is no weighted score.

## Quick start

Keep three trees separate: this tool, the application `--repo`, and a
disposable `--root` for the ledger. Do not point `--repo` at Mosaic itself.

```bash
mosaic --root /tmp/case init
mosaic --root /tmp/case investigate CASE-1 \
  --issue path/to/issue.md \
  --repo path/to/application \
  --signal "The test that encodes the defect passes." \
  --rollback-trigger "Dispose the candidate; do not merge."

mosaic --root /tmp/case candidate prepare CASE-1 --repo path/to/application --kind zero-change
mosaic --root /tmp/case candidate prepare CASE-1 --repo path/to/application --kind code
mosaic --root /tmp/case candidate build CASE-1 CODE_RUN --script fix.json
mosaic --root /tmp/case candidate verify CASE-1 ZERO_RUN
mosaic --root /tmp/case candidate verify CASE-1 CODE_RUN
mosaic --root /tmp/case candidate compare CASE-1 ZERO_RUN CODE_RUN
mosaic --root /tmp/case decide CASE-1 CODE_CHANGE \
  --actor you@example.com \
  --rationale "The code candidate survives the floor that encodes the defect."
mosaic --root /tmp/case verify CASE-1
```

A sibling experiment target, **Spike**, lives next to this repository when you
clone both. Its public floor is a single failing test about completed tickets
staying on the active rail.

## Commands

| Area | Commands |
|------|----------|
| Case | `init` `investigate` `evidence add` `challenge` `propose` `show` `decide` `verify` `rebuild` `observation-plan` |
| Candidate | `candidate prepare` `exec` `build` `verify` `compare` `dispose` `interrupt` `show` |
| After | `observe` `memory invalidate` `tournament run` `ledger export` `ledger compare` |
| Constitution | `amend` (Maintenance Mode only) |

## Development

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Please read [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md)
before sending a change. [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) applies.

## License

[MIT](LICENSE). Brand stills are in [docs/brand/](docs/brand/).
