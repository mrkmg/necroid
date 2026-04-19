"""capture — diff src/ vs src-pristine/, rewrite mods/<name>/patches/."""
from __future__ import annotations

import shutil
from pathlib import Path

from .. import logging_util as log
from ..errors import TargetMismatch
from ..fsops import ensure_dir
from ..hashing import file_sha256
from ..mod import ensure_mod_exists, patch_items, pristine_snapshot, read_mod_json, write_mod_json
from ..patching import git_diff_no_index
from ..state import utc_now_iso


def run(args) -> int:
    p = args.profile
    name = args.name
    md = ensure_mod_exists(p.mods_dir, name)
    mj = read_mod_json(md)
    if mj.target != p.target:
        raise TargetMismatch(
            f"mod '{name}' targets {mj.target}; active profile is {p.target}\n"
            f"    retry with --target {mj.target}"
        )

    patches_dir = md / "patches"
    log.info(f"capture {name}: diffing src/ vs src-pristine/")
    if patches_dir.exists():
        shutil.rmtree(patches_dir)
    ensure_dir(patches_dir)

    src_zombie = p.src / "zombie"
    pristine_zombie = p.pristine / "zombie"
    if not src_zombie.exists():
        raise SystemExit(f"src/zombie/ not found at {src_zombie}")

    touched_count = 0

    # Modified + new
    for java in sorted(src_zombie.rglob("*.java")):
        if not java.is_file():
            continue
        rel = "zombie/" + java.relative_to(src_zombie).as_posix()
        pristine_file = p.pristine / rel
        if not pristine_file.exists():
            dst = patches_dir / f"{rel}.new"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(java, dst)
            log.info(f"new:  {rel}")
            touched_count += 1
            continue
        if file_sha256(java) == file_sha256(pristine_file):
            continue
        patch_bytes = git_diff_no_index(pristine_file, java, rel)
        if patch_bytes is None:
            continue
        dst = patches_dir / f"{rel}.patch"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(patch_bytes)
        log.info(f"mod:  {rel}")
        touched_count += 1

    # Deleted
    for pr in sorted(pristine_zombie.rglob("*.java")):
        if not pr.is_file():
            continue
        rel = "zombie/" + pr.relative_to(pristine_zombie).as_posix()
        if not (p.src / rel).exists():
            dst = patches_dir / f"{rel}.delete"
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(b"")
            log.info(f"del:  {rel}")
            touched_count += 1

    # Refresh snapshot + updatedAt
    items = patch_items(md)
    mj.pristine_snapshot = pristine_snapshot(p.pristine, items)
    mj.updated_at = utc_now_iso()
    write_mod_json(md, mj)

    log.success(f"captured {touched_count} file(s) into {patches_dir}")
    return 0
