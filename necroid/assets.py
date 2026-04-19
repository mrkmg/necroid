"""Asset path resolution for dev mode and PyInstaller-frozen mode.

Derived assets are pre-rendered via assets/build-assets.sh and committed to
the repo. End users and the dist build do NOT need ImageMagick.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Derived assets (pre-rendered, committed).
HEADER_MARK = "necroid-mark-256.png"        # skull only, transparent bg (GUI header)
WINDOW_ICON_SKULL = "necroid-icon-skull-128.png"  # skull-on-tile (best for small icon slots)
WINDOW_ICON_FULL = "necroid-icon-256.png"   # full brand mark (large icon slots)


def asset_path(name: str) -> Path:
    """Resolve an asset path in dev mode or PyInstaller-frozen mode."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return base / "assets" / name
    # dev: <repo>/necroid/assets.py -> <repo>/assets/
    return Path(__file__).resolve().parent.parent / "assets" / name
