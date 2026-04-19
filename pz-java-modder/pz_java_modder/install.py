"""Atomic install of a mod stack.

Phase order (preserved from PS):
    1. Stage   — mirror pristine into build/stage-src, apply stack
    2. Compile — javac only touched .java files
    3. Restore — revert prior-install class files back to classes-original
    4. Deploy  — copy new .class files into PZ install + record SHA256
    5. Commit  — write .mod-state.json

Any failure prior to Deploy leaves the PZ install untouched.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from . import logging_util as log
from .errors import BuildError, ConflictError
from .fsops import empty_dir, inner_class_files, mirror_tree
from .hashing import file_sha256
from .mod import ensure_mod_exists
from .profile import Profile, require_pz_install
from .stackapply import apply_stack
from .state import InstalledEntry, ModState, read_state, reset_state, utc_now_iso, write_state
from . import buildjava


def _pristine_zombie(profile: Profile) -> Path: return profile.pristine / "zombie"


def install_stack(profile: Profile, stack: list[str]) -> None:
    require_pz_install(profile)
    for m in stack:
        ensure_mod_exists(profile.mods_dir, m)

    # --- Phase 1: stage source ---
    log.step(f"stage source ({profile.stage})")
    stage_zombie = profile.stage / "zombie"
    if profile.stage.exists():
        shutil.rmtree(profile.stage)
    profile.stage.mkdir(parents=True, exist_ok=True)
    mirror_tree(_pristine_zombie(profile), stage_zombie)

    result = apply_stack(
        stack=stack,
        work_dir=profile.stage,
        pristine_dir=profile.pristine,
        mods_dir=profile.mods_dir,
        scratch_root=profile.build / "stage-scratch",
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

    # --- Phase 3: restore prior install to originals ---
    log.step("restore prior install to original")
    _restore_installed(profile)

    # --- Phase 4: copy new class files to PZ install ---
    log.step(f"copy class files to {profile.content_dir}")
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
            dst = profile.content_dir / Path(rel_class)
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
            dst = profile.content_dir / Path(rel_class)
            if dst.exists():
                try:
                    dst.unlink()
                    log.info(f"- {rel_class} (deleted — also removed from install)")
                except OSError as e:
                    log.warn(f"failed to delete {dst}: {e}")

    # --- Phase 5: commit state ---
    write_state(profile.state_file, ModState(
        version=1,
        stack=list(stack),
        installed_at=utc_now_iso(),
        installed=installed,
    ))
    log.success(f"install complete. stack=[{', '.join(stack)}]  class files={len(installed)}")


def _restore_installed(profile: Profile) -> None:
    state = read_state(profile.state_file)
    if not state.installed:
        log.info("(nothing to restore)")
        return
    for e in state.installed:
        install_path = profile.content_dir / Path(e.rel)
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


def uninstall_all(profile: Profile) -> None:
    state = read_state(profile.state_file)
    if not state.installed:
        log.info("nothing installed.")
        return
    require_pz_install(profile)
    log.info(f"uninstall: restoring {len(state.installed)} class file(s)")
    _restore_installed(profile)
    reset_state(profile.state_file)
    log.success("done.")
