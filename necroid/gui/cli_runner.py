"""Subprocess + queue pump for shelling out to the `necroid` CLI.

The GUI runs every CLI operation (install, init, resync-pristine, etc.) as a
child process so a crash there cannot take down the GUI. `CliRunner` owns the
Popen + stdout-reader thread + Tk `after()` pump and delivers each line back to
the caller on the Tk main thread.

Usage:
    runner = CliRunner(tk_root, root_path, on_line=..., on_done=...)
    runner.start(["install", "my-mod", "--to", "client"])

Callbacks fire on the Tk main thread — safe to touch widgets from inside them.
The runner refuses a second `start()` while busy; poll `.busy` first.

Pure helpers (`classify_log_line`, `cmd_busy_headline`) live here too because
they tag lines the runner produces and name the operation the runner is
currently executing.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
from pathlib import Path
import threading
from typing import Callable, Optional

from ..paths import package_dir


LineCallback = Callable[[str], None]
DoneCallback = Callable[[int], None]


class CliRunner:
    def __init__(self, tk_root, root_path: Path,
                 on_line: LineCallback, on_done: DoneCallback,
                 pump_interval_ms: int = 80):
        self.tk = tk_root
        self.root = root_path
        self._on_line = on_line
        self._on_done = on_done
        self._pump_ms = pump_interval_ms
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._busy = False
        self._current_cmd: Optional[str] = None
        self._pump_scheduled = False

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def current_cmd(self) -> Optional[str]:
        return self._current_cmd

    def start(self, args: list[str]) -> bool:
        """Spawn `necroid <args>` if idle. Returns False if already running."""
        if self._busy:
            return False
        self._busy = True
        self._current_cmd = args[0] if args else None
        threading.Thread(target=self._worker, args=(list(args),), daemon=True).start()
        self._schedule_pump()
        return True

    def _worker(self, args: list[str]) -> None:
        base_args = ["--root", str(self.root), *args]
        env = os.environ.copy()
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, *base_args]
        else:
            cmd = [sys.executable, "-m", "necroid", *base_args]
            # Let the child find the in-tree `necroid` package when running
            # from source (pip-editable or raw checkout) — sys.path needs
            # the directory that *contains* the package, not the package itself.
            pkg_parent = str(package_dir().parent)
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
                self._queue.put(line.rstrip("\n"))
            code = proc.wait()
        except Exception as e:
            self._queue.put(f"ERROR: {e}")
            code = 99
        self._queue.put(f"[exit {code}]")
        self.tk.after(0, self._finish, code)

    def _schedule_pump(self) -> None:
        if self._pump_scheduled:
            return
        self._pump_scheduled = True
        self.tk.after(self._pump_ms, self._pump)

    def _pump(self) -> None:
        self._pump_scheduled = False
        try:
            while True:
                line = self._queue.get_nowait()
                try:
                    self._on_line(line)
                except Exception:
                    # A broken callback must not wedge the pump.
                    pass
        except queue.Empty:
            pass
        if self._busy:
            self._schedule_pump()

    def _finish(self, code: int) -> None:
        # Drain any lines that arrived between the last pump tick and exit so
        # they're visible before on_done runs.
        self._pump()
        self._busy = False
        try:
            self._on_done(code)
        finally:
            self._current_cmd = None


# --- pure helpers ---------------------------------------------------------


def classify_log_line(raw: str) -> tuple[str, Optional[str]]:
    """Classify a CLI output line for log-pane styling.

    Returns (display_text, tag) where tag is one of "step" | "error" | "warn"
    | "info" | "success" | None. The display_text strips the `==> ` prefix on
    step lines so the log reads more naturally.
    """
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


def cmd_busy_headline(cmd: Optional[str]) -> str:
    """Friendly headline text for the status strip while `cmd` is running."""
    if cmd == "install":
        return "Installing…"
    if cmd == "uninstall":
        return "Uninstalling…"
    if cmd == "init":
        return "Setting up (this can take several minutes)…"
    if cmd == "resync-pristine":
        return "Updating from the game (this can take several minutes)…"
    return f"Running {cmd}…" if cmd else "Working…"
