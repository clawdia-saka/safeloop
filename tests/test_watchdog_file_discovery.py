from pathlib import Path
import subprocess

from safeloop.watchdog_files import discover_repo_files


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _init_git(repo: Path) -> None:
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")


def test_git_discovery_includes_tracked_and_untracked_non_ignored_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git(repo)
    (repo / "tracked.txt").write_text("tracked", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "initial")
    (repo / "untracked.txt").write_text("untracked", encoding="utf-8")

    assert discover_repo_files(repo) == [Path("tracked.txt"), Path("untracked.txt")]


def test_git_discovery_excludes_gitignored_files_and_internal_dirs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git(repo)
    (repo / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (repo / "ignored.txt").write_text("ignored", encoding="utf-8")
    (repo / ".safeloop").mkdir()
    (repo / ".safeloop" / "state.json").write_text("{}", encoding="utf-8")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "pkg.js").write_text("js", encoding="utf-8")
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "mod.pyc").write_bytes(b"pyc")

    assert discover_repo_files(repo) == [Path(".gitignore")]


def test_git_discovery_skips_symlinks(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git(repo)
    (repo / "real.txt").write_text("real", encoding="utf-8")
    (repo / "link.txt").symlink_to("real.txt")

    assert discover_repo_files(repo) == [Path("real.txt")]


def test_non_git_fallback_discovers_files_with_same_exclusions(tmp_path: Path) -> None:
    repo = tmp_path / "plain"
    repo.mkdir()
    (repo / "b.txt").write_text("b", encoding="utf-8")
    (repo / "a").mkdir()
    (repo / "a" / "file.txt").write_text("a", encoding="utf-8")
    (repo / ".git").mkdir()
    (repo / ".git" / "config").write_text("git", encoding="utf-8")
    (repo / ".safeloop").mkdir()
    (repo / ".safeloop" / "state.json").write_text("{}", encoding="utf-8")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "pkg.js").write_text("js", encoding="utf-8")
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "mod.pyc").write_bytes(b"pyc")
    (repo / "link.txt").symlink_to("b.txt")

    assert discover_repo_files(repo) == [Path("a/file.txt"), Path("b.txt")]


def test_discovery_returns_deterministically_sorted_relative_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git(repo)
    (repo / "z.txt").write_text("z", encoding="utf-8")
    (repo / "a").mkdir()
    (repo / "a" / "m.txt").write_text("m", encoding="utf-8")
    (repo / "a.txt").write_text("a", encoding="utf-8")

    files = discover_repo_files(repo)

    assert files == [Path("a.txt"), Path("a/m.txt"), Path("z.txt")]
    assert all(not path.is_absolute() for path in files)
