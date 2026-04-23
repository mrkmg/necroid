"""Subprocess helpers with Windows no-window defaults.

On Windows, the frozen necroid binary is a Windows-subsystem (--windowed)
app. When it spawns a console app (java, javac, jar, vineflower, git, diff3,
etc.) without creationflags, Windows allocates a fresh console for the child
and flashes a cmd window briefly — jarring for GUI users. CREATE_NO_WINDOW
suppresses that allocation while leaving inherited stdio handles intact, so
output still flows through the pipe / parent console as expected.

`run()` and `Popen()` here mirror the subprocess APIs but add the flag on
Windows. Non-Windows platforms get the stdlib call unchanged.
"""
from __future__ import annotations

import subprocess
import sys
from typing import Any


def _extra_kwargs(kwargs: dict) -> dict:
    if sys.platform != "win32":
        return kwargs
    flags = kwargs.get("creationflags", 0)
    kwargs["creationflags"] = flags | subprocess.CREATE_NO_WINDOW
    return kwargs


def run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
    return subprocess.run(*args, **_extra_kwargs(kwargs))


def Popen(*args: Any, **kwargs: Any) -> subprocess.Popen:
    return subprocess.Popen(*args, **_extra_kwargs(kwargs))
