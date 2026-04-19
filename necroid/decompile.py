"""Vineflower download + decompile driver."""
from __future__ import annotations

import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from . import logging_util as log
from .fsops import empty_dir, ensure_dir
from .tools import resolve

VINEFLOWER_VERSION = "1.11.1"
VINEFLOWER_URL = (
    f"https://github.com/Vineflower/vineflower/releases/download/"
    f"{VINEFLOWER_VERSION}/vineflower-{VINEFLOWER_VERSION}.jar"
)


def ensure_vineflower(tools_dir: Path, force: bool = False) -> Path:
    """Download vineflower.jar into tools_dir if missing (or force=True)."""
    ensure_dir(tools_dir)
    target = tools_dir / "vineflower.jar"
    if target.exists() and not force:
        log.info(f"[skip] {target} already exists")
        return target
    log.info(f"downloading {VINEFLOWER_URL}")
    tmp = target.with_suffix(".jar.tmp")
    try:
        with urllib.request.urlopen(VINEFLOWER_URL) as resp, tmp.open("wb") as fp:
            shutil.copyfileobj(resp, fp)
        tmp.replace(target)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise
    size_kb = target.stat().st_size // 1024
    log.info(f"wrote {target} ({size_kb} KB)")
    return target


def decompile_zombie(
    classes_orig: Path,
    out_pristine_dir: Path,
    libs_jars: list[Path],
    vineflower_jar: Path,
    force: bool = False,
) -> int:
    """Decompile `classes_orig/zombie` into `out_pristine_dir/zombie`.

    Vineflower writes files declaring `package zombie;` into the output dir's
    root (not a nested `zombie/` folder). Move the output into place as a
    final step.
    """
    zombie_classes = classes_orig / "zombie"
    if not zombie_classes.exists():
        raise FileNotFoundError(f"{zombie_classes} not found; run the class-copy step first")

    if out_pristine_dir.exists() and not force:
        log.info(f"[skip] {out_pristine_dir} already exists (use --force to regenerate)")
        java_files = list((out_pristine_dir / "zombie").rglob("*.java"))
        return len(java_files)
    if out_pristine_dir.exists():
        log.info(f"wiping existing {out_pristine_dir}")
        shutil.rmtree(out_pristine_dir)

    tmp_out = out_pristine_dir.parent / (out_pristine_dir.name + "-tmp")
    empty_dir(tmp_out)

    java = str(resolve("java"))
    args = [java, "-jar", str(vineflower_jar), "--silent"]
    for j in libs_jars:
        args.append(f"-e={j}")
    args.append(str(zombie_classes))
    args.append(str(tmp_out))

    log.info("decompiling zombie/ (Vineflower, ~1 min)...")
    proc = subprocess.run(args)
    if proc.returncode != 0:
        raise RuntimeError(f"Vineflower failed (exit {proc.returncode})")

    out_pristine_dir.mkdir(parents=True, exist_ok=True)
    tmp_out.rename(out_pristine_dir / "zombie")
    count = sum(1 for _ in (out_pristine_dir / "zombie").rglob("*.java"))
    log.info(f"decompiled {count} .java files into {out_pristine_dir / 'zombie'}")
    return count
