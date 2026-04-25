"""Extract `projectzomboid.jar` into the `classes-original/` layout used by
the rest of the toolchain.

PZ build 42 replaced the loose class tree (`<pz>/zombie/**/*.class`) with a
single fat jar at the install root. The launcher's classpath is still
`./;projectzomboid.jar`, so a loose `.class` under `<pz>/zombie/...` still
overrides the jar entry — the install mechanism survives intact. Only the
pristine-seeding step has to change: for `workspace_layout == "jar"`, we
extract the jar into `workspace/classes-original/<subtree>/...` so every
downstream step (Vineflower decompile, hash-based restore, doctor audits)
sees the same on-disk layout it sees for loose-layout 41 workspaces.

Stdlib `zipfile` only — the jar is a plain zip.
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Iterable

from ..util import logging_util as log
from ..util.fsops import empty_dir, ensure_dir


def extract_fat_jar(
    jar_path: Path,
    out_dir: Path,
    subtrees: Iterable[str],
    *,
    force: bool = False,
) -> int:
    """Extract `.class` entries from `jar_path` into `out_dir/<subtree>/...`.

    Only entries under one of the named subtrees are written — META-INF,
    resource files, and anything else in the jar stays behind. Returns the
    number of files written.

    Per-subtree skip: mirrors `_copy_pz_classes` — if the target subtree dir
    already exists and `force=False`, the whole subtree is left alone. Use
    `force=True` (or delete the dir) to re-seed.

    Layout matches what `_copy_pz_classes` produces for loose installs, so
    the decompiler and all hash-based restore paths don't care which branch
    populated the directory.
    """
    if not jar_path.is_file():
        raise FileNotFoundError(f"fat jar not found: {jar_path}")

    ensure_dir(out_dir)
    subs = tuple(subtrees)

    # Decide per-subtree: do we extract into it at all?
    targets: list[str] = []
    for sub in subs:
        dst_dir = out_dir / sub
        if dst_dir.exists() and not force:
            log.info(f"[skip] classes-original/{sub} (use --force to refresh)")
            continue
        if dst_dir.exists():
            empty_dir(dst_dir)
        else:
            ensure_dir(dst_dir)
        targets.append(sub)

    if not targets:
        return 0

    sub_prefixes = tuple(f"{s}/" for s in targets)
    written = 0
    with zipfile.ZipFile(jar_path, "r") as zf:
        for info in zf.infolist():
            name = info.filename
            if info.is_dir() or not name.endswith(".class"):
                continue
            if not name.startswith(sub_prefixes):
                continue
            dst = out_dir / name
            ensure_dir(dst.parent)
            with zf.open(info, "r") as src, open(dst, "wb") as out:
                out.write(src.read())
            written += 1

    log.info(f"extracted {written} class file(s) from {jar_path.name} -> {out_dir}")
    return written
