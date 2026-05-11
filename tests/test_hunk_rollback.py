from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path



def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "safeloop.cli", *args], cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def run_dir_from(stdout: str) -> Path:
    return Path([line.split(":", 1)[1].strip() for line in stdout.splitlines() if line.startswith("Run dir:")][0])


def make_run(tmp_path: Path, script: str, initial: dict[str, str] | None = None) -> tuple[Path, Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    for name, text in (initial or {}).items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if "\x00" in text:
            p.write_bytes(text.encode("utf-8"))
        else:
            p.write_text(text)
    (repo / "agent.py").write_text(script)
    result = run_cli("watch-run", "--task-id", "hunk", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")
    assert result.returncode == 0, result.stderr
    run_dir = run_dir_from(result.stdout)
    return repo, run_dir, read_json(run_dir / "run.json")["run_id"]


def test_rollback_one_hunk_preserves_another_in_same_file(tmp_path: Path) -> None:
    before = "".join(f"line {i}\n" for i in range(1, 61))
    after = before.replace("line 2\n", "LINE 2\n").replace("line 55\n", "LINE 55\n")
    script = f"open('notes.txt','w').write({after!r})\n"
    repo, run_dir, run_id = make_run(tmp_path, script, {"notes.txt": before})
    hunks = read_json(run_dir / "checkpoints" / "cp-0001" / "hunk-manifest.json")["hunks"]
    assert [h["hunk_id"] for h in hunks] == ["hunk-0001", "hunk-0002"]
    plan = run_cli("rollback", "plan", str(run_dir), run_id, "--hunks", "hunk-0001")
    assert plan.returncode == 0, plan.stderr
    artifact = read_json(run_dir / "rollback-plan.json")
    assert artifact["operation"] == "rollback_selected_hunks"
    assert artifact["selected_hunks"] == ["hunk-0001"]
    assert artifact["affected_files"] == ["notes.txt"]
    apply = run_cli("rollback", "apply", str(run_dir), run_id, "--hunks", "hunk-0001")
    assert apply.returncode == 0, apply.stderr
    text = (repo / "notes.txt").read_text()
    assert "line 2\n" in text
    assert "LINE 55\n" in text
    result = read_json(run_dir / "rollback-result.json")
    assert result["status"] == "applied"
    events = [json.loads(line) for line in (run_dir / "timeline.jsonl").read_text().splitlines()]
    assert "rollback-result.json" in [e for e in events if e.get("type") == "rollback_applied"][-1]["payload"]["artifact_digests"]
    (run_dir / "rollback-result.json").write_text(json.dumps({"tampered": True}))
    verify = run_cli("verify-artifacts", str(run_dir))
    assert verify.returncode == 1
    assert "digest mismatch rollback-result.json" in verify.stdout


def test_hunk_context_mismatch_missing_file_binary_and_large_block(tmp_path: Path) -> None:
    repo, run_dir, run_id = make_run(tmp_path / "ctx", "open('a.txt','w').write('after')\n", {"a.txt": "before"})
    assert run_cli("rollback", "plan", str(run_dir), run_id, "--hunks", "hunk-0001").returncode == 0
    (repo / "a.txt").write_text("drift")
    blocked = run_cli("rollback", "apply", str(run_dir), run_id, "--hunks", "hunk-0001")
    assert blocked.returncode == 1
    assert read_json(run_dir / "rollback-result.json")["blocked_reason"] in {"hunk_context_mismatch", "plan mismatch"}

    repo2, rd2, rid2 = make_run(tmp_path / "missing", "open('a.txt','w').write('after')\n", {"a.txt": "before"})
    (repo2 / "a.txt").unlink()
    miss = run_cli("rollback", "plan", str(rd2), rid2, "--hunks", "hunk-0001")
    assert miss.returncode == 0
    assert read_json(rd2 / "rollback-plan.json")["blockers"][0]["code"] == "missing_file"

    repo3, rd3, rid3 = make_run(tmp_path / "bin", "open('b.bin','wb').write(b'\\x00after')\n", {"b.bin": "\x00before"})
    bin_plan = run_cli("rollback", "plan", str(rd3), rid3, "--hunks", "hunk-0001")
    assert bin_plan.returncode == 0
    assert read_json(rd3 / "rollback-plan.json")["blockers"][0]["code"] == "binary_hunk_unsupported"

    repo4, rd4, rid4 = make_run(tmp_path / "large", "open('big.txt','w').write('b'*1100000)\n", {"big.txt": "a"*1100000})
    large = run_cli("rollback", "plan", str(rd4), rid4, "--hunks", "hunk-0001")
    assert large.returncode == 0
    assert read_json(rd4 / "rollback-plan.json")["blockers"][0]["code"] == "large_file"


def test_hunk_plan_required_and_selected_mismatch_blocks(tmp_path: Path) -> None:
    repo, run_dir, run_id = make_run(tmp_path, "open('a.txt','w').write('after')\n", {"a.txt": "before"})
    apply = run_cli("rollback", "apply", str(run_dir), run_id, "--hunks", "hunk-0001")
    assert apply.returncode == 1
    assert read_json(run_dir / "rollback-result.json")["blocked_reason"] == "missing rollback-plan.json"
    assert run_cli("rollback", "plan", str(run_dir), run_id, "--hunks", "hunk-0001").returncode == 0
    bad = run_cli("rollback", "apply", str(run_dir), run_id, "--hunks", "hunk-0002")
    assert bad.returncode == 1
    assert read_json(run_dir / "rollback-result.json")["blocked_reason"] == "selected_hunks mismatch"
