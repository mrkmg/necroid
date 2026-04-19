import sys


def _maybe_detach_console() -> None:
    """Frozen Windows build: if we uniquely own the console (Windows
    allocated a fresh one because we were launched from Explorer, a
    Start-menu shortcut, or a GUI-style parent), detach it immediately so
    it doesn't linger as a black box behind the GUI.

    Shared-console cases (cmd.exe / PowerShell parent, count >= 2) keep
    their console so CLI output flows back to the user's terminal.

    Subprocess-with-no-console cases (count == 0, e.g. GUI-spawned child
    with CREATE_NO_WINDOW) are left alone -- stdout is a pipe from the
    parent and must not be clobbered.

    Runs before any heavy imports so the console window closes as quickly
    as possible, minimizing flash-of-black-box.
    """
    if not getattr(sys, "frozen", False):
        return
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        buf = (ctypes.c_ulong * 2)()
        count = kernel32.GetConsoleProcessList(buf, 2)
        if count != 1:
            return
        # We own the console alone -> detach + redirect std streams so
        # subsequent print()/log writes don't raise OSError on the now-
        # invalid console handles.
        if not kernel32.FreeConsole():
            return
        import os
        devnull = open(os.devnull, "w", buffering=1, encoding="utf-8")
        sys.stdout = devnull
        sys.stderr = devnull
    except Exception:
        # Best-effort -- any failure here should never prevent the app
        # from launching. Worst case: console window stays visible.
        pass


_maybe_detach_console()

from necroid.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
