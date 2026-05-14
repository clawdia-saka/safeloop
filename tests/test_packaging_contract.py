import os
from pathlib import Path
import subprocess
import sys
import tomllib


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_project_declares_runtime_dependencies_and_api_extra() -> None:
    pyproject = tomllib.loads((project_root() / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]

    assert project["version"] == "0.2.0"
    assert project["requires-python"] == ">=3.11"
    assert project["readme"] == "README.md"
    assert "Local watchdog" in project["description"]
    assert "pydantic>=2,<3" in project["dependencies"]
    assert "fastapi>=0.110" in project["optional-dependencies"]["api"]
    assert project["urls"]["Repository"] == "https://github.com/clawdia-saka/safeloop"
    assert "Environment :: Console" in project["classifiers"]
    assert "ai-agents" in project["keywords"]


def test_cli_help_and_version_are_install_smoke_friendly() -> None:
    root = project_root()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    for args, expected in [(["--help"], "watch-run"), (["--version"], "safeloop ")]:
        result = subprocess.run(
            [sys.executable, "-m", "safeloop.cli", *args],
            cwd=root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert expected in result.stdout


def test_package_init_does_not_import_optional_fastapi_api_at_module_import_time() -> None:
    init_source = (project_root() / "src" / "safeloop" / "__init__.py").read_text(encoding="utf-8")

    top_level_imports = [
        line for line in init_source.splitlines() if line.startswith("from ") or line.startswith("import ")
    ]

    assert "from safeloop.api import RunViewer, create_app" not in top_level_imports
    assert "def __getattr__" in init_source


def test_runviewer_is_importable_without_fastapi(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "fastapi" or name.startswith("fastapi."):
            raise ModuleNotFoundError("blocked fastapi import")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    from safeloop.viewer import RunViewer

    assert RunViewer.__name__ == "RunViewer"
