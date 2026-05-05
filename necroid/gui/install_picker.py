"""Modal dialog for choosing the Project Zomboid client install location.

Lists every candidate found by Steam discovery (appmanifest + library scan)
as a radio row, plus an "Other…" row with a Browse button for custom paths.
On accept the selection is fingerprint-validated; failures keep the dialog
open with an inline error.

Returns Path | None. Used pre-init (when discovery is ambiguous or empty)
and post-init (Change install location button).
"""
from __future__ import annotations

import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import tkinter as tk
from tkinter import filedialog, ttk

from ..pz.steam_discovery import (
    PzCandidate,
    discover_client_install_candidates,
    fingerprint_pz_install,
)
from .constants import PALETTE

if TYPE_CHECKING:
    from .app import ModderApp


def _fmt_last_played(epoch: int | None) -> str:
    if not epoch:
        return ""
    try:
        return datetime.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d")
    except (OSError, ValueError, OverflowError):
        return ""


def _fmt_source(c: PzCandidate) -> str:
    if c.source == "appmanifest":
        return "via Steam manifest"
    if c.source == "directory-probe":
        return "found by scan"
    return c.source


class InstallPicker:
    """Modal dialog. Call `.show()` to run; returns selected `Path | None`."""

    OTHER_VALUE = "__other__"

    def __init__(self, app: "ModderApp", current: Path | None = None,
                 title: str = "Project Zomboid install location") -> None:
        self.app = app
        self.current = current
        self.result: Path | None = None
        self.candidates: list[PzCandidate] = discover_client_install_candidates()

        self.dlg = tk.Toplevel(app.tk)
        self.dlg.title(title)
        self.dlg.transient(app.tk)
        self.dlg.configure(bg=PALETTE["char_900"])
        self.dlg.geometry("640x420")
        self.dlg.protocol("WM_DELETE_WINDOW", self._on_cancel)

        self._selected = tk.StringVar(value="")
        self._other_path = tk.StringVar(value="")
        self._error_text = tk.StringVar(value="")

        self._build()

    def _build(self) -> None:
        outer = ttk.Frame(self.dlg, padding=(16, 14, 16, 12))
        outer.pack(fill=tk.BOTH, expand=True)

        intro = (
            "Choose where Project Zomboid is installed. Necroid uses this folder "
            "to read game files and to install mods."
        )
        ttk.Label(outer, text=intro, style="Brand.TLabel", wraplength=600,
                  justify="left").pack(anchor="w", pady=(0, 10))

        if self.current is not None:
            ttk.Label(outer, text=f"Current: {self.current}",
                      style="Tagline.TLabel").pack(anchor="w", pady=(0, 8))

        list_frame = ttk.Frame(outer)
        list_frame.pack(fill=tk.BOTH, expand=True)

        # Pre-select: current path if it matches a candidate, else first ok
        # candidate, else first candidate, else Other.
        preselect: str | None = None
        ok_candidates = [c for c in self.candidates if c.fingerprint_ok]
        all_candidates = self.candidates

        for c in all_candidates:
            if self.current is not None and c.path == self.current:
                preselect = str(c.path)
                break
        if preselect is None and ok_candidates:
            preselect = str(ok_candidates[0].path)
        elif preselect is None and all_candidates:
            preselect = str(all_candidates[0].path)
        if preselect is None:
            preselect = self.OTHER_VALUE
        self._selected.set(preselect)

        if not all_candidates:
            ttk.Label(list_frame,
                      text="No Project Zomboid installation was auto-detected.",
                      style="Tagline.TLabel").pack(anchor="w", pady=(0, 8))

        for c in all_candidates:
            row = ttk.Frame(list_frame)
            row.pack(fill=tk.X, pady=2)
            rb = ttk.Radiobutton(row, variable=self._selected, value=str(c.path))
            rb.pack(side=tk.LEFT)
            label = str(c.path)
            badge = _fmt_source(c)
            if not c.fingerprint_ok:
                badge += " — invalid (no PZ files found)"
            lp = _fmt_last_played(c.last_played)
            if lp:
                badge += f" · last played {lp}"
            text_frame = ttk.Frame(row)
            text_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))
            ttk.Label(text_frame, text=label, style="Brand.TLabel").pack(anchor="w")
            ttk.Label(text_frame, text=badge, style="Tagline.TLabel").pack(anchor="w")

        # "Other…" row
        other_row = ttk.Frame(list_frame)
        other_row.pack(fill=tk.X, pady=(8, 2))
        ttk.Radiobutton(other_row, variable=self._selected,
                        value=self.OTHER_VALUE).pack(side=tk.LEFT)
        ttk.Label(other_row, text="Other folder…",
                  style="Brand.TLabel").pack(side=tk.LEFT, padx=(6, 8))
        entry = ttk.Entry(other_row, textvariable=self._other_path)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ttk.Button(other_row, text="Browse…",
                   command=self._on_browse).pack(side=tk.LEFT)

        # Error label (hidden by default).
        ttk.Label(outer, textvariable=self._error_text,
                  style="Tagline.TLabel", foreground="#e35d6a",
                  wraplength=600, justify="left").pack(anchor="w", pady=(8, 0))

        # Buttons
        btns = ttk.Frame(outer)
        btns.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(btns, text="Cancel",
                   command=self._on_cancel).pack(side=tk.RIGHT)
        ttk.Button(btns, text="OK", style="Primary.TButton",
                   command=self._on_ok).pack(side=tk.RIGHT, padx=(0, 8))

    def _on_browse(self) -> None:
        initial = self._other_path.get() or (str(self.current) if self.current else "")
        chosen = filedialog.askdirectory(
            parent=self.dlg,
            title="Select Project Zomboid install folder",
            initialdir=initial or None,
            mustexist=True,
        )
        if chosen:
            self._other_path.set(chosen)
            self._selected.set(self.OTHER_VALUE)

    def _resolve_selection(self) -> Path | None:
        sel = self._selected.get()
        if sel == self.OTHER_VALUE:
            raw = self._other_path.get().strip()
            if not raw:
                self._error_text.set("Pick a folder via Browse… or choose one of the detected installs.")
                return None
            return Path(raw).expanduser()
        if not sel:
            self._error_text.set("Select an install location.")
            return None
        return Path(sel)

    def _on_ok(self) -> None:
        path = self._resolve_selection()
        if path is None:
            return
        if not path.exists():
            self._error_text.set(f"Path does not exist: {path}")
            return
        if not path.is_dir():
            self._error_text.set(f"Not a folder: {path}")
            return
        if not fingerprint_pz_install(path):
            self._error_text.set(
                f"That folder doesn't look like a Project Zomboid install: {path}\n"
                "Expected projectzomboid.jar, ProjectZomboid64.exe, or a `zombie/` folder."
            )
            return
        try:
            self.result = path.resolve()
        except (OSError, RuntimeError):
            self.result = path
        self.dlg.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.dlg.destroy()

    def show(self) -> Path | None:
        self.dlg.grab_set()
        self.dlg.wait_window()
        return self.result
