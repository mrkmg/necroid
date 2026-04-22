"""Self-updater for the frozen PyInstaller binary.

Workflow: check GitHub Releases for a newer tag, download the matching
platform zip, extract the binary into place, restart.

Only the binary is replaced. Bundled `data/mods/` and `data/tools/` in the
release zip are ignored — the user's mod library + local Vineflower cache
must not be touched.

Stdlib only. Silent on network failure when callers pass quiet=True.

Env vars:
    NECROID_UPDATE_REPO   override the default `mrkmg/necroid` (test harness)
    NECROID_NO_UPDATE_CHECK=1   suppress the opportunistic CLI-footer notice
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import __version__
from . import logging_util as log
from .errors import UpdateError


DEFAULT_REPO = "mrkmg/necroid"
CHECK_TTL_SECONDS = 24 * 3600
_USER_AGENT = f"necroid/{__version__}"


def _repo() -> str:
    return os.environ.get("NECROID_UPDATE_REPO", DEFAULT_REPO)


def _api_url() -> str:
    return f"https://api.github.com/repos/{_repo()}/releases/latest"


def _platform_tag() -> tuple[str, str]:
    """Return (platform, arch) matching packaging/build_dist.py."""
    if sys.platform == "win32":
        plat = "windows"
    elif sys.platform == "darwin":
        plat = "macos"
    else:
        plat = "linux"
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch = "x64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        arch = machine or "unknown"
    return plat, arch


def _asset_name(version: str) -> str:
    plat, arch = _platform_tag()
    v = version.lstrip("v")
    return f"necroid-v{v}-{plat}-{arch}.zip"


# --------------------------------------------------------------------------- #
# Frozen-binary helpers
# --------------------------------------------------------------------------- #

def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def running_binary_path() -> Path:
    """Path to the currently-running PyInstaller binary. Only meaningful when
    is_frozen() is True."""
    return Path(sys.executable).resolve()


def _old_suffix_path(binary: Path) -> Path:
    """Sibling `.old` file used as the holding spot for the prior binary.
    On Windows the `.old.exe` form keeps Explorer happy; on POSIX a bare
    `.old` is fine."""
    if sys.platform == "win32":
        # necroid.exe -> necroid.old.exe
        return binary.with_name(binary.stem + ".old" + binary.suffix)
    return binary.with_name(binary.name + ".old")


def _new_suffix_path(binary: Path) -> Path:
    if sys.platform == "win32":
        return binary.with_name(binary.stem + ".new" + binary.suffix)
    return binary.with_name(binary.name + ".new")


def cleanup_stale_old(root: Optional[Path] = None) -> None:
    """Best-effort removal of any leftover `.old` binary. Called at the top of
    every CLI invocation and after a --post-restart-cleanup run.

    `root` is unused — only the currently-running binary's sibling path
    matters — but accepted so callers can pass their workspace root without
    branching. Kept as a no-op in non-frozen mode.
    """
    if not is_frozen():
        return
    try:
        old = _old_suffix_path(running_binary_path())
    except Exception:
        return
    if old.exists():
        try:
            old.unlink()
        except OSError:
            # Binary in use by another Necroid invocation, or filesystem
            # weirdness. Harmless — next invocation will retry.
            pass


# --------------------------------------------------------------------------- #
# Version parsing
# --------------------------------------------------------------------------- #

def parse_version(s: str) -> tuple[int, ...]:
    """Parse `v1.2.3` / `1.2.3` / `1.2.3-rc1` into an int tuple for ordering.
    Unparseable strings collapse to `(0,)` so comparisons still work."""
    if not s:
        return (0,)
    s = s.strip().lstrip("v")
    # Drop any pre-release / build suffix (`-rc1`, `+build.5`).
    for sep in ("-", "+"):
        if sep in s:
            s = s.split(sep, 1)[0]
    parts: list[int] = []
    for chunk in s.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            break
    return tuple(parts) if parts else (0,)


# --------------------------------------------------------------------------- #
# Release metadata
# --------------------------------------------------------------------------- #

@dataclass
class ReleaseInfo:
    tag: str
    version: tuple[int, ...]
    html_url: str
    asset_url: Optional[str]
    asset_name: Optional[str]
    published_at: str
    body: str

    @property
    def pretty_version(self) -> str:
        return self.tag.lstrip("v") or ".".join(str(p) for p in self.version)


def _http_get_json(url: str, *, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": _USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise UpdateError(f"GitHub returned an unparseable response: {e}")


def _http_download(url: str, dest: Path, *, timeout: float) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, tmp.open("wb") as fp:
            shutil.copyfileobj(resp, fp)
        tmp.replace(dest)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def fetch_latest_release(timeout: float = 5.0) -> ReleaseInfo:
    """Hit the GitHub API. Raises UpdateError on network / schema failures."""
    try:
        payload = _http_get_json(_api_url(), timeout=timeout)
    except urllib.error.HTTPError as e:
        raise UpdateError(f"GitHub API error: HTTP {e.code} for {_api_url()}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UpdateError(f"cannot reach GitHub: {e}")

    tag = str(payload.get("tag_name") or "")
    if not tag:
        raise UpdateError("GitHub response has no tag_name (no releases yet?)")
    version = parse_version(tag)
    html_url = str(payload.get("html_url") or "")
    published_at = str(payload.get("published_at") or "")
    body = str(payload.get("body") or "")

    want = _asset_name(tag)
    asset_url = None
    asset_name = None
    for asset in payload.get("assets", []) or []:
        name = str(asset.get("name") or "")
        if name == want:
            asset_url = str(asset.get("browser_download_url") or "")
            asset_name = name
            break

    return ReleaseInfo(
        tag=tag,
        version=version,
        html_url=html_url,
        asset_url=asset_url or None,
        asset_name=asset_name,
        published_at=published_at,
        body=body,
    )


# --------------------------------------------------------------------------- #
# Cache file
# --------------------------------------------------------------------------- #

def _cache_path(root: Path) -> Path:
    return root / "data" / ".update-cache.json"


def read_cache(root: Path) -> dict:
    p = _cache_path(root)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_cache(root: Path, data: dict) -> None:
    p = _cache_path(root)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError:
        # Cache is advisory — never fail the command because of it.
        pass


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> Optional[_dt.datetime]:
    if not s:
        return None
    try:
        # Accept trailing 'Z' or explicit offset.
        s2 = s.replace("Z", "+00:00")
        return _dt.datetime.fromisoformat(s2)
    except ValueError:
        return None


def _cache_is_fresh(cache: dict) -> bool:
    last = _parse_iso(str(cache.get("lastCheckIso") or ""))
    if last is None:
        return False
    delta = _dt.datetime.now(_dt.timezone.utc) - last
    return delta.total_seconds() < CHECK_TTL_SECONDS


def _cache_to_release(cache: dict) -> Optional[ReleaseInfo]:
    tag = str(cache.get("latestTag") or "")
    if not tag:
        return None
    return ReleaseInfo(
        tag=tag,
        version=parse_version(tag),
        html_url=str(cache.get("latestHtmlUrl") or ""),
        asset_url=(str(cache.get("latestAssetUrl")) or None) or None,
        asset_name=(str(cache.get("latestAssetName")) or None) or None,
        published_at=str(cache.get("latestPublishedAt") or ""),
        body=str(cache.get("latestBody") or ""),
    )


# --------------------------------------------------------------------------- #
# Public check API
# --------------------------------------------------------------------------- #

def check_for_update(
    root: Path,
    *,
    force: bool = False,
    quiet: bool = False,
    timeout: float = 5.0,
) -> Optional[ReleaseInfo]:
    """Return a `ReleaseInfo` iff a newer version is available.

    Uses the 24h cache unless `force=True`. When `quiet=True`, network / parse
    errors return None instead of raising — appropriate for background / footer
    checks. The explicit `necroid update` flow uses `quiet=False`.
    """
    cache = read_cache(root)
    release: Optional[ReleaseInfo] = None
    if not force and _cache_is_fresh(cache):
        release = _cache_to_release(cache)
    if release is None:
        try:
            release = fetch_latest_release(timeout=timeout)
        except UpdateError:
            if quiet:
                return None
            raise
        cache = {
            "lastCheckIso": _now_iso(),
            "checkedFromVersion": __version__,
            "latestTag": release.tag,
            "latestVersion": ".".join(str(p) for p in release.version),
            "latestHtmlUrl": release.html_url,
            "latestAssetUrl": release.asset_url or "",
            "latestAssetName": release.asset_name or "",
            "latestPublishedAt": release.published_at,
            # body can be long; keep only the first 4 KB
            "latestBody": release.body[:4096],
        }
        write_cache(root, cache)
    if release.version <= parse_version(__version__):
        return None
    return release


def emit_opportunistic_notice(root: Path) -> None:
    """One-line stderr notice after a non-update command, once per 24h window.

    Suppressed when:
      * NECROID_NO_UPDATE_CHECK=1
      * stderr isn't a TTY (piped / redirected invocations — keeps scripts clean)
      * running from an editable install (no target to update to)
      * the cache is absent or stale AND the network check fails silently
    """
    if os.environ.get("NECROID_NO_UPDATE_CHECK"):
        return
    if not sys.stderr.isatty():
        return
    # Editable installs can't self-update; still check so the user knows a
    # release is out, but only when frozen — otherwise noise.
    if not is_frozen():
        return
    try:
        release = check_for_update(root, quiet=True, timeout=3.0)
    except Exception:
        return
    if release is None:
        return
    log.info(
        f"update available: v{__version__} -> v{release.pretty_version}  "
        f"(run: necroid update)"
    )


# --------------------------------------------------------------------------- #
# Apply / restart
# --------------------------------------------------------------------------- #

def _writable(directory: Path) -> bool:
    try:
        probe = directory / ".necroid-write-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _extract_binary_from_zip(zip_path: Path, dest: Path) -> None:
    """Pull the `necroid` / `necroid.exe` entry out of the release archive.

    The archive layout (see packaging/build_dist.py) puts the binary at the
    zip root. We accept any path whose basename matches, to tolerate a future
    change to a nested layout.
    """
    want_name = "necroid.exe" if sys.platform == "win32" else "necroid"
    with zipfile.ZipFile(zip_path, "r") as zf:
        match: Optional[zipfile.ZipInfo] = None
        for info in zf.infolist():
            if info.is_dir():
                continue
            base = info.filename.rsplit("/", 1)[-1]
            if base == want_name:
                match = info
                break
        if match is None:
            raise UpdateError(
                f"release archive has no `{want_name}` entry "
                f"(looked in {zip_path.name})"
            )
        with zf.open(match) as src, dest.open("wb") as out:
            shutil.copyfileobj(src, out)
    if sys.platform != "win32":
        dest.chmod(0o755)


def apply_update(
    release: ReleaseInfo,
    *,
    yes: bool = False,
    timeout: float = 60.0,
    restart: bool = True,
) -> None:
    """Download + swap + restart. Must be called from a frozen binary.

    Flow:
        1. Gate on is_frozen() and a matching platform asset.
        2. Prompt unless yes=True.
        3. Probe write permission on the binary's directory.
        4. Download zip -> tempdir.
        5. Extract binary to `<bindir>/necroid.new[.exe]`.
        6. Rename current -> `.old[.exe]`; rename `.new` -> current.
        7. Respawn the new binary and exit.
    """
    if not is_frozen():
        raise UpdateError(
            "self-update is only available for the packaged Necroid binary.\n"
            "    for editable / source installs: `git pull` (or `pip install -U`)"
        )
    if not release.asset_url:
        plat, arch = _platform_tag()
        raise UpdateError(
            f"release v{release.pretty_version} has no asset for "
            f"{plat}-{arch} (expected `{_asset_name(release.tag)}`).\n"
            f"    download manually: {release.html_url}"
        )

    current = running_binary_path()
    bindir = current.parent
    if not _writable(bindir):
        raise UpdateError(
            f"binary directory is not writable: {bindir}\n"
            f"    re-run with admin privileges, or move Necroid to a "
            f"user-writable location."
        )

    if not yes:
        log.step(f"new release: v{__version__} -> v{release.pretty_version}")
        log.info(f"asset: {release.asset_name}")
        if release.html_url:
            log.info(f"notes: {release.html_url}")
        answer = input("download and install now? [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            log.info("update cancelled")
            return

    new_binary = _new_suffix_path(current)
    old_binary = _old_suffix_path(current)

    # Clean up any leftovers from a previous attempt before starting.
    for stale in (new_binary, old_binary):
        if stale.exists():
            try:
                stale.unlink()
            except OSError as e:
                raise UpdateError(
                    f"cannot remove stale {stale.name}: {e} — delete it "
                    f"manually and retry."
                )

    with tempfile.TemporaryDirectory(prefix="necroid-update-") as tmpdir:
        zip_path = Path(tmpdir) / (release.asset_name or "necroid-release.zip")
        log.step(f"downloading {release.asset_name}")
        try:
            _http_download(release.asset_url, zip_path, timeout=timeout)
        except urllib.error.HTTPError as e:
            raise UpdateError(f"download failed: HTTP {e.code}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise UpdateError(f"download failed: {e}")

        log.step("extracting binary")
        _extract_binary_from_zip(zip_path, new_binary)

    log.step("swapping binary")
    try:
        # On Windows we can't overwrite or unlink the running exe, but we CAN
        # rename it. The rename-out + rename-in dance below is the canonical
        # workaround and works identically on POSIX.
        os.replace(current, old_binary)
        try:
            os.replace(new_binary, current)
        except OSError:
            # Rollback: move the old binary back so we don't leave a broken install.
            try:
                os.replace(old_binary, current)
            except OSError:
                pass
            raise
    except OSError as e:
        raise UpdateError(f"cannot swap binary: {e}")

    log.success(f"updated to v{release.pretty_version}")

    if not restart:
        return

    # Re-launch the new binary so the user gets an immediately-working install.
    # It runs a lightweight --post-restart-cleanup pass, which removes the
    # .old sibling and exits. We then exit ourselves via os._exit so this
    # process releases any handles on `old_binary` cleanly.
    try:
        popen_kwargs: dict = {}
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = 0x00000008  # DETACHED_PROCESS
        subprocess.Popen(
            [str(current), "update", "--post-restart-cleanup"],
            close_fds=True,
            **popen_kwargs,
        )
    except OSError as e:
        # Swap already succeeded — the user can just run `necroid` themselves.
        log.warn(f"couldn't auto-restart: {e}. re-run `necroid` manually.")
    os._exit(0)


def rollback() -> None:
    """Restore `necroid.old` -> `necroid`, undoing the most recent update.
    Only one generation is kept."""
    if not is_frozen():
        raise UpdateError(
            "rollback is only meaningful for the packaged binary."
        )
    current = running_binary_path()
    old = _old_suffix_path(current)
    if not old.exists():
        raise UpdateError(
            f"no previous binary to roll back to (expected {old.name})."
        )
    bindir = current.parent
    if not _writable(bindir):
        raise UpdateError(f"binary directory is not writable: {bindir}")

    # current -> `.roll-tmp`; old -> current; `.roll-tmp` removed.
    tmp = current.with_name(current.name + ".roll-tmp")
    if tmp.exists():
        try:
            tmp.unlink()
        except OSError as e:
            raise UpdateError(f"cannot remove stale {tmp.name}: {e}")
    try:
        os.replace(current, tmp)
        os.replace(old, current)
    except OSError as e:
        raise UpdateError(f"cannot swap during rollback: {e}")
    try:
        tmp.unlink()
    except OSError:
        pass
    log.success("rolled back to previous binary — re-run `necroid` to use it")
    os._exit(0)
