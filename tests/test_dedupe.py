from safeloop.dedupe import ScenarioDedupeGuard, semantic_fingerprint


def scenario(**overrides: str) -> dict[str, str]:
    base = {
        "name": "first_name",
        "kind": "compensation_failure",
        "effect": "compensatable_write",
        "failure_mode": "compensation_failure",
        "goal": "Verify journal records failed cleanup without claiming rollback.",
        "why": "Tests recovery boundary when compensation itself fails!",
    }
    base.update(overrides)
    return base


def test_semantic_fingerprint_ignores_name_and_punctuation_variants() -> None:
    original = scenario(name="compensation_hook_breaks")
    replay = scenario(
        name="renamed_replay",
        goal=" verify JOURNAL records failed cleanup without claiming rollback ",
        why="Tests recovery boundary when compensation itself fails",
    )

    assert semantic_fingerprint(original) == semantic_fingerprint(replay)


def test_global_guard_rejects_duplicate_churn_beyond_recent_window() -> None:
    guard = ScenarioDedupeGuard()
    assert guard.observe(scenario(name="first")).is_novel

    for index in range(10_000):
        distinct = scenario(
            name=f"distinct_{index}",
            goal=f"Exercise unrelated edge {index}",
            why=f"Preserve discovery breadth {index}",
        )
        assert guard.observe(distinct).is_novel

    duplicate = guard.observe(scenario(name="late_renamed_duplicate"))
    assert not duplicate.is_novel
    assert duplicate.first_seen_index == 0
    assert duplicate.seen_count == 2
    assert duplicate.total_evicted_at_observation == 0


def test_guard_allows_distinct_semantic_motifs() -> None:
    guard = ScenarioDedupeGuard()
    assert guard.observe(scenario(kind="handoff", goal="Escalate before execution")).is_novel
    assert guard.observe(scenario(kind="resumable", failure_mode="first_resume", goal="Resume from checkpoint")).is_novel
    assert guard.duplicate_count == 0


def test_guard_can_be_bounded_when_callers_opt_into_eviction() -> None:
    guard = ScenarioDedupeGuard(max_fingerprints=128)

    for index in range(1_000):
        observed = guard.observe(scenario(name=f"scenario_{index}", goal=f"unique goal {index}"))
        assert observed.is_novel

    assert guard.observed_count == 1_000
    assert guard.evicted_count > 0
    assert len(guard) == 128


def test_missing_fields_and_explicit_none_share_a_fingerprint() -> None:
    missing = {"kind": "success"}
    explicit_none = {"kind": "success", "effect": None, "failure_mode": None, "goal": None, "why": None}

    assert semantic_fingerprint(missing) == semantic_fingerprint(explicit_none)


def test_default_guard_keeps_early_run_fingerprints() -> None:
    guard = ScenarioDedupeGuard()
    assert guard.observe(scenario(name="first")).is_novel

    for index in range(12_000):
        assert guard.observe(scenario(name=f"unique_{index}", goal=f"unique global goal {index}")).is_novel

    late_duplicate = guard.observe(scenario(name="late_duplicate_of_first"))
    assert not late_duplicate.is_novel
    assert late_duplicate.first_seen_index == 0
    assert guard.evicted_count == 0


def test_bounded_guard_preserves_all_time_seen_count_after_eviction() -> None:
    guard = ScenarioDedupeGuard(max_fingerprints=1)
    first = scenario(name="first")
    second = scenario(name="second", goal="different")

    assert guard.observe(first).seen_count == 1
    assert guard.observe(second).is_novel
    observed_again = guard.observe(first)

    assert not observed_again.is_novel
    assert observed_again.first_seen_index == 0
    assert observed_again.seen_count == 2
