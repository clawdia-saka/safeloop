# SafeLoop HTML readiness demo artifact

SafeLoop can render a self-contained local HTML report from an existing run directory:

```bash
safeloop html-report <run_dir>
# writes <run_dir>/safeloop-readiness-report.html
```

The HTML embeds local JSON artifact snapshots available at render time, including run metadata,
review summaries, rollback plans/results, policy suggestions, local anchors, and selected checkpoint
artifacts. It does not load external network resources.

Boundary language for demos and public readiness:

- Exact rollback is only claimed for covered local file changes.
- External side effects require compensation/manual review and are not exact rollback.
- External side-effect statuses such as `best_effort` or `verified` are review labels, not exact rollback guarantees.
- Local artifacts are tamper-evident review aids, not tamper-proof guarantees.
- SafeLoop does not claim a remote transparency log unless one is explicitly implemented and configured.
