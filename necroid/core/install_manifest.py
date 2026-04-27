"""Install-side manifest — authoritative record of what Necroid has done to
a PZ install.

Lives at `<pz_install>/necroid/install-manifest.json` for both client and
server (per-install — server's PZ install root is the dedicated server's dir).
The manifest sits at the install ROOT, not the content dir, so all of
Necroid's per-install state lands under one tidy `necroid/` subdir.

Why it lives on the install side (not just in the workspace dir):
    * Steam "Verify Integrity of Game Files" or a patch update can rewrite
      class files out from under us. The local cache still claims "mod X is
      installed" — the manifest lets us detect the discrepancy.
    * A PZ reinstall wipes the whole directory, including our manifest. Local
      cache thinking "installed" + no manifest on disk is the unambiguous
      signal that the install was wiped.
    * A user manually patching a class file outside Necroid leaves files
      that are in neither the manifest nor `classes-original/` — the
      orphan-scan picks those up.

Schema v1:
    {
      "schemaVersion": 1,
      "workspace": {
        "workspaceDir": "...",
        "workspaceMajor": 41,
        "workspaceLayout": "loose" | "jar"
      },
      "destination": "client" | "server",
      "pzVersionAtInstall": "41.78.19",
      "pzJarSha256": "<hex>"|"" (jar layout only),
      "installedAt": "<ISO UTC>",
      "stack": [{"dirname": "admin-xray-41", "version": "0.3.1"}, ...],
      "files": [
        {"rel": "zombie/Foo.class",
         "writtenSha256": "...",
         "originalSha256": "..."|null,
         "wasAdded": false,
         "modOrigin": "admin-xray-41"}
      ]
    }
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from ..errors import (
    InstallManifestTampered,
    OrphanInstalledFile,
)
from ..util.hashing import file_sha256

MANIFEST_SCHEMA_VERSION = 1
MANIFEST_DIRNAME = "necroid"
MANIFEST_FILENAME = "install-manifest.json"


# ---------------------------------------------------------------------------
# dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ManifestFile:
    rel: str
    written_sha256: str
    original_sha256: str | None
    was_added: bool
    mod_origin: str

    def to_json(self) -> dict:
        return {
            "rel": self.rel,
            "writtenSha256": self.written_sha256,
            "originalSha256": self.original_sha256,
            "wasAdded": bool(self.was_added),
            "modOrigin": self.mod_origin,
        }

    @staticmethod
    def from_json(o: dict) -> "ManifestFile":
        orig = o.get("originalSha256")
        return ManifestFile(
            rel=str(o["rel"]),
            written_sha256=str(o.get("writtenSha256") or o.get("sha256") or ""),
            original_sha256=str(orig) if orig else None,
            was_added=bool(o.get("wasAdded", False)),
            mod_origin=str(o.get("modOrigin", "")),
        )


@dataclass
class ManifestStackEntry:
    dirname: str
    version: str = ""

    def to_json(self) -> dict:
        return {"dirname": self.dirname, "version": self.version}

    @staticmethod
    def from_json(o: dict) -> "ManifestStackEntry":
        return ManifestStackEntry(
            dirname=str(o["dirname"]),
            version=str(o.get("version", "")),
        )


@dataclass
class InstallManifest:
    schema_version: int = MANIFEST_SCHEMA_VERSION
    workspace_dir: str = ""
    workspace_major: int = 0
    workspace_layout: str = "loose"          # "loose" or "jar"
    destination: str = "client"
    pz_version_at_install: str = ""
    pz_jar_sha256: str = ""                  # jar-layout only; "" for loose installs
    installed_at: str = ""
    stack: list[ManifestStackEntry] = field(default_factory=list)
    files: list[ManifestFile] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "schemaVersion": self.schema_version,
            "workspace": {
                "workspaceDir": self.workspace_dir,
                "workspaceMajor": int(self.workspace_major),
                "workspaceLayout": self.workspace_layout or "loose",
            },
            "destination": self.destination,
            "pzVersionAtInstall": self.pz_version_at_install,
            "pzJarSha256": self.pz_jar_sha256 or "",
            "installedAt": self.installed_at,
            "stack": [e.to_json() for e in self.stack],
            "files": [f.to_json() for f in self.files],
        }

    @staticmethod
    def from_json(o: dict) -> "InstallManifest":
        ws = o.get("workspace") or {}
        return InstallManifest(
            schema_version=int(o.get("schemaVersion", 1)),
            workspace_dir=str(ws.get("workspaceDir", "") or ""),
            workspace_major=int(ws.get("workspaceMajor", 0) or 0),
            workspace_layout=str(ws.get("workspaceLayout", "") or "loose"),
            destination=str(o.get("destination", "client")),
            pz_version_at_install=str(o.get("pzVersionAtInstall", "") or ""),
            pz_jar_sha256=str(o.get("pzJarSha256", "") or ""),
            installed_at=str(o.get("installedAt", "") or ""),
            stack=[ManifestStackEntry.from_json(e) for e in (o.get("stack") or [])],
            files=[ManifestFile.from_json(f) for f in (o.get("files") or [])],
        )


# ---------------------------------------------------------------------------
# read / write / delete
# ---------------------------------------------------------------------------

def manifest_dir(install_root: Path) -> Path:
    return install_root / MANIFEST_DIRNAME


def manifest_path(install_root: Path) -> Path:
    """Path to the install manifest. `install_root` is the PZ install ROOT
    (not the content_dir — both client and server keep their manifest under
    `<install>/necroid/install-manifest.json`)."""
    return manifest_dir(install_root) / MANIFEST_FILENAME


def read_manifest(install_root: Path) -> InstallManifest | None:
    """Return the parsed manifest, or None if the file doesn't exist. Raises
    `InstallManifestTampered` if the file exists but is unreadable / malformed
    / an unsupported schema version."""
    p = manifest_path(install_root)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise InstallManifestTampered(
            f"cannot read install manifest at {p}: {e}\n"
            f"    it may have been corrupted. Inspect by hand, then either restore "
            f"from backup or run `necroid uninstall` to clear state."
        )
    ver = int(raw.get("schemaVersion", 1) or 1)
    if ver > MANIFEST_SCHEMA_VERSION:
        raise InstallManifestTampered(
            f"install manifest at {p} is schema v{ver}; this Necroid only understands "
            f"up to v{MANIFEST_SCHEMA_VERSION}. Upgrade Necroid or downgrade the install."
        )
    return InstallManifest.from_json(raw)


def write_manifest(install_root: Path, manifest: InstallManifest) -> Path:
    """Atomic write: `<path>.new` + rename (so a crash mid-write doesn't
    leave a half-written authoritative record)."""
    p = manifest_path(install_root)
    tmp = p.with_suffix(p.suffix + ".new")
    p.parent.mkdir(parents=True, exist_ok=True)
    manifest.schema_version = MANIFEST_SCHEMA_VERSION
    tmp.write_text(json.dumps(manifest.to_json(), indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return p


def delete_manifest(install_root: Path) -> bool:
    p = manifest_path(install_root)
    if p.exists():
        try:
            p.unlink()
            return True
        except OSError:
            return False
    return False


# ---------------------------------------------------------------------------
# reconciliation
# ---------------------------------------------------------------------------

class ReconcileStatus(str, Enum):
    CLEAN = "clean"
    """Manifest and local cache agree, no action needed."""

    FIRST_TIME = "first_time"
    """No manifest, no cache — never installed to this destination."""

    WIPED = "wiped"
    """Cache says something was installed, manifest is gone — PZ install was
    wiped or reinstalled. Local cache should be cleared; files are already
    vanilla (whatever Steam put there)."""

    LEGACY_UNMIGRATED = "legacy_unmigrated"
    """Cache says something is installed, manifest is gone, but the state's
    recorded class files are still on disk in the install. Either a pre-v2
    install that never had a manifest, or a workspace migrated from the
    old layout. The next install or uninstall will seed a manifest."""

    CACHE_STALE = "cache_stale"
    """Manifest exists and matches this workspace, but the local cache is
    either missing or out of sync. Refresh the cache from the manifest."""

    TAMPERED = "tampered"
    """Manifest unreadable / malformed. Raised as exception from `read_manifest`
    rather than returned here, but kept in the enum for completeness."""


@dataclass
class Reconciliation:
    status: ReconcileStatus
    manifest: InstallManifest | None
    message: str = ""


def reconcile(install_root: Path, content_dir: Path, local_stack: list[str],
              *, probe_rels: list[str] | None = None) -> Reconciliation:
    """Compare the install-side manifest against what this workspace's local
    cache thinks is installed. Never raises except on truly corrupted manifest
    (delegated to `read_manifest`).

    `install_root` is the PZ install dir; `content_dir` is where class files
    live (PZ install root for client, `<server>/java/` for server) and is
    used only for the legacy probe (do recorded files still exist?).

    `probe_rels` is an optional list of relative paths (typically from
    `ModState.installed`) used to distinguish a truly-wiped install from a
    legacy install whose files are still there but never had a manifest
    written. If any probed path exists, LEGACY_UNMIGRATED is returned instead
    of WIPED.
    """
    manifest = read_manifest(install_root)

    if manifest is None:
        if local_stack:
            if probe_rels and any((content_dir / r).exists() for r in probe_rels[:8]):
                return Reconciliation(
                    status=ReconcileStatus.LEGACY_UNMIGRATED,
                    manifest=None,
                    message=(
                        "install predates the install-side manifest. "
                        "Next install/uninstall will seed one."
                    ),
                )
            return Reconciliation(
                status=ReconcileStatus.WIPED,
                manifest=None,
                message=(
                    f"local cache says stack {local_stack!r} is installed, but the "
                    f"install-side manifest is missing. The PZ install was wiped "
                    f"or reinstalled — local cache will be cleared."
                ),
            )
        return Reconciliation(
            status=ReconcileStatus.FIRST_TIME,
            manifest=None,
            message="no install manifest and no local cache — clean install destination.",
        )

    # Manifest's stack diverges from local cache's stack → cache is stale.
    manifest_stack = [e.dirname for e in manifest.stack]
    if manifest_stack != list(local_stack):
        return Reconciliation(
            status=ReconcileStatus.CACHE_STALE,
            manifest=manifest,
            message=(
                f"local cache stack {local_stack!r} differs from install-side manifest "
                f"stack {manifest_stack!r}. Manifest wins; local cache will be refreshed."
            ),
        )

    return Reconciliation(
        status=ReconcileStatus.CLEAN,
        manifest=manifest,
        message="manifest and local cache agree.",
    )


# ---------------------------------------------------------------------------
# per-file audit
# ---------------------------------------------------------------------------

class FileAuditResult(str, Enum):
    STILL_MODDED = "still_modded"
    """Live hash == writtenSha256. File is still what Necroid put there."""

    REVERTED_TO_OLD_VANILLA = "reverted_to_old_vanilla"
    """Live hash == originalSha256. Something (Steam verify most likely)
    restored the pre-install vanilla. Safe to unmark — no restore needed."""

    NEW_VERSION_DRIFT = "new_version_drift"
    """Live hash is neither written nor original. Steam rewrote with a
    different version's vanilla (patch update) OR the user hand-edited it
    OR another tool touched it. Dangerous for resync."""

    MISSING = "missing"
    """File was tracked but no longer exists in the install."""

    ADDED_UNTOUCHED = "added_untouched"
    """A mod-added file that's still at writtenSha256 (a normal sub-case of
    STILL_MODDED, kept separate so the reporter can be clearer)."""

    ADDED_TAMPERED = "added_tampered"
    """A mod-added file whose bytes no longer match writtenSha256. No original
    exists, so we can't classify further — user hand-edited, or another mod."""


