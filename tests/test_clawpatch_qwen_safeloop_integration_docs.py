from pathlib import Path


DOC = Path("docs/clawpatch-qwen-safeloop-integration.md")


def test_clawpatch_qwen_safeloop_integration_doc_captures_product_roles() -> None:
    text = DOC.read_text(encoding="utf-8")

    assert "Clawpatch = code issue lifecycle" in text
    assert "Qwen Red/Blue = adversarial review quality amplifier" in text
    assert (
        "SafeLoop = local agent-run evidence, bounded rollback, compensation/manual-review, "
        "and readiness-packet layer"
    ) in text


def test_clawpatch_qwen_safeloop_integration_doc_keeps_boundary_claims_narrow() -> None:
    text = DOC.read_text(encoding="utf-8")

    assert "No new release, git tag, PyPI publication, or GitHub Release." in text
    assert "exact rollback only for covered local repo file changes" in text
    assert "external effects are manual-review/compensation only" in text
    assert "not tamper-proof" in text
    assert "No autonomous merge approval." in text
    assert "No policy, legal, compliance, SOC2, or governance guarantee." in text


def test_clawpatch_qwen_safeloop_integration_doc_requires_human_approval() -> None:
    text = DOC.read_text(encoding="utf-8")

    assert "advisory evidence only" in text
    assert "without explicit human maintainer/operator approval" in text
    assert "advisory_readiness" in text
    assert "No Clawpatch lifecycle state, Qwen review result, SafeLoop verification result" in text


def test_clawpatch_qwen_safeloop_integration_doc_treats_inputs_as_untrusted() -> None:
    text = DOC.read_text(encoding="utf-8")

    assert "Clawpatch/Qwen inputs are untrusted data." in text
    assert "validate schemas, sizes, paths, URLs, encodings, and artifact references" in text
    assert "redaction" in text
    assert "secrets, credentials, private source excerpts, customer data, and raw logs" in text


def test_clawpatch_qwen_safeloop_integration_doc_defers_schema_to_later_slice() -> None:
    text = DOC.read_text(encoding="utf-8")

    assert "future machine-readable packet" in text
    assert "clawpatch-qwen-review-packet.v1" in text
    assert "operator-packet-manifest.v1" in text
    assert (
        "Tests cover boundary language, advisory approval semantics, required fields, "
        "redaction status, and at least one invalid packet case."
    ) in text
