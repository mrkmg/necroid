"""Vineflower download + decompile driver."""
from __future__ import annotations

import shutil
import urllib.request
from pathlib import Path

from ..util import logging_util as log
from ..util import procs
from ..util.fsops import empty_dir, ensure_dir
from ..util.tools import require_java_release

VINEFLOWER_VERSION = "1.11.1"
# Vineflower 1.11.1 requires Java 11+ to run. Bare PATH `java` may be JDK 8
# (common via java.com auto-installer), which bombs with no useful output —
# so version-gate. The well-known-roots scan / auto-fetch path will land on
# a usable JDK without forcing the user to fix PATH.
VINEFLOWER_MIN_JAVA = 11
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


def decompile_subtree(
    classes_orig: Path,
    out_pristine_dir: Path,
    subtree: str,
    libs_jars: list[Path],
    vineflower_jar: Path,
    force: bool = False,
) -> int:
    """Decompile `classes_orig/<subtree>` into `out_pristine_dir/<subtree>`.

    Vineflower writes files declaring `package <subtree>;` into the output
    dir's root (not a nested `<subtree>/` folder) because its input directory
    *is* the package root. We therefore decompile into a tmp dir and rename
    it into place under `out_pristine_dir/<subtree>`.
    """
    sub_classes = classes_orig / subtree
    if not sub_classes.exists():
        raise FileNotFoundError(f"{sub_classes} not found; run the class-copy step first")

    out_sub = out_pristine_dir / subtree
    if out_sub.exists() and not force:
        log.info(f"[skip] {out_sub} already exists (use --force to regenerate)")
        return sum(1 for _ in out_sub.rglob("*.java"))
    if out_sub.exists():
        log.info(f"wiping existing {out_sub}")
        shutil.rmtree(out_sub)

    out_pristine_dir.mkdir(parents=True, exist_ok=True)
    tmp_out = out_pristine_dir / (subtree + "-tmp")
    empty_dir(tmp_out)

    # `require_java_release` returns the pinned bundled JDK 25 — Vineflower's
    # output is JVM-version-sensitive (HashMap iteration order in some passes,
    # type-resolution differences), so a single shared JVM is the only way to
    # keep mod patches portable across users. See `tools_fetch.BUNDLED_JDK_RELEASE`.
    java = str(require_java_release(VINEFLOWER_MIN_JAVA))
    # -Xmx2g: zombie/ on B41/B42 is large; Vineflower's default heap can OOM.
    args = [java, "-Xmx2g", "-jar", str(vineflower_jar)]
    for j in libs_jars:
        args.append(f"-e={j}")
    args.append(str(sub_classes))
    args.append(str(tmp_out))

    log.info(f"decompiling {subtree}/ (Vineflower)...")
    proc = procs.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Vineflower failed on {subtree}/ (exit {proc.returncode})\n"
            f"  java: {java}\n"
            f"--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}"
        )

    tmp_out.rename(out_sub)
    count = sum(1 for _ in out_sub.rglob("*.java"))
    log.info(f"decompiled {count} .java files into {out_sub}")
    return count


def decompile_all(
    classes_orig: Path,
    out_pristine_dir: Path,
    subtrees: list[str],
    libs_jars: list[Path],
    vineflower_jar: Path,
    force: bool = False,
) -> int:
    """Decompile every listed subtree that actually exists under `classes_orig`.

    Each subtree is decompiled in its own Vineflower invocation (same pattern
    as the original zombie-only driver) so failures localize and missing
    subtrees are skipped cleanly."""
    total = 0
    for sub in subtrees:
        if not (classes_orig / sub).exists():
            log.info(f"[skip] {sub}/ not present under classes-original/")
            continue
        total += decompile_subtree(
            classes_orig=classes_orig,
            out_pristine_dir=out_pristine_dir,
            subtree=sub,
            libs_jars=libs_jars,
            vineflower_jar=vineflower_jar,
            force=force,
        )
    log.info(f"decompile total: {total} .java files across {len(subtrees)} subtree(s)")
    return total
