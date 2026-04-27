"""Atomic install of a mod stack to a chosen destination (client or server).

Phase order:
    1. Reconcile — read install-side manifest, handle WIPED / CACHE_STALE.
    2. Stage   — mirror workspace pristine into build/stage-src, apply stack
    3. Compile — javac only touched .java files
    4. Restore — revert prior-install class files (for `install_to`) to originals
    5. Deploy  — copy new .class files into PZ install + record SHA256
    6. Commit  — write workspace state cache AND <pz>/necroid/install-manifest.json

Any failure prior to Deploy leaves the PZ install untouched.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from ..paths import package_dir
from ..util import logging_util as log
from ..core.config import read_config
from ..core import install_manifest as manifest_mod
from ..errors import (
    BuildError,
    ClientOnlyViolation,
    ConflictError,
    PristineDrift,
    PzMajorMismatch,
    PzVersionDetectError,
)
from ..util.fsops import inner_class_files, mirror_tree
from ..util.hashing import file_sha256
from ..core.mod import ensure_mod_exists, parse_mod_dirname, read_mod_json
from ..core.profile import Profile, existing_subtrees, require_pz_install
from ..pz.pzversion import PzVersion, detect_pz_version
from .stackapply import apply_stack
from ..core.state import InstalledEntry, ModState, read_state, reset_state, utc_now_iso, write_state
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


def _reconcile_before_write(profile: Profile, install_to: str) -> ModState:
    """Phase 1: read the install-side manifest, compare to local cache, decide
    how to proceed. Returns the ModState the rest of the install should act on
    (which may have been refreshed from the install-side manifest).

    Handles WIPED by resetting local cache. Handles CACHE_STALE by refreshing
    from the manifest.
    """
    install_root = profile.pz_install(install_to)
    assert install_root is not None  # require_pz_install ran in caller
    content_dir = profile.content_dir_for(install_to)
    state = read_state(profile.state_file(install_to))
    rec = manifest_mod.reconcile(
        install_root, content_dir, list(state.stack),
        probe_rels=[e.rel for e in state.installed],
    )

    if rec.status is manifest_mod.ReconcileStatus.LEGACY_UNMIGRATED:
        log.info(rec.message)
        # Fall through — install proceeds normally; the Phase-6 manifest
        # write will create the authoritative record.

    if rec.status is manifest_mod.ReconcileStatus.WIPED:
        log.warn(rec.message)
        log.info("clearing local cache for this destination (install was wiped).")
        state = ModState()
        reset_state(profile.state_file(install_to))
        return state

    if rec.status is manifest_mod.ReconcileStatus.CACHE_STALE and rec.manifest is not None:
        log.warn(rec.message)
        state = _state_from_manifest(rec.manifest)
        write_state(profile.state_file(install_to), state)
        log.info("refreshed local cache from install-side manifest.")

    return state


def _state_from_manifest(m: "manifest_mod.InstallManifest") -> ModState:
    return ModState(
        stack=[e.dirname for e in m.stack],
        installed_at=m.installed_at,
        installed=[
            InstalledEntry(
                rel=f.rel,
                mod_origin=f.mod_origin,
                written_sha256=f.written_sha256,
                original_sha256=f.original_sha256,
                was_added=f.was_added,
            )
            for f in m.files
        ],
        pz_version=m.pz_version_at_install or None,
    )


def install_stack(profile: Profile, stack: list[str], install_to: str) -> None:
    require_pz_install(profile, install_to)
    for m in stack:
        ensure_mod_exists(profile.mods_dir, m)
    _assert_destination_allowed(profile, stack, install_to)

    content_dir = profile.content_dir_for(install_to)

    # --- Phase 1: reconcile with install-side manifest ---
    _reconcile_before_write(profile, install_to)

    # --- PZ version gate -------------------------------------------------
    cfg = read_config(profile.root)
    detected = _detect_and_enforce_pz_version(profile, content_dir, install_to, cfg, stack)

    # --- Phase 2: stage source ---
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

    # --- Phase 3: compile touched .java files ---
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
                java_release=int(profile.java_release or 17),
            )
        except BuildError:
            shutil.rmtree(profile.stage, ignore_errors=True)
            raise

    # --- Phase 4: restore prior install (for this destination) to originals ---
    log.step(f"restore prior {install_to} install to original")
    _restore_installed(profile, install_to)

    # --- Phase 5: copy new class files to PZ install ---
    log.step(f"copy class files to {content_dir}")
    installed: list[InstalledEntry] = []

    # Jar layout (PZ >=42): every installed `.class` lands at a path that
    # didn't previously exist as a loose file (the pre-image was inside
    # `projectzomboid.jar`). Treat all such installs as "added" so uninstall
    # = delete the loose override, letting the JVM fall back to the jar
    # entry on the launcher's `./;projectzomboid.jar` classpath.
    layout_is_jar = (getattr(profile, "workspace_layout", "loose") == "jar")

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
            orig_path = profile.originals / Path(rel_class)
            orig_sha = file_sha256(orig_path) if orig_path.exists() else None
            was_added = layout_is_jar or orig_sha is None
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cf, dst)
            log.info(f"+ {rel_class}" + ("  (added)" if was_added else ""))
            sha = file_sha256(cf) or ""
            installed.append(InstalledEntry(
                rel=rel_class,
                mod_origin=mod_origin,
                written_sha256=sha,
                original_sha256=orig_sha,
                was_added=was_added,
            ))

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

    # --- Phase 6: commit state (both sides) ---
    pz_ver = str(detected) if detected else ""
    write_state(profile.state_file(install_to), ModState(
        stack=list(stack),
        installed_at=utc_now_iso(),
        installed=installed,
        pz_version=pz_ver or None,
    ))
    _write_install_manifest(profile, install_to, stack, installed, pz_ver, cfg)
    log.success(f"install complete. to={install_to} stack=[{', '.join(stack)}]  class files={len(installed)}")


def _write_install_manifest(profile: Profile, install_to: str, stack: list[str],
                            installed: list[InstalledEntry], pz_version: str, cfg) -> None:
    install_root = profile.pz_install(install_to)
    assert install_root is not None
    content_dir = profile.content_dir_for(install_to)
    stack_entries: list[manifest_mod.ManifestStackEntry] = []
    for name in stack:
        try:
            mj = read_mod_json(profile.mods_dir / name)
            ver = mj.version or ""
        except Exception:
            ver = ""
        stack_entries.append(manifest_mod.ManifestStackEntry(dirname=name, version=ver))

    # On jar-layout installs, hash the live `projectzomboid.jar` so resync /
    # doctor / verify can detect Steam patch updates that swapped the fat
    # jar out from under us.
    layout = getattr(profile, "workspace_layout", "loose") or "loose"
    pz_jar_sha = ""
    if layout == "jar":
        jar_path = content_dir / "projectzomboid.jar"
        if jar_path.is_file():
            pz_jar_sha = (file_sha256(jar_path) or "").upper()

    workspace_dir_str = ""
    if profile.pz_necroid_dir is not None:
        workspace_dir_str = str(profile.pz_necroid_dir).replace("\\", "/")

    m = manifest_mod.InstallManifest(
        workspace_dir=workspace_dir_str,
        workspace_major=int(cfg.workspace_major or 0),
        workspace_layout=layout,
        destination=install_to,
        pz_version_at_install=pz_version,
        pz_jar_sha256=pz_jar_sha,
        installed_at=utc_now_iso(),
        stack=stack_entries,
        files=[
            manifest_mod.ManifestFile(
                rel=e.rel,
                written_sha256=e.written_sha256,
                original_sha256=e.original_sha256,
                was_added=e.was_added,
                mod_origin=e.mod_origin,
            )
            for e in installed
        ],
    )
    p = manifest_mod.write_manifest(install_root, m)
    log.info(f"install manifest: {p}")


def _detect_and_enforce_pz_version(profile: Profile, content_dir: Path, install_to: str,
                                   cfg, stack: list[str]) -> PzVersion | None:
    """Detect the target install's PZ version and enforce the major gate.

    Hard failures:
      * workspace has a bound major and the install's major differs,
      * any mod in the stack has a `-<major>` suffix that disagrees with the
        workspace major (defense in depth; CLI resolver should already block).

    Soft warnings (install still proceeds):
      * mod.expected_version differs from the detected version (minor/patch drift),
      * mod dir has no `-<major>` suffix (legacy unversioned).

    Returns the detected `PzVersion` (stored in ModState) or raises on a
    hard failure."""
    try:
        detected = detect_pz_version(content_dir, package_dir(), profile.data_dir)
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
            "workspace has no bound major — workspace config may be incomplete. "
            "Run `necroid init` to repair."
        )

    for name in stack:
        parsed = parse_mod_dirname(name)
        if parsed is None:
            log.warn(f"mod '{name}' has no `-<major>` suffix.")
            continue
        _, mod_m = parsed
        if ws_major and mod_m != ws_major:
            raise PzMajorMismatch(
                f"mod '{name}' is for PZ {mod_m}; workspace is bound to PZ {ws_major}."
            )
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


def _restore_installed(profile: Profile, install_to: str, *, strict: bool = False) -> None:
    """Restore files recorded in local state back to their pre-install form.

    For each entry:
      * `was_added` → delete the added file (we'd put it there; no vanilla to fall back to).
      * otherwise → the file was overwritten. Examine the live install copy:
          - hash matches `written_sha256` → still what we wrote; restore from
            `classes-original/`, but first sanity-check the pristine hash matches
            `original_sha256`. If pristine has drifted, raise PristineDrift.
          - hash matches `original_sha256` → already reverted (Steam verify most
            likely). No-op.
          - hash matches neither → Steam-rewrote with different-version vanilla,
            or user hand-edited. In non-strict mode, warn + skip. In strict
            mode, raise.

    `strict=True` is used by `resync_pristine`'s pre-flight so drift forces the
    user to deal with it explicitly. Normal install/uninstall use strict=False.
    """
    state = read_state(profile.state_file(install_to))
    if not state.installed:
        log.info("(nothing to restore)")
        return
    content_dir = profile.content_dir_for(install_to)
    for e in state.installed:
        install_path = content_dir / Path(e.rel)
        orig_path = profile.originals / Path(e.rel)

        # Infer was_added for legacy entries where the flag defaulted to
        # False but the reality (no pristine counterpart) says it's an add.
        effective_was_added = e.was_added or (
            e.original_sha256 is None and not orig_path.exists()
        )

        if effective_was_added:
            if install_path.exists():
                try:
                    install_path.unlink()
                    log.info(f"delete: {e.rel} (mod-added)")
                except OSError as err:
                    log.warn(f"failed to delete {install_path}: {err}")
            continue

        # Overwritten file path.
        live_hash = file_sha256(install_path) if install_path.exists() else None
        written = (e.written_sha256 or "").upper()
        original = (e.original_sha256 or "").upper() if e.original_sha256 else None

        if original and live_hash == original:
            log.info(f"skip:    {e.rel} (already at recorded original)")
            continue

        if live_hash is not None and live_hash != written and (original is None or live_hash != original):
            msg = (
                f"{e.rel}: installed bytes match neither Necroid's record nor the "
                f"recorded original. Install appears to have drifted (Steam patch "
                f"or manual edit)."
            )
            if strict:
                from ..errors import InstallVersionDrift
                raise InstallVersionDrift(msg)
            log.warn(msg + "  skipping restore — Steam's version will remain.")
            continue

        if not orig_path.exists():
            if live_hash is None:
                log.info(f"skip:    {e.rel} (already gone; no pristine to restore)")
                continue
            raise PristineDrift(
                f"{e.rel}: no file at classes-original/ to restore from. "
                f"Run `necroid resync-pristine` to rebuild pristine, or restore "
                f"this file manually."
            )

        if original is not None:
            pristine_hash = file_sha256(orig_path)
            if pristine_hash != original:
                raise PristineDrift(
                    f"{e.rel}: classes-original/ hash has changed since install "
                    f"(recorded {original[:16]}…, now {pristine_hash[:16] if pristine_hash else 'None'}…). "
                    f"Restoring from drifted pristine would corrupt the install. "
                    f"Run `necroid doctor --to {install_to}` to inspect."
                )

        install_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(orig_path, install_path)
        log.info(f"restore: {e.rel}")


def uninstall_all(profile: Profile, install_to: str) -> None:
    state = read_state(profile.state_file(install_to))
    install_root = profile.pz_install(install_to)
    if not state.installed:
        log.info(f"nothing installed to {install_to}.")
        # Even if state is empty, clear the install-side manifest so the next
        # install-time reconcile doesn't get confused by a stale leftover.
        if install_root is not None:
            manifest_mod.delete_manifest(install_root)
        return
    require_pz_install(profile, install_to)
    log.info(f"uninstall: restoring {len(state.installed)} class file(s) on {install_to}")
    _restore_installed(profile, install_to)
    reset_state(profile.state_file(install_to))
    if install_root is not None and manifest_mod.delete_manifest(install_root):
        log.info("removed install-side manifest.")
    log.success("done.")
