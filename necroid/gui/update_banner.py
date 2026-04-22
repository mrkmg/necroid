"""Self-update nag banner.

A thin wrapper around a styled `ttk.Frame` that sits beneath the main header
and appears only when the background update-check reports a newer release.

Visibility, label text, and button wiring live here. The caller owns the
actual update-check worker + dispatch — the banner just fires `on_install` /
`on_dismiss` callbacks when the user clicks.
"""
from __future__ import annotations

from typing import Callable, Optional

import tkinter as tk
from tkinter import ttk


class UpdateBanner:
    def __init__(self, parent: tk.Widget,
                 on_install: Callable[[], None],
                 on_dismiss: Callable[[], None]):
        self.parent = parent
        self._on_install = on_install
        self._on_dismiss = on_dismiss

        self.frame = ttk.Frame(
            parent, style="UpdateBanner.TFrame", padding=(12, 6, 12, 6),
        )
        # Intentionally no .pack() here — shown from `show()`.

        self._label_var = tk.StringVar(value="")
        ttk.Label(
            self.frame,
            textvariable=self._label_var,
            style="UpdateBanner.TLabel",
        ).pack(side=tk.LEFT)

        ttk.Button(
            self.frame, text="Dismiss",
            style="UpdateBanner.TButton",
            command=self._on_dismiss,
        ).pack(side=tk.RIGHT, padx=(6, 0))

        ttk.Button(
            self.frame, text="Install Update",
            style="UpdateBanner.TButton",
            command=self._on_install,
        ).pack(side=tk.RIGHT)

    def show(self, text: str, *, after: Optional[tk.Widget] = None) -> None:
        self._label_var.set(text)
        if not self.frame.winfo_ismapped():
            if after is not None:
                self.frame.pack(fill=tk.X, after=after)
            else:
                self.frame.pack(fill=tk.X)

    def hide(self) -> None:
        if self.frame.winfo_ismapped():
            self.frame.pack_forget()

    @property
    def visible(self) -> bool:
        return bool(self.frame.winfo_ismapped())
