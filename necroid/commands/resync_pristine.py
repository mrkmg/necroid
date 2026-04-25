"""resync-pristine — after a PZ update, regenerate the shared workspace from
the source PZ install and flag mods whose patches no longer apply.

Why this file is so careful: the PZ install at resync time is not guaranteed
to be clean vanilla. Steam's integrity-verify and patch-update flows are
asymmetric — they overwrite files Steam "controls" (e.g. ones we'd modded)
but leave files Necroid added (new `.class` files produced by a mod's
`.java.new`) untouched. If we blindly copy the install → `classes-original/`
we poison every mod's diff. The flow below:

  1. Reconcile local state with the install-side manifest. A missing manifest
     + non-empty local stack means the install was wiped — harmless; we just
     drop the stale local cache. A fingerprint mismatch means another
     Necroid workspace is managing this install; we refuse unless the user
     passes `--adopt-install`.

  2. Per-file audit of every file the manifest claims we installed. We
     classify each as STILL_MODDED / REVERTED_TO_OLD_VANILLA / NEW_VERSION_DRIFT.
     Drift is the dangerous case: restoring from `classes-original/` in the
     pre-resync guard would overwrite Steam's new vanilla with old vanilla
     and leave `classes-original/` Frankensteined. We abort unless
     `--force-version-drift` is passed, in which case we skip the restore
     for drifted files (letting Steam's version serve as the new pristine
     for that file) and mark every mod as needing recapture.

  3. Orphan scan — any `.class` file under a mod-touched subtree that's in
     neither the manifest nor `classes-original/`. Abort unless forced.

  4. Pre-resync uninstall. Restores STILL_MODDED files to their recorded
     originals and deletes added files. Does NOT touch REVERTED or DRIFT
     files (they're already not-ours). Hash-verifies `classes-original/`
     matches the recorded `original_sha256` before restoring each file; a
     mismatch raises `PristineDrift` and the user must recover by hand.

  5. `init --force` with the mirror_tree's hash-verify mode enabled, so a
     Steam-reverted file with a coincidentally-close mtime isn't silently
     skipped.

  6. Per-mod patch applicability check (unchanged from v1).
"""
from __future__ import annotations

import shutil
from argparse import Namespace

from ..util import logging_util as log
from ..core.config import read_config
from ..core.depgraph import resolve_deps
from ..core import install_manifest as manifest_mod
from ..errors import (
    ConfigError,
    InstallFingerprintMismatch,
    InstallVersionDrift,
    ModDependencyCycle,
    ModDependencyMissing,
    OrphanInstalledFile,
    PzMajorMismatch,
    PzVersionDetectError,
)
from ..paths import package_dir
from ..util.fsops import empty_dir, mirror_tree
from ..build.install import uninstall_all
from ..core.mod import list_mods, patch_items, pristine_snapshot, read_mod_json, write_mod_json
from ..build.patching import patched_theirs_file
from ..core.profile import PZ_CLASS_SUBTREES, existing_subtrees, require_pz_install
from ..pz.pzversion import detect_pz_version
from ..build.stackapply import apply_stack
from ..core.state import read_state
from . import init as init_cmd


