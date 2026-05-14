from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs" / "v0.2-readiness-matrix.md"
AUDIT = ROOT / "docs" / "completion-gap-audit.md"
DOD = ROOT / "docs" / "v0.2.0-rc-definition-of-done.md"

REQUIRED_CAPABILITIES = [
    "long-running task watchdog",
    "artifact verification",
    "timeline/explain UX",
    "rollback plan/apply",
    "selective rollback",
    "compensation adapter contract",
    "manual review boundary",
    "operator packet",
    "full demo flow",
    "public readiness script",
    "local tamper-evident guarantees",
    "remote transparency/signing status",
    "hosted control plane status",
    "external side-effect registry",
    "compensation plan/result",
    "fake external demo",
    "v0.2.0 RC definition of done",
    "external side-effect exact rollback boundary",
]

REQUIRED_RC_GATES = [
    "local exact rollback",
    "external side-effect registry",
    "compensation plan/result",
    "compensation adapter contract",
    "fake external demo",
    "operator packet integration",
    "readiness-matrix/audit coverage",
]

REQUIRED_RC_EXCLUSIONS = [
    "No real external adapters",
    "No external exact rollback",
    "No hosted control plane",
    "No automatic external remediation",
]

REQUIRED_STATUSES = ["complete", "partial", "planned", "out_of_scope"]


def read_matrix() -> str:
    return MATRIX.read_text(encoding="utf-8")


def read_audit() -> str:
    return AUDIT.read_text(encoding="utf-8")


def read_dod() -> str:
    return DOD.read_text(encoding="utf-8")


def test_v02_readiness_matrix_file_exists():
    assert MATRIX.exists()
    assert AUDIT.exists()
    assert DOD.exists()


def test_required_capability_names_are_present():
    text = read_matrix()
    for capability in REQUIRED_CAPABILITIES:
        assert capability in text


def test_required_statuses_are_present():
    text = read_matrix()
    for status in REQUIRED_STATUSES:
        assert f"`{status}`" in text or f"| {status} |" in text


def test_external_side_effect_exact_rollback_boundary_is_explicit():
    combined = f"{read_matrix()}\n{read_audit()}\n{read_dod()}"
    required_phrases = [
        "exact rollback only for covered local file changes",
        "Actions outside the local repo",
        "never claimed as exact rollback",
        "compensation and manual review",
    ]
    for phrase in required_phrases:
        assert phrase in combined


def test_v020_rc_definition_of_done_names_required_gates_and_exclusions():
    combined = f"{read_matrix()}\n{read_dod()}"
    for gate in REQUIRED_RC_GATES:
        assert gate in combined
    for exclusion in REQUIRED_RC_EXCLUSIONS:
        assert exclusion in combined


def test_v020_rc_exclusions_are_repeated_across_review_docs():
    combined = f"{read_matrix()}\n{read_audit()}\n{read_dod()}".lower()
    required_exclusion_phrases = [
        "no real external adapters",
        "no external exact rollback",
        "no hosted control plane",
        "no automatic external remediation",
        "fake/local",
        "operator evidence",
    ]
    for phrase in required_exclusion_phrases:
        assert phrase in combined


def test_hosted_control_plane_and_remote_transparency_not_complete():
    text = read_matrix()
    remote_row = next(
        line for line in text.splitlines() if "| remote transparency/signing status |" in line
    )
    hosted_row = next(
        line for line in text.splitlines() if "| hosted control plane status |" in line
    )

    assert "| planned |" in remote_row or "| out_of_scope |" in remote_row
    assert "| complete |" not in remote_row
    assert "| planned |" in hosted_row or "| out_of_scope |" in hosted_row
    assert "| complete |" not in hosted_row


def test_recent_operator_demo_explain_and_compensation_references_present():
    text = read_matrix()
    required_references = [
        "docs/specs/operator-packet-v1.md",
        "examples/full_demo.sh",
        "safeloop explain",
        "tests/test_rollback_groups.py",
        "docs/compensation-adapter-contracts.md",
        "examples/compensation_adapter_contracts.json",
    ]
    for reference in required_references:
        assert reference in text


def test_every_capability_row_has_required_columns():
    text = read_matrix()
    rows = [line for line in text.splitlines() if line.startswith("|")]
    capability_rows = [
        line
        for line in rows
        if any(f"| {capability} |" in line for capability in REQUIRED_CAPABILITIES)
    ]
    assert len(capability_rows) == len(REQUIRED_CAPABILITIES)

    for row in capability_rows:
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        assert len(cells) == 5
        capability, status, evidence, gap, next_pr = cells
        assert capability
        assert status in REQUIRED_STATUSES
        assert evidence
        assert gap
        assert next_pr
