# Task 9 plan: technical hardening + positioning hardening

## Branch and baseline
- Branch target: `feat/task-9-hardening`
- Baseline commit on this branch today: `6f5ad68` (`main` + current branch point)
- Current repo reality on this branch:
  - implemented now: package skeleton, `ActionEnvelope`, `EffectClass`, `JournalState` state machine
  - still placeholder on this branch: `src/safeloop/storage.py`, `src/safeloop/hooks.py`, `src/safeloop/runtime.py`, `src/safeloop/api.py`
  - minimal README only; no integrated example or operator docs yet
- Sibling task branches exist but are not merged here:
  - `feat/task-4-journal-storage`
  - `feat/task-5-hooks`
  - `feat/task-6-runtime`
  - `feat/task-7-api`
  - `feat/task-8-demo`

## Task 9 redefinition
Task 9 is no longer a generic "final integration review and hardening" task.
It should be executed as two explicit streams:

1. **Technical hardening**: merge-safe integration, contract tightening, test stabilization, and production-of-MVP consistency across storage, hooks, runtime, API, and example flow.
2. **Positioning hardening**: README rewrite, capability framing, comparison/FAQ/case-study docs, and OSS-facing messaging that explains what safeloop is, what it is not, and why the MVP matters.

---

## Stream 1: technical hardening

### TH-1. Land the missing MVP building blocks onto this branch
**Goal:** replace placeholders with the concrete MVP from Tasks 4-8 before hardening behavior.

**Recommended integration strategy**
- Cherry-pick in dependency order, resolving conflicts locally instead of trying to merge all branches at once:
  1. Task 4: `9dc801b`, `d1f6575`
  2. Task 5: `3b5fbbc`, `6dcf172`
  3. Task 6: `149736a`, `3fe9b7d`, `b0c82d5`
  4. Task 7: `75f902a`, `0673e78`
  5. Task 8: `de538b7`, `f4ee85e`
- If cherry-picks create semantic conflicts, prefer **re-implementation over blind conflict resolution** for:
  - `src/safeloop/runtime.py`
  - `src/safeloop/api.py`
  - `README.md`
  - `examples/github_pr_demo.py`

**Files likely to change**
- `src/safeloop/storage.py`
- `src/safeloop/hooks.py`
- `src/safeloop/runtime.py`
- `src/safeloop/api.py`
- `examples/__init__.py`
- `examples/github_pr_demo.py`
- `tests/test_storage.py`
- `tests/test_hooks.py`
- `tests/test_runtime.py`
- `tests/test_api.py`
- `tests/test_examples_github_demo.py`

### TH-2. Replace in-memory seams with one source of truth
**Goal:** make storage-backed journal history the authoritative run record for runtime, API, and demo inspection.

**Concrete work**
- Integrate `LocalJournalStorage` from Task 4 as the persistence path for runtime and API reads.
- Remove or minimize duplicated run reconstruction logic between runtime and API.
- Add a shared helper or tiny read-model layer so `list_runs`, `get_run`, and `list_journal_entries` derive from one contract.
- Ensure append order is explicitly the MVP ordering rule unless sequence metadata is introduced.

**Files likely to change**
- `src/safeloop/storage.py`
- `src/safeloop/runtime.py`
- `src/safeloop/api.py`
- possibly `src/safeloop/journal.py` if shared projection helpers belong there

### TH-3. Tighten approval, handoff, compensation, and resume semantics
**Goal:** make Task 5 and Task 6 semantics explicit and internally consistent.

**Concrete work**
- Normalize approval outcomes so hook decisions map cleanly to runtime transitions.
- Resolve the current naming mismatch between Task 5 approval decisions (`allow/block/escalate`) and Task 6 runtime handling (`handoff` string path).
- Decide and document whether `ESCALATE` maps to `HANDED_OFF` or to a blocked/pre-execution terminal outcome.
- Verify compensation only runs for `COMPENSATABLE_WRITE` and only after execution has begun.
- Verify resumed runs cannot bypass approval bookkeeping or mutate `run_id`/`action_id` identity.

