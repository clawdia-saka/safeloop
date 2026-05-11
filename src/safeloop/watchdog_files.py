"""File discovery helpers for watchdog repository snapshots."""

from __future__ import annotations

from pathlib import Path
import subprocess

_EXCLUDED_DIRS = {".git", ".safeloop", "__pycache__", "node_modules"}


def discover_repo_files(repo: Path) -> list[Path]:
    """Return sorted relative file paths that should be considered for snapshots.

    Git repositories use one scalable ``git ls-files`` invocation to include tracked
    plus untracked non-ignored files. Non-git directories fall back to a safe rglob
    walk with the same internal-directory exclusions. Symlinks are always skipped.
    """

    root = repo.resolve()
    files = _discover_with_git(root)
    if files is None:
        files = _discover_with_rglob(root)
    return sorted(files, key=lambda path: path.as_posix())


def _discover_with_git(root: Path) -> list[Path] | None:
    try:
        result = subprocess.run(
            ["git", "ls-files", "-co", "--exclude-standard", "-z"],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    files: list[Path] = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        rel = Path(raw.decode("utf-8", errors="surrogateescape"))
        if _should_include(root, rel):
            files.append(rel)
    return files


def _discover_with_rglob(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if _should_include(root, rel):
            files.append(rel)
    return files


def _should_include(root: Path, rel: Path) -> bool:
    if rel.is_absolute() or any(part in _EXCLUDED_DIRS for part in rel.parts):
        return False
    path = root / rel
    return path.is_file() and not path.is_symlink()
