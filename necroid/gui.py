"""Minimal tkinter GUI for end users.

Layout:
    ┌──────────────────────────────────────────────────────────┐
    │  [skull]  Necroid   Install to: [client ▾]   [ Set Up ]   │
    │           Beyond Workshop — Project Zomboid mod manager  │
    ├──────────────────────────────────────────────────────────┤
    │  Treeview of mods: ☑/☐ | name | cli-only | status | desc │
    ├──────────────────────────────────────────────────────────┤
    │  [Refresh]                      [ Revert ] [Apply Changes]│
    ├──────────────────────────────────────────────────────────┤
    │  ● Ready                                     [ progress ] │
    ├──────────────────────────────────────────────────────────┤
    │  ▸ Show details                                  [ Copy ] │
    │  (log, collapsed by default; auto-opens on error)         │
    └──────────────────────────────────────────────────────────┘

State-based model: the checkbox column auto-reflects the installed stack for
the selected destination. Users edit the selection, then hit Apply Changes to
reconcile (install what was added, uninstall what was removed). Switching the
install-to destination re-seeds checkboxes from that destination's state file.

Single shared workspace + a destination toggle in the header. Mods are never
hidden — clientOnly mods simply can't be installed while install-to is server
(their rows grey out and the checkbox won't toggle).
"""
from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Literal, Optional

import tkinter as tk
from tkinter import messagebox, ttk

from . import __version__, updater
from .assets import HEADER_MARK, WINDOW_ICON_FULL, WINDOW_ICON_SKULL, asset_path
from .config import read_config
from . import markdown_render
from .depgraph import resolve_deps, reverse_dependents
from .errors import (
    ModDependencyCycle,
    ModDependencyMissing,
    ModIncompatibility,
    PzVersionDetectError,
)
from .mod import (
    has_origin,
    list_mods,
    mod_base_name,
    mod_major,
    read_mod_json,
    read_origin,
)
from .commands.mod_update import read_cache as read_update_cache
from .profile import load_profile
from .pzversion import PzVersion, detect_pz_version
from .state import read_state


InstallTo = Literal["client", "server"]


# Charcoal + Bone palette — sampled from the brand mark.
PALETTE = {
    "char_900":  "#1F1F22",
    "char_700":  "#2B2B30",
    "char_500":  "#3D3D44",
    "char_300":  "#5A5A63",
    "bone":      "#EDE6D3",
    "bone_dim":  "#C7BFA8",
    "accent":    "#8FA68E",
    "warn":      "#D9A441",
    "error":     "#C86060",
}


STEP_FRIENDLY = {
    "stage source": "Preparing files…",
    "compile": "Compiling Java classes…",
    "restore prior": "Restoring previous mods…",
    "copy class files to": "Writing to Project Zomboid…",
    "resolve PZ install path": "Looking for Project Zomboid…",
    "tools check": "Checking Java + Git…",
    "vineflower.jar": "Downloading decompiler…",
    "copy PZ jars": "Copying game libraries…",
    "copy PZ class trees": "Copying game classes…",
    "rejar class trees": "Repackaging classes…",
    "write data/.mod-config": "Saving settings…",
    "decompile class subtrees": "Decompiling game code (this takes a while)…",
    "scaffold mods": "Finishing setup…",
    "checking mod patches": "Re-checking mod patches…",
    "downloading": "Downloading update…",
    "extracting binary": "Extracting update…",
    "swapping binary": "Installing update…",
}

CMD_FAILURE_TITLE = {
    "install": "Apply changes failed",
    "uninstall": "Apply changes failed",
    "apply": "Apply changes failed",
    "init": "Setup failed",
    "resync-pristine": "Update failed",
    "update": "Self-update failed",
}

_STEP_RE = re.compile(r"^==>\s+(?:step\s+(\d+)/(\d+):\s+)?(.+)$")


class _Tooltip:
    def __init__(self, widget: tk.Widget, text: str, delay_ms: int = 600):
        self.widget = widget
        self.text = text
        self.delay = delay_ms
        self._after_id: Optional[str] = None
        self._tip: Optional[tk.Toplevel] = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def set_text(self, text: str) -> None:
        self.text = text

    def _schedule(self, _e=None) -> None:
        self._cancel()
        self._after_id = self.widget.after(self.delay, self._show)

    def _cancel(self) -> None:
        if self._after_id:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self) -> None:
        if self._tip is not None:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        tip.configure(bg=PALETTE["char_500"])
        lbl = tk.Label(
            tip, text=self.text,
            bg=PALETTE["char_500"], fg=PALETTE["bone"],
            font=("Segoe UI", 9), padx=8, pady=4,
            justify="left",
        )
        lbl.pack()
        self._tip = tip

    def _hide(self, _e=None) -> None:
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


