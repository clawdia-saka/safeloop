# Issue 8 Spec: HANDED_OFF semantics and operator boundary

Issue: #8

## Decision
`HANDED_OFF` is a pre-execution terminal state. It means approval escalated to an operator boundary before any executor call occurred.

## Required semantics
- `ApprovalDecision.ESCALATE` maps to `HANDED_OFF`.
- `ApprovalDecision.BLOCK` maps to `FAILED` and is distinct from `HANDED_OFF`.
- `HANDED_OFF` is terminal for the current runtime run and does not auto-resume.
- SafeLoop does not auto-run compensation around handoff because no execution happened.
- `HANDED_OFF` must not be reachable from `EXECUTING` in the MVP transition graph.

## Runtime / journal requirements
- Allowed transition: `APPROVED -> HANDED_OFF`.
- Forbidden transition: `EXECUTING -> HANDED_OFF`.
- Escalated runs must not call the executor.
- Escalated runs must not invoke compensation hooks.

## API / viewer requirements
- Viewer and API continue surfacing `handed_off` as the terminal state.
- Docs must explain that `handed_off` means operator-owned follow-up, not paused execution.

## Acceptance criteria
- Transition tests lock in `APPROVED -> HANDED_OFF` and reject `EXECUTING -> HANDED_OFF`.
- Runtime tests verify escalated runs do not execute or compensate.
- Documentation explicitly differentiates block vs escalate and explains operator boundary.
