from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "examples" / "full-demo-golden"
FULL_DEMO = ROOT / "examples" / "full_demo.sh"


def normalize_text(text: str, tmp_root: Path, run_dir: Path, run_id: str) -> str:
    text = text.replace(str(run_dir), "$RUN_DIR").replace(str(tmp_root), "$TMP_ROOT").replace(run_id, "$RUN_ID")
    text = re.sub(r"run-\d{8}-\d{6}-\d+-full-demo", "$RUN_ID", text)
    text = re.sub(r"\d{4}-\d{2}-\d{2}T[0-9:.+-]+(?:Z|\+00:00)?", "$TIMESTAMP", text)
    text = re.sub(r"sha256:[0-9a-f]{64}", "sha256:$HASH", text)
    return text


def normalize_json(path: Path, tmp_root: Path, run_dir: Path, run_id: str) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))

    def walk(value):
        if isinstance(value, dict):
            return {key: walk(item) for key, item in value.items()}
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, str):
            return normalize_text(value, tmp_root, run_dir, run_id)
        return value

    return json.dumps(walk(data), indent=2, sort_keys=True) + "\n"


def run_full_demo(tmp_path: Path) -> tuple[Path, Path, str, str]:
    tmp_root = tmp_path / "full-demo"
    env = os.environ.copy()
    env["SAFELOOP_FULL_DEMO_ROOT"] = str(tmp_root)
    result = subprocess.run(
        ["bash", str(FULL_DEMO)],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    run_dir_line = next(line for line in result.stdout.splitlines() if line.startswith("Run dir: "))
    run_dir = Path(run_dir_line.removeprefix("Run dir: "))
    run_id = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["run_id"]
    return tmp_root, run_dir, run_id, result.stdout


def test_full_demo_produces_operator_packet_v2(tmp_path: Path) -> None:
    _, run_dir, _, stdout = run_full_demo(tmp_path)
    packet = run_dir / "operator-packet-v2.md"
    assert packet.exists()
    assert f"Operator packet v2: {packet}" in stdout


def test_normalized_operator_packet_v2_matches_golden_expectations(tmp_path: Path) -> None:
    tmp_root, run_dir, run_id, _ = run_full_demo(tmp_path)
    actual = normalize_text((run_dir / "operator-packet-v2.md").read_text(encoding="utf-8"), tmp_root, run_dir, run_id)
    expected = (GOLDEN / "operator-packet-v2.md").read_text(encoding="utf-8")
    assert actual == expected

    for phrase in [
        "Exact rollback only applies to covered local file changes.",
        "External side effects are manual-review/compensation only.",
        "SafeLoop does not claim exact rollback for actions outside the local repo.",
        "## 4. Rollback decision table",
        "## 5. Compensation decision table",
        "## 6. Manual review queue",
        "## 7. Recommended next action",
    ]:
        assert phrase in actual
    assert "external_side_effect | $TMP_ROOT/fake-external-service.log | manual_review_required | true" not in actual


def test_v1_operator_packet_and_explain_output_match_golden(tmp_path: Path) -> None:
    tmp_root, run_dir, run_id, _ = run_full_demo(tmp_path)
    explain = subprocess.run(
        ["python", "-m", "safeloop.cli", "explain", str(run_dir)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    assert explain.returncode == 0, explain.stdout + explain.stderr

    actual_packet = normalize_text((run_dir / "operator-packet.md").read_text(encoding="utf-8"), tmp_root, run_dir, run_id)
    actual_explain = normalize_text(explain.stdout, tmp_root, run_dir, run_id)
    assert actual_packet == (GOLDEN / "operator-packet.md").read_text(encoding="utf-8")
    assert actual_explain == (GOLDEN / "explain.md").read_text(encoding="utf-8")
    assert "rollback" in actual_explain
    assert "compensation" in actual_explain
    assert "manual handoff" in actual_explain


def test_rollback_plan_and_result_match_golden_schema(tmp_path: Path) -> None:
    tmp_root, run_dir, run_id, _ = run_full_demo(tmp_path)
    actual_plan = normalize_json(run_dir / "rollback-plan.json", tmp_root, run_dir, run_id)
    actual_result = normalize_json(run_dir / "rollback-result.json", tmp_root, run_dir, run_id)
    expected_plan = (GOLDEN / "rollback-plan.json").read_text(encoding="utf-8")
    expected_result = (GOLDEN / "rollback-result.json").read_text(encoding="utf-8")
    assert actual_plan == expected_plan
    assert actual_result == expected_result

    plan = json.loads(actual_plan)
    result = json.loads(actual_result)
    assert plan["schema_version"] == "rollback-plan.v1"
    assert result["schema_version"] == "rollback-result.v1"
    assert result["verification"]["status"] == "valid"


def test_verification_result_matches_golden_and_boundary_summary_is_present(tmp_path: Path) -> None:
    tmp_root, run_dir, run_id, _ = run_full_demo(tmp_path)
    actual_verification = normalize_json(run_dir / "verification" / "verify-artifacts-result.json", tmp_root, run_dir, run_id)
    assert actual_verification == (GOLDEN / "verification-result.json").read_text(encoding="utf-8")

    summary = (GOLDEN / "manual-review-summary.md").read_text(encoding="utf-8")
    for phrase in [
        "exact_rollback=false",
        "Exact rollback only applies to covered local file changes.",
        "External side effects are manual-review/compensation only.",
        "SafeLoop does not claim exact rollback for actions outside the local repo.",
    ]:
        assert phrase in summary


def test_public_readiness_script_still_detects_full_demo_flow() -> None:
    result = subprocess.run(
        ["python", "scripts/public_readiness.py", "--check"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "public-readiness: ok" in result.stdout
    assert "full-demo=present" in result.stdout
