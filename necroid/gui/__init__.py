"""Tkinter GUI for Necroid end users.

The `gui` subpackage groups the GUI into small, focused modules:
  - `constants`     — palette, progress-label map, error titles, type aliases
  - `progress`      — `==>` step-line parser
  - `theme`         — ttk style application (`apply_theme`)
  - `tooltip`       — hover-tooltip widget helper
  - `cli_runner`    — subprocess + queue pump for shelling out to `necroid` CLI
  - `update_banner` — self-update nag-banner widget + handlers
  - `import_dialog` — two-stage GitHub import modal
  - `app`           — `ModderApp`, the main controller

External callers use just `launch()`.
"""
from .app import ModderApp, launch

__all__ = ["ModderApp", "launch"]