def _audit_destination(profile, dest: str, *, force_version_drift: bool,
                       force_orphans: bool, adopt: bool) -> None:
    """Run the reconciliation matrix + per-file audit + orphan scan for one
    destination, then roll back any still-modded stack. Raises if the user's
    flags don't authorize a detected hazard."""
    content_dir = profile.content_dir_for(dest)
    if not content_dir.exists():
        return  # destination not configured / not present

    cfg = read_config(profile.root, required=False)
    local_fp = cfg.workspace_fingerprint if cfg else ""
    state = read_state(profile.state_file(dest))

    rec = manifest_mod.reconcile(
        content_dir, local_fp, list(state.stack),
        probe_rels=[e.rel for e in state.installed],
    )

    if rec.status is manifest_mod.ReconcileStatus.FIRST_TIME:
        return  # nothing to do on this dest

    if rec.status is manifest_mod.ReconcileStatus.FINGERPRINT_MISMATCH and not adopt:
        raise InstallFingerprintMismatch(rec.message)

    if rec.status is manifest_mod.ReconcileStatus.WIPED:
        log.warn(f"{dest}: {rec.message}")
        from ..core.state import reset_state
        reset_state(profile.state_file(dest))
        return

    if rec.status is manifest_mod.ReconcileStatus.LEGACY_UNMIGRATED:
        # Pre-v2 install. Fall back to the state-based audit using written
        # and (possibly-absent) original hashes. An adapter lets us reuse
        # `audit_manifest_files` without duplicating the logic.
        log.info(f"{dest}: {rec.message}")
        legacy_manifest = _state_as_manifest(state)
        audit = manifest_mod.audit_manifest_files(content_dir, legacy_manifest)
    else:
        # At this point a manifest is present. Audit its files.
        assert rec.manifest is not None
        audit = manifest_mod.audit_manifest_files(content_dir, rec.manifest)

    # Fat-jar drift: jar-layout installs record `pz_jar_sha256`. A live jar
    # whose hash diverged from the recorded one is the jar-layout equivalent
    # of NEW_VERSION_DRIFT — Steam shipped a patch update that swapped the
    # fat jar's bytes. Without --force-version-drift, abort: the workspace's
    # pristine + classes-original/ are still pinned to the old jar, and a
    # re-init now would copy the new jar but every mod's diffs are still
    # against the old contents.
    if rec.status is not manifest_mod.ReconcileStatus.LEGACY_UNMIGRATED:
        jar_audit = manifest_mod.audit_pz_jar(content_dir, rec.manifest)
        if jar_audit is manifest_mod.JarAuditResult.JAR_DRIFT and not force_version_drift:
            raise InstallVersionDrift(
                f"{dest}: projectzomboid.jar hash differs from the install-time "
                f"record. Steam patched PZ to a new build (the jar contents have "
                f"changed). Pass `--force-version-drift` to adopt the new jar as "
                f"the new pristine — every installed mod will be flagged for "
                f"re-capture against the new bytes."
            )
        elif jar_audit is manifest_mod.JarAuditResult.JAR_DRIFT:
            log.warn(
                f"{dest}: projectzomboid.jar drifted (Steam patch update). "
                f"--force-version-drift set: adopting new jar as pristine."
            )

    drifted = [a for a in audit if a.result is manifest_mod.FileAuditResult.NEW_VERSION_DRIFT]
    tampered_added = [a for a in audit if a.result is manifest_mod.FileAuditResult.ADDED_TAMPERED]
    if drifted or tampered_added:
        lines = "\n".join(
            f"    - [{a.result.value}] {a.rel}  (mod: {a.mod_origin})"
            for a in (drifted + tampered_added)[:20]
        )
        more = len(drifted) + len(tampered_added) - 20
        more_s = f"\n    … and {more} more" if more > 0 else ""
        msg = (
            f"{dest}: {len(drifted)} file(s) drifted to a different PZ version "
            f"(Steam patch or manual edit), plus {len(tampered_added)} mod-added "
            f"file(s) with unexpected contents:\n{lines}{more_s}"
        )
        if not force_version_drift:
            raise InstallVersionDrift(
                msg + "\n    Re-run Steam's 'Verify Integrity of Game Files' and then retry,\n"
                      "    or pass `--force-version-drift` to skip restore for these files\n"
                      "    (every mod will be flagged as needing re-capture)."
            )
        log.warn(
            msg + "\n    --force-version-drift set: drifted files will be left at "
                  "Steam's current bytes and adopted as new pristine."
        )

    # Orphan scan — files in the install that aren't in the manifest and
    # differ from classes-original (user hand-patched, or a prior crash).
    # Skip on LEGACY since we have no manifest to compare against.
    subs = existing_subtrees(profile.originals) or list(PZ_CLASS_SUBTREES)
    if rec.status is manifest_mod.ReconcileStatus.LEGACY_UNMIGRATED:
        orphans: list[str] = []
    else:
        orphans = manifest_mod.scan_orphans(content_dir, profile.originals, rec.manifest, subs)
    if orphans:
        lines = "\n".join(f"    - {r}" for r in orphans[:20])
        more = f"\n    … and {len(orphans) - 20} more" if len(orphans) > 20 else ""
        msg = (
            f"{dest}: {len(orphans)} orphan file(s) (not in manifest, not vanilla):\n"
            f"{lines}{more}"
        )
        if not force_orphans:
            raise OrphanInstalledFile(
                msg + "\n    These would be adopted into the new pristine. Run "
                      "`necroid doctor` to inspect; pass `--force-orphans` to adopt anyway."
            )
        log.warn(msg + "\n    --force-orphans set: these will be adopted into the new pristine.")


