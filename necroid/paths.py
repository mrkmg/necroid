"""Package-path helpers. Single source of truth for "where does necroid/ live on disk."

`package_dir()` returns the on-disk directory that contains the `necroid` package
itself (i.e. the parent of `necroid/__init__.py`). Several call sites need this to
locate sibling assets like `necroid/java/NecroidGetPzVersion.java` — centralising
the computation here means moving a module into a subpackage doesn't silently
break probe / asset lookups.

In a PyInstaller-frozen binary the resolution routes through `sys._MEIPASS`,
where `--add-data "…{sep}necroid/java"` drops the probe source.
"""
from __future__ import annotations

import sys
from pathlib import Path


def package_dir() -> Path:
    """Absolute path to the `necroid/` package directory (or its runtime
    equivalent under `_MEIPASS` when frozen)."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return base / "necroid"
    return Path(__file__).resolve().parent
