# safeloop

Minimal Python package skeleton for a transactional runtime.

## Quickstart

### Install

```bash
python -m pip install -e .
```

### Run tests

```bash
pytest tests/ -q
```

### GitHub-style reference demo

The demo in `examples/github_pr_demo.py` shows one concrete side-effecting action:
a fake GitHub `create_pull_request` call marked as a compensatable write. If a
follow-up step fails, the demo triggers compensation by calling `close_pr`.

Run it from the repository root:

```bash
python examples/github_pr_demo.py
```

Or import it from a Python snippet started from the repository root:

```python
from examples.github_pr_demo import run_github_pr_demo


def fake_create_pr(*, repo: str, title: str, body: str) -> dict[str, object]:
    return {"number": 101, "url": f"https://example.test/{repo}/pull/101"}


def fake_close_pr(*, repo: str, pr_number: int) -> None:
    print(f"Closed PR #{pr_number} for {repo}")


result = run_github_pr_demo(
    repo="nous/safeloop",
    title="Add rollback-safe demo",
    body="This is a demo PR.",
    actor="builder-bot",
    create_pr=fake_create_pr,
    close_pr=fake_close_pr,
    fail_after_create=True,
)

print(result.final_state)
print(result.compensation_triggered)
```

To run just the demo test file:

```bash
pytest tests/test_examples_github_demo.py -v
```