@dataclass
class FileAudit:
    rel: str
    result: FileAuditResult
    mod_origin: str
    live_sha256: str | None


def audit_manifest_files(content_dir: Path, manifest: InstallManifest) -> list[FileAudit]:
    """Hash every file the manifest claims we installed and classify. Cheap
    relative to anything javac does; runs top-to-bottom without short-circuit."""
    results: list[FileAudit] = []
    for f in manifest.files:
        live = file_sha256(content_dir / f.rel)
        if live is None:
            results.append(FileAudit(f.rel, FileAuditResult.MISSING, f.mod_origin, None))
            continue
        written = (f.written_sha256 or "").upper()
        orig = (f.original_sha256 or "").upper() if f.original_sha256 else None
        if live == written:
            results.append(FileAudit(
                f.rel,
                FileAuditResult.ADDED_UNTOUCHED if f.was_added else FileAuditResult.STILL_MODDED,
                f.mod_origin,
                live,
            ))
            continue
        if orig and live == orig:
            results.append(FileAudit(
                f.rel, FileAuditResult.REVERTED_TO_OLD_VANILLA, f.mod_origin, live
            ))
            continue
        results.append(FileAudit(
            f.rel,
            FileAuditResult.ADDED_TAMPERED if f.was_added else FileAuditResult.NEW_VERSION_DRIFT,
            f.mod_origin,
            live,
        ))
    return results


