# Transactional Agent Runtime Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Build the first working OSS core of a transactional agent runtime that makes agent actions planned, typed, approval-aware, compensatable, resumable, and auditable.

**Architecture:** Start from a narrow compensation kernel instead of a broad control plane. The first deliverable is a Python package centered on `ActionEnvelope`, `EffectClass`, `Journal`, `Checkpoint`, `CompensationHook`, and `ApprovalHook`, with a local run viewer and one reference action flow. Then expand outward toward approval/policy and control-plane concerns.

**Tech Stack:** Python 3.11, FastAPI (small local run UI/API), Pydantic, pytest, uv or pip, simple file-backed journal storage (JSONL/SQLite acceptable for MVP), git.

---

## Product roadmap

### Week 1 MVP — Compensation Kernel
- typed action envelope
- effect classes
- journal state machine
- checkpoint + resume
- compensation hook interface
- approval hook interface
- one reference integration: GitHub-style dummy action or local mock action
- minimal local run viewer

### 1 Month MVP — Safe Execution Surface
- richer approval/risk interface
- idempotency handling
- partial retry / resume UX
- agent-native operator packets for OpenClaw/Hermes/Codex/Claude Code review
- browser/API connector examples as reference surfaces, not primary operator UX
- better run search + filtering

### 90 Day Product — Agent-Native Operator Plane
- packet manifest + tamper-evident evidence bundles
- strict verdict import with RBAC, HMAC/signature checks, expiry, and replay protection
- CLI operator inbox for pending approvals and evidence review
- target-specific prompt exporters for common coding/review agents
- optional localhost HTTP or Telegram transports only after the packet/verdict path is stable
- reliability analytics / incident review
- hosted connectors / secret isolation as later hardening work

---

## Repository shape

### Task 1: Create repo skeleton and packaging

**Objective:** Create the minimal Python package and test structure for the transactional runtime.

**Files:**
- Create: `src/safeloop/__init__.py`
- Create: `src/safeloop/types.py`
- Create: `src/safeloop/journal.py`
- Create: `src/safeloop/runtime.py`
- Create: `src/safeloop/hooks.py`
- Create: `src/safeloop/storage.py`
- Create: `src/safeloop/api.py`
- Create: `tests/test_smoke.py`
- Create: `pyproject.toml`
- Create: `README.md`

**Step 1: Write failing smoke test**
Create `tests/test_smoke.py` asserting imports for `EffectClass`, `ActionEnvelope`, and `Runtime`.

**Step 2: Run test to verify failure**
Run: `pytest tests/test_smoke.py -v`
Expected: FAIL — modules do not exist.

**Step 3: Write minimal package skeleton**
Create the files above with empty/minimal symbols.

**Step 4: Run test to verify pass**
Run: `pytest tests/test_smoke.py -v`
Expected: PASS.

**Step 5: Commit**
```bash
git add pyproject.toml src tests README.md
git commit -m "feat: scaffold safeloop package skeleton"
```

### Task 2: Add typed action envelope and effect classes

**Objective:** Define the core action unit and effect taxonomy.

**Files:**
- Modify: `src/safeloop/types.py`
- Create: `tests/test_types.py`

**Step 1: Write failing tests**
Test for:
- `EffectClass` enum values: `read_only`, `reversible_write`, `compensatable_write`, `irreversible_write`
- `ActionEnvelope` fields: name, target, args, diff, actor, privileges, idempotency_key, effect
- Pydantic validation for required fields

**Step 2: Run tests to verify failure**
Run: `pytest tests/test_types.py -v`
Expected: FAIL.

**Step 3: Implement minimal types**
Add Pydantic models and enum.

**Step 4: Run tests to verify pass**
Run: `pytest tests/test_types.py -v`
Expected: PASS.

**Step 5: Commit**
```bash
git add src/safeloop/types.py tests/test_types.py
git commit -m "feat: add action envelope and effect classes"
```

### Task 3: Add journal state machine

**Objective:** Create the canonical run/action state transitions.

**Files:**
- Modify: `src/safeloop/journal.py`
- Create: `tests/test_journal.py`

**Step 1: Write failing tests**
Test allowed states:
- `proposed`
- `approved`
- `executing`
- `applied`
- `compensating`
- `compensated`
- `failed`
- `resumable`
- `handed_off`

Also test invalid transition rejection.

**Step 2: Run tests to verify failure**
Run: `pytest tests/test_journal.py -v`
Expected: FAIL.

**Step 3: Implement journal model**
Create journal entry model and transition validator.

**Step 4: Run tests to verify pass**
Run: `pytest tests/test_journal.py -v`
Expected: PASS.

**Step 5: Commit**
```bash
git add src/safeloop/journal.py tests/test_journal.py
git commit -m "feat: add journal state machine"
```

### Task 4: Add file-backed journal storage

**Objective:** Persist action journal entries locally for MVP runs.

**Files:**
- Modify: `src/safeloop/storage.py`
- Create: `tests/test_storage.py`

**Step 1: Write failing tests**
Test append/read/list behavior for a local JSONL or SQLite-backed store.

**Step 2: Run tests to verify failure**
Run: `pytest tests/test_storage.py -v`
Expected: FAIL.

**Step 3: Implement storage**
Keep it simple and local-first.

