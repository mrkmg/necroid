"""doctor — read-only audit of a destination's install state.

Same audit `verify` runs, formatted as a diagnosis + suggested remediation.
Useful as the first thing to run when something looks wrong, and safe to run
at any time (never writes). Exit code 0 on clean, 1 if any issue was found.

Surfaced from the GUI as "Diagnose" on the status strip when the local
update cache flags drift.
"""
from __future__ import annotations

from pathlib import Path

from ..core import install_manifest as manifest_mod
from ..core.profile import PZ_CLASS_SUBTREES, existing_subtrees, require_pz_install
from ..core.state import read_state
from ..errors import PzVersionDetectError
from ..paths import package_dir
from ..pz.pzversion import detect_pz_version
from ..util.hashing import file_sha256


def run(args) -> int:
    p = args.profile
    install_to: str = args.install_to
    install_root = require_pz_install(p, install_to)

    state = read_state(p.state_file(install_to))
    content_dir = p.content_dir_for(install_to)

    print(f"=== doctor: {install_to}  ({content_dir}) ===\n")

    issues: list[str] = []
    hints: list[str] = []

    # --- reconciliation
    rec = manifest_mod.reconcile(
        install_root, content_dir, list(state.stack),
        probe_rels=[e.rel for e in state.installed],
    )
    print(f"manifest status: {rec.status.value}")
    if rec.message and rec.status is not manifest_mod.ReconcileStatus.CLEAN:
        for line in rec.message.splitlines():
            print(f"    {line}")

    if rec.status is manifest_mod.ReconcileStatus.WIPED:
        issues.append("local cache thinks a stack is installed but manifest is gone")
        hints.append(f"`necroid uninstall --to {install_to}` to clear local cache, "
                     f"then `necroid install ...` fresh.")
    elif rec.status is manifest_mod.ReconcileStatus.CACHE_STALE:
        issues.append("local cache stack differs from install-side manifest stack")
        hints.append("any install/uninstall command will auto-refresh the cache.")
    elif rec.status is manifest_mod.ReconcileStatus.LEGACY_UNMIGRATED:
        # Informational only — not a real issue.
        hints.append(f"`necroid install ... --to {install_to}` will seed the install-side "
                     f"manifest; until then, some audits are skipped.")

    manifest = rec.manifest

    # --- fat-jar drift (jar layout only)
    if manifest is not None:
        jar_audit = manifest_mod.audit_pz_jar(content_dir, manifest)
        if jar_audit is manifest_mod.JarAuditResult.JAR_MISSING:
            issues.append("projectzomboid.jar is missing from the install")
            hints.append("PZ may have been uninstalled or moved; reinstall PZ in Steam, "
                         f"then `necroid install ... --to {install_to}` to redeploy.")
            print("\nfat jar: MISSING")
        elif jar_audit is manifest_mod.JarAuditResult.JAR_DRIFT:
            issues.append("projectzomboid.jar hash drifted (Steam patch update)")
            hints.append(f"`necroid resync-pristine --from {install_to} --force-version-drift` "
                         f"to adopt the new jar as pristine (every mod gets flagged for "
                         f"re-capture).")
            print("\nfat jar: DRIFT  (Steam patched PZ to a new build)")
        elif jar_audit is manifest_mod.JarAuditResult.CLEAN:
            print("\nfat jar: clean (jar hash matches install-time record)")

    # --- PZ version
    try:
        detected = str(detect_pz_version(content_dir, package_dir(), p.root / "data"))
    except PzVersionDetectError as e:
        detected = None
        print(f"PZ version: (detect failed: {e})")
        issues.append("PZ version probe failed")
    else:
        print(f"PZ version (live):   {detected}")
    rec_ver = (manifest.pz_version_at_install if manifest else state.pz_version) or None
    if rec_ver:
        print(f"PZ version (at install): {rec_ver}")
        if detected and rec_ver and detected != rec_ver:
            issues.append(f"PZ version drifted since install ({rec_ver} → {detected})")
            hints.append(f"`necroid resync-pristine --from {install_to}` to rebuild pristine "
                         f"against the new PZ version.")

    # --- file audit
    if manifest is not None:
        audit = manifest_mod.audit_manifest_files(content_dir, manifest)
        buckets: dict[str, list] = {}
        for a in audit:
            buckets.setdefault(a.result.value, []).append(a)
        print(f"\nfile audit: {len(audit)} file(s)")
        for k in sorted(buckets.keys()):
            print(f"  {k}: {len(buckets[k])}")

        drifted = buckets.get("new_version_drift", []) + buckets.get("added_tampered", [])
        reverted = buckets.get("reverted_to_old_vanilla", [])
        missing = buckets.get("missing", [])
        if drifted:
            issues.append(f"{len(drifted)} file(s) rewritten by Steam or manual edit")
            hints.append(f"Steam 'Verify Integrity of Game Files' then `necroid install ... --to {install_to}` "
                         f"to re-deploy; or `necroid resync-pristine --from {install_to} --force-version-drift` "
                         f"to adopt Steam's current bytes as new pristine (every mod flagged for re-capture).")
            print("  drift details:")
            for a in drifted[:10]:
                print(f"    - {a.rel}  (mod: {a.mod_origin})")
            if len(drifted) > 10:
                print(f"    … and {len(drifted) - 10} more")
        if reverted:
            issues.append(f"{len(reverted)} file(s) reverted to vanilla (Steam Verify most likely)")
            hints.append(f"`necroid install ... --to {install_to}` to re-deploy the stack.")
        if missing:
            issues.append(f"{len(missing)} file(s) went missing from the install")
            hints.append(f"`necroid install ... --to {install_to}` to re-deploy.")

    # --- pristine drift
    pristine_drift: list[str] = []
    source = manifest.files if manifest else [
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
        issues.append(f"{len(pristine_drift)} pristine file(s) drifted since install time")
        hints.append("pristine is no longer trustworthy; `necroid resync-pristine --from <source>` to rebuild.")
        print(f"\npristine drift: {len(pristine_drift)} file(s)")
        for r in pristine_drift[:10]:
            print(f"  - {r}")
        if len(pristine_drift) > 10:
            print(f"  … and {len(pristine_drift) - 10} more")

    # --- orphan scan (skip on legacy installs — every installed file would look orphan)
    subs = existing_subtrees(p.originals) or list(PZ_CLASS_SUBTREES)
    if rec.status is manifest_mod.ReconcileStatus.LEGACY_UNMIGRATED:
        orphans: list[str] = []
    else:
        orphans = manifest_mod.scan_orphans(content_dir, p.originals, manifest, subs)
    if orphans:
        issues.append(f"{len(orphans)} orphan file(s) in the install")
        hints.append("run Steam 'Verify Integrity of Game Files' to restore vanilla, "
                     "or delete the specific files by hand.")
        print(f"\norphans: {len(orphans)} file(s)")
        for r in orphans[:10]:
            print(f"  - {r}")
        if len(orphans) > 10:
            print(f"  … and {len(orphans) - 10} more")

    # --- summary
    print()
    if not issues:
        print("diagnosis: clean — nothing to do.")
        return 0

    print(f"diagnosis: {len(issues)} issue(s) found")
    for i in issues:
        print(f"  - {i}")
    print("\nsuggested remediations:")
    # de-dupe hints preserving order
    seen: set[str] = set()
    for h in hints:
        if h not in seen:
            seen.add(h)
            print(f"  • {h}")
    return 1


class _StateFileShim:
    def __init__(self, e):
        self.rel = e.rel
        self.written_sha256 = e.written_sha256
        self.original_sha256 = e.original_sha256
        self.was_added = e.was_added
        self.mod_origin = e.mod_origin
