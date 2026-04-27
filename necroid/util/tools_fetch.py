"""Auto-fetch portable JDK + Git into `data/tools/` when the system has none.

Mirrors the existing Vineflower auto-download pattern (see
`necroid/build/decompile.py:ensure_vineflower`). Stdlib only.

- JDK: Eclipse Temurin via the Adoptium API. Works on Windows, macOS, Linux.
- Git: MinGit on Windows only. macOS/Linux fall back to `ToolMissing` install
  hints (no first-party portable git distribution exists).

Opt-out: set `NECROID_NO_AUTO_FETCH=1`.
"""
from __future__ import annotations

import hashlib
import os
import platform
import shutil
import sys
import tarfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from . import logging_util as log


# Pinned MinGit (Git for Windows portable). Bump deliberately.
_MINGIT_VERSION = "2.46.0"
_MINGIT_TAG = f"v{_MINGIT_VERSION}.windows.1"
_MINGIT_URL = (
    f"https://github.com/git-for-windows/git/releases/download/"
    f"{_MINGIT_TAG}/MinGit-{_MINGIT_VERSION}-64-bit.zip"
)

_USER_AGENT = "necroid-auto-fetch/1.0"


def auto_fetch_disabled() -> bool:
    return os.environ.get("NECROID_NO_AUTO_FETCH") == "1"


# ----- JDK ---------------------------------------------------------------

def _adoptium_os() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "mac"
    return "linux"


def _adoptium_arch() -> str:
    m = platform.machine().lower()
    if m in ("amd64", "x86_64", "x64"):
        return "x64"
    if m in ("arm64", "aarch64"):
        return "aarch64"
    raise RuntimeError(f"unsupported architecture for portable JDK fetch: {m}")


def _adoptium_archive_ext() -> str:
    return "zip" if sys.platform == "win32" else "tar.gz"


def ensure_portable_jdk(tools_dir: Path, major: int) -> Path | None:
    """Download + extract a Temurin JDK for `major` into `tools_dir/jdk-<major>/`.

    Returns the directory containing the extracted JDK (a parent of one or more
    `jdk-X.Y.Z+B/` entries). Callers pass it as an `extra_roots` entry to
    `_discover_jdk_binaries`, which already walks one level to find `bin/`.

    Returns None when auto-fetch is disabled, or when the platform isn't
    supported (the caller falls back to `ToolMissing`).
    """
    if auto_fetch_disabled():
        return None
    target = tools_dir / f"jdk-{int(major)}"
    if target.is_dir() and any(target.iterdir()):
        return target

    try:
        os_name = _adoptium_os()
        arch = _adoptium_arch()
    except RuntimeError as e:
        log.warn(str(e))
        return None
    ext = _adoptium_archive_ext()

    binary_url = (
        f"https://api.adoptium.net/v3/binary/latest/{int(major)}/ga/"
        f"{os_name}/{arch}/jdk/hotspot/normal/eclipse"
    )
    checksum_url = (
        f"https://api.adoptium.net/v3/checksum/latest/{int(major)}/ga/"
        f"{os_name}/{arch}/jdk/hotspot/normal/eclipse"
    )

    tools_dir.mkdir(parents=True, exist_ok=True)
    tmp_archive = tools_dir / f"jdk-{int(major)}.{ext}.tmp"
    tmp_extract = tools_dir / f"jdk-{int(major)}.extract.tmp"
    if tmp_archive.exists():
        tmp_archive.unlink()
    if tmp_extract.exists():
        shutil.rmtree(tmp_extract)

    log.info(f"downloading portable JDK {major} from Adoptium ({os_name}/{arch})")
    try:
        _download(binary_url, tmp_archive)
        expected_sha = _fetch_checksum(checksum_url)
        if expected_sha:
            actual = _sha256(tmp_archive)
            if actual != expected_sha:
                raise RuntimeError(
                    f"JDK download SHA mismatch: got {actual}, expected {expected_sha}"
                )
        log.info(f"extracting -> {target}")
        tmp_extract.mkdir(parents=True, exist_ok=True)
        if ext == "zip":
            with zipfile.ZipFile(tmp_archive) as zf:
                zf.extractall(tmp_extract)
        else:
            with tarfile.open(tmp_archive, "r:gz") as tf:
                _safe_tar_extract(tf, tmp_extract)
        # Atomic publish.
        if target.exists():
            shutil.rmtree(target)
        tmp_extract.replace(target)
    except (urllib.error.URLError, OSError, RuntimeError) as e:
        log.warn(f"portable JDK fetch failed: {e}")
        if tmp_extract.exists():
            shutil.rmtree(tmp_extract, ignore_errors=True)
        return None
    finally:
        if tmp_archive.exists():
            try:
                tmp_archive.unlink()
            except OSError:
                pass

    return target


