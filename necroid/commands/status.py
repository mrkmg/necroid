"""status — working-tree divergence, or per-mod patch applicability."""
from __future__ import annotations

import shutil
from pathlib import Path

from ..util import logging_util as log
from ..core.config import read_config
from ..core.depgraph import resolve_deps
from ..errors import (
    ModDependencyCycle,
    ModDependencyMissing,
    PzVersionDetectError,
)
from ..paths import package_dir
from ..util.fsops import empty_dir, mirror_tree
from ..util.hashing import file_sha256
from ..core.mod import ensure_mod_exists, patch_items, read_mod_json
from ..build.patching import patched_theirs_file
from ..core.profile import existing_subtrees
from ..pz.pzversion import PzVersion, detect_pz_version
from ..build.stackapply import apply_stack
from ..core.state import read_enter, read_state
from ._resolve import resolve_mod


def _detect_destination_version(profile, install_to: str) -> str:
    """Return a short descriptor string for the given destination's PZ version,
    or a human-readable reason it could not be detected. Never raises."""
    pz = profile.pz_install(install_to)
    if pz is None:
        return "(not configured)"
    if not pz.exists():
        return f"(install missing: {pz})"
    content = profile.content_dir_for(install_to)
    try:
        v = detect_pz_version(content, package_dir(), profile.root / "data")
        return str(v)
    except PzVersionDetectError as e:
        return f"(detect failed: {e})"


def _describe_drift(expected: str, detected: str) -> str:
    """Return '' if ok, 'recapture' for minor/patch drift, 'INCOMPATIBLE' for major."""
    if not expected:
        return "no expected version"
    try:
        ev = PzVersion.parse(expected)
        dv = PzVersion.parse(detected)
    except Exception:
        return ""
    if ev.major != dv.major:
        return "INCOMPATIBLE"
    if (ev.minor, ev.patch, ev.suffix) != (dv.minor, dv.patch, dv.suffix):
        return "recapture"
    return ""


def _status_mod(profile, install_to: str, name: str) -> int:
    cfg = read_config(profile.root, required=False)
    name = resolve_mod(profile.mods_dir, cfg.workspace_major, name)
    md = ensure_mod_exists(profile.mods_dir, name)
    mj = read_mod_json(md)
    effective_to = "client" if mj.client_only else install_to
    items = patch_items(md, effective_to)
    print(f"mod: {name}")
    print(f"  clientOnly: {mj.client_only}")
    if mj.description:
        print(f"  desc: {mj.description}")
    if mj.dependencies:
        print(f"  dependencies: {', '.join(mj.dependencies)}")
        # Flag any deps that don't resolve yet so authors catch typos early.
        for d in mj.dependencies:
            try:
                resolve_mod(profile.mods_dir, cfg.workspace_major, d)
            except Exception as e:
                log.warn(f"dependency '{d}' doesn't resolve: {e}")
    if mj.incompatible_with:
        print(f"  incompatibleWith: {', '.join(mj.incompatible_with)}")

    # PZ version diagnostic.
    detected = _detect_destination_version(profile, effective_to)
    expected = mj.expected_version or ""
    if expected:
        drift = _describe_drift(expected, detected) if not detected.startswith("(") else ""
        if drift:
            print(f"  PZ: expected {expected} — {effective_to} install is {detected} ({drift})")
        else:
            print(f"  PZ: expected {expected} — {effective_to} install is {detected}")
    else:
        print(f"  PZ: (not stamped; run `capture`) — {effective_to} install is {detected}")
    n_p = sum(1 for i in items if i.kind == "patch")
    n_n = sum(1 for i in items if i.kind == "new")
    n_d = sum(1 for i in items if i.kind == "delete")
    print(f"  patches (for install_to={effective_to}): {n_p}  new: {n_n}  delete: {n_d}")
    if not items:
        return 0

    # Build a baseline = pristine + applied deps so dependent mods' patches
    # are checked for applicability against the same tree they were captured
    # from (plain pristine would flag every dep-overlapping patch as STALE).
    try:
        deps = resolve_deps(profile.mods_dir, cfg.workspace_major, name)
    except (ModDependencyMissing, ModDependencyCycle) as e:
        print(f"  (dep graph broken: {e}) — applicability checked against pristine")
        deps = []

    baseline_dir, cleanup_baseline = _baseline_for_status(
        profile, name, deps, effective_to
    )
    scratch = profile.build / f"stage-scratch-status-{name}"
    empty_dir(scratch)
    stale_any = False
    try:
        for it in items:
            if it.kind == "patch":
                theirs = patched_theirs_file(baseline_dir, scratch, it.file, it.rel)
                tag = "ok" if theirs else "STALE"
                if not theirs:
                    stale_any = True
                print(f"  {tag:<6} {it.rel} ({it.kind})")
            else:
                print(f"  {'-':<6} {it.rel} ({it.kind})")
    finally:
        if scratch.exists():
            shutil.rmtree(scratch, ignore_errors=True)
        cleanup_baseline()
    return 1 if stale_any else 0


def _baseline_for_status(profile, name: str, deps: list[str], install_to: str):
    """Throwaway pristine+deps tree for a dependent mod's status check.
    Dep-less mods reuse the real pristine dir."""
    if not deps:
        return profile.pristine, lambda: None
    root = profile.build / "status-baseline" / name
    empty_dir(root)
    subs = existing_subtrees(profile.pristine)
    for sub in subs:
        mirror_tree(profile.pristine / sub, root / sub)
    result = apply_stack(
        stack=deps,
        work_dir=root,
        pristine_dir=profile.pristine,
        mods_dir=profile.mods_dir,
        scratch_root=profile.build / "status-baseline-scratch",
        install_to=install_to,
    )
    if result.conflicts:
        log.warn(f"  (dep baseline wouldn't build — falling back to pristine)")
        shutil.rmtree(root, ignore_errors=True)
        return profile.pristine, lambda: None
    return root, lambda: shutil.rmtree(root, ignore_errors=True)


def _status_tree(profile) -> int:
    cfg = read_config(profile.root, required=False)
    ws_major = int(getattr(cfg, "workspace_major", 0) or 0)
    ws_version = str(getattr(cfg, "workspace_version", "") or "")
    if ws_major or ws_version:
        header_parts = []
        if ws_version:
            header_parts.append(f"PZ {ws_version}")
        if ws_major:
            header_parts.append(f"major {ws_major}")
        print(f"workspace: {' · '.join(header_parts)}")
        for dest in ("client", "server"):
            if profile.pz_install(dest) is not None:
                d = _detect_destination_version(profile, dest)
                print(f"  {dest}: {d}")
        print()

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