**Files likely to change**
- `src/safeloop/hooks.py`
- `src/safeloop/runtime.py`
- `src/safeloop/journal.py`
- `tests/test_hooks.py`
- `tests/test_runtime.py`
- `tests/test_journal.py`

### TH-4. Harden API/read model behavior
**Goal:** make Task 7 a stable operator-facing inspection surface.

**Concrete work**
- Keep `GET /runs`, `GET /runs/{run_id}`, and `GET /runs/{run_id}/journal` aligned with runtime/storage truth.
- Preserve `JournalState` strings as the external state contract.
- Fix unknown-run semantics once and enforce them everywhere (`404` for detail/journal; empty list only for top-level list).
- Confirm deterministic ordering for list/detail/journal responses.
- Avoid API-only state translation layers that duplicate runtime semantics.

**Files likely to change**
- `src/safeloop/api.py`
- `src/safeloop/runtime.py`
- `src/safeloop/storage.py`
- `tests/test_api.py`

### TH-5. Rebuild the demo around runtime-owned truth
**Goal:** make Task 8 demonstrate the real system instead of a parallel toy flow.

**Concrete work**
- Rework `examples/github_pr_demo.py` so it executes through the real runtime rather than manually inventing journal states.
- Ensure demo-generated `run_id`, `action_id`, and final state are runtime-owned and API-readable.
- Keep GitHub behavior mocked/local-safe.
- Validate both success and compensation flows through the same runtime/storage/API surface.

**Files likely to change**
- `examples/github_pr_demo.py`
- `tests/test_examples_github_demo.py`
- `README.md`
- possibly `src/safeloop/api.py` if the demo exposes inspection helpers

### TH-6. Public surface cleanup and package polish
**Goal:** make the MVP discoverable and import-safe.

**Concrete work**
- Review package exports in `src/safeloop/__init__.py`.
- Export the minimal stable surface intentionally: core types, runtime, storage/viewer entrypoints as appropriate.
- Verify there are no circular imports between journal/runtime/storage/api modules.
- Add smoke tests for import stability after integration.

**Files likely to change**
- `src/safeloop/__init__.py`
- `src/safeloop/runtime.py`
- `src/safeloop/api.py`
- `tests/test_smoke.py`

---

## Stream 2: positioning hardening

### PH-1. README rewrite
**Goal:** replace the current 3-line placeholder README with a real OSS-facing entry point.

**README scope**
- What safeloop is: a minimal transactional runtime for controlled side effects
- Problem statement: why agent/action systems need journaling, approvals, compensation, and resumability
- Core concepts:
  - effect classes
  - action envelope
  - journal lifecycle / state machine
- MVP boundaries / non-goals:
  - local-only
  - file-backed persistence
  - no live GitHub integration by default
  - not production durability/distributed orchestration yet
- Quickstart:
  - install
  - run tests
  - run the GitHub-style demo
  - run/view the local API if present
- Architecture overview with exact module pointers
- Link out to FAQ/comparison/case-study docs added below

**Primary file**
- `README.md`

### PH-2. Comparison / FAQ documentation
**Goal:** position safeloop clearly against adjacent ideas and reduce first-contact confusion.

**Proposed docs to add**
- `docs/faq.md`
  - What is a compensatable write?
  - Difference between `run_id` and `action_id`
  - When do approvals run?
  - What does resumable mean in this MVP?
  - Why file-backed storage instead of a database?
- `docs/comparisons.md`
  - safeloop vs ordinary task queues
  - safeloop vs workflow/orchestration engines
  - safeloop vs ad hoc retries / "just call the API"
  - safeloop vs fully durable saga platforms

**Potential supporting file changes**
- `README.md`
- `docs/index` entry only if a docs landing page is introduced later

### PH-3. Case-study / reference narrative
**Goal:** give one persuasive concrete story for users evaluating the project.

**Proposed doc**
- `docs/case-studies/github-pr-demo.md`
  - frame the GitHub pull-request flow as a transactional action example
  - show success path and rollback path
  - map example behavior to `ActionEnvelope`, `EffectClass`, journal states, compensation, and inspection API
  - explicitly note the example is mocked/reference-only

**Likely files**
- `docs/case-studies/github-pr-demo.md`
- `README.md`
- `examples/github_pr_demo.py`

