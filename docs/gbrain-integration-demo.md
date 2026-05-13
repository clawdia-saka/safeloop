# SafeLoop + Gbrain integration demo

This demo shows a local-only SafeLoop integration pattern for agents that use Gbrain-style retrieved knowledge without giving Gbrain control over execution.

## Role split

- Gbrain provides retrieved context/knowledge evidence: a local mock fixture writes `retrieved_context.json` and `retrieved_context.brief.md` with citations, confidence, and decision notes.
- The agent decides and acts: the demo agent reads the retrieved context and edits a file in a temporary git repository.
- SafeLoop records action, evidence, rollback, and manual review: `safeloop watch-run` captures the local file change, the script copies the retrieved-context evidence into the run directory, generates `review-summary.json`, creates `rollback-plan.json`, and writes an operator packet.
- Gbrain is not the scheduler or control plane: it does not launch commands, approve work, enforce policy, schedule retries, or apply rollback. SafeLoop and the human operator own audit, review, and rollback flow.

## Safety boundaries

This fixture intentionally does not require a real Gbrain install. It does not read or write `~/.gbrain`, does not use a network, and does not mutate TT's production Gbrain DB. All files live under a temporary demo workspace.

The mock exists to document the integration contract:

1. Retrieval systems may supply evidence to the agent.
2. Agents may use that evidence to make a local change.
3. SafeLoop records what happened and offers exact rollback for covered local file changes.
4. External systems and knowledge stores remain manual-review/compensation territory unless separately instrumented.

## Run it

```bash
bash examples/gbrain_context_demo.sh
```

The script prints the retained temp workspace and writes:

- `gbrain-mock/retrieved_context.json`: local mock retrieval payload.
- `gbrain-mock/retrieved_context.brief.md`: operator-readable retrieval brief.
- SafeLoop run directory with copied `retrieved_context.json` and `retrieved_context.brief.md`.
- `review-summary.json`, `rollback-plan.json`, and `rollback-result.json` in the run directory.
- `operator-packet.md` with the role split, evidence paths, manual-review notes, and rollback commands.

The demo applies rollback at the end to prove exact local rollback is possible for the covered file change.
