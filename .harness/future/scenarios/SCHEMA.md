# Future scenario contract

Future Maintainer Tournament scenarios are intentionally a post-V0 capability.
A future scenario must record:

- an opaque scenario identifier;
- its provenance class without leaking the task to candidate Builders;
- preconditions and applicable base revision;
- target and preservation constraints;
- an equal model, harness, time, token, context, and verifier budget;
- floor tests and independent probes;
- change surface, reversibility, observability, and human-intervention measures;
- contamination controls; and
- raw run artifact references.

Candidate selection must apply hard floors first, remove dominated candidates,
construct the Pareto frontier, and only then apply organization risk policy.
A weighted aggregate score must not override a failed hard constraint.

