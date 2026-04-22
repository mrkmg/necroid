"""Package-path helpers. Single source of truth for "where does necroid/ live on disk."

`package_dir()` returns the on-disk directory that contains the `necroid` package
itself (i.e. the parent of `necroid/__init__.py`). Several call sites need this to
locate sibling assets like `necroid/java/NecroidGetPzVersion.java` — centralising
the computation here means moving a module into a subpackage doesn't silently
break probe / asset lookups.
"""
from __future__ import annotations

from pathlib import Path


def package_dir() -> Path:
    """Absolute path to the `necroid/` package directory."""
    return Path(__file__).resolve().parent
