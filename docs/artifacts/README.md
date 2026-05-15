# HTML human-review artifacts

These static HTML files are reviewer-facing views only. Keep Markdown and JSON records canonical; do not treat the HTML copies as state or as a replacement for source review data.

- [`pr-review-explainer.html`](./pr-review-explainer.html) — plain-language PR review explainer with independent-review checklist.
- [`dd-research-20-items-report.html`](./dd-research-20-items-report.html) — DD / research 20項目 report view with evidence and verification sections.

## Review boundary

The templates intentionally contain placeholders (`TBD`, `YYYY-MM-DD`, `go / no-go / watch`) rather than generated claims. Populate generated HTML from canonical Markdown/JSON during report production, and verify generated views against their canonical sources before publishing.
