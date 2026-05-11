from pathlib import Path
import tomllib


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_project_declares_runtime_dependencies_and_api_extra() -> None:
    pyproject = tomllib.loads((project_root() / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]

    assert project["version"] == "0.1.4"
    assert "pydantic>=2,<3" in project["dependencies"]
    assert "fastapi>=0.110" in project["optional-dependencies"]["api"]


def test_package_init_does_not_import_optional_fastapi_api_at_module_import_time() -> None:
    init_source = (project_root() / "src" / "safeloop" / "__init__.py").read_text(encoding="utf-8")

    top_level_imports = [
        line for line in init_source.splitlines() if line.startswith("from ") or line.startswith("import ")
    ]

    assert "from safeloop.api import RunViewer, create_app" not in top_level_imports
    assert "def __getattr__" in init_source
