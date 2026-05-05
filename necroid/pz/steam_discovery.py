"""Cross-platform discovery of Project Zomboid installs via Steam.

Detection prefers Steam's authoritative `appmanifest_<appid>.acf` files (one
per library where the app is installed). Each candidate is fingerprinted to
confirm it actually looks like a PZ install — moved/uninstalled apps often
leave stale dirs under `steamapps/common/`. Callers can pull the full
candidate list (with provenance, fingerprint, last-played time) for UI
selection, or use the back-compat shortcut that returns just the best path.

Order of operations:
    1. Locate candidate Steam roots (OS-specific).
    2. For each root, parse `<root>/steamapps/libraryfolders.vdf` to
       enumerate every library folder.
    3. For each library, read `appmanifest_<appid>.acf` if present and resolve
       `<library>/steamapps/common/<installdir>`. If absent, fall back to a
       directory probe with the conventional dir name.
    4. Fingerprint each candidate (PZ launcher / fat jar / loose `zombie/`).
    5. Sort: appmanifest > directory-probe; fingerprint-ok first; lastPlayed
       desc; path string.

Stdlib only. `winreg` is imported lazily (Windows only).
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from ..util import logging_util as log


STEAM_APP_ID_CLIENT = "108600"
STEAM_APP_ID_SERVER = "380870"
PZ_CLIENT_DIR_NAME = "ProjectZomboid"
PZ_SERVER_DIR_NAME = "Project Zomboid Dedicated Server"


@dataclass(frozen=True)
class PzCandidate:
    """A possible PZ install location with provenance.

    `source` is "appmanifest" (Steam authoritatively says the app is here),
    "directory-probe" (we found a matching dir but no manifest), or "manual"
    (user-specified). `fingerprint_ok` confirms the path actually contains
    PZ files — stale post-move directories will be False.
    """
    path: Path
    steam_root: Path | None
    library: Path | None
    source: str
    fingerprint_ok: bool
    last_played: int | None = None


# --------------------------------------------------------------------------- #
# Steam root detection
# --------------------------------------------------------------------------- #

def _windows_steam_roots() -> list[Path]:
    roots: list[Path] = []
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        winreg = None  # type: ignore[assignment]

    if winreg is not None:
        # HKCU\Software\Valve\Steam → SteamPath (forward slashes on Windows)
        for hive, key_path, value_name in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        ):
            try:
                with winreg.OpenKey(hive, key_path) as k:
                    val, _ = winreg.QueryValueEx(k, value_name)
                    if val:
                        roots.append(Path(val))
            except OSError:
                continue

    roots.extend([
        Path(r"C:\Program Files (x86)\Steam"),
        Path(r"C:\Program Files\Steam"),
    ])
    return roots


def _linux_steam_roots() -> list[Path]:
    home = Path(os.path.expanduser("~"))
    return [
        home / ".steam" / "steam",
        home / ".local" / "share" / "Steam",
        home / ".steam" / "root",
        home / ".steam" / "debian-installation",
        home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam",
    ]


def _darwin_steam_roots() -> list[Path]:
    home = Path(os.path.expanduser("~"))
    return [home / "Library" / "Application Support" / "Steam"]


def discover_steam_roots() -> list[Path]:
    """Candidate Steam install roots for the current OS that actually exist
    and contain a `steamapps/` subdir. De-duplicated, resolved."""
    if sys.platform == "win32":
        candidates = _windows_steam_roots()
    elif sys.platform == "darwin":
        candidates = _darwin_steam_roots()
    else:
        # Treat every non-Windows, non-Mac platform as Linux-like.
        candidates = _linux_steam_roots()

    seen: set[Path] = set()
    out: list[Path] = []
    for c in candidates:
        try:
            resolved = c.resolve()
        except (OSError, RuntimeError):
            continue
        if resolved in seen:
            continue
        if (resolved / "steamapps").is_dir():
            seen.add(resolved)
            out.append(resolved)
    return out


# --------------------------------------------------------------------------- #
# libraryfolders.vdf parsing
# --------------------------------------------------------------------------- #

_PATH_RE = re.compile(r'"path"\s*"([^"]+)"', re.IGNORECASE)


def parse_library_folders(steam_root: Path) -> list[Path]:
    """Return all Steam library roots declared in
    `<steam_root>/steamapps/libraryfolders.vdf`, including `steam_root` itself.
    """
    libraries: list[Path] = [steam_root]
    vdf = steam_root / "steamapps" / "libraryfolders.vdf"
    if not vdf.is_file():
        return libraries

    try:
        text = vdf.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        log.warn(f"could not read {vdf}: {e}")
        return libraries

    for raw in _PATH_RE.findall(text):
        # VDF escapes backslashes as "\\". Unescape before constructing Path.
        unescaped = raw.replace("\\\\", "\\")
        p = Path(unescaped)
        if (p / "steamapps").is_dir():
            try:
                libraries.append(p.resolve())
            except (OSError, RuntimeError):
                continue

    seen: set[Path] = set()
    out: list[Path] = []
    for lib in libraries:
        if lib not in seen:
            seen.add(lib)
            out.append(lib)
    return out


# --------------------------------------------------------------------------- #
# appmanifest_<id>.acf parsing
# --------------------------------------------------------------------------- #

_ACF_INSTALLDIR_RE = re.compile(r'"installdir"\s*"([^"]+)"', re.IGNORECASE)
_ACF_LASTPLAYED_RE = re.compile(r'"LastPlayed"\s*"(\d+)"')


def _read_appmanifest(library: Path, app_id: str) -> tuple[str, int | None] | None:
    """Return (installdir, last_played_epoch) from `appmanifest_<app_id>.acf`
    in the given library, or None if the manifest is missing/malformed."""
    acf = library / "steamapps" / f"appmanifest_{app_id}.acf"
    if not acf.is_file():
        return None
    try:
        text = acf.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        log.warn(f"could not read {acf}: {e}")
        return None
    m = _ACF_INSTALLDIR_RE.search(text)
    if not m:
        return None
    installdir = m.group(1).replace("\\\\", "\\")
    lp_match = _ACF_LASTPLAYED_RE.search(text)
    last_played = int(lp_match.group(1)) if lp_match else None
    return (installdir, last_played)


# --------------------------------------------------------------------------- #
# Fingerprinting
# --------------------------------------------------------------------------- #

def fingerprint_pz_install(path: Path) -> bool:
    """True if `path` looks like a real PZ install (client or server).

    Accepts any of: fat jar (PZ 42+), loose `zombie/` class tree (PZ ≤41),
    or one of the platform launchers. Stale leftover directories from a
    Steam move/uninstall typically have none of these.
    """
    if not path.is_dir():
        return False
    markers = (
        "projectzomboid.jar",
        "ProjectZomboid64.exe",
        "ProjectZomboid32.exe",
        "ProjectZomboidServer.exe",
        "projectzomboid64.sh",
        "projectzomboid32.sh",
        "ProjectZomboid64",
        "ProjectZomboid32",
        "start-server.sh",
    )
    for name in markers:
        if (path / name).exists():
            return True
    if (path / "zombie").is_dir():
        return True
    return False


# --------------------------------------------------------------------------- #
# Candidate enumeration
# --------------------------------------------------------------------------- #

def discover_pz_install_candidates(app_id: str, dir_name: str) -> list[PzCandidate]:
    """Return every plausible PZ install for the given app, sorted best-first.

    Iterates Steam roots × libraries: prefers `appmanifest_<id>.acf` matches;
    falls back to direct directory probes when no manifest is present in any
    library. Every candidate is fingerprinted; ranking is appmanifest >
    probe, fingerprint-ok > not, lastPlayed desc, then path string.
    """
    seen_paths: set[Path] = set()
    out: list[PzCandidate] = []
    found_any_manifest = False

    for root in discover_steam_roots():
        for lib in parse_library_folders(root):
            mf = _read_appmanifest(lib, app_id)
            if mf is not None:
                installdir, last_played = mf
                cand_path = (lib / "steamapps" / "common" / installdir)
                try:
                    cand_path = cand_path.resolve()
                except (OSError, RuntimeError):
                    pass
                if cand_path in seen_paths:
                    continue
                seen_paths.add(cand_path)
                out.append(PzCandidate(
                    path=cand_path,
                    steam_root=root,
                    library=lib,
                    source="appmanifest",
                    fingerprint_ok=fingerprint_pz_install(cand_path),
                    last_played=last_played,
                ))
                found_any_manifest = True

    # Directory probe fallback — only when no manifest was found anywhere.
    # If Steam authoritatively places the app in library X, a stale dir at
    # library Y should not poison results.
    if not found_any_manifest:
        for root in discover_steam_roots():
            for lib in parse_library_folders(root):
                cand_path = lib / "steamapps" / "common" / dir_name
                if not cand_path.exists():
                    continue
                try:
                    resolved = cand_path.resolve()
                except (OSError, RuntimeError):
                    resolved = cand_path
                if resolved in seen_paths:
                    continue
                seen_paths.add(resolved)
                out.append(PzCandidate(
                    path=resolved,
                    steam_root=root,
                    library=lib,
                    source="directory-probe",
                    fingerprint_ok=fingerprint_pz_install(resolved),
                    last_played=None,
                ))

    def sort_key(c: PzCandidate) -> tuple:
        return (
            0 if c.source == "appmanifest" else 1,
            0 if c.fingerprint_ok else 1,
            -(c.last_played or 0),
            str(c.path),
        )

    out.sort(key=sort_key)
    return out


def discover_client_install_candidates() -> list[PzCandidate]:
    return discover_pz_install_candidates(STEAM_APP_ID_CLIENT, PZ_CLIENT_DIR_NAME)


def discover_server_install_candidates() -> list[PzCandidate]:
    return discover_pz_install_candidates(STEAM_APP_ID_SERVER, PZ_SERVER_DIR_NAME)


def _log_candidates(label: str, candidates: list[PzCandidate], picked: PzCandidate | None) -> None:
    if not candidates:
        log.info(f"steam discovery: no Steam installation detected for {label}")
        return
    log.info(f"steam discovery: {label} — {len(candidates)} candidate(s)")
    for c in candidates:
        marker = " <-- picked" if (picked is not None and c.path == picked.path) else ""
        fp = "ok" if c.fingerprint_ok else "no PZ files found"
        log.info(f"  [{c.source}] {c.path} ({fp}){marker}")


def discover_pz_install(dir_name: str, label: str, app_id: str | None = None) -> Path | None:
    """Back-compat: return the best candidate's path, or None.

    Prefers appmanifest matches with passing fingerprints. Falls back to a
    fingerprint-failing candidate only if no better option exists (so that
    a user-confirmed-but-quirky install still resolves)."""
    if app_id is None:
        app_id = STEAM_APP_ID_CLIENT if dir_name == PZ_CLIENT_DIR_NAME else STEAM_APP_ID_SERVER
    candidates = discover_pz_install_candidates(app_id, dir_name)
    picked: PzCandidate | None = None
    for c in candidates:
        if c.fingerprint_ok:
            picked = c
            break
    if picked is None and candidates:
        picked = candidates[0]
    _log_candidates(label, candidates, picked)
    return picked.path if picked else None


def discover_client_install() -> Path | None:
    return discover_pz_install(PZ_CLIENT_DIR_NAME, "Project Zomboid (client)", STEAM_APP_ID_CLIENT)


def discover_server_install() -> Path | None:
    return discover_pz_install(PZ_SERVER_DIR_NAME, "Project Zomboid Dedicated Server", STEAM_APP_ID_SERVER)
