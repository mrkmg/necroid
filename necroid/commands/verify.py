"""verify — re-hash installed files on the chosen destination, report drift.

What `verify` covers:
    * Install-side manifest reconciliation vs the local cache
      (MISSING_MANIFEST / FINGERPRINT_MISMATCH / CACHE_STALE).
    * Per-file audit: STILL_MODDED / REVERTED_TO_OLD_VANILLA /
      NEW_VERSION_DRIFT / MISSING / ADDED_UNTOUCHED / ADDED_TAMPERED.
    * Pristine drift: does classes-original/<rel> still hash to the
      `originalSha256` we recorded at install time?
    * Orphan scan: `.class` files in the install under mod-touched subtrees
      that aren't in the manifest and aren't vanilla.
    * PZ version drift (unchanged: install version vs state's pz_version).

Exit code is 0 if everything is clean, 1 otherwise. The GUI's status strip
chip uses this signal."""
from __future__ import annotations

from pathlib import Path

from ..errors import PzVersionDetectError
from ..paths import package_dir
from ..util.hashing import file_sha256
from ..core import install_manifest as manifest_mod
from ..core.config import read_config
from ..core.profile import PZ_CLASS_SUBTREES, existing_subtrees, require_pz_install
from ..pz.pzversion import detect_pz_version
from ..core.state import read_state


def run(args) -> int:
    p = args.profile
    install_to: str = args.install_to
    require_pz_install(p, install_to)

    state = read_state(p.state_file(install_to))
    cfg = read_config(args.root, required=False)
    content_dir = p.content_dir_for(install_to)

    print(f"verify {install_to}: {content_dir}")

    # --- reconciliation (manifest <-> local cache) ---
    rec = manifest_mod.reconcile(
        content_dir, cfg.workspace_fingerprint or "", list(state.stack),
        probe_rels=[e.rel for e in state.installed],
    )
    print(f"  reconcile: {rec.status.value}")
    if rec.message and rec.status is not manifest_mod.ReconcileStatus.CLEAN:
        for line in rec.message.splitlines():
            print(f"    {line}")
    any_issue = rec.status not in (
        manifest_mod.ReconcileStatus.CLEAN,
        manifest_mod.ReconcileStatus.FIRST_TIME,
        manifest_mod.ReconcileStatus.LEGACY_UNMIGRATED,
    )

    manifest = rec.manifest
    if manifest is None and not state.installed:
        print("  nothing installed; nothing to verify.")
        return 0 if not any_issue else 1

    # --- PZ version drift ---
    try:
        detected = str(detect_pz_version(content_dir, package_dir(), p.root / "data"))
    except PzVersionDetectError as e:
        detected = None
        print(f"  (could not detect {install_to} install version: {e})")
        any_issue = True
    recorded_ver = (manifest.pz_version_at_install if manifest else state.pz_version) or None
    if detected and recorded_ver and detected != recorded_ver:
        print(f"  VERSION: installed against PZ {recorded_ver}, install is now PZ {detected} — re-install recommended")
        any_issue = True
    elif detected and recorded_ver:
        print(f"  version ok: PZ {detected}")

    # --- per-file audit ---
    if manifest is not None:
        audit = manifest_mod.audit_manifest_files(content_dir, manifest)
        counts: dict[str, int] = {}
        for a in audit:
            counts[a.result.value] = counts.get(a.result.value, 0) + 1
        print(f"  files: {len(audit)}  " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        for a in audit:
            tag = a.result.value
            if tag in ("still_modded", "added_untouched"):
                continue
            print(f"  [{tag}] {a.rel}  (mod: {a.mod_origin})")
            any_issue = True
    else:
        # Legacy: no manifest, but local state has entries. Verify from state.
        if state.installed:
            print(f"  files: {len(state.installed)}  (legacy — no install-side manifest yet)")
            for e in state.installed:
                actual = file_sha256(content_dir / e.rel)
                if actual is None:
                    print(f"  MISSING: {e.rel}")
                    any_issue = True
                elif actual != (e.written_sha256 or "").upper():
                    print(f"  DRIFT:   {e.rel}")
                    any_issue = True

    # --- pristine drift (classes-original/ vs original_sha256) ---
    pristine_drift: list[str] = []
    source = manifest.files if manifest else [
        # Adapt state entries to look manifest-ish for this check.
        _StateFileShim(e) for e in state.installed
    ]
    for f in source:
        if f.was_added or not f.original_sha256:
            continue
        orig_path = p.originals / Path(f.rel)
        if not orig_path.exists():
            pristine_drift.append(f"{f.rel}  (classes-original/ missing)")
            continue
        live = file_sha256(orig_path)
        if live != (f.original_sha256 or "").upper():
            pristine_drift.append(f"{f.rel}  (hash diverged)")
    if pristine_drift:
        any_issue = True
        print(f"  PRISTINE DRIFT: {len(pristine_drift)} file(s) in classes-original/ no longer match recorded hashes:")
        for r in pristine_drift[:10]:
            print(f"    - {r}")
        if len(pristine_drift) > 10:
            print(f"    … and {len(pristine_drift) - 10} more")

    # --- orphan scan ---
    # Skip this on legacy-unmigrated installs — every installed file will look
    # orphan until the first install/uninstall seeds the manifest.
    subs = existing_subtrees(p.originals) or list(PZ_CLASS_SUBTREES)
    if rec.status is manifest_mod.ReconcileStatus.LEGACY_UNMIGRATED:
        orphans: list[str] = []
        print("  (orphan scan skipped — legacy install; run an install/uninstall to migrate.)")
    else:
        orphans = manifest_mod.scan_orphans(content_dir, p.originals, manifest, subs)
    if orphans:
        any_issue = True
        print(f"  ORPHANS: {len(orphans)} unmanaged file(s) in the install:")
        for r in orphans[:10]:
            print(f"    - {r}")
        if len(orphans) > 10:
            print(f"    … and {len(orphans) - 10} more")

    if not any_issue:
        print("  all clean.")
        return 0

    print(f"\n  Issues found. Run `necroid doctor --to {install_to}` for a diagnosis + remediation hints.")
    return 1


class _StateFileShim:
    """Duck-type a local-cache InstalledEntry as a ManifestFile (only the
    attributes the pristine-drift check touches). Lets us treat legacy (no-manifest)
    installs the same way as modern ones for that check."""

    def __init__(self, e):
        self.rel = e.rel
        self.written_sha256 = e.written_sha256
        self.original_sha256 = e.original_sha256
        self.was_added = e.was_added
        self.mod_origin = e.mod_origin
