"""GitHub fetch helpers for `necroid import` / `mod-update`.

Stdlib only — `urllib` for HTTP, `zipfile` for extraction. Mirrors the pattern
in `necroid/updater.py` (which fetches binary releases). Two API calls max per
import: optional `/repos/{o}/{r}` for the default branch + `/repos/{o}/{r}/
commits/{ref}` for the SHA. Archive download itself goes through codeload,
which is not REST-rate-limited.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..errors import ModImportError
# Shared, provider-agnostic helpers re-exported for back-compat with callers
# that import these names from ``github``.
from ._archive import (  # noqa: F401  (re-export)
    CommitResolution,
    DiscoveredMod,
    copy_mod_tree,
    discover_mods,
    extract_archive,
)
from .updater import _USER_AGENT, _http_download


# --- URL parsing ----------------------------------------------------------

_OWNER_REPO_RE = re.compile(r"^([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)$")


@dataclass
class GithubRef:
    owner: str
    repo: str
    ref_from_url: Optional[str] = None  # set if the URL itself encoded a ref


def parse_github_ref(s: str) -> GithubRef:
    """Parse one of:
        owner/repo
        https://github.com/owner/repo[.git]
        https://github.com/owner/repo/tree/<ref>
        https://github.com/owner/repo/blob/<ref>/...
        https://codeload.github.com/owner/repo/zip/refs/{heads,tags}/<ref>
    Returns GithubRef. Raises ModImportError on anything unrecognized.
    """
    if not s or not isinstance(s, str):
        raise ModImportError("unrecognized GitHub reference: <empty>")
    raw = s.strip()
    # owner/repo shorthand
    m = _OWNER_REPO_RE.match(raw)
    if m:
        return GithubRef(owner=m.group(1), repo=_strip_git(m.group(2)))

    try:
        u = urllib.parse.urlparse(raw)
    except ValueError as e:
        raise ModImportError(f"unrecognized GitHub reference: {raw} ({e})")
    host = (u.netloc or "").lower()
    path = (u.path or "").strip("/")
    parts = path.split("/") if path else []

    if host in ("github.com", "www.github.com"):
        # /owner/repo[.git][/tree/<ref>...] or /owner/repo/blob/<ref>/...
        if len(parts) < 2:
            raise ModImportError(f"unrecognized GitHub reference: {raw} (expected /owner/repo)")
        owner = parts[0]
        repo = _strip_git(parts[1])
        if not _OWNER_REPO_RE.match(f"{owner}/{repo}"):
            raise ModImportError(f"unrecognized GitHub reference: {raw}")
        ref = None
        if len(parts) >= 4 and parts[2] in ("tree", "blob"):
            ref = parts[3]
        return GithubRef(owner=owner, repo=repo, ref_from_url=ref)

    if host == "codeload.github.com":
        # /owner/repo/zip/refs/{heads|tags}/<ref...>
        if len(parts) >= 5 and parts[2] == "zip" and parts[3] == "refs" and parts[4] in ("heads", "tags"):
            owner = parts[0]
            repo = _strip_git(parts[1])
            if not _OWNER_REPO_RE.match(f"{owner}/{repo}"):
                raise ModImportError(f"unrecognized GitHub reference: {raw}")
            ref = "/".join(parts[5:]) if len(parts) > 5 else None
            return GithubRef(owner=owner, repo=repo, ref_from_url=ref)

    raise ModImportError(
        f"unrecognized GitHub reference: {raw} (expected 'owner/repo' or a github.com URL)"
    )


def _strip_git(name: str) -> str:
    return name[:-4] if name.endswith(".git") else name


# --- API + archive --------------------------------------------------------

def _api_url(owner: str, repo: str, suffix: str) -> str:
    return f"https://api.github.com/repos/{owner}/{repo}{suffix}"


def _http_get_json(url: str, *, timeout: float) -> dict:
    """Like updater._http_get_json but raises ModImportError so callers can
    surface a uniform error class to the user. Kept tiny + deduplicated from
    the updater because the error type matters."""
    req = urllib.request.Request(url, headers={
        "User-Agent": _USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ModImportError(f"GitHub says no such repo or ref (HTTP 404): {url}")
        raise ModImportError(f"GitHub API error: HTTP {e.code} for {url}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ModImportError(f"cannot reach GitHub: {e}")
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ModImportError(f"GitHub returned an unparseable response: {e}")


def resolve_default_branch(owner: str, repo: str, *, timeout: float = 10.0) -> str:
    """One API call. Returns the upstream default branch name (e.g. 'main')."""
    payload = _http_get_json(_api_url(owner, repo, ""), timeout=timeout)
    branch = str(payload.get("default_branch") or "")
    if not branch:
        raise ModImportError(f"could not determine default branch for {owner}/{repo}")
    return branch


def resolve_commit_sha(owner: str, repo: str, ref: str, *, timeout: float = 10.0) -> CommitResolution:
    """Resolve a branch / tag / sha to a 40-hex commit SHA via /commits/{ref}.

    Tag-vs-branch detection: try /git/refs/tags/{ref} first to disambiguate
    so the caller knows which codeload path (heads vs tags) to use. If that
    404s, treat as a branch / sha.
    """
    is_tag = False
    try:
        # Cheap probe — 404 = not a tag.
        _http_get_json(_api_url(owner, repo, f"/git/refs/tags/{urllib.parse.quote(ref)}"),
                       timeout=timeout)
        is_tag = True
    except ModImportError as e:
        if "404" not in str(e):
            # Network / other error — re-raise; the next call would just fail
            # the same way and we want one clean error not two.
            raise
        is_tag = False

    payload = _http_get_json(
        _api_url(owner, repo, f"/commits/{urllib.parse.quote(ref)}"),
        timeout=timeout,
    )
    sha = str(payload.get("sha") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ModImportError(
            f"GitHub returned no usable commit SHA for {owner}/{repo}@{ref}"
        )
    return CommitResolution(sha=sha, is_tag=is_tag)


def archive_url(owner: str, repo: str, ref: str, *, is_tag: bool) -> str:
    kind = "tags" if is_tag else "heads"
    return (f"https://codeload.github.com/{owner}/{repo}/zip/refs/{kind}/"
            f"{urllib.parse.quote(ref)}")


def download_repo_zip(owner: str, repo: str, ref: str, *, is_tag: bool,
                      dest: Path, timeout: float = 60.0) -> str:
    """Download the repo archive to `dest`. Returns the URL fetched."""
    url = archive_url(owner, repo, ref, is_tag=is_tag)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        _http_download(url, dest, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ModImportError(f"GitHub archive not found (HTTP 404): {url}")
        raise ModImportError(f"GitHub archive fetch failed: HTTP {e.code} for {url}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ModImportError(f"cannot reach GitHub: {e}")
    return url


# Extraction, multi-mod discovery, and mod-tree copy live in
# ``necroid/remote/_archive.py`` — they're provider-agnostic. Re-exported at
# the top of this file so existing callers keep working.
