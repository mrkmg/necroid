"""GitLab fetch helpers for `necroid import` / `mod-update`.

Parallel to ``necroid/remote/github.py``. Stdlib only. Handles gitlab.com and
arbitrary self-hosted instances — the host is always carried in the parsed
``GitlabRef`` / origin block, never hardcoded.

GitLab specifics (vs GitHub):
    - Project path can include nested groups (``group/sub/project``).
    - REST path encodes ``namespace/project`` as a single URL-encoded blob.
    - Archive URL is ``/-/archive/<ref>/<proj>-<ref>.zip`` — no heads-vs-tags
      switch (unlike codeload). ``is_tag`` is kept informational for parity.
    - No Accept vendor header required.

Two API calls max per import: optional ``/projects/{encoded}`` for the default
branch + ``/projects/{encoded}/repository/commits/{ref}`` for the SHA.
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
# Re-export shared, provider-agnostic helpers so callers can import everything
# they need from a single provider module.
from ._archive import (  # noqa: F401  (re-export)
    CommitResolution,
    DiscoveredMod,
    copy_mod_tree,
    discover_mods,
    extract_archive,
)
from .updater import _USER_AGENT, _http_download


# --- URL parsing ----------------------------------------------------------

# Each path segment (namespace element or project) allows the same charset as
# GitHub + '+' (GitLab is a touch more permissive in practice).
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._+-]+$")


@dataclass
class GitlabRef:
    host: str          # e.g. "gitlab.com" or "gitlab.example.com"
    project_path: str  # e.g. "namespace/project" or "group/sub/project"
    ref_from_url: Optional[str] = None  # set if URL itself encoded a ref


def parse_gitlab_ref(s: str) -> GitlabRef:
    """Parse one of:
        https://{host}/{ns}/{proj}[.git]
        https://{host}/{ns}/{proj}/-/tree/<ref>[/...]
        https://{host}/{ns}/{proj}/-/blob/<ref>/...
        https://{host}/{ns}/{proj}/-/archive/<ref>/<proj>-<ref>.zip
    Nested groups are allowed (``/{g}/{sub}/{proj}/...``).

    Bare ``namespace/project`` slugs are **not** accepted — GitLab support is
    opt-in via full URL (to avoid collision with the GitHub default).
    """
    if not s or not isinstance(s, str):
        raise ModImportError("unrecognized GitLab reference: <empty>")
    raw = s.strip()
    try:
        u = urllib.parse.urlparse(raw)
    except ValueError as e:
        raise ModImportError(f"unrecognized GitLab reference: {raw} ({e})")
    if u.scheme not in ("http", "https"):
        raise ModImportError(
            f"unrecognized GitLab reference: {raw} (expected https://<host>/<ns>/<proj>)"
        )
    host = (u.netloc or "").lower()
    if not host:
        raise ModImportError(f"unrecognized GitLab reference: {raw} (missing host)")
    path = (u.path or "").strip("/")
    if not path:
        raise ModImportError(f"unrecognized GitLab reference: {raw} (missing project path)")
    parts = path.split("/")

    # Split on the ``/-/`` sentinel that GitLab uses to separate project path
    # from the action suffix (tree / blob / archive / …).
    ref_from_url: Optional[str] = None
    try:
        dash_idx = parts.index("-")
    except ValueError:
        dash_idx = -1

    if dash_idx >= 0:
        proj_parts = parts[:dash_idx]
        tail = parts[dash_idx + 1:]
        if len(proj_parts) < 2:
            raise ModImportError(
                f"unrecognized GitLab reference: {raw} (need /<ns>/<proj>/-/…)"
            )
        if tail and tail[0] in ("tree", "blob") and len(tail) >= 2:
            ref_from_url = tail[1]
        elif tail and tail[0] == "archive" and len(tail) >= 2:
            ref_from_url = tail[1]
    else:
        proj_parts = parts

    if len(proj_parts) < 2:
        raise ModImportError(
            f"unrecognized GitLab reference: {raw} (expected /<ns>/<proj>)"
        )
    # Trailing ``.git`` suffix on the project name.
    proj_parts[-1] = _strip_git(proj_parts[-1])
    for seg in proj_parts:
        if not _SEGMENT_RE.match(seg):
            raise ModImportError(
                f"unrecognized GitLab reference: {raw} (bad segment {seg!r})"
            )

    project_path = "/".join(proj_parts)
    return GitlabRef(host=host, project_path=project_path, ref_from_url=ref_from_url)


def _strip_git(name: str) -> str:
    return name[:-4] if name.endswith(".git") else name


# --- API + archive --------------------------------------------------------

def _encode_project(project_path: str) -> str:
    # URL-encode the whole ``namespace/project`` as one blob; slashes become %2F.
    return urllib.parse.quote(project_path, safe="")


def _api_url(host: str, project_path: str, suffix: str) -> str:
    return f"https://{host}/api/v4/projects/{_encode_project(project_path)}{suffix}"


def _http_get_json(url: str, *, timeout: float) -> dict | list:
    """Like the github variant but raises ModImportError and drops the
    GitHub-specific Accept header. GitLab returns JSON by default."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ModImportError(f"GitLab says no such project or ref (HTTP 404): {url}")
        raise ModImportError(f"GitLab API error: HTTP {e.code} for {url}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ModImportError(f"cannot reach GitLab: {e}")
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ModImportError(f"GitLab returned an unparseable response: {e}")


def resolve_default_branch(host: str, project_path: str, *,
                            timeout: float = 10.0) -> str:
    """One API call. Returns the upstream default branch name."""
    payload = _http_get_json(_api_url(host, project_path, ""), timeout=timeout)
    if not isinstance(payload, dict):
        raise ModImportError(f"GitLab returned non-object project payload for {project_path}")
    branch = str(payload.get("default_branch") or "")
    if not branch:
        raise ModImportError(f"could not determine default branch for {project_path}")
    return branch


def resolve_commit_sha(host: str, project_path: str, ref: str, *,
                        timeout: float = 10.0) -> CommitResolution:
    """Resolve a branch / tag / sha to a 40-hex commit SHA.

    Tag-vs-branch detection via ``/repository/tags/{ref}`` — purely
    informational on GitLab (the archive URL is the same either way).
    """
    is_tag = False
    try:
        _http_get_json(
            _api_url(host, project_path,
                     f"/repository/tags/{urllib.parse.quote(ref, safe='')}"),
            timeout=timeout,
        )
        is_tag = True
    except ModImportError as e:
        if "404" not in str(e):
            raise
        is_tag = False

    payload = _http_get_json(
        _api_url(host, project_path,
                 f"/repository/commits/{urllib.parse.quote(ref, safe='')}"),
        timeout=timeout,
    )
    if not isinstance(payload, dict):
        raise ModImportError(
            f"GitLab returned non-object commit payload for {project_path}@{ref}"
        )
    sha = str(payload.get("id") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ModImportError(
            f"GitLab returned no usable commit SHA for {project_path}@{ref}"
        )
    return CommitResolution(sha=sha, is_tag=is_tag)


def archive_url(host: str, project_path: str, ref: str, *, is_tag: bool) -> str:
    # GitLab uses one archive shape — is_tag is accepted for API parity only.
    proj_name = project_path.rsplit("/", 1)[-1]
    quoted_ref = urllib.parse.quote(ref, safe="")
    return (
        f"https://{host}/{project_path}/-/archive/{quoted_ref}/"
        f"{proj_name}-{quoted_ref}.zip"
    )


def download_repo_zip(host: str, project_path: str, ref: str, *,
                       is_tag: bool, dest: Path, timeout: float = 60.0) -> str:
    """Download the repo archive to ``dest``. Returns the URL fetched."""
    url = archive_url(host, project_path, ref, is_tag=is_tag)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        _http_download(url, dest, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ModImportError(f"GitLab archive not found (HTTP 404): {url}")
        raise ModImportError(f"GitLab archive fetch failed: HTTP {e.code} for {url}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ModImportError(f"cannot reach GitLab: {e}")
    return url
