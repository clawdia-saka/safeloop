# SafeLoop Quarantine v0

SafeLoop quarantine converts destructive local file cleanup into an inspectable lifecycle event before the irreversible boundary.

The v0 scope is intentionally small:

- local single regular file delete only
- no directory quarantine
- no symlink quarantine
- no external side effects
- no hosted control plane
- no automatic tool firewall

Quarantine is not a replacement for review. It preserves evidence and a restore path for covered local files.

## CLI

Put a file into quarantine:

```bash
safeloop quarantine put generated.txt --run-dir "$RUN_DIR" --reason "cleanup generated artifact"
```

List items:

```bash
safeloop quarantine list --run-dir "$RUN_DIR"
```

Verify payload and metadata:

```bash
safeloop quarantine verify ITEM_ID --run-dir "$RUN_DIR"
```

Restore the file:

```bash
safeloop quarantine restore ITEM_ID --run-dir "$RUN_DIR"
```

Restore refuses overwrite by default. Use `--overwrite` only after reviewing the destination.

Purge payload bytes while keeping tombstone and audit evidence:

```bash
safeloop quarantine purge ITEM_ID --run-dir "$RUN_DIR" --reason "retention expired"
```

Purge old retained payloads:

```bash
safeloop quarantine empty --run-dir "$RUN_DIR" --older-than 30d
```

`empty` only purges retained items. Restored, purged, and tampered items are skipped.

## Artifacts

Artifacts live under the explicit run directory:

```text
RUN_DIR/
  quarantine/
    index.jsonl
    items/
      ITEM_ID/
        item.json
        restore-manifest.json
        audit.jsonl
        payload/
          file
```

`item.json` records the lifecycle status, original workspace-relative path, reason, actor, permissions, size, and pre-delete hash.

`restore-manifest.json` records the machine-readable restore policy. Restore paths must be relative, must not contain `..`, and must resolve inside the workspace root.

`audit.jsonl` records lifecycle events such as `captured`, `restored`, `purged`, and `tampered`.

After purge, `payload/file` is removed and `item.json` becomes a `quarantine-tombstone.v1` record. The audit trail remains.

## Operator Packet Manifest

When quarantine exists, `operator-packet-manifest.json` includes metadata evidence:

- `quarantine/index.jsonl`
- `quarantine/items/*/item.json`
- `quarantine/items/*/restore-manifest.json`
- `quarantine/items/*/audit.jsonl`

It intentionally excludes `quarantine/items/*/payload/file`. Packet manifests should verify metadata and lifecycle evidence, not copy quarantined payload bytes into operator packets.

## Boundary

Quarantine is exact local restore for covered files while payload evidence is retained. Purge is irreversible disposal of the payload; it is not rollback. Actions outside the local workspace remain compensation/manual-review only and are never exact rollback.
