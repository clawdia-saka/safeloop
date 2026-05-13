# Real-world agent run examples

These examples show realistic SafeLoop review shapes without overclaiming. They are local-only fixtures, not production integrations, and they use no network access. Their purpose is to demonstrate what evidence an operator should expect from an agent run and where SafeLoop draws the rollback boundary.

## Run all examples locally

```bash
python examples/coding_agent_run.py --output-dir /tmp/safeloop-real-world/coding
python examples/research_intel_run.py --output-dir /tmp/safeloop-real-world/research
python examples/browser_api_action_run.py --output-dir /tmp/safeloop-real-world/outside-action
```

Each script prints JSON and writes a `run-summary.json` file under its output directory.

## Coding agent run: local file change, test evidence, rollback plan

Script: `examples/coding_agent_run.py`

What it simulates:
- An agent edits a local Python file.
- A focused pytest check runs against the changed behavior.
- Artifacts capture `before.txt`, `after.txt`, `diff.patch`, `test-output.txt`, and `rollback-plan.json`.

Rollback boundary:
- `exact_rollback: true` applies only to the covered local file fixture.
- The rollback plan is a restore-original-file-contents plan, not a claim about external systems or hidden state.

## Research/intel run: source brief with stale/low confidence marker

Script: `examples/research_intel_run.py`

What it simulates:
- An agent assembles a short evidence brief from local fixture sources.
- The brief records source IDs, source dates, and an explicit confidence section.
- The output includes `STALE / LOW CONFIDENCE` because no live source refresh is performed.

Rollback boundary:
- The artifacts can be deleted or regenerated locally.
- SafeLoop does not claim that the real-world facts are current; the example is evidence packaging only.

## Browser/API-like action run: local fixture, blocked/manual review

Script: `examples/browser_api_action_run.py`

What it simulates:
- An agent proposes an outside action such as posting a customer-visible comment.
- The example uses a `local-fixture://` target and never calls a browser or API.
- SafeLoop records `blocked-action.json` and `manual-review.md` instead of performing the action.

Rollback boundary:
- `exact_rollback: false` for the outside action.
- The correct outcome is `blocked_manual_review` until an operator approves the real-world action and records a compensation plan if needed.

## Claims these examples intentionally avoid

- No network calls are made.
- No hosted browser, SaaS, CRM, GitHub, Slack, or ticketing system is contacted.
- External actions are not described as exactly reversible.
- Fixture evidence is not presented as current intelligence.
