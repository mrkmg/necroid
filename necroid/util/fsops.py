"""Filesystem helpers: tree mirroring, empty-dir, inner-class globbing.

`mirror_tree` replaces robocopy /MIR — walks both trees, copies only files
whose mtime or size differ, prunes orphans. Much cheaper than a blind
`shutil.copytree` on the ~3000-file zombie tree.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterable


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def empty_dir(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def mirror_tree(src: Path, dst: Path, *, verify: bool = False) -> tuple[int, int, int]:
    """Mirror src onto dst. Returns (copied, skipped, deleted).

    Default skip heuristic: same size AND mtime differs by <= 2s (FAT mtime
    resolution).

    When `verify=True`, skip decisions fall back to SHA-256 equality whenever
    the cheap heuristic says "looks equal" — used by `resync-pristine` so a
    Steam-reverted file that coincidentally landed with a close mtime is NOT
    silently skipped. ~3000 files × one hash each is cheap compared to the
    correctness win; resync is not a hot path.

    Any file in dst not present in src is deleted. Empty orphan dirs are pruned.
    """
    if not src.exists():
        raise FileNotFoundError(f"source missing: {src}")
    dst.mkdir(parents=True, exist_ok=True)

    copied = skipped = deleted = 0
    src_rels: set[str] = set()

    # Pass 1: copy src -> dst
    for root, _dirs, files in os.walk(src):
        root_p = Path(root)
        rel_root = root_p.relative_to(src)
        for fname in files:
            rel = (rel_root / fname).as_posix()
            src_rels.add(rel)
            s = root_p / fname
            d = dst / rel_root / fname
            d.parent.mkdir(parents=True, exist_ok=True)
            if d.exists():
                try:
                    s_stat = s.stat()
                    d_stat = d.stat()
                    size_mtime_match = (
                        s_stat.st_size == d_stat.st_size
                        and abs(s_stat.st_mtime - d_stat.st_mtime) <= 2
                    )
                    if size_mtime_match:
                        if not verify:
                            skipped += 1
                            continue
                        # verify mode: confirm with a hash before skipping.
                        from .hashing import file_sha256
                        if file_sha256(s) == file_sha256(d):
                            skipped += 1
                            continue
                except OSError:
                    pass
            shutil.copy2(s, d)
            copied += 1

    # Pass 2: delete dst files not in src
    for root, _dirs, files in os.walk(dst):
        root_p = Path(root)
        rel_root = root_p.relative_to(dst)
        for fname in files:
            rel = (rel_root / fname).as_posix()
            if rel not in src_rels:
                try:
                    (root_p / fname).unlink()
                    deleted += 1
                except OSError:
                    pass

    # Pass 3: prune empty dirs in dst (bottom-up)
    for root, dirs, files in os.walk(dst, topdown=False):
        if root == str(dst):
            continue
        if not dirs and not files:
            try:
                Path(root).rmdir()
            except OSError:
                pass

    return copied, skipped, deleted


def inner_class_files(class_dir: Path, leaf_base: str) -> list[Path]:
    """All .class files matching `Leaf.class` or `Leaf$*.class` (nested/anon classes).

    Javac emits nested classes as `Outer$Inner.class`, anonymous as `Outer$1.class`.
    Exact match on the outer stem + any `$`-prefixed siblings.
    """
    if not class_dir.exists():
        return []
    out: list[Path] = []
    exact = f"{leaf_base}.class"
    prefix = f"{leaf_base}$"
    for child in class_dir.iterdir():
        if not child.is_file() or child.suffix != ".class":
            continue
        if child.name == exact or child.name.startswith(prefix):
            out.append(child)
    return sorted(out)


def copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def iter_rel_files(root: Path, suffix: str | None = None) -> Iterable[tuple[str, Path]]:
    """Yield (posix-relative-path, absolute-path) for files under root."""
    if not root.exists():
        return
    for dirpath, _dirs, files in os.walk(root):
        dp = Path(dirpath)
        rel_root = dp.relative_to(root)
        for fname in files:
            if suffix is not None and not fname.endswith(suffix):
                continue
            rel = (rel_root / fname).as_posix()
            yield rel, dp / fname
