"""Two-stage modal for importing mods from GitHub.

Stage 1 — user enters `owner/repo` (optionally a `/tree/<ref>`) plus an
optional branch/tag. `Discover` runs `necroid import --list --json` in a
worker thread and parses the output.

Stage 2 — treeview of discovered mods with one checkable row each. Rows whose
PZ major doesn't match the workspace major are disabled. Submitting dispatches
the actual `necroid import` via the parent app's `_run_cli` pipeline so the
main window shows progress + log.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from typing import TYPE_CHECKING

import tkinter as tk
from tkinter import ttk

from ..paths import package_dir
from .constants import PALETTE

if TYPE_CHECKING:
    from .app import ModderApp


class ImportDialog:
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
            from ..remote.github import parse_github_ref
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
                # sys.path needs the directory that *contains* the package.
                pkg_parent = str(package_dir().parent)
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
                payload = json.loads(proc.stdout)
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
