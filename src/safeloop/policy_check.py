from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any

from safeloop.agent_watchdog import atomic_json
from safeloop.side_effect_ledger import read_side_effect_events

POLICY_SCHEMA_VERSION = "do-not-do-policy.v1"
RESULT_SCHEMA_VERSION = "policy-check-result.v1"
ROLLBACK_SUGGESTION_SCHEMA_VERSION = "policy-rollback-suggestion.v1"


class PolicyCheckError(ValueError):
    pass


def load_policy(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise PolicyCheckError("YAML policy files require optional dependency PyYAML; use JSON or install PyYAML") from exc
        data = yaml.safe_load(text)
    else:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PolicyCheckError(f"malformed JSON policy: {exc}") from exc
    if not isinstance(data, dict):
        raise PolicyCheckError("policy must be an object")
    if data.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise PolicyCheckError(f"unsupported policy schema_version {data.get('schema_version')!r}; expected {POLICY_SCHEMA_VERSION}")
    policies = data.get("policies") or data.get("rules")
    if not isinstance(policies, list):
        raise PolicyCheckError("policy must contain a policies list")
    for p in policies:
        if not isinstance(p, dict) or not isinstance(p.get("policy_id"), str) or not p.get("policy_id"):
            raise PolicyCheckError("each policy requires a non-empty policy_id")
    data["policies"] = policies
    return data


def _checkpoint_dirs(run_dir: Path) -> list[Path]:
    root = run_dir / "checkpoints"
    return sorted([p for p in root.glob("cp-*") if p.is_dir()]) if root.exists() else []


def _changed_files(run_dir: Path) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for cp in _checkpoint_dirs(run_dir):
        cid = cp.name
        rm_path = cp / "restore-manifest.json"
        if not rm_path.exists():
            continue
        rm = json.loads(rm_path.read_text(encoding="utf-8"))
        for op, key in (("created", "created_files"), ("modified", "modified_files"), ("deleted", "deleted_files")):
            for rel in rm.get(key, []):
                item = (cid, rel)
                if item not in seen:
                    seen.add(item)
                    out.append({"checkpoint_id": cid, "path": rel, "operation": op})
    return out


def _file_kind(path: str) -> str:
    name = Path(path).name.lower()
    parts = set(Path(path).parts)
    if name in {"poetry.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "pdm.lock", "uv.lock", "requirements.txt", "gemfile.lock", "cargo.lock", "go.sum", "go.mod"} or name.endswith(".lock"):
        return "dependency"
    if path.startswith("docs/") or name.endswith(('.md', '.rst')):
        return "docs"
    if "tests" in parts or "test" in parts or name.startswith("test_") or name.endswith("_test.py"):
        return "tests"
    return "code"


def _hunks(run_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for cp in _checkpoint_dirs(run_dir):
        hp = cp / "hunk-manifest.json"
        if not hp.exists():
            continue
        data = json.loads(hp.read_text(encoding="utf-8"))
        patch_text = (cp / "changes.patch").read_text(encoding="utf-8") if (cp / "changes.patch").exists() else ""
        for h in data.get("hunks", []):
            if isinstance(h, dict):
                clean = {k: h.get(k) for k in ("hunk_id", "checkpoint_id", "path", "operation", "old_start", "old_lines", "new_start", "new_lines", "binary")}
                clean["_patch_text"] = patch_text
                out.append(clean)
    return out


def _side_effects(run_dir: Path) -> list[dict[str, str]]:
    safe: list[dict[str, str]] = []
    for e in read_side_effect_events(run_dir / "side-effects.jsonl"):
        typ = str(e.get("type") or e.get("kind") or e.get("effect_type") or "unknown")
        item = {"type": typ}
        if isinstance(e.get("class"), str):
            item["class"] = str(e["class"])
        safe.append(item)
    return safe


def _actions(run_dir: Path) -> list[str]:
    path = run_dir / "timeline.jsonl"
    names: list[str] = []
    if not path.exists():
        return names
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = event.get("payload", {}) if isinstance(event, dict) else {}
        name = payload.get("action_name") or payload.get("action") if isinstance(payload, dict) else None
        if isinstance(name, str) and name not in names:
            names.append(name)
    return names


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(x, str) for x in value):
        return value
    return []


def _confidence_for_violation(v: dict[str, Any]) -> str:
    if v.get("matched_side_effects") or v.get("matched_actions"):
        return "weak"
    if v.get("matched_hunks") or v.get("matched_files"):
        return "strong"
    return "weak"


def build_policy_rollback_suggestion(run_dir: Path, policy_path: Path, result: dict[str, Any] | None = None) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    if result is None:
        result = run_policy_check(run_dir, policy_path)
    run_id = result.get("run_id")
    local_exact: list[dict[str, Any]] = []
    external: list[dict[str, Any]] = []
    manual_review_required = False
    dependency_warnings: list[dict[str, str]] = []
    for v in result.get("violations", []):
        confidence = _confidence_for_violation(v)
        if confidence == "weak":
            manual_review_required = True
            dependency_warnings.append({"policy_id": str(v.get("policy_id", "unknown")), "code": "weak_confidence"})
        cp_arg = f" --from-checkpoint {v.get('checkpoint_id')}" if v.get("checkpoint_id") else ""
        files = list(v.get("matched_files", []))
        if files:
            local_exact.append({"policy_id": v["policy_id"], "type": "files", "confidence": confidence, "files": files, "plan_command": f"safeloop rollback plan {run_dir} {run_id} --policy {policy_path}{cp_arg} --files {' '.join(files)}"})
        hunk_ids = [str(h.get("hunk_id")) for h in v.get("matched_hunks", []) if h.get("hunk_id")]
        if hunk_ids:
            local_exact.append({"policy_id": v["policy_id"], "type": "hunks", "confidence": confidence, "hunks": hunk_ids, "plan_command": f"safeloop rollback plan {run_dir} {run_id} --policy {policy_path} --hunks {' '.join(hunk_ids)}"})
        actions = list(v.get("matched_actions", []))
        if actions:
            local_exact.append({"policy_id": v["policy_id"], "type": "action", "confidence": "weak", "actions": actions, "plan_command": f"safeloop rollback plan {run_dir} {run_id} --policy {policy_path} --action {' '.join(actions)}"})
            dependency_warnings.append({"policy_id": v["policy_id"], "code": "action_scope_requires_review"})
            manual_review_required = True
        if v.get("matched_side_effects"):
            external.append({"policy_id": v["policy_id"], "type": "side_effect", "classification": "compensation_required", "manual_review_required": True, "exact_rollback": False, "side_effects": v["matched_side_effects"], "review_command": f"safeloop review {run_dir}"})
            manual_review_required = True
    suggestion = {"schema_version": ROLLBACK_SUGGESTION_SCHEMA_VERSION, "run_id": run_id, "policy_path": str(policy_path), "status": "suggested" if result.get("violations") else "pass", "auto_apply": False, "matching_plan_required_before_apply": True, "exact_rollback": bool(local_exact) and not external and not manual_review_required, "manual_review_required": manual_review_required, "local_exact_rollback": local_exact, "external_compensation": external, "dependency_warnings": dependency_warnings, "raw_payloads_included": False}
    atomic_json(run_dir / "policy-rollback-suggestion.json", suggestion)
    return suggestion


def run_policy_check(run_dir: Path, policy_path: Path) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    policy = load_policy(policy_path)
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    files = _changed_files(run_dir)
    hunks = _hunks(run_dir)
    side_effects = _side_effects(run_dir)
    actions = _actions(run_dir)
    violations: list[dict[str, Any]] = []
    for rule in policy["policies"]:
        matched_files: list[str] = []
        for f in files:
            rel = f["path"]
            if _as_list(rule.get("checkpoint_id")) and f["checkpoint_id"] not in _as_list(rule.get("checkpoint_id")):
                continue
            if any(fnmatch.fnmatch(rel, pat) for pat in _as_list(rule.get("files"))):
                matched_files.append(rel)
            if rel in _as_list(rule.get("paths")):
                matched_files.append(rel)
            if _file_kind(rel) in _as_list(rule.get("file_kinds")):
                matched_files.append(rel)
        matched_files = sorted(set(matched_files))
        matched_hunks: list[dict[str, Any]] = []
        needles = _as_list(rule.get("hunk_text_contains"))
        if needles:
            for h in hunks:
                if _as_list(rule.get("checkpoint_id")) and h.get("checkpoint_id") not in _as_list(rule.get("checkpoint_id")):
                    continue
                text = str(h.get("_patch_text", ""))
                if any(n in text for n in needles):
                    matched_hunks.append({k: v for k, v in h.items() if not k.startswith("_")})
        wanted_se = _as_list(rule.get("side_effect_class"))
        matched_side_effects = [s for s in side_effects if wanted_se and (s.get("type") in wanted_se or s.get("class") in wanted_se)]
        wanted_actions = _as_list(rule.get("action_name"))
        matched_actions = sorted(set(actions).intersection(wanted_actions)) if wanted_actions else []
        if matched_files or matched_hunks or matched_side_effects or matched_actions:
            cp = None
            if matched_hunks:
                cp = matched_hunks[0].get("checkpoint_id")
            elif files:
                cp = next((f["checkpoint_id"] for f in files if f["path"] in matched_files), None)
            exact = not bool(matched_side_effects)
            v: dict[str, Any] = {
                "policy_id": rule["policy_id"],
                "matched_files": matched_files,
                "matched_hunks": matched_hunks,
                "matched_side_effects": matched_side_effects,
                "matched_actions": matched_actions,
                "exact_rollback": exact,
                "checkpoint_id": cp,
                "suggested_rollback_command": f"safeloop rollback plan {run_dir} {run.get('run_id')} {cp}" if cp else None,
                "suggested_compensation_command": f"safeloop review {run_dir}" if matched_side_effects else None,
            }
            violations.append(v)
    result = {"schema_version": RESULT_SCHEMA_VERSION, "run_id": run.get("run_id"), "policy_path": str(policy_path), "status": "violations" if violations else "pass", "violation_count": len(violations), "violations": violations, "raw_payloads_included": False, "auto_apply": False, "force_rollback": False}
    atomic_json(run_dir / "policy-check-result.json", result)
    return result
