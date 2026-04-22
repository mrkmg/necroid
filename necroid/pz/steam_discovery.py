"""Cross-platform discovery of Project Zomboid installs via Steam.

Order of operations for each entry point:
    1. Locate candidate Steam roots (OS-specific).
    2. For each root, parse `<root>/steamapps/libraryfolders.vdf` to get every
       Steam library folder the user has configured.
    3. Probe `<library>/steamapps/common/<PZ dir>` for the app.

Stdlib only. `winreg` is imported lazily (Windows only).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from ..util import logging_util as log


STEAM_APP_ID_CLIENT = "108600"
STEAM_APP_ID_SERVER = "380870"
PZ_CLIENT_DIR_NAME = "ProjectZomboid"
PZ_SERVER_DIR_NAME = "Project Zomboid Dedicated Server"


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

    Valve's KeyValues format is structurally nested, but for this file we
    only need the top-level `"path"` value of each entry. A regex over the
    whole file is sufficient — the file is small and the key name is unique.
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
            libraries.append(p.resolve())

    # De-dup while preserving order.
    seen: set[Path] = set()
    out: list[Path] = []
    for lib in libraries:
        if lib not in seen:
            seen.add(lib)
            out.append(lib)
    return out


# --------------------------------------------------------------------------- #
# App discovery
# --------------------------------------------------------------------------- #

def _iter_candidate_installs(dir_name: str) -> list[tuple[Path, Path]]:
    """Yield (steam_root, candidate_app_path) pairs across all detected
    Steam roots and their library folders."""
    pairs: list[tuple[Path, Path]] = []
    for root in discover_steam_roots():
        for lib in parse_library_folders(root):
            pairs.append((root, lib / "steamapps" / "common" / dir_name))
    return pairs


def discover_pz_install(dir_name: str, label: str) -> Path | None:
    """Probe every Steam root × library for `<lib>/steamapps/common/<dir_name>`.
    Logs a short trace so non-standard installs are easy to diagnose."""
    pairs = _iter_candidate_installs(dir_name)
    if not pairs:
        log.info(f"steam discovery: no Steam installation detected for {label}")
        return None
    for _root, candidate in pairs:
        if candidate.exists():
            log.info(f"steam discovery: found {label} at {candidate}")
            return candidate.resolve()
    log.info(
        f"steam discovery: {label} not found in any Steam library "
        f"(checked {len(pairs)} location(s))"
    )
    return None


def discover_client_install() -> Path | None:
    return discover_pz_install(PZ_CLIENT_DIR_NAME, "Project Zomboid (client)")


def discover_server_install() -> Path | None:
    return discover_pz_install(PZ_SERVER_DIR_NAME, "Project Zomboid Dedicated Server")