### PH-4. Terminology alignment pass
**Goal:** eliminate vocabulary drift across code, docs, tests, and API output.

**Concrete work**
- Standardize on one term set for:
  - approval / block / escalate / handoff
  - compensation vs rollback
  - run viewer vs API
  - journal entry vs run detail vs run summary
- Ensure README, API docs, example comments, and tests all use the same state names and capability claims.

**Files likely to change**
- `README.md`
- `docs/faq.md`
- `docs/comparisons.md`
- `docs/case-studies/github-pr-demo.md`
- `src/safeloop/api.py`
- test files as needed

---

## Integration strategy: what to cherry-pick vs what to re-implement

### Cherry-pick directly with minimal change
- **Task 4 storage**: concrete and self-contained; cherry-pick both commits
- **Task 5 hooks**: mostly self-contained; cherry-pick both commits, then harden semantics during Task 9
- **Task 7 API**: cherry-pick as a starting point, then refactor to consume runtime/storage truth

### Cherry-pick, then immediately harden
- **Task 6 runtime**: cherry-pick, but expect follow-up edits for approval decision alignment, storage wiring, and run reconstruction consistency
- **Task 8 demo**: cherry-pick only as scaffolding; rewrite to use the real runtime path rather than manual state assembly

### Re-implement instead of preserving branch behavior when conflicts arise
- `README.md`: Task 8 README is too narrow for Task 9 positioning needs
- `examples/github_pr_demo.py`: current branch version manually fabricates journal flow and should become runtime-backed
- any API or runtime helper that duplicates projection logic instead of sharing one source of truth

---

## Technical hardening checklist
- [ ] All placeholders removed from storage/hooks/runtime/api paths
- [ ] Runtime, API, and demo use one shared source of truth for run/journal state
- [ ] Approval decision vocabulary is reconciled and documented
- [ ] `ESCALATE`/handoff semantics are explicit and tested
- [ ] Compensation behavior is limited to compensatable writes
- [ ] Resume/checkpoint flow preserves identity and approval invariants
- [ ] Unknown-run semantics are consistent across API detail/journal endpoints
- [ ] Journal ordering is deterministic and documented
- [ ] Demo uses real runtime transitions, not hand-built state sequences
- [ ] README instructions match actual entrypoints and commands
- [ ] Package exports/imports are stable
- [ ] Full test suite passes from a clean repo root run

## Positioning hardening checklist
- [ ] README explains problem, model, quickstart, and non-goals
- [ ] FAQ covers recurring operator/developer questions
- [ ] Comparison doc positions safeloop relative to nearby categories
- [ ] GitHub PR case study connects conceptual docs to runnable example
- [ ] Documentation does not overpromise unsupported capabilities
- [ ] Terminology is consistent across README, code, tests, and API

## Verification commands
Run from repo root after integration work lands:

```bash
python -m pip install -e .
pytest tests/ -q
pytest tests/test_storage.py tests/test_hooks.py tests/test_runtime.py tests/test_api.py tests/test_examples_github_demo.py -q
python examples/github_pr_demo.py
python -c "from safeloop import ActionEnvelope, EffectClass, Runtime; print(Runtime.__name__)"
python -c "from safeloop.api import create_app; app = create_app([]); print(len(app.routes))"
```

If API demo wiring is added, also verify with an HTTP smoke test, e.g.:

```bash
python -c "from safeloop.api import create_app; from fastapi.testclient import TestClient; client = TestClient(create_app([])); print(client.get('/runs').status_code)"
```

## Suggested execution order for Task 9
1. Cherry-pick/re-implement Tasks 4-8 onto this branch in dependency order.
2. Unify runtime/storage/API read semantics before touching docs.
3. Rewrite the GitHub demo to use real runtime behavior.
4. Run and fix the full test suite.
5. Rewrite README.
6. Add FAQ/comparison/case-study docs.
7. Do a final terminology and command-verification pass.

## Expected deliverables from Task 9
- integrated storage/hooks/runtime/api/demo on `feat/task-9-hardening`
- hardened tests covering cross-task contracts
- rewritten `README.md`
- comparison/FAQ/case-study docs
- documented verification commands that pass on repo root
