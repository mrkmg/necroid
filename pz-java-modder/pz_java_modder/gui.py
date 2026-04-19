"""Minimal tkinter GUI for end users.

Layout:
    ┌──────────────────────────────────────────────────────────┐
    │  PZ Java Modder  [client]         [ Init / Resync ]      │
    ├──────────────────────────────────────────────────────────┤
    │  Treeview of mods: ☑/☐ | name | status | description     │
    ├──────────────────────────────────────────────────────────┤
    │                              [ Install ]  [ Uninstall ]  │
    ├──────────────────────────────────────────────────────────┤
    │  log output (stderr)                                     │
    └──────────────────────────────────────────────────────────┘

Install/uninstall run in a subprocess (`python -m pz_java_modder`) so the GUI
stays responsive and any crash in the command doesn't kill the GUI.

The mod list is filtered to the active target: a server-launched GUI only
shows server-target mods; default client GUI only shows client-target mods.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Literal

import tkinter as tk
from tkinter import messagebox, ttk

from .config import read_config
from .mod import list_mods, read_mod_json
from .profile import load_profile
from .state import read_state


Target = Literal["client", "server"]


class ModderApp:
    def __init__(self, root: Path, target: Target):
        self.root = root
        self.target = target
        self.checked: set[str] = set()

        self.tk = tk.Tk()
        self.tk.title(f"PZ Java Modder [{target}]")
        self.tk.geometry("860x520")
        self.tk.minsize(640, 400)

        self._build_header()
        self._build_mod_list()
        self._build_footer()
        self._build_log()

        self._log_queue: "queue.Queue[str]" = queue.Queue()
        self._busy = False
        self.tk.after(100, self._drain_log)
        self.refresh_mods()

    # --- layout ---

    def _build_header(self) -> None:
        hdr = ttk.Frame(self.tk, padding=8)
        hdr.pack(fill=tk.X)
        ttk.Label(hdr, text=f"PZ Java Modder  [{self.target}]",
                  font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT)
        self.btn_init = ttk.Button(hdr, text="Init / Resync", command=self.on_init)
        self.btn_init.pack(side=tk.RIGHT)

    def _build_mod_list(self) -> None:
        frame = ttk.Frame(self.tk, padding=(8, 0))
        frame.pack(fill=tk.BOTH, expand=True)

        columns = ("check", "name", "status", "desc")
        tv = ttk.Treeview(frame, columns=columns, show="headings", selectmode="none")
        tv.heading("check", text="")
        tv.heading("name", text="Mod")
        tv.heading("status", text="Status")
        tv.heading("desc", text="Description")
        tv.column("check", width=30, anchor=tk.CENTER, stretch=False)
        tv.column("name", width=180, anchor=tk.W)
        tv.column("status", width=90, anchor=tk.W)
        tv.column("desc", width=520, anchor=tk.W)
        tv.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tv.yview)
        tv.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        tv.bind("<Button-1>", self._on_row_click)
        self.tv = tv

    def _build_footer(self) -> None:
        ft = ttk.Frame(self.tk, padding=8)
        ft.pack(fill=tk.X)
        ttk.Button(ft, text="Refresh", command=self.refresh_mods).pack(side=tk.LEFT)
        self.btn_uninstall = ttk.Button(ft, text="Uninstall", command=self.on_uninstall)
        self.btn_uninstall.pack(side=tk.RIGHT)
        self.btn_install = ttk.Button(ft, text="Install", command=self.on_install)
        self.btn_install.pack(side=tk.RIGHT, padx=(0, 6))

    def _build_log(self) -> None:
        log_frame = ttk.Frame(self.tk, padding=(8, 0, 8, 8))
        log_frame.pack(fill=tk.BOTH)
        self.log_text = tk.Text(log_frame, height=8, wrap="word",
                                font=("Consolas", 9), state=tk.DISABLED)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

    # --- data ---

    def refresh_mods(self) -> None:
        self.tv.delete(*self.tv.get_children())
        try:
            cfg = read_config(self.root, required=False)
            profile = load_profile(self.root, self.target, cfg=cfg, require_pz=False) \
                if cfg and cfg.pz_install(self.target) else None
        except Exception:
            profile = None
        mods_dir = self.root / "data" / "mods"
        if not mods_dir.exists():
            self._log(f"(no mods directory at {mods_dir}; run Init / Resync)")
            return
        installed_stack: list[str] = []
        if profile and profile.state_file.exists():
            installed_stack = read_state(profile.state_file).stack
        for name in list_mods(mods_dir):
            try:
                mj = read_mod_json(mods_dir / name)
            except Exception:
                continue
            if mj.target != self.target:
                continue  # filter off-target
            status = "installed" if name in installed_stack else "available"
            check = "☑" if name in self.checked else "☐"
            self.tv.insert("", tk.END, iid=name, values=(check, name, status, mj.description))

    def _on_row_click(self, event) -> None:
        col = self.tv.identify_column(event.x)
        row = self.tv.identify_row(event.y)
        if not row:
            return
        # Toggle check on click in check column, or anywhere in the row:
        if col == "#1" or True:
            if row in self.checked:
                self.checked.discard(row)
            else:
                self.checked.add(row)
            vals = list(self.tv.item(row, "values"))
            vals[0] = "☑" if row in self.checked else "☐"
            self.tv.item(row, values=vals)

    # --- actions ---

    def _run_cli(self, args: list[str]) -> None:
        if self._busy:
            messagebox.showinfo("busy", "another command is already running.")
            return
        self._busy = True
        self._set_buttons(False)
        self._log(f"\n$ pz-java-modder {' '.join(args)}")

        def worker():
            base_args = ["--root", str(self.root), "--target", self.target, *args]
            env = os.environ.copy()
            if getattr(sys, "frozen", False):
                # PyInstaller onefile: sys.executable IS pz-java-modder; call it directly.
                cmd = [sys.executable, *base_args]
            else:
                cmd = [sys.executable, "-m", "pz_java_modder", *base_args]
                # Running from a dev checkout: subprocess needs pz-java-modder/ on PYTHONPATH.
                pkg_parent = str(Path(__file__).resolve().parent.parent)
                env["PYTHONPATH"] = pkg_parent + os.pathsep + env.get("PYTHONPATH", "")
            try:
                proc = subprocess.Popen(
                    cmd, cwd=str(self.root),
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1,
                    env=env,
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

    def _on_done(self, code: int) -> None:
        self._busy = False
        self._set_buttons(True)
        self.refresh_mods()
        if code != 0:
            messagebox.showerror("command failed", f"exit code {code} — see log")

    def _set_buttons(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        for b in (self.btn_init, self.btn_install, self.btn_uninstall):
            b.configure(state=state)

    def on_init(self) -> None:
        # init or resync? If profile is bootstrapped already, run resync-pristine.
        profile_dir = self.root / "data" / self.target
        if (profile_dir / "src-pristine").exists():
            self._run_cli(["resync-pristine"])
        else:
            self._run_cli(["init"])

    def on_install(self) -> None:
        names = sorted(self.checked)
        if not names:
            messagebox.showinfo("no selection", "check at least one mod to install.")
            return
        self._run_cli(["install", *names])
        self.checked.clear()

    def on_uninstall(self) -> None:
        names = sorted(self.checked)
        if not names:
            # No checks = full uninstall. Confirm first.
            if not messagebox.askyesno("uninstall all", "Uninstall everything and restore originals?"):
                return
            self._run_cli(["uninstall"])
            return
        self._run_cli(["uninstall", *names])
        self.checked.clear()

    # --- logging ---

    def _log(self, msg: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _drain_log(self) -> None:
        try:
            while True:
                line = self._log_queue.get_nowait()
                self._log(line)
        except queue.Empty:
            pass
        self.tk.after(80, self._drain_log)

    def run(self) -> int:
        self.tk.mainloop()
        return 0


def launch(root: Path, target: Target) -> int:
    return ModderApp(root=root, target=target).run()
