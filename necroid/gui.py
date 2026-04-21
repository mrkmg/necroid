"""Minimal tkinter GUI for end users.

Layout:
    ┌──────────────────────────────────────────────────────────┐
    │  [skull]  Necroid   Install to: [client ▾]   [ Set Up ]   │
    │           Beyond Workshop — Project Zomboid mod manager  │
    ├──────────────────────────────────────────────────────────┤
    │  Treeview of mods: ☑/☐ | name | cli-only | status | desc │
    ├──────────────────────────────────────────────────────────┤
    │  [Refresh]                       [ Install ] [Uninstall] │
    ├──────────────────────────────────────────────────────────┤
    │  ● Ready                                     [ progress ] │
    ├──────────────────────────────────────────────────────────┤
    │  ▸ Show details                                  [ Copy ] │
    │  (log, collapsed by default; auto-opens on error)         │
    └──────────────────────────────────────────────────────────┘

Single shared workspace + a destination toggle in the header. Mods are never
hidden — clientOnly mods simply can't be installed while install-to is server
(the Install button disables itself with a tooltip).
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

from .assets import HEADER_MARK, WINDOW_ICON_FULL, WINDOW_ICON_SKULL, asset_path
from .config import read_config
from . import markdown_render
from .mod import list_mods, read_mod_json
from .profile import load_profile
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
    "decompile zombie": "Decompiling game code (this takes a while)…",
    "scaffold mods": "Finishing setup…",
    "checking mod patches": "Re-checking mod patches…",
}

CMD_FAILURE_TITLE = {
    "install": "Install failed",
    "uninstall": "Uninstall failed",
    "init": "Setup failed",
    "resync-pristine": "Update failed",
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

        self._build_header()
        self._build_mod_list()
        self._build_footer()
        self._build_status_strip()
        self._build_log()

        self._log_queue: "queue.Queue[str]" = queue.Queue()
        self.tk.after(100, self._drain_log)
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
                    foreground=PALETTE["bone"], arrowcolor=PALETTE["bone"])

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

        ttk.Frame(self.tk, style="Sep.TFrame", height=1).pack(fill=tk.X, padx=12)

    def _on_install_to_changed(self, _e=None) -> None:
        self.install_to = self.install_to_var.get()  # type: ignore[assignment]
        self.refresh_mods()

    def _build_mod_list(self) -> None:
        frame = ttk.Frame(self.tk, padding=(12, 8, 12, 0))
        frame.pack(fill=tk.BOTH, expand=True)

        columns = ("check", "name", "info", "kind", "status", "desc")
        tv = ttk.Treeview(frame, columns=columns, show="headings", selectmode="none")
        tv.heading("check", text="")
        tv.heading("name", text="Mod")
        tv.heading("info", text="")
        tv.heading("kind", text="Type")
        tv.heading("status", text="Status")
        tv.heading("desc", text="Description")
        tv.column("check", width=30, anchor=tk.CENTER, stretch=False)
        tv.column("name", width=170, anchor=tk.W)
        tv.column("info", width=36, anchor=tk.CENTER, stretch=False)
        tv.column("kind", width=78, anchor=tk.W, stretch=False)
        tv.column("status", width=96, anchor=tk.W)
        tv.column("desc", width=420, anchor=tk.W)
        tv.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tv.yview)
        tv.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        tv.tag_configure("blocked", foreground=PALETTE["bone_dim"])

        tv.bind("<Button-1>", self._on_row_click)
        self.tv = tv

    def _build_footer(self) -> None:
        ft = ttk.Frame(self.tk, padding=(12, 8, 12, 0))
        ft.pack(fill=tk.X)
        btn_refresh = ttk.Button(ft, text="Refresh", command=self.refresh_mods)
        btn_refresh.pack(side=tk.LEFT)
        _Tooltip(btn_refresh, "Reload the mod list from disk.")

        self.btn_uninstall = ttk.Button(ft, text="Uninstall", command=self.on_uninstall)
        self.btn_uninstall.pack(side=tk.RIGHT)
        _Tooltip(self.btn_uninstall,
                 "With mods checked: remove just those from the chosen destination.\n"
                 "With nothing checked: remove everything and restore originals for that destination.")

        self.btn_install = ttk.Button(ft, text="Install", command=self.on_install)
        self.btn_install.pack(side=tk.RIGHT, padx=(0, 6))
        _Tooltip(self.btn_install,
                 "Install every checked mod into the chosen Project Zomboid install.")

    def _build_status_strip(self) -> None:
        wrap = ttk.Frame(self.tk, padding=(12, 8, 12, 4))
        wrap.pack(fill=tk.X)

        self.status_dot = ttk.Label(wrap, text="●", style="StatusDot.TLabel")
        self.status_dot.pack(side=tk.LEFT, padx=(0, 8))

        self.status_headline = ttk.Label(wrap, text="Ready",
                                         style="StatusHeadline.TLabel",
                                         anchor="w")
        self.status_headline.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.progress = ttk.Progressbar(wrap, mode="indeterminate", length=180)
        self.progress.pack(side=tk.RIGHT)
        self.progress.pack_forget()

        ttk.Frame(self.tk, style="Sep.TFrame", height=1).pack(fill=tk.X, padx=12)

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

    def refresh_mods(self) -> None:
        self.tv.delete(*self.tv.get_children())
        try:
            cfg = read_config(self.root, required=False)
            profile = load_profile(self.root, cfg=cfg) if cfg else None
        except Exception:
            profile = None
        mods_dir = self.root / "data" / "mods"
        if not mods_dir.exists():
            self._log(f"(no mods directory at {mods_dir}; run Set Up)", tag="info")
            self._update_primary_button()
            return
        installed_stack: list[str] = []
        if profile:
            state = read_state(profile.state_file(self.install_to))
            installed_stack = state.stack
        has_blocked = False
        for name in list_mods(mods_dir):
            try:
                mj = read_mod_json(mods_dir / name)
            except Exception:
                continue
            blocked = mj.client_only and self.install_to == "server"
            status = "installed" if name in installed_stack else ("N/A here" if blocked else "available")
            check = "☑" if (name in self.checked and not blocked) else "☐"
            info = "ⓘ" if (mods_dir / name / "README.md").exists() else ""
            kind = "client-only" if mj.client_only else "any"
            tag_args = ("blocked",) if blocked else ()
            self.tv.insert("", tk.END, iid=name,
                           values=(check, name, info, kind, status, mj.description),
                           tags=tag_args)
            if blocked:
                has_blocked = True
                self.checked.discard(name)
        self._has_blocked = has_blocked
        self._update_primary_button()
        self._update_install_button_state()

    def _update_install_button_state(self) -> None:
        # Simple rule: the button itself is always enabled; the preflight in
        # on_install refuses checked clientOnly rows while install-to is server.
        pass

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
            self.checked.discard(row)
        else:
            self.checked.add(row)
        vals = list(self.tv.item(row, "values"))
        vals[0] = "☑" if row in self.checked else "☐"
        self.tv.item(row, values=vals)

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
        self._set_buttons(True)
        self.refresh_mods()

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
        for b in (self.btn_init, self.btn_install, self.btn_uninstall):
            b.configure(state=state)
        self.install_to_combo.configure(state="readonly" if enabled else tk.DISABLED)

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

    def on_install(self) -> None:
        names = sorted(self.checked)
        if not names:
            messagebox.showinfo("No selection", "Check at least one mod to install.")
            return
        self._run_cli(["install", *names, "--to", self.install_to])
        self.checked.clear()

    def on_uninstall(self) -> None:
        names = sorted(self.checked)
        if not names:
            if not messagebox.askyesno(
                "Uninstall all",
                f"Uninstall every mod from {self.install_to} and restore the original game files?",
            ):
                return
            self._run_cli(["uninstall", "--to", self.install_to])
            return
        self._run_cli(["uninstall", *names, "--to", self.install_to])
        self.checked.clear()

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
