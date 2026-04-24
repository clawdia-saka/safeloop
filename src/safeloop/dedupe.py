"""Semantic duplicate detection helpers for scenario sweepers."""

from __future__ import annotations

import hashlib
import re
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import Mapping

FINGERPRINT_FIELDS = ("kind", "effect", "failure_mode", "goal", "why")
_ALLOWED_CHARS_RE = re.compile(r"[^\w:+-]+", flags=re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize(value: object) -> str:
    """Return a stable token string for free-form scenario metadata."""

    if value is None:
        return ""
    text = str(value).casefold().strip()
    text = _ALLOWED_CHARS_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def semantic_fingerprint(scenario: Mapping[str, object]) -> str:
    """Compute a stable semantic fingerprint for a proposed scenario.

    The scenario name is deliberately excluded: long overnight runs often
    regenerate the same semantic motif under a new name, and those renamed
    replays should still be treated as duplicate churn. Missing fields and
    explicit ``None`` values normalize the same way so partial proposal records
    remain comparable.
    """

    payload = "\n".join(
        f"{field}={_normalize(scenario.get(field, ''))}" for field in FINGERPRINT_FIELDS
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class DedupeObservation:
    """Result of observing a scenario in a run-wide duplicate guard."""

    fingerprint: str
    is_novel: bool
    first_seen_index: int
    seen_count: int
    total_evicted_at_observation: int


class ScenarioDedupeGuard:
    """Track semantic fingerprints across a large scenario-sweeper run.

    By default the ledger is unbounded so early-run fingerprints are not
    forgotten later in the same run. Callers with strict memory budgets can set
    ``max_fingerprints`` to opt into FIFO eviction explicitly. Observations are
    protected by a small lock so threaded sweepers cannot corrupt counters.
    """

    def __init__(self, *, max_fingerprints: int | None = None) -> None:
        if max_fingerprints is not None and max_fingerprints <= 0:
            raise ValueError("max_fingerprints must be positive or None")
        self.max_fingerprints = max_fingerprints
        self._active_first_seen: OrderedDict[str, int] = OrderedDict()
        self._all_first_seen: dict[str, int] = {}
        self._seen_counts: dict[str, int] = {}
        self._observations = 0
        self._duplicate_count = 0
        self._evicted_count = 0
        self._lock = Lock()

    @property
    def duplicate_count(self) -> int:
        with self._lock:
            return self._duplicate_count

    @property
    def observed_count(self) -> int:
        with self._lock:
            return self._observations

    @property
    def evicted_count(self) -> int:
        with self._lock:
            return self._evicted_count

    def observe(self, scenario: Mapping[str, object]) -> DedupeObservation:
        fingerprint = semantic_fingerprint(scenario)
        with self._lock:
            index = self._observations
            self._observations += 1

            is_novel = fingerprint not in self._all_first_seen
            first_seen_index = self._remember_locked(fingerprint, index)
            if not is_novel:
                self._duplicate_count += 1
            return DedupeObservation(
                fingerprint=fingerprint,
                is_novel=is_novel,
                first_seen_index=first_seen_index,
                seen_count=self._seen_counts[fingerprint],
                total_evicted_at_observation=self._evicted_count,
            )

    def __len__(self) -> int:
        with self._lock:
            return len(self._active_first_seen)

    def _remember_locked(self, fingerprint: str, index: int) -> int:
        """Update ledgers while ``self._lock`` is held by ``observe``."""
        # Preserve all-time counts even if a caller opted into bounded FIFO
        # eviction and this fingerprint was forgotten from the active ledger.
        self._all_first_seen.setdefault(fingerprint, index)
        self._seen_counts[fingerprint] = self._seen_counts.get(fingerprint, 0) + 1
        self._active_first_seen.setdefault(fingerprint, self._all_first_seen[fingerprint])
        if self.max_fingerprints is not None:
            while len(self._active_first_seen) > self.max_fingerprints:
                self._active_first_seen.popitem(last=False)
                self._evicted_count += 1
        return self._all_first_seen[fingerprint]