class ModderApp:
    def __init__(self, root: Path, initial_install_to: InstallTo):
        self.root = root
        self.install_to: InstallTo = initial_install_to
        self.checked: set[str] = set()
        self.installed_stack: list[str] = []
        self.mod_order: list[str] = []
        self._ws_major: int = 0
        # Relation maps (keyed by canonical mod dir name, e.g. admin-xray-41).
        # Populated in refresh_mods; used by row-click and apply preflight.
        self._dep_closure: dict[str, list[str]] = {}
        self._incompat: dict[str, set[str]] = {}
        self._effective_client_only: dict[str, bool] = {}
        self._dep_graph_error: dict[str, str] = {}

        self.tk = tk.Tk()
        self.tk.title("Necroid")
        self.tk.geometry("900x620")
        self.tk.minsize(720, 480)

        self._apply_theme()

        self._busy = False
        self._last_error: Optional[str] = None
        self._warnings: list[str] = []
        self._current_cmd: Optional[str] = None
        self._log_expanded = False
        self._pz_mismatch_reason: Optional[str] = None

        self._build_header()
        self._build_update_banner()
        self._build_mod_list()
        self._build_footer()
        self._build_status_strip()
        self._build_log()

        self._log_queue: "queue.Queue[str]" = queue.Queue()
        self.tk.after(100, self._drain_log)

        # Update-check state.
        self._update_release: Optional["updater.ReleaseInfo"] = None
        self._update_dismissed = False
        self._update_check_queue: "queue.Queue[Optional[updater.ReleaseInfo]]" = queue.Queue()
        self.tk.after(120, self._drain_update_check)
        self._start_update_check()

        self.refresh_mods()
        self._update_primary_button()
        self._set_status("idle", "Ready", progress=None)

    # --- theme ---

    def _apply_theme(self) -> None:
        self.tk.configure(bg=PALETTE["char_900"])

        try:
            self._mark_full = tk.PhotoImage(file=str(asset_path(HEADER_MARK)))
            self._mark = self._mark_full.subsample(4, 4)
            self._icon_small = tk.PhotoImage(file=str(asset_path(WINDOW_ICON_SKULL)))
            self._icon_large = tk.PhotoImage(file=str(asset_path(WINDOW_ICON_FULL)))
            self.tk.iconphoto(True, self._icon_small, self._icon_large)
        except tk.TclError:
            self._mark = None

        s = ttk.Style(self.tk)
        s.theme_use("clam")
        s.configure(".", background=PALETTE["char_900"], foreground=PALETTE["bone"],
                    font=("Segoe UI", 10))
        s.configure("TFrame", background=PALETTE["char_900"])
        s.configure("TLabel", background=PALETTE["char_900"], foreground=PALETTE["bone"])
        s.configure("Brand.TLabel",
                    background=PALETTE["char_900"], foreground=PALETTE["bone"],
                    font=("Segoe UI", 18, "bold"))
        s.configure("Pill.TLabel",
                    background=PALETTE["char_700"], foreground=PALETTE["accent"],
                    font=("Segoe UI", 9, "bold"), padding=(6, 2))
        s.configure("Tagline.TLabel",
                    background=PALETTE["char_900"], foreground=PALETTE["bone_dim"],
                    font=("Segoe UI", 9, "italic"))
        s.configure("StatusHeadline.TLabel",
                    background=PALETTE["char_900"], foreground=PALETTE["bone"],
                    font=("Segoe UI", 10))
        s.configure("StatusDot.TLabel",
                    background=PALETTE["char_900"], foreground=PALETTE["bone_dim"],
                    font=("Segoe UI", 14))
        s.configure("Disclosure.TLabel",
                    background=PALETTE["char_900"], foreground=PALETTE["bone_dim"],
                    font=("Segoe UI", 9))
        s.configure("Sep.TFrame", background=PALETTE["char_500"])
        s.configure("UpdateBanner.TFrame", background=PALETTE["warn"])
        s.configure("UpdateBanner.TLabel",
                    background=PALETTE["warn"], foreground=PALETTE["char_900"],
                    font=("Segoe UI", 10, "bold"))
        s.configure("UpdateBanner.TButton",
                    background=PALETTE["char_900"], foreground=PALETTE["bone"],
                    borderwidth=0, focusthickness=0, padding=(10, 4),
                    font=("Segoe UI", 9, "bold"))
        s.map("UpdateBanner.TButton",
              background=[("active", PALETTE["char_700"])],
              foreground=[("active", PALETTE["bone"])])
        s.configure("TButton",
                    background=PALETTE["char_500"], foreground=PALETTE["bone"],
                    borderwidth=0, focusthickness=0, padding=(12, 6))
        s.map("TButton",
              background=[("active", PALETTE["char_300"]),
                          ("disabled", PALETTE["char_700"])],
              foreground=[("disabled", PALETTE["bone_dim"])])
        s.configure("Primary.TButton",
                    background=PALETTE["accent"], foreground=PALETTE["char_900"],
                    borderwidth=0, focusthickness=0, padding=(14, 6),
                    font=("Segoe UI", 10, "bold"))
        s.map("Primary.TButton",
              background=[("active", PALETTE["bone_dim"]),
                          ("disabled", PALETTE["char_700"])],
              foreground=[("disabled", PALETTE["bone_dim"])])
        s.configure("Link.TButton",
                    background=PALETTE["char_900"], foreground=PALETTE["bone_dim"],
                    borderwidth=0, focusthickness=0, padding=(4, 2),
                    font=("Segoe UI", 9))
        s.map("Link.TButton",
              background=[("active", PALETTE["char_900"])],
              foreground=[("active", PALETTE["bone"])])
        s.configure("Treeview",
                    background=PALETTE["char_700"], fieldbackground=PALETTE["char_700"],
                    foreground=PALETTE["bone"], rowheight=24, borderwidth=0)
        s.configure("Treeview.Heading",
                    background=PALETTE["char_500"], foreground=PALETTE["bone"],
                    font=("Segoe UI", 10, "bold"), borderwidth=0)
        s.map("Treeview",
              background=[("selected", PALETTE["accent"])],
              foreground=[("selected", PALETTE["char_900"])])
        s.configure("Vertical.TScrollbar",
                    background=PALETTE["char_500"], troughcolor=PALETTE["char_700"],
                    borderwidth=0, arrowcolor=PALETTE["bone"])
        s.configure("Horizontal.TProgressbar",
                    background=PALETTE["accent"], troughcolor=PALETTE["char_700"],
                    borderwidth=0, lightcolor=PALETTE["accent"],
                    darkcolor=PALETTE["accent"])
        s.configure("TCombobox",
                    fieldbackground=PALETTE["char_700"], background=PALETTE["char_500"],
                    foreground=PALETTE["bone"], arrowcolor=PALETTE["bone"],
                    selectbackground=PALETTE["char_700"],
                    selectforeground=PALETTE["bone"],
                    insertcolor=PALETTE["bone"])
        s.map("TCombobox",
              fieldbackground=[("readonly", PALETTE["char_700"]),
                               ("disabled", PALETTE["char_700"])],
              foreground=[("readonly", PALETTE["bone"]),
                          ("disabled", PALETTE["bone_dim"])],
              selectbackground=[("readonly", PALETTE["char_700"])],
              selectforeground=[("readonly", PALETTE["bone"])],
              arrowcolor=[("disabled", PALETTE["bone_dim"])])

        # The dropdown listbox is a plain tk.Listbox owned by the Combobox
        # popdown toplevel — ttk.Style doesn't reach it. Configure via the
        # option database instead.
        self.tk.option_add("*TCombobox*Listbox.background", PALETTE["char_700"])
        self.tk.option_add("*TCombobox*Listbox.foreground", PALETTE["bone"])
        self.tk.option_add("*TCombobox*Listbox.selectBackground", PALETTE["accent"])
        self.tk.option_add("*TCombobox*Listbox.selectForeground", PALETTE["char_900"])
        self.tk.option_add("*TCombobox*Listbox.font", ("Segoe UI", 10))
        self.tk.option_add("*TCombobox*Listbox.borderWidth", 0)
        self.tk.option_add("*TCombobox*Listbox.relief", "flat")

    # --- layout ---

    def _build_header(self) -> None:
        hdr = ttk.Frame(self.tk, padding=(12, 10, 12, 8))
        hdr.pack(fill=tk.X)

        if self._mark is not None:
            ttk.Label(hdr, image=self._mark).pack(side=tk.LEFT, padx=(0, 14))

        title_col = ttk.Frame(hdr)
        title_col.pack(side=tk.LEFT, anchor="w")

        ttk.Label(title_col, text="Necroid", style="Brand.TLabel").pack(anchor="w")
        ttk.Label(title_col,
                  text="Beyond Workshop — Project Zomboid mod manager",
                  style="Tagline.TLabel").pack(anchor="w", pady=(2, 0))
        # Workspace PZ-version label (set by refresh_mods / _reload_cfg).
        self.pz_label_var = tk.StringVar(value="")
        ttk.Label(title_col, textvariable=self.pz_label_var,
                  style="Tagline.TLabel").pack(anchor="w", pady=(2, 0))

        # Install-to toggle on the right.
        right = ttk.Frame(hdr)
        right.pack(side=tk.RIGHT)

        ttk.Label(right, text="Install to:", style="Tagline.TLabel").pack(
            side=tk.LEFT, padx=(0, 6))
        self.install_to_var = tk.StringVar(value=self.install_to)
        self.install_to_combo = ttk.Combobox(
            right, textvariable=self.install_to_var,
            values=("client", "server"), width=8, state="readonly",
        )
        self.install_to_combo.pack(side=tk.LEFT, padx=(0, 10))
        self.install_to_combo.bind("<<ComboboxSelected>>", self._on_install_to_changed)

        self.btn_init = ttk.Button(right, text="Set Up", style="Primary.TButton",
                                   command=self.on_init)
        self.btn_init.pack(side=tk.LEFT)
        _Tooltip(self.btn_init,
                 "First-time setup copies game files into this folder so mods\n"
                 "can be built. After setup, this re-syncs when the game updates.")

        self.btn_check_updates = ttk.Button(right, text="Check Updates",
                                            command=self.on_check_updates)
        self.btn_check_updates.pack(side=tk.LEFT, padx=(8, 0))
        _Tooltip(self.btn_check_updates,
                 "Query GitHub for newer versions of every imported mod.\n"
                 "Results decorate the Version column with ⬆ badges.")

        self.btn_import = ttk.Button(right, text="Import…",
                                     command=self.on_import_clicked)
        self.btn_import.pack(side=tk.LEFT, padx=(8, 0))
        _Tooltip(self.btn_import,
                 "Pull mods from a GitHub repository.\n"
                 "Single-mod and multi-mod repos both supported.")

        ttk.Frame(self.tk, style="Sep.TFrame", height=1).pack(fill=tk.X, padx=12)

    def _on_install_to_changed(self, _e=None) -> None:
        self.install_to = self.install_to_var.get()  # type: ignore[assignment]
        self.refresh_mods()

    def _build_update_banner(self) -> None:
        """Create (but don't show) the 'update available' banner. Rendered
        beneath the header; appears only after a background check reports a
        newer release."""
        self.update_banner = ttk.Frame(
            self.tk, style="UpdateBanner.TFrame", padding=(12, 6, 12, 6),
        )
        # Intentionally no .pack() here — shown from _show_update_banner().

        self.update_banner_label_var = tk.StringVar(value="")
        ttk.Label(
            self.update_banner,
            textvariable=self.update_banner_label_var,
            style="UpdateBanner.TLabel",
        ).pack(side=tk.LEFT)

        self.btn_update_dismiss = ttk.Button(
            self.update_banner, text="Dismiss",
            style="UpdateBanner.TButton",
            command=self._on_dismiss_update,
        )
        self.btn_update_dismiss.pack(side=tk.RIGHT, padx=(6, 0))

        self.btn_update_install = ttk.Button(
            self.update_banner, text="Install Update",
            style="UpdateBanner.TButton",
            command=self._on_install_update,
        )
        self.btn_update_install.pack(side=tk.RIGHT)

    def _start_update_check(self) -> None:
        """Spawn a background thread that does the GitHub check without
        blocking GUI startup. Result is posted to self._update_check_queue
        and picked up by `_drain_update_check` on the Tk thread."""
        # Editable / source installs don't expose a working self-update path
        # from the GUI button (the subprocess `update` command prints a hint
        # and exits). Skip the banner entirely there — we'd just be nagging
        # the developer.
        if not updater.is_frozen():
            return

        def worker() -> None:
            try:
                release = updater.check_for_update(
                    self.root, quiet=True, timeout=5.0,
                )
            except Exception:
                release = None
            self._update_check_queue.put(release)

        threading.Thread(target=worker, daemon=True).start()

    def _drain_update_check(self) -> None:
        try:
            while True:
                release = self._update_check_queue.get_nowait()
                if release is not None and not self._update_dismissed:
                    self._show_update_banner(release)
        except queue.Empty:
            pass
        # Poll every ~500ms for another 30s after which we stop (the check
        # completes in one shot, so subsequent polls are harmless no-ops).
        self.tk.after(500, self._drain_update_check)

    def _show_update_banner(self, release: "updater.ReleaseInfo") -> None:
        self._update_release = release
        self.update_banner_label_var.set(
            f"Update available: v{__version__} → v{release.pretty_version}"
        )
        # Insert the banner just after the header (which is `.pack`ed first).
        if not self.update_banner.winfo_ismapped():
            self.update_banner.pack(fill=tk.X, after=self._get_header_separator())

    def _get_header_separator(self) -> tk.Widget:
        """Return the 1px Sep.TFrame produced by `_build_header` so the
        banner can be inserted just below it via `pack(..., after=...)`."""
        # The header built two frames: the inner hdr frame and a 1px Sep
        # separator. The separator is the last packed child before the banner
        # would appear.
        children = self.tk.pack_slaves()
        # Find the most-recent Sep.TFrame.
        for child in reversed(children):
            try:
                style = child.cget("style")
            except tk.TclError:
                continue
            if style == "Sep.TFrame":
                return child
        # Fallback: just pack at current insertion point.
        return children[0]

    def _hide_update_banner(self) -> None:
        if self.update_banner.winfo_ismapped():
            self.update_banner.pack_forget()

    def _on_dismiss_update(self) -> None:
        self._update_dismissed = True
        self._hide_update_banner()

    def _on_install_update(self) -> None:
        if self._busy:
            messagebox.showinfo(
                "Busy",
                "Another command is already running. Wait for it to finish "
                "before installing the update.",
            )
            return
        release = self._update_release
        if release is None:
            return
        pretty = release.pretty_version
        msg = (
            f"Download and install Necroid v{pretty}?\n\n"
            f"Current: v{__version__}\n"
            f"Latest:  v{pretty}\n\n"
            f"The current binary will be replaced in place. Necroid will "
            f"close when the update finishes — re-open it to start using "
            f"the new version."
        )
        if release.html_url:
            msg += f"\n\nRelease notes:\n{release.html_url}"
        if not messagebox.askyesno("Install update", msg):
            return
        self._hide_update_banner()
        # Delegate to the CLI — reuses step parsing and progress UI. The
        # updater calls os._exit(0) after spawning the restart, so this
        # subprocess ends cleanly with code 0.
        self._run_cli(["update", "--yes"])

    def _build_mod_list(self) -> None:
        frame = ttk.Frame(self.tk, padding=(12, 8, 12, 0))
        frame.pack(fill=tk.BOTH, expand=True)

        columns = ("check", "name", "info", "origin", "kind", "version", "status", "desc")
        tv = ttk.Treeview(frame, columns=columns, show="headings", selectmode="none")
        tv.heading("check", text="")
        tv.heading("name", text="Mod")
        tv.heading("info", text="")
        tv.heading("origin", text="")
        tv.heading("kind", text="Type")
        tv.heading("version", text="Version")
        tv.heading("status", text="Status")
        tv.heading("desc", text="Description")
        tv.column("check", width=30, anchor=tk.CENTER, stretch=False)
        tv.column("name", width=170, anchor=tk.W)
        tv.column("info", width=30, anchor=tk.CENTER, stretch=False)
        tv.column("origin", width=30, anchor=tk.CENTER, stretch=False)
        tv.column("kind", width=78, anchor=tk.W, stretch=False)
        tv.column("version", width=110, anchor=tk.W, stretch=False)
        tv.column("status", width=96, anchor=tk.W)
        tv.column("desc", width=380, anchor=tk.W)
        tv.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tv.yview)
        tv.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        tv.tag_configure("blocked", foreground=PALETTE["bone_dim"])
        tv.tag_configure("outdated", foreground=PALETTE["warn"])

        tv.bind("<Button-1>", self._on_row_click)
        # Right-click context menu — Button-3 on Win/Linux, Button-2 on macOS.
        tv.bind("<Button-3>", self._on_row_context)
        tv.bind("<Button-2>", self._on_row_context)
        self.tv = tv

    def _build_footer(self) -> None:
        ft = ttk.Frame(self.tk, padding=(12, 8, 12, 0))
        ft.pack(fill=tk.X)
        btn_refresh = ttk.Button(ft, text="Refresh", command=self.refresh_mods)
        btn_refresh.pack(side=tk.LEFT)
        _Tooltip(btn_refresh, "Reload the mod list from disk and reset the\n"
                              "selection to what's actually installed.")

        self.btn_apply = ttk.Button(ft, text="Apply Changes",
                                    style="Primary.TButton",
                                    command=self.on_apply_changes)
        self.btn_apply.pack(side=tk.RIGHT)
        _Tooltip(self.btn_apply,
                 "Reconcile the installed stack on the chosen destination to\n"
                 "match what's checked here: installs added mods, uninstalls\n"
                 "removed ones. Atomic — nothing changes in the game install\n"
                 "until the full operation succeeds.")

        self.btn_revert = ttk.Button(ft, text="Revert", command=self.on_revert)
        self.btn_revert.pack(side=tk.RIGHT, padx=(0, 6))
        _Tooltip(self.btn_revert,
                 "Discard pending check/uncheck edits and re-seed the\n"
                 "selection from the currently installed stack.")

    def _build_status_strip(self) -> None:
        wrap = ttk.Frame(self.tk, padding=(12, 8, 12, 4))
        wrap.pack(fill=tk.X)

        self.status_dot = ttk.Label(wrap, text="●", style="StatusDot.TLabel")
        self.status_dot.pack(side=tk.LEFT, padx=(0, 8))

        self.status_headline = ttk.Label(wrap, text="Ready",
                                         style="StatusHeadline.TLabel",
                                         anchor="w")
        self.status_headline.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Outdated-mods chip — clickable, hidden when count == 0.
        self.outdated_label_var = tk.StringVar(value="")
        self.btn_outdated = ttk.Button(
            wrap, textvariable=self.outdated_label_var,
            style="Link.TButton", command=self.on_update_all,
        )
        # Not packed initially — _update_outdated_label controls visibility.

        self.progress = ttk.Progressbar(wrap, mode="indeterminate", length=180)
        self.progress.pack(side=tk.RIGHT)
        self.progress.pack_forget()

        ttk.Frame(self.tk, style="Sep.TFrame", height=1).pack(fill=tk.X, padx=12)

    def _update_outdated_label(self) -> None:
        n = int(getattr(self, "_outdated_count", 0) or 0)
        if not hasattr(self, "btn_outdated"):
            return
        if n <= 0:
            try:
                self.btn_outdated.pack_forget()
            except Exception:
                pass
            self.outdated_label_var.set("")
            return
        plural = "" if n == 1 else "s"
        self.outdated_label_var.set(f"⬆ {n} update{plural} available")
        try:
            self.btn_outdated.pack(side=tk.RIGHT, padx=(0, 8))
        except Exception:
            pass

    def _build_log(self) -> None:
        bar = ttk.Frame(self.tk, padding=(12, 4, 12, 0))
        bar.pack(fill=tk.X)
        self.btn_disclose = ttk.Button(bar, text="▸ Show details",
                                       style="Link.TButton",
                                       command=self._toggle_log)
        self.btn_disclose.pack(side=tk.LEFT)
        self.btn_copy = ttk.Button(bar, text="Copy log", style="Link.TButton",
                                   command=self._copy_log)
        self.btn_copy.pack(side=tk.RIGHT)
        _Tooltip(self.btn_copy, "Copy the full log to the clipboard.")

        self.log_body = ttk.Frame(self.tk, padding=(12, 4, 12, 10))

        self.log_text = tk.Text(self.log_body, height=10, wrap="word",
                                font=("Consolas", 9), state=tk.DISABLED,
                                bg=PALETTE["char_700"], fg=PALETTE["bone"],
                                insertbackground=PALETTE["bone"],
                                borderwidth=0, highlightthickness=0)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(self.log_body, orient=tk.VERTICAL,
                           command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        self.log_text.tag_configure("step", foreground=PALETTE["bone"])
        self.log_text.tag_configure("info", foreground=PALETTE["bone_dim"])
        self.log_text.tag_configure("warn", foreground=PALETTE["warn"])
        self.log_text.tag_configure("error", foreground=PALETTE["error"])
        self.log_text.tag_configure("success", foreground=PALETTE["accent"])

    # --- data ---

    def refresh_mods(self, reseed_checked: bool = True) -> None:
        self.tv.delete(*self.tv.get_children())
        try:
            cfg = read_config(self.root, required=False)
            profile = load_profile(self.root, cfg=cfg) if cfg else None
        except Exception:
            cfg = None
            profile = None

        ws_major = int(getattr(cfg, "workspace_major", 0) or 0) if cfg else 0
        ws_version = str(getattr(cfg, "workspace_version", "") or "") if cfg else ""

        # Update workspace label in header.
        if ws_major and ws_version:
            self.pz_label_var.set(f"PZ {ws_version} · major {ws_major}")
        elif ws_major:
            self.pz_label_var.set(f"PZ major {ws_major}")
        else:
            self.pz_label_var.set("")

        mods_dir = self.root / "data" / "mods"
        if not mods_dir.exists():
            self._log(f"(no mods directory at {mods_dir}; run Set Up)", tag="info")
            self.installed_stack = []
            self.mod_order = []
            self.checked = set()
            self._pz_mismatch_reason = None
            self._update_primary_button()
            self._update_apply_button_state()
            return
        installed_stack: list[str] = []
        if profile:
            state = read_state(profile.state_file(self.install_to))
            installed_stack = list(state.stack)
        self.installed_stack = installed_stack
        if reseed_checked:
            self.checked = set(installed_stack)

        # Detect destination install's PZ version (for major-mismatch banner + drift badges).
        detected_dest = None
        mismatch_reason: str | None = None
        if profile and ws_major:
            pz = profile.pz_install(self.install_to)
            if pz is not None and pz.exists():
                content = profile.content_dir_for(self.install_to)
                try:
                    detected_dest = detect_pz_version(
                        content, Path(__file__).resolve().parent, self.root / "data")
                    if detected_dest.major != ws_major:
                        mismatch_reason = (
                            f"Workspace is PZ major {ws_major}; {self.install_to} install is "
                            f"PZ {detected_dest}. Install disabled — run "
                            f"`necroid resync-pristine --from {self.install_to} --force-major-change`."
                        )
                except PzVersionDetectError as e:
                    mismatch_reason = f"cannot read {self.install_to} install's PZ version: {e}"
        self._pz_mismatch_reason = mismatch_reason

        # Filter mods to workspace major (or everything if workspace unbound — legacy).
        if ws_major:
            candidates = list_mods(mods_dir, workspace_major=ws_major)
        else:
            candidates = list_mods(mods_dir, include_all=True)

        self._ws_major = int(ws_major or 0)

        # Recompute relation maps before rendering rows — row rendering reads
        # `effective_client_only` for blocking decisions.
        self._rebuild_relation_maps(mods_dir, candidates)

        # Pull update-cache once per refresh — drives outdated badges.
        cache_doc = read_update_cache(self.root / "data") if cfg else {}
        update_cache = dict((cache_doc.get("mods") or {}))
        self._update_cache = update_cache
        self._mj_by_name = {}      # name -> ModJson, for context menu / origin reads
        outdated_count = 0

        has_blocked = False
        order: list[str] = []
        for name in candidates:
            try:
                mj = read_mod_json(mods_dir / name)
            except Exception:
                continue
            order.append(name)
            self._mj_by_name[name] = mj
            effective_co = self._effective_client_only.get(name, mj.client_only)
            blocked = effective_co and self.install_to == "server"
            if blocked:
                # Blocked rows can't be part of the selection on this dest.
                self.checked.discard(name)

            # Minor/patch-drift badge (informational; does not block install).
            drift = ""
            if detected_dest and mj.expected_version:
                try:
                    ev = PzVersion.parse(mj.expected_version)
                    if ev.major == detected_dest.major and (
                            ev.minor, ev.patch, ev.suffix
                    ) != (detected_dest.minor, detected_dest.patch, detected_dest.suffix):
                        drift = " ⚠"
                except Exception:
                    pass

            origin = read_origin(mj)
            origin_glyph = "⤓" if origin else ""
            cache_entry = update_cache.get(name) or {}
            up_v = cache_entry.get("upstreamVersion")
            cache_status = cache_entry.get("status", "")
            is_outdated = (
                origin and up_v and cache_status == "outdated"
            )
            if is_outdated:
                version_cell = f"{mj.version}  ⬆ {up_v}"
                outdated_count += 1
            elif origin and up_v:
                # Have cache + up-to-date.
                version_cell = f"{mj.version}  ✓"
            else:
                version_cell = mj.version

            status = self._row_status(name, blocked=blocked) + drift
            if is_outdated and "⚠" not in status:
                status = "⚠ " + status if status else "⚠"
            check = "☑" if (name in self.checked and not blocked) else "☐"
            info = "ⓘ" if (mods_dir / name / "README.md").exists() else ""
            if mj.client_only:
                kind = "client-only"
            elif effective_co:
                kind = "client-only*"  # via dep
            else:
                kind = "any"
            display_name = mod_base_name(name)
            tags: list[str] = []
            if blocked:
                tags.append("blocked")
            if is_outdated:
                tags.append("outdated")
            tag_args = tuple(tags)
            desc = self._decorate_desc(name, mj)
            self.tv.insert("", tk.END, iid=name,
                           values=(check, display_name, info, origin_glyph,
                                   kind, version_cell, status, desc),
                           tags=tag_args)
            if blocked:
                has_blocked = True
        self.mod_order = order
        self._has_blocked = has_blocked
        self._outdated_count = outdated_count
        if mismatch_reason:
            self._log(mismatch_reason, tag="warn")
        self._update_primary_button()
        self._update_apply_button_state()
        self._update_outdated_label()

    def _rebuild_relation_maps(self, mods_dir: Path, candidates: list[str]) -> None:
        """Compute per-mod dep closure, incompat set, effective clientOnly.
        Tolerant: a broken mod graph doesn't poison the whole view — its
        error is recorded and the row still renders (uncheckable)."""
        self._dep_closure = {}
        self._incompat = {n: set() for n in candidates}
        self._effective_client_only = {}
        self._dep_graph_error = {}
        ws_major = self._ws_major

        # Cache mod.jsons in a local dict to avoid re-reads.
        mjs: dict = {}
        for n in candidates:
            try:
                mjs[n] = read_mod_json(mods_dir / n)
            except Exception:
                continue

        if not ws_major:
            # Without a workspace major, dep resolution is meaningless — bail
            # gracefully; every row will look dep-free.
            for n in candidates:
                self._dep_closure[n] = []
                self._effective_client_only[n] = bool(
                    mjs.get(n) and mjs[n].client_only
                )
            return

        for n in candidates:
            mj = mjs.get(n)
            if mj is None:
                continue
            try:
                closure = resolve_deps(mods_dir, ws_major, n)
            except (ModDependencyMissing, ModDependencyCycle) as e:
                self._dep_graph_error[n] = str(e)
                closure = []
            self._dep_closure[n] = closure
            eff = bool(mj.client_only)
            for d in closure:
                dm = mjs.get(d)
                if dm and dm.client_only:
                    eff = True
                    break
            self._effective_client_only[n] = eff

        # Incompatibilities: symmetric — if A lists B or B lists A, mark both.
        for n in candidates:
            mj = mjs.get(n)
            if mj is None:
                continue
            for other_bare in mj.incompatible_with:
                other = f"{other_bare}-{ws_major}"
                if other in self._incompat:
                    self._incompat[n].add(other)
                    self._incompat[other].add(n)

    def _decorate_desc(self, name: str, mj) -> str:
        extras: list[str] = []
        if mj.dependencies:
            extras.append(f"requires: {', '.join(mj.dependencies)}")
        if mj.incompatible_with:
            extras.append(f"conflicts: {', '.join(mj.incompatible_with)}")
        err = self._dep_graph_error.get(name)
        if err:
            extras.append(f"⚠ {err}")
        base = mj.description or ""
        if not extras:
            return base
        tail = " [" + "; ".join(extras) + "]"
        return (base + tail) if base else tail.lstrip()

    def _checked_incompat_for(self, name: str) -> list[str]:
        """Which currently-checked mods would conflict with `name`?"""
        inc = self._incompat.get(name, set())
        return [m for m in inc if m in self.checked]

    def _flash_tooltip(self, anchor_widget: tk.Widget, text: str,
                       duration_ms: int = 2200) -> None:
        """One-shot transient tooltip below `anchor_widget`. Used to explain
        refused toggles in the mod list."""
        try:
            x = anchor_widget.winfo_rootx() + 12
            y = anchor_widget.winfo_rooty() + anchor_widget.winfo_height() + 4
        except tk.TclError:
            return
        tip = tk.Toplevel(self.tk)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        tip.configure(bg=PALETTE["warn"])
        tk.Label(
            tip, text=text,
            bg=PALETTE["warn"], fg=PALETTE["char_900"],
            font=("Segoe UI", 9, "bold"), padx=10, pady=6,
            justify="left",
        ).pack()
        self.tk.after(duration_ms, lambda: tip.destroy() if tip.winfo_exists() else None)

    def _row_status(self, name: str, *, blocked: bool) -> str:
        if blocked:
            return "N/A here"
        in_stack = name in self.installed_stack
        checked = name in self.checked
        if in_stack and checked:
            return "installed"
        if checked and not in_stack:
            return "pending add"
        if in_stack and not checked:
            return "pending remove"
        return "available"

    def _update_row(self, name: str) -> None:
        if not self.tv.exists(name):
            return
        tags = self.tv.item(name, "tags") or ()
        blocked = "blocked" in tags
        outdated = "outdated" in tags
        vals = list(self.tv.item(name, "values"))
        vals[0] = "☑" if (name in self.checked and not blocked) else "☐"
        # Column order: check, name, info, origin, kind, version, status, desc
        status = self._row_status(name, blocked=blocked)
        if outdated and "⚠" not in status:
            status = "⚠ " + status if status else "⚠"
        vals[6] = status
        self.tv.item(name, values=vals)

    def _compute_desired(self) -> list[str]:
        """Preserve prior stack order for retained mods; append new ones in mod-list order."""
        desired = [m for m in self.installed_stack if m in self.checked]
        existing = set(desired)
        for m in self.mod_order:
            if m in self.checked and m not in existing:
                desired.append(m)
                existing.add(m)
        return desired

    def _has_pending_changes(self) -> bool:
        return self._compute_desired() != list(self.installed_stack)

    def _update_apply_button_state(self) -> None:
        if not hasattr(self, "btn_apply"):
            return
        if self._busy:
            return  # _set_buttons owns state while busy.
        # PZ major mismatch (workspace vs destination install) hard-disables Apply.
        if self._pz_mismatch_reason:
            self.btn_apply.configure(state=tk.DISABLED)
            self.btn_revert.configure(
                state=tk.NORMAL if self._has_pending_changes() else tk.DISABLED)
            return
        state = tk.NORMAL if self._has_pending_changes() else tk.DISABLED
        self.btn_apply.configure(state=state)
        self.btn_revert.configure(state=state)

    def _update_primary_button(self) -> None:
        pristine = self.root / "data" / "workspace" / "src-pristine"
        if pristine.exists():
            self.btn_init.configure(text="Update from Game")
        else:
            self.btn_init.configure(text="Set Up")

    def _on_row_click(self, event) -> None:
        row = self.tv.identify_row(event.y)
        if not row:
            return
        # Clicking the info column opens the README.
        col = self.tv.identify_column(event.x)
        if col == "#3":
            self._open_readme(row)
            return
        # Don't allow checking blocked rows.
        tags = self.tv.item(row, "tags") or ()
        if "blocked" in tags:
            return
        if row in self.checked:
            self._try_uncheck(row)
        else:
            self._try_check(row)
        self._update_apply_button_state()

    def _try_check(self, row: str) -> None:
        """Check `row` and auto-pull deps. Refuse (with tooltip) if the
        resulting set would contain an incompatibility with something already
        checked, or if the dep graph is broken."""
        err = self._dep_graph_error.get(row)
        if err:
            self._flash_tooltip(self.tv, f"cannot check '{mod_base_name(row)}': {err}")
            return

        # Incompatibility check against the full resolved closure.
        closure = self._dep_closure.get(row, [])
        conflict_pairs: list[tuple[str, str]] = []
        new_set = set(self.checked) | {row} | set(closure)
        for m in new_set:
            for other in self._incompat.get(m, set()):
                if other in new_set and (other, m) not in conflict_pairs:
                    conflict_pairs.append((m, other))
        if conflict_pairs:
            a, b = conflict_pairs[0]
            self._flash_tooltip(
                self.tv,
                f"'{mod_base_name(a)}' conflicts with '{mod_base_name(b)}' — "
                f"uncheck one first",
            )
            return

        # Pull in deps. If any dep is blocked (effective clientOnly on a
        # server destination), refuse up-front.
        for d in closure:
            if d not in self.mod_order:
                continue  # not visible in current filtered list
            if self._effective_client_only.get(d) and self.install_to == "server":
                self._flash_tooltip(
                    self.tv,
                    f"'{mod_base_name(row)}' requires client-only mod "
                    f"'{mod_base_name(d)}' — switch Install to: client",
                )
                return

        self.checked.add(row)
        added_deps: list[str] = []
        for d in closure:
            if d not in self.checked and d in self.mod_order:
                self.checked.add(d)
                added_deps.append(d)
        self._update_row(row)
        for d in added_deps:
            self._update_row(d)
        if added_deps:
            pretty = ", ".join(mod_base_name(d) for d in added_deps)
            self._flash_tooltip(
                self.tv, f"also checked dependencies: {pretty}",
                duration_ms=1800,
            )

    def _try_uncheck(self, row: str) -> None:
        """Uncheck `row`; if any currently-checked mod depends on it,
        prompt the user to cascade the uncheck."""
        # Find dependents of `row` within the current checked set.
        dependents: list[str] = []
        if self._ws_major:
            try:
                dependents = reverse_dependents(
                    self.root / "data" / "mods", self._ws_major, row,
                    within=list(self.checked),
                )
            except Exception:
                dependents = []
        if dependents:
            pretty = ", ".join(mod_base_name(d) for d in dependents)
            ok = messagebox.askyesno(
                "Also uncheck dependents?",
                f"'{mod_base_name(row)}' is required by:\n\n  {pretty}\n\n"
                f"Also uncheck {len(dependents)} dependent mod(s)?",
            )
            if not ok:
                return  # abort — don't touch `row` or its dependents
            for d in dependents:
                self.checked.discard(d)
        self.checked.discard(row)
        self._update_row(row)
        for d in dependents:
            self._update_row(d)

    def _open_readme(self, mod_name: str) -> None:
        path = self.root / "data" / "mods" / mod_name / "README.md"
        if not path.exists():
            messagebox.showinfo(
                "No README",
                f"This mod ({mod_name}) doesn't ship a README.")
            return
        try:
            md = path.read_text(encoding="utf-8")
        except OSError as e:
            messagebox.showerror("Couldn't open README", str(e))
            return

        win = tk.Toplevel(self.tk)
        win.title(f"{mod_name} — README")
        win.geometry("760x620")
        win.minsize(520, 400)
        win.configure(bg=PALETTE["char_900"])
        try:
            win.iconphoto(False, self._icon_small, self._icon_large)
        except (AttributeError, tk.TclError):
            pass

        body = ttk.Frame(win, padding=(0, 0, 0, 0))
        body.pack(fill=tk.BOTH, expand=True)

        text = tk.Text(body, wrap="word", borderwidth=0, highlightthickness=0,
                       bg=PALETTE["char_900"], fg=PALETTE["bone"],
                       insertbackground=PALETTE["bone"],
                       padx=18, pady=14, font=("Segoe UI", 10))
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(body, orient=tk.VERTICAL, command=text.yview)
        text.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        try:
            markdown_render.render(md, text, PALETTE)
        except Exception as e:
            text.configure(state=tk.NORMAL)
            text.delete("1.0", tk.END)
            text.insert(tk.END, f"(markdown render failed: {e})\n\n{md}")
            text.configure(state=tk.DISABLED)

        footer = ttk.Frame(win, padding=(12, 6, 12, 10))
        footer.pack(fill=tk.X)
        ttk.Button(footer, text="Close", command=win.destroy).pack(side=tk.RIGHT)

        win.transient(self.tk)
        win.focus_set()

    # --- actions ---

    def _run_cli(self, args: list[str]) -> None:
        if self._busy:
            messagebox.showinfo("Busy", "Another command is already running.")
            return
        self._busy = True
        self._last_error = None
        self._warnings = []
        self._current_cmd = args[0] if args else None
        self._set_buttons(False)
        self._reset_log()
        self._log(f"$ necroid {' '.join(args)}", tag="info")
        self._set_status("busy", self._cmd_busy_headline(), progress="indeterminate")

        def worker():
            base_args = ["--root", str(self.root), *args]
            env = os.environ.copy()
            if getattr(sys, "frozen", False):
                cmd = [sys.executable, *base_args]
            else:
                cmd = [sys.executable, "-m", "necroid", *base_args]
                pkg_parent = str(Path(__file__).resolve().parent.parent)
                env["PYTHONPATH"] = pkg_parent + os.pathsep + env.get("PYTHONPATH", "")
            popen_kwargs: dict = {}
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            try:
                proc = subprocess.Popen(
                    cmd, cwd=str(self.root),
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1,
                    env=env,
                    **popen_kwargs,
                )
                assert proc.stdout is not None
                for line in proc.stdout:
                    self._log_queue.put(line.rstrip("\n"))
                code = proc.wait()
            except Exception as e:
                self._log_queue.put(f"ERROR: {e}")
                code = 99
            self._log_queue.put(f"[exit {code}]")
            self.tk.after(0, self._on_done, code)

        threading.Thread(target=worker, daemon=True).start()

    def _cmd_busy_headline(self) -> str:
        cmd = self._current_cmd or ""
        if cmd == "install":
            return "Installing…"
        if cmd == "uninstall":
            return "Uninstalling…"
        if cmd == "init":
            return "Setting up (this can take several minutes)…"
        if cmd == "resync-pristine":
            return "Updating from the game (this can take several minutes)…"
        return f"Running {cmd}…" if cmd else "Working…"

    def _on_done(self, code: int) -> None:
        self._busy = False
        # On failure, keep the user's pending edits so they can correct and
        # retry. Install is atomic, so state on disk is unchanged — but still
        # re-read it to surface any CLI-side changes.
        self.refresh_mods(reseed_checked=(code == 0))
        self._set_buttons(True)

        if code == 0:
            if self._warnings:
                self._set_status("warn",
                                 f"Done with {len(self._warnings)} warning(s).",
                                 progress=None)
            else:
                self._set_status("success", "Done.", progress=None)
        else:
            self._set_status("error", "Failed — see details below.", progress=None)
            if not self._log_expanded:
                self._toggle_log()
            self._show_failure_dialog(code)
        self._current_cmd = None

    def _show_failure_dialog(self, code: int) -> None:
        title = CMD_FAILURE_TITLE.get(self._current_cmd or "", "Command failed")
        body = self._last_error or f"The command exited with code {code}."
        dlg = tk.Toplevel(self.tk)
        dlg.title(title)
        dlg.transient(self.tk)
        dlg.configure(bg=PALETTE["char_900"])
        dlg.grab_set()
        ttk.Label(dlg, text=title, style="Brand.TLabel").pack(
            anchor="w", padx=16, pady=(14, 4))
        msg = tk.Message(dlg, text=body, width=440,
                         bg=PALETTE["char_900"], fg=PALETTE["bone"],
                         font=("Segoe UI", 10))
        msg.pack(anchor="w", padx=16, pady=(0, 12))
        hint = ttk.Label(
            dlg,
            text="Full details are in the log panel. You can copy them for a bug report.",
            style="Tagline.TLabel", wraplength=440,
        )
        hint.pack(anchor="w", padx=16, pady=(0, 12))
        btns = ttk.Frame(dlg)
        btns.pack(fill=tk.X, padx=12, pady=(0, 12))
        ttk.Button(btns, text="Copy log", command=self._copy_log).pack(side=tk.LEFT)
        ttk.Button(btns, text="OK", style="Primary.TButton",
                   command=dlg.destroy).pack(side=tk.RIGHT)
        dlg.update_idletasks()
        px = self.tk.winfo_rootx() + (self.tk.winfo_width() - dlg.winfo_width()) // 2
        py = self.tk.winfo_rooty() + (self.tk.winfo_height() - dlg.winfo_height()) // 3
        dlg.geometry(f"+{max(px, 0)}+{max(py, 0)}")

    def _set_buttons(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        widgets = [self.btn_init, self.btn_apply, self.btn_revert]
        for opt in ("btn_import", "btn_check_updates", "btn_outdated"):
            w = getattr(self, opt, None)
            if w is not None:
                widgets.append(w)
        for b in widgets:
            b.configure(state=state)
        self.install_to_combo.configure(state="readonly" if enabled else tk.DISABLED)
        if enabled:
            # Re-evaluate apply/revert: only enabled when a diff exists.
            self._update_apply_button_state()

    def on_init(self) -> None:
        # First-time setup or resync? Workspace pristine is the tell.
        workspace_pristine = self.root / "data" / "workspace" / "src-pristine"
        if workspace_pristine.exists():
            if not messagebox.askyesno(
                "Update from game",
                "Re-sync the frozen source from your Project Zomboid install?\n\n"
                "Use this after the game updates. It may flag mods as stale "
                "if their patches no longer apply cleanly.",
            ):
                return
            self._run_cli(["resync-pristine", "--to", self.install_to])
        else:
            self._run_cli(["init", "--from", self.install_to])

    def on_revert(self) -> None:
        if not self._has_pending_changes():
            return
        self.checked = set(self.installed_stack)
        for name in self.mod_order:
            self._update_row(name)
        self._update_apply_button_state()

    def on_apply_changes(self) -> None:
        desired = self._compute_desired()
        added = [m for m in desired if m not in self.installed_stack]
        removed = [m for m in self.installed_stack if m not in set(desired)]
        if not added and not removed:
            return

        # Preflight — defence-in-depth. Blocked rows already drop out on flip,
        # but a stale state file could theoretically have a clientOnly mod in
        # the stack after a mod.json flip.
        if self.install_to == "server":
            mods_dir = self.root / "data" / "mods"
            offenders: list[str] = []
            for name in desired:
                if self._effective_client_only.get(name):
                    offenders.append(name)
            if offenders:
                messagebox.showerror(
                    "Client-only mods can't go to server",
                    "These mods are client-only (directly or via a dependency) "
                    "and can't be installed to server:\n\n  "
                    + "\n  ".join(offenders)
                    + "\n\nUncheck them or switch Install to: client.",
                )
                return

        # Incompatibility preflight: the row-click path already blocks this,
        # but verify once more against the full desired set before we commit.
        for m in desired:
            conflicts = [o for o in self._incompat.get(m, set()) if o in desired]
            if conflicts:
                messagebox.showerror(
                    "Incompatible mods",
                    f"'{mod_base_name(m)}' is declared incompatible with:\n\n  "
                    + "\n  ".join(mod_base_name(c) for c in conflicts)
                    + "\n\nUncheck one side.",
                )
                return

        if not self._confirm_apply(added, removed, desired):
            return

        if not desired:
            self._run_cli(["uninstall", "--to", self.install_to])
        else:
            # `--replace` gives exact-replace semantics — unchecked mods actually
            # leave the stack. Plain `install` is additive and would silently
            # drop removals.
            self._run_cli(["install", *desired, "--to", self.install_to, "--replace"])

    def _confirm_apply(self, added: list[str], removed: list[str],
                       desired: list[str]) -> bool:
        if not desired:
            prompt = (
                f"This will uninstall every mod from {self.install_to} and\n"
                f"restore the original game files.\n\n"
                f"Removing: {', '.join(removed)}\n\nContinue?"
            )
            return messagebox.askyesno("Uninstall all", prompt)
        lines = [f"Apply changes to {self.install_to}?\n"]
        if added:
            lines.append("Install:")
            lines.extend(f"  + {m}" for m in added)
        if removed:
            if added:
                lines.append("")
            lines.append("Uninstall:")
            lines.extend(f"  - {m}" for m in removed)
        lines.append("")
        lines.append(f"Final stack: {', '.join(desired)}")
        return messagebox.askyesno("Apply changes", "\n".join(lines))

    # --- import / mod-update integration ---

    def on_import_clicked(self) -> None:
        if self._busy:
            return
        _ImportDialog(self)

    def on_check_updates(self) -> None:
        """Run `mod-update --check` in the background to refresh the cache,
        then redraw rows. Uses the same _run_cli pipeline so log output streams
        into the existing log pane and failure surfaces via the failure dialog."""
        if self._busy:
            return
        self._run_cli(["mod-update", "--check"])

    def on_update_all(self) -> None:
        if self._busy:
            return
        n = int(getattr(self, "_outdated_count", 0) or 0)
        if n <= 0:
            messagebox.showinfo("No updates", "No imported mods are outdated.")
            return
        if not messagebox.askyesno(
            "Update mods",
            f"Update {n} outdated mod(s) from their source repos?\n\n"
            "Mods that are currently entered will be skipped.",
        ):
            return
        self._run_cli(["mod-update"])

    def _on_row_context(self, event) -> None:
        row = self.tv.identify_row(event.y)
        if not row:
            return
        if self._busy:
            return
        mj = (getattr(self, "_mj_by_name", {}) or {}).get(row)
        if mj is None:
            try:
                mj = read_mod_json(self.root / "data" / "mods" / row)
            except Exception:
                return

        origin = read_origin(mj)
        is_imported = bool(origin)
        # Currently-entered guard.
        try:
            from .state import read_enter
            from .profile import load_profile as _load
            cfg = read_config(self.root, required=False)
            prof = _load(self.root, cfg=cfg) if cfg else _load(self.root)
            entered = read_enter(prof.enter_file)
        except Exception:
            entered = None
        is_entered = bool(entered and entered.mod == row)

        # Collect peers sharing (repo, ref).
        peers: list[str] = []
        if is_imported:
            for other_name, other_mj in (self._mj_by_name or {}).items():
                if other_name == row:
                    continue
                o = read_origin(other_mj)
                if not o:
                    continue
                if (o.get("repo") == origin.get("repo")
                        and o.get("ref") == origin.get("ref")):
                    peers.append(other_name)

        m = tk.Menu(self.tk, tearoff=0,
                    bg=PALETTE["char_700"], fg=PALETTE["bone"],
                    activebackground=PALETTE["char_500"],
                    activeforeground=PALETTE["bone"])

        m.add_command(
            label="Check for update",
            command=lambda r=row: self._run_cli(["mod-update", r, "--check"]),
            state=tk.NORMAL if is_imported else tk.DISABLED,
        )
        m.add_command(
            label="Update now",
            command=lambda r=row: self._run_cli(["mod-update", r]),
            state=tk.NORMAL if (is_imported and not is_entered) else tk.DISABLED,
        )
        peers_label = (f"Update with peers from same repo  ({len(peers)})"
                       if peers else "Update with peers from same repo")
        m.add_command(
            label=peers_label,
            command=lambda r=row: self._run_cli(["mod-update", r, "--include-peers"]),
            state=tk.NORMAL if (is_imported and peers and not is_entered) else tk.DISABLED,
        )
        m.add_separator()
        m.add_command(
            label="Reimport (force)",
            command=lambda r=row, o=origin: self._reimport_mod(r, o),
            state=tk.NORMAL if (is_imported and not is_entered) else tk.DISABLED,
        )
        m.add_command(
            label="Show origin",
            command=lambda r=row, o=origin: self._show_origin_dialog(r, o),
            state=tk.NORMAL if is_imported else tk.DISABLED,
        )
        m.add_command(
            label="Open on GitHub",
            command=lambda o=origin: self._open_origin_in_browser(o),
            state=tk.NORMAL if is_imported else tk.DISABLED,
        )
        if (self.root / "data" / "mods" / row / "README.md").exists():
            m.add_separator()
            m.add_command(label="Show README",
                          command=lambda r=row: self._open_readme(r))

        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _reimport_mod(self, name: str, origin: dict) -> None:
        if not messagebox.askyesno(
            "Reimport mod",
            f"Force-reimport '{name}' from {origin.get('repo')}@{origin.get('ref')}?\n\n"
            "Local mod.json + patches will be overwritten.",
        ):
            return
        base = mod_base_name(name)
        args = ["import", str(origin.get("repo")), "--ref", str(origin.get("ref")),
                "--mod", str(origin.get("subdir") or base),
                "--name", base, "--force"]
        self._run_cli(args)

    def _show_origin_dialog(self, name: str, origin: dict) -> None:
        import json as _json
        body = _json.dumps(origin, indent=2)
        dlg = tk.Toplevel(self.tk)
        dlg.title(f"Origin — {name}")
        dlg.transient(self.tk)
        dlg.configure(bg=PALETTE["char_900"])
        ttk.Label(dlg, text=f"Origin of {name}", style="Brand.TLabel").pack(
            anchor="w", padx=16, pady=(14, 4))
        txt = tk.Text(dlg, width=64, height=12,
                      bg=PALETTE["char_700"], fg=PALETTE["bone"],
                      font=("Consolas", 10), borderwidth=0)
        txt.pack(padx=16, pady=(0, 12))
        txt.insert("1.0", body)
        txt.configure(state=tk.DISABLED)
        btns = ttk.Frame(dlg)
        btns.pack(fill=tk.X, padx=12, pady=(0, 12))

        def _copy():
            self.tk.clipboard_clear()
            self.tk.clipboard_append(body)

        ttk.Button(btns, text="Copy", command=_copy).pack(side=tk.LEFT)
        ttk.Button(btns, text="Close", style="Primary.TButton",
                   command=dlg.destroy).pack(side=tk.RIGHT)

    def _open_origin_in_browser(self, origin: dict) -> None:
        import webbrowser
        repo = str(origin.get("repo") or "")
        ref = str(origin.get("ref") or "")
        subdir = str(origin.get("subdir") or "")
        if not repo:
            return
        url = f"https://github.com/{repo}"
        if ref:
            url += f"/tree/{ref}"
            if subdir:
                url += f"/{subdir}"
        try:
            webbrowser.open(url)
        except Exception:
            pass

    # --- status strip ---

    def _set_status(self, kind: str, text: str,
                    progress: Optional[str]) -> None:
        color = {
            "idle": PALETTE["bone_dim"],
            "busy": PALETTE["accent"],
            "success": PALETTE["accent"],
            "warn": PALETTE["warn"],
            "error": PALETTE["error"],
        }.get(kind, PALETTE["bone_dim"])
        self.status_dot.configure(foreground=color)
        self.status_headline.configure(text=text, foreground=PALETTE["bone"])

        if progress is None:
            try:
                self.progress.stop()
            except Exception:
                pass
            self.progress.pack_forget()
        else:
            if not self.progress.winfo_ismapped():
                self.progress.pack(side=tk.RIGHT)
            if progress == "indeterminate":
                self.progress.configure(mode="indeterminate")
                self.progress.start(80)
            else:
                self.progress.stop()
                self.progress.configure(mode="determinate")

    def _parse_status_line(self, line: str) -> None:
        if line.startswith("ERROR:"):
            msg = line[len("ERROR:"):].strip()
            self._last_error = msg
            return
        stripped = line.lstrip()
        if stripped.startswith("WARN:"):
            self._warnings.append(stripped[len("WARN:"):].strip())
            return
        m = _STEP_RE.match(line)
        if not m:
            return
        step_n, step_total, tail = m.group(1), m.group(2), m.group(3).strip()
        friendly = tail
        for key, text in STEP_FRIENDLY.items():
            if tail.startswith(key):
                friendly = text
                break
        if step_n and step_total:
            try:
                pct = 100.0 * int(step_n) / int(step_total)
            except ValueError:
                pct = 0.0
            if not self.progress.winfo_ismapped():
                self.progress.pack(side=tk.RIGHT)
            self.progress.stop()
            self.progress.configure(mode="determinate", maximum=100, value=pct)
            self.status_headline.configure(
                text=f"Step {step_n} of {step_total} — {friendly}")
        else:
            self.status_headline.configure(text=friendly)

    # --- log ---

    def _toggle_log(self) -> None:
        if self._log_expanded:
            self.log_body.pack_forget()
            self.btn_disclose.configure(text="▸ Show details")
            self._log_expanded = False
        else:
            self.log_body.pack(fill=tk.BOTH, expand=True)
            self.btn_disclose.configure(text="▾ Hide details")
            self._log_expanded = True

    def _copy_log(self) -> None:
        text = self.log_text.get("1.0", tk.END).rstrip()
        self.tk.clipboard_clear()
        self.tk.clipboard_append(text)

    def _reset_log(self) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _log(self, msg: str, tag: Optional[str] = None) -> None:
        display, resolved_tag = self._classify_log_line(msg) if tag is None else (msg, tag)
        self.log_text.configure(state=tk.NORMAL)
        if resolved_tag:
            self.log_text.insert(tk.END, display + "\n", resolved_tag)
        else:
            self.log_text.insert(tk.END, display + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _classify_log_line(self, raw: str) -> tuple[str, Optional[str]]:
        if raw.startswith("==> "):
            return (raw[len("==> "):], "step")
        if raw.startswith("ERROR:"):
            return (raw, "error")
        stripped = raw.lstrip()
        if stripped.startswith("WARN:"):
            return (stripped, "warn")
        if raw.startswith("[exit "):
            return (raw, "info" if raw == "[exit 0]" else "error")
        if raw.startswith("$ "):
            return (raw, "info")
        low = raw.lower()
        if "complete" in low or low.startswith("done."):
            return (raw, "success")
        return (raw, None)

    def _drain_log(self) -> None:
        try:
            while True:
                line = self._log_queue.get_nowait()
                self._parse_status_line(line)
                self._log(line)
        except queue.Empty:
            pass
        self.tk.after(80, self._drain_log)

    def run(self) -> int:
        self.tk.mainloop()
        return 0


def launch(root: Path, initial_install_to: InstallTo) -> int:
    return ModderApp(root=root, initial_install_to=initial_install_to).run()


# ---------------------------------------------------------------------------
# Import dialog — two-stage (Discover → Select)
# ---------------------------------------------------------------------------

class _ImportDialog:
    """Modal that walks the user through:
        1. enter repo URL / ref → run `import --list --json` to discover
        2. select which mods to pull (multi-select treeview)
        3. submit → dispatches `import` via the parent's _run_cli pipeline.

    Long-running ops (discovery + import) run in worker threads. UI updates
    happen on the Tk thread via `after()`.
    """

    def __init__(self, app: "ModderApp") -> None:
        self.app = app
        self.discovered: list[dict] = []
        self.workspace_major: int = int(getattr(app, "_ws_major", 0) or 0)
        # Stage 2 state.
        self._row_check_vars: dict[str, tk.BooleanVar] = {}
        self._row_major_ok: dict[str, bool] = {}

        self.dlg = tk.Toplevel(app.tk)
        self.dlg.title("Import mods from GitHub")
        self.dlg.transient(app.tk)
        self.dlg.configure(bg=PALETTE["char_900"])
        self.dlg.geometry("640x520")

        # --- Stage 1 (always present) ---
        self.stage1 = ttk.Frame(self.dlg, padding=(16, 14, 16, 8))
        self.stage1.pack(fill=tk.X)

        ttk.Label(self.stage1, text="Repository", style="Brand.TLabel").pack(anchor="w")
        ttk.Label(self.stage1,
                  text="owner/repo, or any github.com URL "
                       "(optionally including /tree/<branch>).",
                  style="Tagline.TLabel", wraplength=600).pack(anchor="w", pady=(0, 6))

        self.repo_var = tk.StringVar()
        self.repo_var.trace_add("write", lambda *a: self._validate_repo())
        self.repo_entry = ttk.Entry(self.stage1, textvariable=self.repo_var, width=60)
        self.repo_entry.pack(anchor="w", fill=tk.X, pady=(0, 4))

        self.repo_hint_var = tk.StringVar(value="")
        self.repo_hint = ttk.Label(self.stage1, textvariable=self.repo_hint_var,
                                   style="Tagline.TLabel", foreground=PALETTE["error"])
        self.repo_hint.pack(anchor="w")

        ref_row = ttk.Frame(self.stage1)
        ref_row.pack(anchor="w", fill=tk.X, pady=(8, 0))
        ttk.Label(ref_row, text="Branch / tag (optional):",
                  style="Tagline.TLabel").pack(side=tk.LEFT)
        self.ref_var = tk.StringVar()
        ttk.Entry(ref_row, textvariable=self.ref_var, width=30).pack(
            side=tk.LEFT, padx=(8, 0))

        # --- Stage 2 container (hidden until Discover succeeds) ---
        self.stage2_wrap = ttk.Frame(self.dlg, padding=(16, 8, 16, 8))
        # Not packed yet.

        # --- Footer (buttons + spinner) ---
        self.footer = ttk.Frame(self.dlg, padding=(16, 8, 16, 14))
        self.footer.pack(side=tk.BOTTOM, fill=tk.X)

        self.spinner = ttk.Progressbar(self.footer, mode="indeterminate", length=120)
        # not packed initially

        self.btn_cancel = ttk.Button(self.footer, text="Cancel",
                                     command=self.dlg.destroy)
        self.btn_cancel.pack(side=tk.LEFT)

        self.btn_back = ttk.Button(self.footer, text="Back",
                                   command=self._back_to_stage1)
        # not packed initially

        self.btn_primary = ttk.Button(self.footer, text="Discover",
                                      style="Primary.TButton",
                                      command=self._on_discover)
        self.btn_primary.pack(side=tk.RIGHT)
        self.btn_primary.configure(state=tk.DISABLED)

        # Inline error banner (shown if discovery / import fails).
        self.error_var = tk.StringVar(value="")
        self.error_label = ttk.Label(self.dlg, textvariable=self.error_var,
                                     style="Tagline.TLabel",
                                     foreground=PALETTE["error"],
                                     wraplength=600, padding=(16, 0, 16, 0))
        # Not packed unless an error occurs.

        self._discover_queue: queue.Queue = queue.Queue()
        self._import_queue: queue.Queue = queue.Queue()

        self.repo_entry.focus_set()

    # ---- Stage 1: validate + discover ----

    def _validate_repo(self) -> None:
        raw = self.repo_var.get().strip()
        if not raw:
            self.repo_hint_var.set("")
            self.btn_primary.configure(state=tk.DISABLED)
            return
        try:
            from .github import parse_github_ref
            parse_github_ref(raw)
            self.repo_hint_var.set("")
            self.btn_primary.configure(state=tk.NORMAL)
        except Exception as e:
            self.repo_hint_var.set(str(e))
            self.btn_primary.configure(state=tk.DISABLED)

    def _on_discover(self) -> None:
        self._clear_error()
        self.btn_primary.configure(state=tk.DISABLED, text="Discovering…")
        self.spinner.pack(side=tk.RIGHT, padx=(0, 8))
        self.spinner.start(80)

        repo = self.repo_var.get().strip()
        ref = self.ref_var.get().strip()
        args = ["import", repo, "--list", "--json"]
        if ref:
            args.extend(["--ref", ref])

        def worker():
            base_args = ["--root", str(self.app.root), *args]
            env = os.environ.copy()
            if getattr(sys, "frozen", False):
                cmd = [sys.executable, *base_args]
            else:
                cmd = [sys.executable, "-m", "necroid", *base_args]
                pkg_parent = str(Path(__file__).resolve().parent.parent)
                env["PYTHONPATH"] = pkg_parent + os.pathsep + env.get("PYTHONPATH", "")
            popen_kwargs: dict = {}
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            try:
                proc = subprocess.run(
                    cmd, cwd=str(self.app.root),
                    capture_output=True, text=True, env=env, **popen_kwargs,
                )
            except Exception as e:
                self._discover_queue.put({"ok": False, "error": str(e)})
                return
            if proc.returncode != 0:
                self._discover_queue.put({
                    "ok": False,
                    "error": (proc.stderr or proc.stdout or "command failed").strip(),
                })
                return
            try:
                payload = __import__("json").loads(proc.stdout)
            except Exception as e:
                self._discover_queue.put({
                    "ok": False,
                    "error": f"could not parse discovery output: {e}",
                })
                return
            self._discover_queue.put({"ok": True, "payload": payload})

        threading.Thread(target=worker, daemon=True).start()
        self.dlg.after(120, self._poll_discover)

    def _poll_discover(self) -> None:
        try:
            r = self._discover_queue.get_nowait()
        except queue.Empty:
            self.dlg.after(120, self._poll_discover)
            return
        self.spinner.stop()
        self.spinner.pack_forget()
        self.btn_primary.configure(text="Discover", state=tk.NORMAL)
        if not r.get("ok"):
            self._show_error(r.get("error") or "discovery failed")
            return
        payload = r["payload"]
        self.discovered = list(payload.get("mods") or [])
        self.workspace_major = int(payload.get("workspaceMajor") or self.workspace_major)
        if not self.discovered:
            self._show_error("repo contains no importable mods")
            return
        self._build_stage2()

    # ---- Stage 2: select + import ----

    def _build_stage2(self) -> None:
        self.stage1.pack_forget()
        for child in self.stage2_wrap.winfo_children():
            child.destroy()
        self.stage2_wrap.pack(fill=tk.BOTH, expand=True, after=None)

        repo = self.repo_var.get().strip()
        ref = self.ref_var.get().strip() or "(default branch)"
        ttk.Label(self.stage2_wrap,
                  text=f"{repo} @ {ref} — {len(self.discovered)} mod(s)",
                  style="Brand.TLabel").pack(anchor="w")
        ttk.Label(self.stage2_wrap,
                  text=f"Workspace major: {self.workspace_major}. "
                       "Mods that don't match the workspace major are disabled.",
                  style="Tagline.TLabel", wraplength=600).pack(anchor="w", pady=(0, 8))

        # Treeview with checkboxes (simulated via the first column).
        cols = ("check", "subdir", "name", "version", "kind", "expected")
        tv = ttk.Treeview(self.stage2_wrap, columns=cols,
                          show="headings", selectmode="none", height=10)
        tv.heading("check", text="")
        tv.heading("subdir", text="Subdir")
        tv.heading("name", text="Mod (dir)")
        tv.heading("version", text="Version")
        tv.heading("kind", text="Type")
        tv.heading("expected", text="PZ Major")
        tv.column("check", width=30, anchor=tk.CENTER, stretch=False)
        tv.column("subdir", width=160, anchor=tk.W)
        tv.column("name", width=160, anchor=tk.W)
        tv.column("version", width=70, anchor=tk.W, stretch=False)
        tv.column("kind", width=80, anchor=tk.W, stretch=False)
        tv.column("expected", width=80, anchor=tk.W, stretch=False)
        tv.pack(fill=tk.BOTH, expand=True)
        tv.tag_configure("incompat", foreground=PALETTE["error"])

        self._row_check_vars.clear()
        self._row_major_ok.clear()

        for dm in self.discovered:
            mod_major = dm.get("modMajor")
            major_ok = bool(dm.get("majorOk", True))
            checked = major_ok  # default: select compatible rows
            self._row_check_vars[dm["subdir"]] = tk.BooleanVar(value=checked)
            self._row_major_ok[dm["subdir"]] = major_ok
            check_glyph = "☑" if checked else ("☒" if not major_ok else "☐")
            kind = "client-only" if dm.get("clientOnly") else "any"
            major_cell = (str(mod_major) if mod_major is not None
                          else "(no suffix)")
            tags = ("incompat",) if not major_ok else ()
            dirname_cell = dm.get("dirname") or dm.get("name") or ""
            tv.insert("", tk.END, iid=dm["subdir"],
                      values=(check_glyph, dm["subdir"] or "<root>",
                              dirname_cell, dm["version"], kind, major_cell),
                      tags=tags)

        def on_click(event):
            row = tv.identify_row(event.y)
            if not row:
                return
            if not self._row_major_ok.get(row, True):
                return  # disabled
            v = self._row_check_vars[row]
            v.set(not v.get())
            vals = list(tv.item(row, "values"))
            vals[0] = "☑" if v.get() else "☐"
            tv.item(row, values=vals)
            self._update_primary_label()
            self._update_name_field()

        tv.bind("<Button-1>", on_click)
        self._stage2_tv = tv

        # --name override row.
        name_row = ttk.Frame(self.stage2_wrap, padding=(0, 8, 0, 0))
        name_row.pack(fill=tk.X)
        ttk.Label(name_row, text="Override mod base name:",
                  style="Tagline.TLabel").pack(side=tk.LEFT)
        self.name_var = tk.StringVar()
        self.name_entry = ttk.Entry(name_row, textvariable=self.name_var, width=24)
        self.name_entry.pack(side=tk.LEFT, padx=(8, 8))
        self.name_hint_var = tk.StringVar(value="(only when one mod selected)")
        ttk.Label(name_row, textvariable=self.name_hint_var,
                  style="Tagline.TLabel").pack(side=tk.LEFT)
        self._update_name_field()

        # Force checkbox.
        self.force_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.stage2_wrap, text="Overwrite existing mods (--force)",
                        variable=self.force_var).pack(anchor="w", pady=(8, 0))

        # Footer rebuild — now includes Back, Import N.
        self.btn_cancel.pack_forget()
        self.btn_primary.pack_forget()
        self.btn_back.pack(side=tk.LEFT)
        self.btn_cancel.pack(side=tk.LEFT, padx=(8, 0))
        self.btn_primary.configure(text="Import 0", command=self._on_import)
        self.btn_primary.pack(side=tk.RIGHT)
        self._update_primary_label()

    def _update_name_field(self) -> None:
        n = sum(1 for v in self._row_check_vars.values() if v.get())
        if n == 1:
            self.name_entry.configure(state=tk.NORMAL)
            self.name_hint_var.set("(blank = use upstream name)")
        else:
            self.name_var.set("")
            self.name_entry.configure(state=tk.DISABLED)
            self.name_hint_var.set("(only when one mod selected)")

    def _update_primary_label(self) -> None:
        n = sum(1 for v in self._row_check_vars.values() if v.get())
        self.btn_primary.configure(
            text=f"Import {n}",
            state=tk.NORMAL if n > 0 else tk.DISABLED,
        )

    def _back_to_stage1(self) -> None:
        self.stage2_wrap.pack_forget()
        self.stage1.pack(fill=tk.X, before=self.footer)
        self.btn_back.pack_forget()
        self.btn_cancel.pack_forget()
        self.btn_primary.pack_forget()
        self.btn_cancel.pack(side=tk.LEFT)
        self.btn_primary.configure(text="Discover", command=self._on_discover)
        self.btn_primary.pack(side=tk.RIGHT)
        self._validate_repo()

    def _on_import(self) -> None:
        self._clear_error()
        selected_subdirs = [s for s, v in self._row_check_vars.items() if v.get()]
        if not selected_subdirs:
            return
        repo = self.repo_var.get().strip()
        ref = self.ref_var.get().strip()
        all_selected = (len(selected_subdirs) == len(self.discovered))

        args = ["import", repo]
        if ref:
            args.extend(["--ref", ref])
        if all_selected and len(selected_subdirs) > 1:
            args.append("--all")
        else:
            for s in selected_subdirs:
                args.extend(["--mod", s])
        if len(selected_subdirs) == 1 and self.name_var.get().strip():
            args.extend(["--name", self.name_var.get().strip()])
        if self.force_var.get():
            args.append("--force")

        self.dlg.destroy()
        self.app._run_cli(args)

    # ---- Error display ----

    def _show_error(self, msg: str) -> None:
        self.error_var.set(msg)
        try:
            self.error_label.pack(side=tk.TOP, fill=tk.X, before=self.footer)
        except Exception:
            self.error_label.pack(side=tk.TOP, fill=tk.X)

    def _clear_error(self) -> None:
        self.error_var.set("")
        try:
            self.error_label.pack_forget()
        except Exception:
            pass