# ----- Git (Windows) -----------------------------------------------------

def ensure_portable_git(tools_dir: Path) -> Path | None:
    """Download + extract MinGit into `tools_dir/git/`. Windows only.

    Returns the path to `git.exe` (`tools_dir/git/cmd/git.exe`), or None when
    auto-fetch is disabled, the platform isn't Windows, or the download failed.
    """
    if auto_fetch_disabled():
        return None
    if sys.platform != "win32":
        return None
    target = tools_dir / "git"
    git_exe = target / "cmd" / "git.exe"
    if git_exe.is_file():
        return git_exe

    tools_dir.mkdir(parents=True, exist_ok=True)
    tmp_archive = tools_dir / "git.zip.tmp"
    tmp_extract = tools_dir / "git.extract.tmp"
    if tmp_archive.exists():
        tmp_archive.unlink()
    if tmp_extract.exists():
        shutil.rmtree(tmp_extract)

    log.info(f"downloading portable Git {_MINGIT_VERSION} (MinGit)")
    try:
        _download(_MINGIT_URL, tmp_archive)
        log.info(f"extracting -> {target}")
        tmp_extract.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(tmp_archive) as zf:
            zf.extractall(tmp_extract)
        if target.exists():
            shutil.rmtree(target)
        tmp_extract.replace(target)
    except (urllib.error.URLError, OSError) as e:
        log.warn(f"portable Git fetch failed: {e}")
        if tmp_extract.exists():
            shutil.rmtree(tmp_extract, ignore_errors=True)
        return None
    finally:
        if tmp_archive.exists():
            try:
                tmp_archive.unlink()
            except OSError:
                pass

    return git_exe if git_exe.is_file() else None


# ----- helpers -----------------------------------------------------------

def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req) as resp, dest.open("wb") as fp:
        shutil.copyfileobj(resp, fp)


def _fetch_checksum(url: str) -> str | None:
    """Adoptium's `/checksum/...` endpoint returns plain text:
    `<sha256>  <filename>`. Return the hex digest, or None if unreachable
    (we still let the install proceed — the SHA check is best-effort)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req) as resp:
            text = resp.read().decode("ascii", errors="replace").strip()
    except (urllib.error.URLError, OSError) as e:
        log.warn(f"checksum fetch failed (continuing without verification): {e}")
        return None
    parts = text.split()
    if not parts:
        return None
    digest = parts[0].lower()
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        return None
    return digest


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_tar_extract(tf: tarfile.TarFile, dest: Path) -> None:
    """Reject path traversal entries before extracting."""
    dest_resolved = dest.resolve()
    for member in tf.getmembers():
        member_path = (dest / member.name).resolve()
        try:
            member_path.relative_to(dest_resolved)
        except ValueError:
            raise RuntimeError(f"unsafe tar entry: {member.name}")
    tf.extractall(dest)


# ----- discovery surface -------------------------------------------------

def fetched_jdk_roots(tools_dir: Path) -> tuple[Path, ...]:
    """Return any `tools_dir/jdk-*/` already on disk so the JDK scanner can
    consider them without re-downloading. Used by `tools._discover_jdk_binaries`
    via the `extra_roots` mechanism."""
    if not tools_dir.is_dir():
        return ()
    return tuple(p for p in tools_dir.iterdir() if p.is_dir() and p.name.startswith("jdk-"))


def fetched_git_exe(tools_dir: Path) -> Path | None:
    """Return the previously-fetched `git.exe` if one exists on disk."""
    cand = tools_dir / "git" / "cmd" / ("git.exe" if sys.platform == "win32" else "git")
    return cand if cand.is_file() else None
