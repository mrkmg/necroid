"""Auto-fetch portable JDK + Git into `data/tools/` when the system has none.

Mirrors the existing Vineflower auto-download pattern (see
`necroid/build/decompile.py:ensure_vineflower`). Stdlib only.

- JDK: Eclipse Temurin via the Adoptium API. Pinned to a specific release
  (BUNDLED_JDK_RELEASE) so every user gets byte-identical decompile output —
  Vineflower's pass ordering depends on the JVM running it. PATH and
  well-known JDK install roots are NEVER consulted by `tools.resolve` for
  java/javac/jar; the bundled JDK is the single source of truth.
- Git: MinGit on Windows only. macOS/Linux fall back to `ToolMissing` install
  hints (no first-party portable git distribution exists).

Opt-out: set `NECROID_NO_AUTO_FETCH=1`. With the bundled JDK pin, opt-out
means "I have already provisioned a JDK at the expected path" — there is no
PATH-fallback for java/javac/jar.
"""
from __future__ import annotations

import hashlib
import os
import platform
import shutil
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from ..errors import ToolMissing
from . import logging_util as log


# Pinned bundled JDK. Bumping this is a deliberate change — every user who
# upgrades will re-decompile their pristine, and bundled mods may need
# recapture if Vineflower's output shifts under the new JVM. When bumping,
# verify the new release_name resolves on Adoptium's `/v3/binary/version/...`
# endpoint and update verification notes.
BUNDLED_JDK_RELEASE = "jdk-25.0.2+10"
BUNDLED_JDK_MAJOR = 25
_BUNDLED_DIRNAME = "jdk-bundled"
_PIN_MARKER_NAME = ".pinned-version"

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


def bundled_jdk_dir(tools_dir: Path) -> Path:
    """Path of the pinned bundled-JDK install dir (may not exist yet)."""
    return tools_dir / _BUNDLED_DIRNAME


def _read_pin_marker(jdk_home: Path) -> str:
    try:
        return (jdk_home / _PIN_MARKER_NAME).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _cleanup_legacy_caches(tools_dir: Path) -> None:
    """Remove pre-pinning auto-fetch caches (`jdk-25/`, `jdk-21/`, etc.).
    Silent when nothing matches. Saves ~200MB per stale cache."""
    if not tools_dir.is_dir():
        return
    for entry in tools_dir.iterdir():
        if not entry.is_dir():
            continue
        name = entry.name
        if name == _BUNDLED_DIRNAME:
            continue
        # Match `jdk-25`, `jdk-21`, etc. — never the new `jdk-bundled` dir.
        if name.startswith("jdk-") and name[4:].isdigit():
            log.info(f"removing legacy JDK cache: {entry}")
            shutil.rmtree(entry, ignore_errors=True)


def ensure_bundled_jdk(tools_dir: Path) -> Path:
    """Download + extract the pinned Temurin JDK into `tools_dir/jdk-bundled/`.

    Returns the directory containing the extracted JDK (a parent of one
    `jdk-X.Y.Z+B/` entry on Windows/Linux, or a `jdk-X.Y.Z+B/Contents/Home/`
    layout on macOS). Idempotent: a hit on the pin marker short-circuits.

    Raises `ToolMissing` when auto-fetch is disabled and no bundled JDK is on
    disk, when the platform/arch isn't supported by Adoptium, or when the
    download or extract fails. The pin guarantees byte-identical decompile
    output across users, so "fall back to PATH" is no longer offered.
    """
    target = bundled_jdk_dir(tools_dir)
    if target.is_dir() and any(target.iterdir()) and _read_pin_marker(target) == BUNDLED_JDK_RELEASE:
        return target

    if auto_fetch_disabled():
        raise ToolMissing(
            "java",
            f"NECROID_NO_AUTO_FETCH=1 set, but the pinned bundled JDK is not on disk.\n"
            f"    Expected: {target} containing release {BUNDLED_JDK_RELEASE}\n"
            f"    Either unset NECROID_NO_AUTO_FETCH, or pre-stage the JDK at that path\n"
            f"    with a `{_PIN_MARKER_NAME}` file containing the release name.",
        )

    try:
        os_name = _adoptium_os()
        arch = _adoptium_arch()
    except RuntimeError as e:
        raise ToolMissing("java", f"unsupported platform for bundled JDK fetch: {e}")
    ext = _adoptium_archive_ext()
    quoted_release = urllib.parse.quote(BUNDLED_JDK_RELEASE, safe="")

    binary_url = (
        f"https://api.adoptium.net/v3/binary/version/{quoted_release}/"
        f"{os_name}/{arch}/jdk/hotspot/normal/eclipse"
    )
    checksum_url = (
        f"https://api.adoptium.net/v3/checksum/version/{quoted_release}/"
        f"{os_name}/{arch}/jdk/hotspot/normal/eclipse"
    )

    tools_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_legacy_caches(tools_dir)
    tmp_archive = tools_dir / f"{_BUNDLED_DIRNAME}.{ext}.tmp"
    tmp_extract = tools_dir / f"{_BUNDLED_DIRNAME}.extract.tmp"
    if tmp_archive.exists():
        tmp_archive.unlink()
    if tmp_extract.exists():
        shutil.rmtree(tmp_extract)

    log.info(
        f"downloading pinned JDK {BUNDLED_JDK_RELEASE} from Adoptium "
        f"({os_name}/{arch}) -> {target}"
    )
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
        if target.exists():
            shutil.rmtree(target)
        tmp_extract.replace(target)
        (target / _PIN_MARKER_NAME).write_text(BUNDLED_JDK_RELEASE, encoding="utf-8")
    except (urllib.error.URLError, OSError, RuntimeError) as e:
        if tmp_extract.exists():
            shutil.rmtree(tmp_extract, ignore_errors=True)
        raise ToolMissing("java", f"bundled JDK fetch failed: {e}")
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

def fetched_git_exe(tools_dir: Path) -> Path | None:
    """Return the previously-fetched `git.exe` if one exists on disk."""
    cand = tools_dir / "git" / "cmd" / ("git.exe" if sys.platform == "win32" else "git")
    return cand if cand.is_file() else None