**Step 4: Run tests to verify pass**
Run: `pytest tests/test_storage.py -v`
Expected: PASS.

**Step 5: Commit**
```bash
git add src/safeloop/storage.py tests/test_storage.py
git commit -m "feat: add local journal storage"
```

### Task 5: Add approval and compensation hook interfaces

**Objective:** Define pre-execution and post-failure extension points.

**Files:**
- Modify: `src/safeloop/hooks.py`
- Create: `tests/test_hooks.py`

**Step 1: Write failing tests**
Test interface contracts for:
- approval hook returning allow/block/escalate
- compensation hook callable on compensatable actions

**Step 2: Run tests to verify failure**
Run: `pytest tests/test_hooks.py -v`
Expected: FAIL.

**Step 3: Implement minimal hook interfaces**
Use Protocols/base classes/simple registries.

**Step 4: Run tests to verify pass**
Run: `pytest tests/test_hooks.py -v`
Expected: PASS.

**Step 5: Commit**
```bash
git add src/safeloop/hooks.py tests/test_hooks.py
git commit -m "feat: add approval and compensation hook interfaces"
```

### Task 6: Add runtime executor with checkpoint/resume

**Objective:** Execute one action through proposal → approval → execution → failure/compensation/resume.

**Files:**
- Modify: `src/safeloop/runtime.py`
- Create: `tests/test_runtime.py`

**Step 1: Write failing tests**
Cover:
- read-only action executes without approval
- compensatable action can fail and call compensation
- irreversible action escalates when approval hook demands it
- resumable state can be re-entered and continued

**Step 2: Run tests to verify failure**
Run: `pytest tests/test_runtime.py -v`
Expected: FAIL.

**Step 3: Implement minimal runtime**
No overengineering: one in-process runtime class is enough.

**Step 4: Run tests to verify pass**
Run: `pytest tests/test_runtime.py -v`
Expected: PASS.

**Step 5: Run broader tests**
Run: `pytest tests/ -q`
Expected: all PASS.

**Step 6: Commit**
```bash
git add src/safeloop/runtime.py tests/test_runtime.py
git commit -m "feat: add action runtime with checkpoint and compensation"
```

### Task 7: Add minimal local run viewer API

**Objective:** Expose run/journal state in a tiny local API for demos and operator inspection.

**Files:**
- Modify: `src/safeloop/api.py`
- Create: `tests/test_api.py`

**Step 1: Write failing tests**
Test endpoints for:
- list runs
- get run details
- list journal entries

**Step 2: Run tests to verify failure**
Run: `pytest tests/test_api.py -v`
Expected: FAIL.

**Step 3: Implement minimal FastAPI app**
Keep it local-only and lightweight.

**Step 4: Run tests to verify pass**
Run: `pytest tests/test_api.py -v`
Expected: PASS.

**Step 5: Commit**
```bash
git add src/safeloop/api.py tests/test_api.py
git commit -m "feat: add local run viewer api"
```

### Task 8: Add GitHub-style reference action demo

**Objective:** Show the model with one concrete side-effecting action flow.

**Files:**
- Create: `examples/github_pr_demo.py`
- Create: `tests/test_examples_github_demo.py`
- Modify: `README.md`

**Step 1: Write failing test**
Test demo flow with mocked action + mocked compensation.

**Step 2: Run test to verify failure**
Run: `pytest tests/test_examples_github_demo.py -v`
Expected: FAIL.

**Step 3: Implement example**
Use a fake GitHub create-PR action with compensation = close-PR.

**Step 4: Run tests to verify pass**
Run: `pytest tests/test_examples_github_demo.py -v`
Expected: PASS.

**Step 5: Update README quickstart**
Add install, run tests, and demo usage.

**Step 6: Run full suite**
Run: `pytest tests/ -q`
Expected: all PASS.

**Step 7: Commit**
```bash
git add examples README.md tests
git commit -m "feat: add github-style reference demo"
```

### Task 9: Final integration review and hardening

**Objective:** Verify that the Week 1 MVP hangs together as a coherent OSS kernel.

**Files:**
- Review: entire repo
- Modify if needed: any touched files

**Step 1: Run full test suite**
Run: `pytest tests/ -q`
Expected: all PASS.

**Step 2: Run import smoke**
Run: `python -c "from safeloop.runtime import Runtime; print('ok')"`
Expected: prints `ok`.

**Step 3: Review README**
Ensure README explains:
- the problem
- effect classes
- action envelope
- lifecycle
- quickstart

**Step 4: Commit polish if needed**
```bash
git add -A
git commit -m "chore: finalize week-1 safeloop mvp"
```

---

## Review protocol per task

For every implementation task:
1. Implementer subagent executes task
2. Spec-compliance reviewer checks against this plan
3. Code-quality reviewer checks clarity, correctness, and scope
4. Only then proceed to next task

## Immediate execution order
1. Task 1
2. Task 2
3. Task 3
4. Task 4
5. Task 5
6. Task 6
7. Task 7
8. Task 8
9. Task 9

## Deliverable at end of Week 1
A usable OSS kernel proving:
- typed action/effect model
- journaled state transitions
- approval-aware execution
- compensation-aware failure handling
- checkpoint/resume
- local run inspection
- one demo flow
