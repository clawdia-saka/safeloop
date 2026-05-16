from pathlib import Path


DOC = Path("docs/v0.2.1-release-decision-packet.md")


def test_v021_release_decision_packet_holds_release_until_tt_approval() -> None:
    text = DOC.read_text(encoding="utf-8")

    required_markers = [
        "# SafeLoop v0.2.1 Release Decision Packet",
        "candidate readiness: complete",
        "release decision: pending",
        "release action: HOLD",
        "pytest: 434 passed, 1 xfailed",
        "public_readiness: ok",
        "build: success",
        "release-tag: not-created",
        "No release action without explicit TT approval",
        "No tag / GitHub Release / PyPI without explicit TT approval",
        "exact rollback only for covered local file changes",
        "external actions exact_rollback=false",
        "compensation/manual-review only",
        "no hosted control plane",
        "no remote transparency log",
        "SafeLoop = agent action safety evidence layer",
    ]
    for marker in required_markers:
        assert marker in text

    for option in ["1. HOLD", "2. GitHub tag only", "3. GitHub Release", "4. PyPI publish"]:
        assert option in text
