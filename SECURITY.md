# Security

Mosaic is a local decision harness. It is not a sandbox for untrusted network
services, and `permissions.yaml` is not a security boundary.

## What is in scope

- Path escape from a Builder script into the source tree
- Hidden evaluator contents appearing in Builder input, logs, or artifacts
- Ledger rewrite or deletion through the public CLI
- Constitution mutation in Normal Mode
- Secrets leaking from a candidate workspace

## What is out of scope until claimed

- Linux or Windows process isolation
- Stopping an operator who can replace the entire ledger file
- Authenticating evidence provenance
- Multi-tenant or remote execution

## Reporting

Open a private security advisory on the repository host if one exists, or
email the maintainers listed in the hosting project. Do not file a public
issue that includes hidden evaluator contents or exploit payloads.

## Isolation note

On macOS, Builder and Verifier commands run under `/usr/bin/sandbox-exec`.
That enforcement is only as strong as the tested profile. Do not treat a
passing unit test as proof that an untested host is isolated.
