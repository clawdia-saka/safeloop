"""Local development helper so repo-root Python commands can import src/ packages.

This keeps smoke commands like:

    python -c "from safeloop.runtime import Runtime; print('ok')"

working from the repository root without requiring callers to export PYTHONPATH.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
