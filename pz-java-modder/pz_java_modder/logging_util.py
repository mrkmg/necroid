"""Tiny stderr logger. `info()` / `warn()` / `error()` / `step()` — all to stderr
so stdout stays clean for machine-readable command output (e.g. `list`)."""
from __future__ import annotations

import os
import sys

_COLOR = sys.stderr.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def step(msg: str) -> None:
    print(f"==> {msg}", file=sys.stderr)


def info(msg: str) -> None:
    print(f"  {msg}", file=sys.stderr)


def warn(msg: str) -> None:
    print(_c("33", f"  WARN: {msg}"), file=sys.stderr)


def error(msg: str) -> None:
    print(_c("31", f"ERROR: {msg}"), file=sys.stderr)


def success(msg: str) -> None:
    print(_c("32", msg), file=sys.stderr)