def _uninstall_active_stacks(profile, *, force_version_drift: bool) -> None:
    """Roll back both destinations' installed stacks before pristine sources
    are refreshed. Order: audit-then-uninstall per destination. Raises if
    state says something is installed but the PZ install path isn't
    configured/present — we won't silently skip, since adopting modded
    classes as pristine would corrupt every mod in the library."""
    for dest in ("client", "server"):
        state = read_state(profile.state_file(dest))
        if not state.installed:
            continue
        log.step(
            f"guard: uninstall {dest} stack [{', '.join(state.stack)}] "
            f"({len(state.installed)} class file(s)) before resync"
        )
        try:
            require_pz_install(profile, dest)
        except ConfigError as e:
            raise ConfigError(
                f"cannot resync-pristine: {dest} has an installed stack but its PZ install "
                f"is unreachable. Roll it back manually, then retry.\n    {e}"
            )
        # Uninstall is now hash-aware; it'll no-op on REVERTED files and
        # warn/skip on drift (since we set strict=False by default) — the
        # audit above already gated drift with --force-version-drift.
        uninstall_all(profile, dest)


def run(args) -> int:
    p = args.profile
    source = args.source  # populated in cli.py from --from (or config.workspace_source)
    install_to = args.install_to  # used for postfix resolution during applicability check
    force_major = bool(getattr(args, "force_major_change", False))
    force_version_drift = bool(getattr(args, "force_version_drift", False))
    force_orphans = bool(getattr(args, "force_orphans", False))
    adopt_install = bool(getattr(args, "adopt_install", False))
    assume_yes = bool(getattr(args, "yes", False))

    # Major-change guard runs BEFORE the uninstall pre-flight — otherwise a
    # guard failure leaves the user without their installed stacks.
    src_install = p.pz_install(source)
    if src_install is None or not src_install.exists():
        raise ConfigError(
            f"{source}PzInstall is not configured or does not exist. "
            f"Run `necroid init --from {source}` first."
        )
    src_content = src_install / "java" if source == "server" else src_install

    cfg = read_config(args.root)
    try:
        detected = detect_pz_version(
            src_content,
            package_dir(),
            args.root / "data",
        )
    except PzVersionDetectError as e:
        raise ConfigError(f"could not detect PZ version at {src_content}: {e}")

    if cfg.workspace_major and detected.major != cfg.workspace_major and not force_major:
        raise PzMajorMismatch(
            f"workspace is bound to major {cfg.workspace_major}, but {source} install "
            f"is now PZ {detected}. Run with --force-major-change to re-bind the "
            f"workspace to major {detected.major} (this invalidates every mod's "
            f"patches against pristine — expect 3-way merge conflicts)."
        )
    if cfg.workspace_major and detected.major != cfg.workspace_major:
        log.warn(
            f"major change: workspace {cfg.workspace_major} → {detected.major}. "
            f"All major-{cfg.workspace_major} mods will filter out of default views; "
            f"re-enter and re-capture each one to port it."
        )

    log.step("integrity audit (both destinations)")
    for dest in ("client", "server"):
        _audit_destination(
            p,
            dest,
            force_version_drift=force_version_drift,
            force_orphans=force_orphans,
            adopt=adopt_install,
        )

    _uninstall_active_stacks(p, force_version_drift=force_version_drift)

    log.info(f"resync-pristine [from={source}]: re-running init with --force")
    init_args = Namespace(
        root=args.root,
        source=source,
        pz_install=None,
        force=True,
        yes=True,                  # don't re-prompt; resync is non-interactive
        major=detected.major,      # explicit: match the detected install
    )
    init_cmd.run(init_args)
    cfg = read_config(args.root)

    log.step("checking mod patches against new pristine...")
    any_stale = False
    subs = existing_subtrees(p.pristine)
    for name in list_mods(p.mods_dir, workspace_major=cfg.workspace_major):
        md = p.mods_dir / name
        mj = read_mod_json(md)
        # For applicability checking, use the effective install destination;
        # clientOnly mods are always checked against the client variant.
        effective_to = "client" if mj.client_only else install_to
        items = patch_items(md, effective_to)

        # Build the baseline: pristine + applied deps. Dependent mods'
        # patches are captured against this baseline, so we must check
        # applicability against the same thing (plain pristine would show
        # every dep-overlapping patch as STALE).
        try:
            deps = resolve_deps(p.mods_dir, cfg.workspace_major, name)
        except (ModDependencyMissing, ModDependencyCycle) as e:
            log.warn(f"{name}: dep graph broken — {e}")
            any_stale = True
            continue

        baseline_dir, cleanup_baseline = _build_baseline(
            p, name, deps, effective_to, subs
        )
        scratch = p.build / f"resync-scratch-{name}"
        empty_dir(scratch)
        try:
            stale: list[str] = []
            for it in items:
                if it.kind != "patch":
                    continue
                theirs = patched_theirs_file(baseline_dir, scratch, it.file, it.rel)
                if theirs is None:
                    stale.append(it.rel)
            if not stale:
                mj.pristine_snapshot = pristine_snapshot(baseline_dir, items)
                write_mod_json(md, mj)
                tag = " (vs pristine+deps)" if deps else ""
                log.info(f"{name}: OK ({len(items)} item(s), snapshot refreshed{tag})")
            else:
                any_stale = True
                log.warn(f"{name}: STALE — re-enter and re-capture manually")
                for s in stale:
                    log.warn(f"    - {s}")
        finally:
            if scratch.exists():
                shutil.rmtree(scratch, ignore_errors=True)
            cleanup_baseline()
    return 1 if any_stale else 0


