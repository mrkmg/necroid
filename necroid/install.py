"""Atomic install of a mod stack to a chosen destination (client or server).

Phase order:
    1. Stage   — mirror workspace pristine into build/stage-src, apply stack
    2. Compile — javac only touched .java files
    3. Restore — revert prior-install class files (for `install_to`) to originals
    4. Deploy  — copy new .class files into PZ install + record SHA256
    5. Commit  — write .mod-state-<install_to>.json

Any failure prior to Deploy leaves the PZ install untouched.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from . import logging_util as log
from .config import read_config
from .errors import BuildError, ClientOnlyViolation, ConflictError, PzMajorMismatch, PzVersionDetectError
from .fsops import empty_dir, inner_class_files, mirror_tree
from .hashing import file_sha256
from .mod import ensure_mod_exists, mod_major, parse_mod_dirname, read_mod_json
from .profile import Profile, existing_subtrees, require_pz_install
from .pzversion import PzVersion, detect_pz_version
from .stackapply import apply_stack
from .state import InstalledEntry, ModState, read_state, reset_state, utc_now_iso, write_state
from . import buildjava


def _assert_destination_allowed(profile: Profile, stack: list[str], install_to: str) -> None:
    """clientOnly mods may only install to client."""
    if install_to != "server":
        return
    offenders: list[str] = []
    for name in stack:
        md = ensure_mod_exists(profile.mods_dir, name)
        mj = read_mod_json(md)
        if mj.client_only:
            offenders.append(name)
    if offenders:
        raise ClientOnlyViolation(
            f"cannot install to server — clientOnly mod(s): {', '.join(offenders)}\n"
            f"    retry with `--to client`, or drop the clientOnly flag."
        )


def install_stack(profile: Profile, stack: list[str], install_to: str) -> None:
    require_pz_install(profile, install_to)
    for m in stack:
        ensure_mod_exists(profile.mods_dir, m)
    _assert_destination_allowed(profile, stack, install_to)

    content_dir = profile.content_dir_for(install_to)

    # --- PZ version gate -------------------------------------------------
    cfg = read_config(profile.root)
    detected = _detect_and_enforce_pz_version(profile, content_dir, install_to, cfg, stack)

    # --- Phase 1: stage source ---
    log.step(f"stage source ({profile.stage})")
    subs = existing_subtrees(profile.pristine)
    if not subs:
        raise BuildError(f"src-pristine/ is empty at {profile.pristine} (run `necroid init`)")
    if profile.stage.exists():
        shutil.rmtree(profile.stage)
    profile.stage.mkdir(parents=True, exist_ok=True)
    for sub in subs:
        mirror_tree(profile.pristine / sub, profile.stage / sub)

    result = apply_stack(
        stack=stack,
        work_dir=profile.stage,
        pristine_dir=profile.pristine,
        mods_dir=profile.mods_dir,
        scratch_root=profile.build / "stage-scratch",
        install_to=install_to,
    )
    if result.conflicts:
        shutil.rmtree(profile.stage, ignore_errors=True)
        log.error("install aborted — PZ install untouched")
        raise ConflictError([c.to_dict() for c in result.conflicts])
    log.info(f"applied: {len(result.touched)} file(s)")

    # --- Phase 2: compile touched .java files ---
    java_files = [
        profile.stage / rel for rel in result.touched.keys()
        if rel.endswith(".java") and (profile.stage / rel).exists()
    ]
    if not java_files and not result.deletes:
        shutil.rmtree(profile.stage, ignore_errors=True)
        raise BuildError("no files to install (no patches/new/delete in requested stack)")
    if java_files:
        log.step(f"compile {len(java_files)} file(s)")
        try:
            buildjava.javac_compile(
                files=java_files,
                libs=profile.libs,
                classpath_originals=profile.classpath_originals,
                out_dir=profile.classes_out,
                clean=True,
            )
        except BuildError:
            shutil.rmtree(profile.stage, ignore_errors=True)
            raise

    # --- Phase 3: restore prior install (for this destination) to originals ---
    log.step(f"restore prior {install_to} install to original")
    _restore_installed(profile, install_to)

    # --- Phase 4: copy new class files to PZ install ---
    log.step(f"copy class files to {content_dir}")
    installed: list[InstalledEntry] = []

    for rel, mod_origin in result.touched.items():
        if not rel.endswith(".java"):
            continue
        base = rel[:-len(".java")]                # zombie/Lua/Event
        class_dir_rel = base.rsplit("/", 1)[0] if "/" in base else ""
        leaf_base = base.rsplit("/", 1)[-1]
        build_class_dir = profile.classes_out / class_dir_rel
        matches = inner_class_files(build_class_dir, leaf_base)
        if not matches:
            log.warn(f"no class output under {build_class_dir} for {rel}")
            continue
        for cf in matches:
            rel_class = f"{class_dir_rel}/{cf.name}" if class_dir_rel else cf.name
            dst = content_dir / Path(rel_class)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cf, dst)
            log.info(f"+ {rel_class}")
            sha = file_sha256(cf) or ""
            installed.append(InstalledEntry(rel=rel_class, mod_origin=mod_origin, sha256=sha))

    # Deletes: if an original exists, remove from install (pristine is fine); else remove added file.
    for rel in result.deletes:
        base = rel[:-len(".java")]
        class_dir_rel = base.rsplit("/", 1)[0] if "/" in base else ""
        leaf_base = base.rsplit("/", 1)[-1]
        orig_class_dir = profile.originals / class_dir_rel
        for orig in inner_class_files(orig_class_dir, leaf_base):
            rel_class = f"{class_dir_rel}/{orig.name}" if class_dir_rel else orig.name
            dst = content_dir / Path(rel_class)
            if dst.exists():
                try:
                    dst.unlink()
                    log.info(f"- {rel_class} (deleted — also removed from install)")
                except OSError as e:
                    log.warn(f"failed to delete {dst}: {e}")

    # --- Phase 5: commit state ---
    write_state(profile.state_file(install_to), ModState(
        version=1,
        stack=list(stack),
        installed_at=utc_now_iso(),
        installed=installed,
        pz_version=str(detected) if detected else None,
    ))
    log.success(f"install complete. to={install_to} stack=[{', '.join(stack)}]  class files={len(installed)}")


def _detect_and_enforce_pz_version(profile: Profile, content_dir: Path, install_to: str,
                                   cfg, stack: list[str]) -> PzVersion | None:
    """Detect the target install's PZ version and enforce the major gate.

    Hard failures:
      * workspace has a bound major and the install's major differs,
      * any mod in the stack has a `-<major>` suffix that disagrees with the
        workspace major (defense in depth; CLI resolver should already block).

    Soft warnings (install still proceeds):
      * workspace major unset (legacy config without the v4 binding),
      * mod.expected_version differs from the detected version (minor/patch drift),
      * mod dir has no `-<major>` suffix (legacy unversioned).

    Returns the detected `PzVersion` (stored in ModState) or raises on a
    hard failure."""
    try:
        necroid_pkg = Path(__file__).resolve().parent
        detected = detect_pz_version(content_dir, necroid_pkg, profile.root / "data")
    except PzVersionDetectError as e:
        raise PzVersionDetectError(
            f"cannot install to {install_to}: {e}\n"
            f"    the install's PZ version must be detectable before any .class files are touched."
        )

    ws_major = int(getattr(cfg, "workspace_major", 0) or 0)
    ws_version = str(getattr(cfg, "workspace_version", "") or "")

    if ws_major and detected.major != ws_major:
        raise PzMajorMismatch(
            f"{install_to} install is PZ {detected} (major {detected.major}), but "
            f"workspace is bound to major {ws_major}. "
            f"Run `necroid resync-pristine --from {install_to} --force-major-change` "
            f"to re-bind the workspace to {install_to}'s major."
        )
    if not ws_major:
        log.warn(
            "workspace has no bound major (legacy config). "
            "Run `necroid init` to upgrade — some checks are skipped."
        )

    for name in stack:
        parsed = parse_mod_dirname(name)
        if parsed is None:
            log.warn(f"mod '{name}' has no `-<major>` suffix (legacy). "
                     f"Run `necroid init` to migrate.")
            continue
        _, mod_m = parsed
        if ws_major and mod_m != ws_major:
            raise PzMajorMismatch(
                f"mod '{name}' is for PZ {mod_m}; workspace is bound to PZ {ws_major}."
            )
        # Minor/patch drift (soft).
        try:
            mj = read_mod_json(profile.mods_dir / name)
        except Exception:
            continue
        if mj.expected_version:
            try:
                ev = PzVersion.parse(mj.expected_version)
            except Exception:
                log.warn(f"mod '{name}' has unparseable expectedVersion='{mj.expected_version}'.")
                continue
            if ev.major == detected.major and (ev.minor, ev.patch, ev.suffix) != (
                    detected.minor, detected.patch, detected.suffix):
                log.warn(
                    f"mod '{name}' was captured against PZ {ev}; {install_to} install is "
                    f"PZ {detected}. Recapture recommended."
                )

    if ws_version and ws_version != str(detected):
        log.warn(
            f"workspace was seeded against PZ {ws_version}, but {install_to} install is "
            f"PZ {detected}. Consider `necroid resync-pristine --from {install_to}`."
        )

    return detected


def _restore_installed(profile: Profile, install_to: str) -> None:
    state = read_state(profile.state_file(install_to))
    if not state.installed:
        log.info("(nothing to restore)")
        return
    content_dir = profile.content_dir_for(install_to)
    for e in state.installed:
        install_path = content_dir / Path(e.rel)
        orig_path = profile.originals / Path(e.rel)
        if orig_path.exists():
            install_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(orig_path, install_path)
            log.info(f"restore: {e.rel}")
        elif install_path.exists():
            try:
                install_path.unlink()
                log.info(f"delete: {e.rel} (no pristine — was mod-added)")
            except OSError as err:
                log.warn(f"failed to delete {install_path}: {err}")


def uninstall_all(profile: Profile, install_to: str) -> None:
    state = read_state(profile.state_file(install_to))
    if not state.installed:
        log.info(f"nothing installed to {install_to}.")
        return
    require_pz_install(profile, install_to)
    log.info(f"uninstall: restoring {len(state.installed)} class file(s) on {install_to}")
    _restore_installed(profile, install_to)
    reset_state(profile.state_file(install_to))
    log.success("done.")