# ---------------------------------------------------------------------------
# fat-jar drift (jar layout only)
# ---------------------------------------------------------------------------

class JarAuditResult(str, Enum):
    NOT_TRACKED = "not_tracked"
    """Loose layout, or no jar sha was recorded at install time. No-op."""

    CLEAN = "clean"
    """Live `projectzomboid.jar` hash matches the recorded sha — install is
    pinned to the same PZ build it was installed against."""

    JAR_MISSING = "jar_missing"
    """The jar was tracked but no longer exists in the install — PZ was
    uninstalled / reinstalled, or the path moved."""

    JAR_DRIFT = "jar_drift"
    """Jar exists but its hash differs from what was recorded at install
    time. Steam patch update is the typical cause; the install's vanilla
    classes are now a different version than the workspace's pristine."""


def audit_pz_jar(content_dir: Path, manifest: InstallManifest) -> JarAuditResult:
    """Compare the live `projectzomboid.jar` hash to the manifest's record."""
    if (manifest.workspace_layout or "loose") != "jar":
        return JarAuditResult.NOT_TRACKED
    if not manifest.pz_jar_sha256:
        return JarAuditResult.NOT_TRACKED
    jar_path = content_dir / "projectzomboid.jar"
    if not jar_path.is_file():
        return JarAuditResult.JAR_MISSING
    live = (file_sha256(jar_path) or "").upper()
    recorded = (manifest.pz_jar_sha256 or "").upper()
    if live != recorded:
        return JarAuditResult.JAR_DRIFT
    return JarAuditResult.CLEAN


