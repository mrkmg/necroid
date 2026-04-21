"""status — working-tree divergence, or per-mod patch applicability."""
from __future__ import annotations

import shutil
from pathlib import Path

from ..fsops import empty_dir
from ..hashing import file_sha256
from ..mod import ensure_mod_exists, patch_items, read_mod_json
from ..patching import patched_theirs_file
from ..profile import existing_subtrees
from ..state import read_enter, read_state


def _status_mod(profile, install_to: str, name: str) -> int:
    md = ensure_mod_exists(profile.mods_dir, name)
    mj = read_mod_json(md)
    effective_to = "client" if mj.client_only else install_to
    items = patch_items(md, effective_to)
    print(f"mod: {name}")
    print(f"  clientOnly: {mj.client_only}")
    if mj.description:
        print(f"  desc: {mj.description}")
    n_p = sum(1 for i in items if i.kind == "patch")
    n_n = sum(1 for i in items if i.kind == "new")
    n_d = sum(1 for i in items if i.kind == "delete")
    print(f"  patches (for install_to={effective_to}): {n_p}  new: {n_n}  delete: {n_d}")
    if not items:
        return 0
    scratch = profile.build / f"stage-scratch-status-{name}"
    empty_dir(scratch)
    stale_any = False
    try:
        for it in items:
            if it.kind == "patch":
                theirs = patched_theirs_file(profile.pristine, scratch, it.file, it.rel)
                tag = "ok" if theirs else "STALE"
                if not theirs:
                    stale_any = True
                print(f"  {tag:<6} {it.rel} ({it.kind})")
            else:
                print(f"  {'-':<6} {it.rel} ({it.kind})")
    finally:
        if scratch.exists():
            shutil.rmtree(scratch, ignore_errors=True)
    return 1 if stale_any else 0


def _status_tree(profile) -> int:
    es = read_enter(profile.enter_file)
    subs = existing_subtrees(profile.pristine)

    if not es:
        print("no mod is entered. (run `necroid enter <mod>` to start editing)")
    else:
        src_dir = profile.src_for(es.mod)
        print(f"entered: {es.mod}  (as {es.install_as})")
        print(f"working tree: {src_dir}")
        diverged: list[str] = []
        if not src_dir.exists():
            print(f"  (working tree missing — run `necroid enter {es.mod} --force`)")
        elif not subs:
            print("  (src-pristine/ empty — run `necroid init`)")
        else:
            for sub in subs:
                src_sub = src_dir / sub
                pristine_sub = profile.pristine / sub
                if not src_sub.exists():
                    print(f"  ({src_dir.name}/{sub}/ missing — consider `reset`)")
                    continue
                for p in src_sub.rglob("*.java"):
                    if not p.is_file():
                        continue
                    rel = f"{sub}/" + p.relative_to(src_sub).as_posix()
                    pr = profile.pristine / rel
                    if not pr.exists():
                        diverged.append(f"+ {rel}")
                        continue
                    if file_sha256(p) != file_sha256(pr):
                        diverged.append(f"M {rel}")
                for p in pristine_sub.rglob("*.java"):
                    if not p.is_file():
                        continue
                    rel = f"{sub}/" + p.relative_to(pristine_sub).as_posix()
                    if not (src_dir / rel).exists():
                        diverged.append(f"- {rel}")
            if not diverged:
                print("  clean (matches src-pristine)")
            else:
                print(f"  {len(diverged)} diverging file(s):")
                for d in sorted(diverged):
                    print(f"    {d}")

    # List every on-disk per-mod tree (including ones not currently entered).
    stray_dirs = sorted(d for d in profile.root.glob("src-*") if d.is_dir())
    if stray_dirs:
        print()
        print("on-disk per-mod working trees:")
        for d in stray_dirs:
            tag = " (entered)" if es and d == profile.src_for(es.mod) else ""
            print(f"  {d.name}{tag}")

    for to in ("client", "server"):
        state = read_state(profile.state_file(to))
        if state.installed:
            print()
            print(f"installed to {to}: {', '.join(state.stack)}  ({len(state.installed)} class files)")
    return 0


def run(args) -> int:
    profile = args.profile
    install_to: str = args.install_to
    if args.name:
        return _status_mod(profile, install_to, args.name)
    return _status_tree(profile)
