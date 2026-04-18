"""Development shim so repo-root Python commands can import the src-layout package.

This makes commands like:

    python -c "from safeloop.runtime import Runtime; print('ok')"

work from the repository root without requiring editable installation first.
"""

from __future__ import annotations

from pathlib import Path

_SRC_PACKAGE = Path(__file__).resolve().parent.parent / "src" / "safeloop"
__file__ = str(_SRC_PACKAGE / "__init__.py")
__path__ = [str(_SRC_PACKAGE)]

exec(compile((_SRC_PACKAGE / "__init__.py").read_text(encoding="utf-8"), __file__, "exec"))
