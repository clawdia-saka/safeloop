# SafeLoop threat model and audit boundaries

SafeLoop's current public alpha is intentionally narrow. It gives operators a local, reviewable record of what a supervised command did, plus rollback tooling for the covered local file changes that SafeLoop can verify. It is not a security boundary around a hostile agent or host.

## What SafeLoop protects today

SafeLoop focuses on recoverability and review for local CLI runs:

- It records local run metadata, command output, timeline entries, checkpoint metadata, manifests, diffs, rollback plans, and verification results.
- It makes those artifacts tamper-evident, not tamper-proof: hash chains and recorded digests can reveal many after-the-fact edits, but they do not prevent edits by someone who controls the machine or artifact directory.
- It supports exact rollback only for covered local file changes. Rollback is scoped to files and hunks represented in SafeLoop's manifests/diffs, and the target state is verified again at apply time before SafeLoop reports success.
- It classifies external actions as requiring manual review or compensation; in short, external actions require manual review or compensation. API calls, hosted service changes, messages, tickets, deployments, payments, and other outside-system effects are not made exactly reversible by SafeLoop.
- It treats audit artifacts as review aids, not absolute truth. They help an operator inspect evidence and spot inconsistencies; they are not a forensic guarantee that every relevant fact was captured.

## Covered local rollback boundary

"Exact rollback" means SafeLoop can apply a reviewed rollback plan for covered local file changes and verify the result it can observe locally. This claim does not extend to:

- files excluded from capture, ignored by policy, outside the configured repository/worktree, or changed by tools SafeLoop was not watching;
- hidden state such as databases, caches, background daemons, browser profiles, cloud resources, or local services unless a future integration explicitly covers them;
- concurrent edits that conflict with the recorded pre-apply state; or
- external systems that require their own cleanup, confirmation, or compensation workflow.

If apply-time verification cannot confirm the covered local target state, SafeLoop should surface that uncertainty instead of presenting the rollback as complete.

## External action boundary

External actions require manual review or compensation because SafeLoop cannot un-send a message, un-create a remote issue, reverse a payment, or prove a hosted service returned to an earlier state. Compensation may be useful, but compensation is not exact rollback. A compensation record means a configured cleanup or corrective step was attempted or completed; it does not mean the original outside effect never happened.

## Audit artifact boundary

SafeLoop audit artifacts are review aids, not absolute truth. Operators should use them to answer practical questions such as what command ran, which local files changed, whether recorded artifacts still match their digests, and what remains outside SafeLoop's rollback scope.

Artifact verification can detect mismatches in the evidence SafeLoop recorded. It cannot prove that the agent was honest, that the operating system was uncompromised, that every side effect was observed, or that no attacker with host access rewrote both artifacts and code before review.

## Non-goals for the current public alpha

The following are non-goals for the current public alpha:

- defending against a malicious agent that intentionally evades logging, tampers with the working tree, or abuses credentials;
- defending a compromised machine, kernel, filesystem, shell, Python environment, or SafeLoop installation;
- defeating a hosted attacker with control over remote services, hosted runners, dashboards, or adapter infrastructure;
- providing a hosted control-plane security boundary, remote transparency log, or production governance system; and
- proving complete forensic truth for every local and external action.

These limits keep the public alpha modest: SafeLoop is a local recoverability and audit-assistance tool for cooperative or partially trusted agent workflows, not a complete security product.