def _state_as_manifest(state) -> "manifest_mod.InstallManifest":
    """Adapt a local-cache ModState into an InstallManifest-shaped object so
    the audit can classify files uniformly. Used only for LEGACY installs
    (pre-v2) where no install-side manifest exists yet."""
    return manifest_mod.InstallManifest(
        workspace_fingerprint=state.workspace_fingerprint or "",
        pz_version_at_install=state.pz_version or "",
        installed_at=state.installed_at or "",
        stack=[manifest_mod.ManifestStackEntry(dirname=n) for n in state.stack],
        files=[
            manifest_mod.ManifestFile(
                rel=e.rel,
                written_sha256=e.written_sha256,
                original_sha256=e.original_sha256,
                was_added=e.was_added,
                mod_origin=e.mod_origin,
            )
            for e in state.installed
        ],
    )


def _build_baseline(profile, name: str, deps: list[str],
                    install_to: str, subs: list[str]):
    """Construct a throw-away pristine+deps tree for `name`. For dep-less
    mods, returns the profile's pristine dir and a no-op cleanup."""
    if not deps:
        return profile.pristine, lambda: None
    root = profile.build / "resync-baseline" / name
    empty_dir(root)
    for sub in subs:
        mirror_tree(profile.pristine / sub, root / sub)
    result = apply_stack(
        stack=deps,
        work_dir=root,
        pristine_dir=profile.pristine,
        mods_dir=profile.mods_dir,
        scratch_root=profile.build / "resync-baseline-scratch",
        install_to=install_to,
    )
    if result.conflicts:
        # A dep's own patches are stale — surface it and fall back to pristine
        # so the outer check still runs (and will flag this mod STALE too).
        log.warn(
            f"{name}: dep baseline could not be built cleanly; "
            f"falling back to pristine for applicability check"
        )
        shutil.rmtree(root, ignore_errors=True)
        return profile.pristine, lambda: None
    return root, lambda: shutil.rmtree(root, ignore_errors=True)
