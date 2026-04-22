"""capture — diff src-<name>/ vs baseline, rewrite mods/<name>/patches/.

The baseline is `src-pristine/` plus every transitive dependency of <name>
applied on top (in topo order). For a mod with no deps the baseline is just
pristine, so behaviour matches the legacy flow.

A dependent mod's captured patches therefore represent only what it adds
*beyond* its deps — the deps' own patch sets stay authoritative for their
contribution. At enter time `apply_stack([*deps, name])` reproduces the
working tree, keeping the round-trip consistent.

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

from ..util import logging_util as log
from ..core.config import read_config
from ..core.depgraph import resolve_deps
from ..errors import ConflictError
from ..util.fsops import ensure_dir, empty_dir, mirror_tree
from ..util.hashing import file_sha256
from ..core.mod import (
    ensure_mod_exists,
    parse_patch_filename,
    patch_items,
    pristine_snapshot,
    prune_empty_dirs,
    read_mod_json,
    write_mod_json,
)
from ..build.patching import git_diff_no_index
from ..core.profile import existing_subtrees
from ..build.stackapply import apply_stack
from ..core.state import read_enter, utc_now_iso
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

    # Build the baseline tree = pristine + applied deps. For dep-less mods
    # the baseline is a verbatim mirror of pristine, so the downstream diff
    # logic is unchanged from the pre-deps behaviour.
    deps = resolve_deps(p.mods_dir, cfg.workspace_major, name)
    baseline_dir, cleanup_baseline = _materialise_baseline(
        p, cfg.workspace_major, name, deps, install_as
    )

    patches_dir = md / "patches"
    ensure_dir(patches_dir)
    if deps:
        log.info(
            f"capture {name} (as {install_as}): diffing {src_dir.name}/ vs "
            f"pristine+[{', '.join(deps)}]"
        )
    else:
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

    try:
        # Modified + new (every subtree that has a src/ counterpart).
        # Reference = baseline (pristine + deps), not raw pristine.
        for sub in subs:
            src_sub = src_dir / sub
            if not src_sub.exists():
                continue
            for java in sorted(src_sub.rglob("*.java")):
                if not java.is_file():
                    continue
                rel = f"{sub}/" + java.relative_to(src_sub).as_posix()
                baseline_file = baseline_dir / rel
                if not baseline_file.exists():
                    dst = patches_dir / _out_name(rel, "new")
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(java, dst)
                    log.info(f"new:  {rel}")
                    touched_count += 1
                    continue
                if file_sha256(java) == file_sha256(baseline_file):
                    continue
                patch_bytes = git_diff_no_index(baseline_file, java, rel)
                if patch_bytes is None:
                    continue
                dst = patches_dir / _out_name(rel, "patch")
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(patch_bytes)
                log.info(f"mod:  {rel}")
                touched_count += 1

        # Deleted — any baseline file with no src/ counterpart.
        for sub in subs:
            baseline_sub = baseline_dir / sub
            if not baseline_sub.exists():
                continue
            for br in sorted(baseline_sub.rglob("*.java")):
                if not br.is_file():
                    continue
                rel = f"{sub}/" + br.relative_to(baseline_sub).as_posix()
                if not (src_dir / rel).exists():
                    dst = patches_dir / _out_name(rel, "delete")
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_bytes(b"")
                    log.info(f"del:  {rel}")
                    touched_count += 1

        # Refresh snapshot (hashed against the baseline so the invariant
        # "snapshot of what we diffed against" holds for dependent mods too),
        # updatedAt, and expectedVersion from workspace.
        items = patch_items(md, install_as)
        mj.pristine_snapshot = pristine_snapshot(baseline_dir, items)
        mj.updated_at = utc_now_iso()
        if cfg.workspace_version:
            mj.expected_version = cfg.workspace_version
        write_mod_json(md, mj)
    finally:
        cleanup_baseline()

    log.success(f"captured {touched_count} file(s) into {patches_dir}")
    return 0


def _materialise_baseline(
    profile, ws_major: int, name: str, deps: list[str], install_as: str,
):
    """Build a fresh pristine+deps tree under profile.build/capture-baseline/<name>.
    Returns (baseline_dir, cleanup_fn). For dep-less mods, returns the real
    pristine dir and a no-op cleanup (avoids copying ~1600 files for nothing)."""
    if not deps:
        return profile.pristine, lambda: None

    baseline_root = profile.build / "capture-baseline" / name
    empty_dir(baseline_root)
    subs = existing_subtrees(profile.pristine)
    for sub in subs:
        mirror_tree(profile.pristine / sub, baseline_root / sub)
    result = apply_stack(
        stack=deps,
        work_dir=baseline_root,
        pristine_dir=profile.pristine,
        mods_dir=profile.mods_dir,
        scratch_root=profile.build / "capture-baseline-scratch",
        install_to=install_as,
    )
    if result.conflicts:
        # Clean up before we raise so subsequent runs start fresh.
        shutil.rmtree(baseline_root, ignore_errors=True)
        log.error("dependency baseline CONFLICTS (capture aborted):")
        for cf in result.conflicts:
            print(f"  {cf.rel}  [{cf.type}]  mods: {', '.join(cf.mods)}")
        raise ConflictError([c.to_dict() for c in result.conflicts])

    def _cleanup() -> None:
        shutil.rmtree(baseline_root, ignore_errors=True)

    return baseline_root, _cleanup
