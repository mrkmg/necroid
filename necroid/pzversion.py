"""PZ version detection.

Strategy: compile a small Java probe (`necroid/java/NecroidGetPzVersion.java`)
on first use, then invoke it with `-cp <cache>:<pz_install>` — the probe uses
reflection to read `zombie.core.Core.gameVersion` (a `GameVersion` object whose
`toString()` returns `"MAJOR.MINOR[SUFFIX]"`) and `zombie.core.Core.buildVersion`
(an int), printing `"<major>.<minor>[<suffix>].<build>"` on stdout (e.g.
`"41.78.19"`).

This avoids bytecode parsing entirely: PZ's own code tells us its version. The
probe's compiled .class is cached under `data/tools/pz-version-probe/`, keyed
by the source .java's sha256 so stub updates force a recompile.

Install-path note: the client lays its class tree at the install root, while
the dedicated server nests it under `<install>/java/`. Callers should pass the
_content_ directory — `Profile.content_dir_for(install_to)` — not the raw
install root.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import logging_util as log
from .errors import PzVersionDetectError
from .hashing import file_sha256
from .tools import resolve


_PROBE_JAVA_REL = Path("java") / "NecroidGetPzVersion.java"
_PROBE_CLASS_NAME = "NecroidGetPzVersion"
_OUT_RE = re.compile(r"^(\d+)\.(\d+)([^.\d][^.]*)?\.(\d+)$")


@dataclass(frozen=True)
class PzVersion:
    major: int
    minor: int
    suffix: str   # optional trailing non-numeric tag on GameVersion.toString() — usually ""
    patch: int    # Core.buildVersion (e.g. 19 for "41.78.19")

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}{self.suffix}.{self.patch}"

    @staticmethod
    def parse(s: str) -> "PzVersion":
        m = _OUT_RE.match(s.strip())
        if not m:
            raise PzVersionDetectError(f"unparseable PZ version string: {s!r}")
        return PzVersion(
            major=int(m.group(1)),
            minor=int(m.group(2)),
            suffix=m.group(3) or "",
            patch=int(m.group(4)),
        )


def _probe_source(necroid_pkg_dir: Path) -> Path:
    return necroid_pkg_dir / _PROBE_JAVA_REL


def _probe_cache_dir(data_dir: Path) -> Path:
    return data_dir / "tools" / "pz-version-probe"


def _probe_class_file(data_dir: Path) -> Path:
    return _probe_cache_dir(data_dir) / f"{_PROBE_CLASS_NAME}.class"


def _stamp_file(data_dir: Path) -> Path:
    return _probe_cache_dir(data_dir) / ".source-sha256"


def ensure_probe_compiled(necroid_pkg_dir: Path, data_dir: Path) -> Path:
    """Compile the probe into `data/tools/pz-version-probe/` if the cached
    .class is missing or its source hash has changed. Returns the cache dir."""
    src = _probe_source(necroid_pkg_dir)
    if not src.is_file():
        raise PzVersionDetectError(
            f"probe source missing: {src}. This is a bug in the necroid package."
        )

    cache_dir = _probe_cache_dir(data_dir)
    class_file = _probe_class_file(data_dir)
    stamp = _stamp_file(data_dir)
    src_hash = file_sha256(src) or ""

    if class_file.is_file() and stamp.is_file():
        try:
            if stamp.read_text(encoding="utf-8").strip() == src_hash:
                return cache_dir
        except OSError:
            pass  # fall through to recompile

    cache_dir.mkdir(parents=True, exist_ok=True)
    javac = str(resolve("javac"))
    log.info(f"compiling PZ-version probe -> {cache_dir}")
    cmd = [javac, "--release", "17", "-encoding", "UTF-8", "-d", str(cache_dir), str(src)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise PzVersionDetectError(
            f"probe compile failed (exit {proc.returncode}):\n{proc.stderr.strip()}"
        )
    try:
        stamp.write_text(src_hash, encoding="utf-8")
    except OSError:
        pass  # not fatal; worst case we recompile next call
    return cache_dir


def detect_pz_version(content_dir: Path, necroid_pkg_dir: Path, data_dir: Path) -> PzVersion:
    """Run the probe against `content_dir` (the directory containing the
    `zombie/core/Core.class` tree) and return the parsed PzVersion.

    Pass `Profile.content_dir_for(install_to)` here, not the raw install root —
    the dedicated server puts its class tree under `<install>/java/`.
    """
    if not content_dir.exists():
        raise PzVersionDetectError(f"PZ content dir does not exist: {content_dir}")
    core_class = content_dir / "zombie" / "core" / "Core.class"
    if not core_class.is_file():
        raise PzVersionDetectError(
            f"zombie/core/Core.class not found under {content_dir} — "
            f"is this a Project Zomboid install?"
        )

    cache_dir = ensure_probe_compiled(necroid_pkg_dir, data_dir)
    java = str(resolve("java"))
    cp = os.pathsep.join([str(cache_dir), str(content_dir)])
    cmd = [java, "-cp", cp, _PROBE_CLASS_NAME]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise PzVersionDetectError(
            f"probe invocation failed (exit {proc.returncode}) against {content_dir}:\n"
            f"stderr: {proc.stderr.strip()}\n"
            f"stdout: {proc.stdout.strip()}"
        )
    out = proc.stdout.strip()
    if not out:
        raise PzVersionDetectError(
            f"probe produced no output against {content_dir}. stderr: {proc.stderr.strip()}"
        )
    return PzVersion.parse(out)