# ---------------------------------------------------------------------------
# orphan scan
# ---------------------------------------------------------------------------

def scan_orphans(content_dir: Path, originals_dir: Path, manifest: InstallManifest | None,
                 subtrees: list[str]) -> list[str]:
    """Walk the mod-touched class subtrees under the install. A file is an
    "orphan" if: it's a `.class` under one of `subtrees`, it's not listed in
    `manifest.files`, and its hash differs from `originals_dir/<rel>` (or no
    original exists). Returns forward-slash relative paths.
    """
    if not content_dir.exists():
        return []
    known = {f.rel for f in (manifest.files if manifest else [])}
    orphans: list[str] = []
    for sub in subtrees:
        root = content_dir / sub
        if not root.exists():
            continue
        for dirpath, _dirs, files in os.walk(root):
            dp = Path(dirpath)
            rel_root = dp.relative_to(content_dir)
            for fname in files:
                if not fname.endswith(".class"):
                    continue
                rel = (rel_root / fname).as_posix()
                if rel in known:
                    continue
                orig_path = originals_dir / rel
                if orig_path.exists():
                    live = file_sha256(dp / fname)
                    orig = file_sha256(orig_path)
                    if live == orig:
                        continue  # identical to vanilla — not orphan
                orphans.append(rel)
    return orphans


# ---------------------------------------------------------------------------
# helpers used by installers / commands
# ---------------------------------------------------------------------------

def raise_if_orphans(orphans: list[str], *, context: str) -> None:
    """Helper for resync_pristine: abort if the install carries untracked
    class files that aren't vanilla."""
    if not orphans:
        return
    lines = "\n".join(f"    - {r}" for r in orphans[:20])
    more = f"\n    … and {len(orphans) - 20} more" if len(orphans) > 20 else ""
    raise OrphanInstalledFile(
        f"{context}: {len(orphans)} file(s) exist under mod-touched subtrees "
        f"but are in neither the install manifest nor `classes-original/`:\n"
        f"{lines}{more}\n"
        f"    Run `necroid doctor` to inspect, then either delete them by hand "
        f"or run Steam's 'Verify Integrity of Game Files' before retrying."
    )
