# Security Policy

SafeLoop is a local-first runtime for agent evidence, rollback planning, and
manual-review/compensation boundaries. Security reports are welcome, especially
when they affect artifact integrity, rollback correctness, approval enforcement,
secret handling, or external side-effect classification.

## Supported Versions

| Version | Support status |
| --- | --- |
| `main` | Security fixes accepted before the next release. |
| `0.2.x` | Supported after the `v0.2.0` release is tagged. |
| `0.1.x` | Best-effort fixes only. |

## Reporting a Vulnerability

Do not open a public issue with exploit details, secrets, private run artifacts,
or sensitive logs.

Use GitHub private vulnerability reporting for this repository when available:

https://github.com/clawdia-saka/safeloop/security/advisories/new

If private reporting is unavailable, open a minimal public issue asking for a
private disclosure path. Include only the affected area and impact category, not
reproduction details.

## What Helps

- SafeLoop version or commit SHA.
- A minimal local reproduction using fake data.
- The affected command, artifact, or API surface.
- Whether the issue can cause unsafe rollback, hidden external side effects,
  artifact tampering, approval bypass, or secret exposure.

## Boundaries

SafeLoop does not claim tamper-proof storage, hosted production governance, or
exact rollback for external systems. Reports that show SafeLoop overclaiming
those boundaries are still useful because public safety language is part of the
security surface.
