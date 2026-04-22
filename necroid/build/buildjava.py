"""javac wrapper. Compiles only the files passed in; no -sourcepath — decompiled
siblings don't round-trip, so unexplored source must come from the original
classpath jars."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ..util import logging_util as log
from ..errors import BuildError
from ..util.tools import resolve


def _classpath_jars(libs: Path, classpath_originals: Path) -> list[Path]:
    jars: list[Path] = []
    if libs.exists():
        jars.extend(sorted(libs.glob("*.jar")))
    if classpath_originals.exists():
        jars.extend(sorted(classpath_originals.glob("*.jar")))
    return jars


def javac_compile(
    files: list[Path],
    libs: Path,
    classpath_originals: Path,
    out_dir: Path,
    clean: bool = False,
    java_release: int = 17,
) -> None:
    if not files:
        raise BuildError("no source files given. Pass specific files (decompiled siblings don't round-trip).")

    if clean and out_dir.exists():
        import shutil
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    jars = _classpath_jars(libs, classpath_originals)
    if not jars:
        raise BuildError(f"no jars in {libs} or {classpath_originals}")

    cp = os.pathsep.join(str(j) for j in jars)
    abs_files: list[str] = []
    for f in files:
        p = f if f.is_absolute() else Path.cwd() / f
        if not p.exists():
            raise BuildError(f"source not found: {f}")
        abs_files.append(str(p.resolve()))

    javac = str(resolve("javac"))
    log.info(f"compiling {len(abs_files)} file(s) -> {out_dir} (Java {java_release})")
    cmd = [javac, "--release", str(java_release), "-encoding", "UTF-8",
           "-cp", cp, "-d", str(out_dir), *abs_files]
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        raise BuildError(f"javac failed (exit {proc.returncode})")
