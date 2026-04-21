"""capture — diff src-<name>/ vs src-pristine/, rewrite mods/<name>/patches/.

The mod's postfix layout (generic `.patch` vs `.patch.{client|server}`) is
preserved per-file:
  - If the opposite destination already has a postfixed file for the same rel,
    capture writes the entered-side variant as a matching postfix (keeps the
    two variants distinct).
  - Otherwise capture writes a shared (non-postfixed) file.

Only patches matching the currently entered destination (install_as) are
rewritten; opposite-destination postfixed files are preserved untouched.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .. import logging_util as log
from ..config import read_config
from ..fsops import ensure_dir
from ..hashing import file_sha256
from ..mod import (
    ensure_mod_exists,
    parse_patch_filename,
    patch_items,
    pristine_snapshot,
    prune_empty_dirs,
    read_mod_json,
    write_mod_json,
)
from ..patching import git_diff_no_index
from ..profile import existing_subtrees
from ..state import read_enter, utc_now_iso
from ._resolve import resolve_mod


def _existing_postfixed_opposite(patches_dir: Path, rel: str, kind: str, other: str) -> bool:
    """Does a `.{other}`-postfixed file exist for (rel, kind) under patches/?"""
    # rel is like "zombie/Lua/Foo.java"; patch filename is "zombie/Lua/Foo.java.<kind>.<other>"
    target = patches_dir / f"{rel}.{kind}.{other}"
    return target.is_file()


def run(args) -> int:
    p = args.profile
    cfg = read_config(args.root)
    name = resolve_mod(p.mods_dir, cfg.workspace_major, args.name)
    md = ensure_mod_exists(p.mods_dir, name)
    mj = read_mod_json(md)

    # Figure out which destination's variant we're rewriting.
    es = read_enter(p.enter_file)
    if es and es.mod != name:
        raise SystemExit(
            f"currently entered mod is '{es.mod}', not '{name}'. "
            f"Run `necroid enter {name}` first."
        )
    install_as: str = es.install_as if es else args.install_as
    if mj.client_only and install_as != "client":
        raise SystemExit(
            f"mod '{name}' is clientOnly but capture would run as install_as={install_as}. "
            "Re-enter with --as client."
        )
    other = "server" if install_as == "client" else "client"

    src_dir = p.src_for(name)
    if not src_dir.exists():
        raise SystemExit(
            f"no working tree at {src_dir} — run `necroid enter {name}` first."
        )

    patches_dir = md / "patches"
    ensure_dir(patches_dir)
    log.info(f"capture {name} (as {install_as}): diffing {src_dir.name}/ vs src-pristine/")

    # Remove every file applicable to install_as (shared + `.{install_as}`).
    # Opposite-side `.{other}` files stay put.
    for fp in list(patches_dir.rglob("*")):
        if not fp.is_file():
            continue
        rel_full = fp.relative_to(patches_dir).as_posix()
        parsed = parse_patch_filename(rel_full)
        if parsed is None:
            continue
        _rel, _kind, applies = parsed
        if install_as in applies and other not in applies:
            fp.unlink()  # postfixed for this side
        elif applies == frozenset(("client", "server")):
            fp.unlink()  # shared
    prune_empty_dirs(patches_dir)

    subs = existing_subtrees(p.pristine)
    if not subs:
        raise SystemExit(f"src-pristine/ is empty at {p.pristine} (run `necroid init`)")

    def _out_name(rel: str, ext: str) -> str:
        if _existing_postfixed_opposite(patches_dir, rel, ext, other):
            return f"{rel}.{ext}.{install_as}"
        return f"{rel}.{ext}"

    touched_count = 0

    # Modified + new (every subtree that has a src/ counterpart)
    for sub in subs:
        src_sub = src_dir / sub
        if not src_sub.exists():
            continue
        for java in sorted(src_sub.rglob("*.java")):
            if not java.is_file():
                continue
            rel = f"{sub}/" + java.relative_to(src_sub).as_posix()
            pristine_file = p.pristine / rel
            if not pristine_file.exists():
                dst = patches_dir / _out_name(rel, "new")
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
            dst = patches_dir / _out_name(rel, "patch")
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(patch_bytes)
            log.info(f"mod:  {rel}")
            touched_count += 1

    # Deleted — any pristine file with no src/ counterpart
    for sub in subs:
        pristine_sub = p.pristine / sub
        for pr in sorted(pristine_sub.rglob("*.java")):
            if not pr.is_file():
                continue
            rel = f"{sub}/" + pr.relative_to(pristine_sub).as_posix()
            if not (src_dir / rel).exists():
                dst = patches_dir / _out_name(rel, "delete")
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(b"")
                log.info(f"del:  {rel}")
                touched_count += 1

    # Refresh snapshot, updatedAt, and stamp expectedVersion from workspace.
    items = patch_items(md, install_as)
    mj.pristine_snapshot = pristine_snapshot(p.pristine, items)
    mj.updated_at = utc_now_iso()
    if cfg.workspace_version:
        mj.expected_version = cfg.workspace_version
    write_mod_json(md, mj)

    log.success(f"captured {touched_count} file(s) into {patches_dir}")
    return 0
