"""Provider dispatch for ``necroid import`` / ``mod-update``.

Thin wrappers that route between ``github`` and ``gitlab`` based on either a
user-supplied URL/slug (``detect_provider``) or an origin block stamped into
``mod.json`` (``resolve_origin_sha`` / ``download_origin_zip`` / ``browser_url``).

GitHub is the default — bare ``owner/repo`` slugs always map to GitHub. GitLab
requires a full URL so nested-group paths (``group/sub/project``) disambiguate
cleanly.
"""
from __future__ import annotations

import urllib.parse
from pathlib import Path

from ..errors import ModImportError, ModUpdateError
from . import github as _gh
from . import gitlab as _gl


PROVIDER_GITHUB = "github"
PROVIDER_GITLAB = "gitlab"


def detect_provider(s: str) -> str:
    """Pick the provider for a user-supplied repo reference.

    Bare slugs and github.com URLs → github. Any URL whose host looks like a
    GitLab instance (``gitlab.com``, ``gitlab.*``, or ``*.gitlab.io``) → gitlab.
    Unknown hosts fall through to github, which will reject them with a clear
    "expected 'owner/repo' or a github.com URL" error.
    """
    if not s:
        return PROVIDER_GITHUB
    raw = s.strip()
    if "://" not in raw:
        return PROVIDER_GITHUB
    try:
        u = urllib.parse.urlparse(raw)
    except ValueError:
        return PROVIDER_GITHUB
    host = (u.netloc or "").lower()
    if _is_gitlab_host(host):
        return PROVIDER_GITLAB
    return PROVIDER_GITHUB


def _is_gitlab_host(host: str) -> bool:
    if not host:
        return False
    return (
        host == "gitlab.com"
        or host.startswith("gitlab.")
        or host.endswith(".gitlab.io")
    )


# --- Origin-block dispatch -------------------------------------------------

def _origin_host(origin: dict) -> str:
    """Resolve the host for an origin block, falling back to the canonical
    host for the provider if ``host`` is missing."""
    provider = origin.get("type") or PROVIDER_GITHUB
    host = str(origin.get("host") or "").strip()
    if host:
        return host
    if provider == PROVIDER_GITLAB:
        return "gitlab.com"
    return "github.com"


def resolve_origin_sha(origin: dict, ref: str, *, timeout: float = 10.0):
    """Return ``CommitResolution`` for an origin block's upstream ref."""
    provider = origin.get("type") or PROVIDER_GITHUB
    repo = str(origin.get("repo") or "")
    if not repo:
        raise ModUpdateError("origin block is missing repo")
    if provider == PROVIDER_GITLAB:
        host = _origin_host(origin)
        return _gl.resolve_commit_sha(host, repo, ref, timeout=timeout)
    if "/" not in repo:
        raise ModUpdateError(f"origin.repo malformed: {repo!r}")
    owner, name = repo.split("/", 1)
    return _gh.resolve_commit_sha(owner, name, ref, timeout=timeout)


def download_origin_zip(origin: dict, ref: str, *, is_tag: bool,
                         dest: Path, timeout: float = 60.0) -> str:
    """Download an origin block's archive. Returns the URL fetched."""
    provider = origin.get("type") or PROVIDER_GITHUB
    repo = str(origin.get("repo") or "")
    if not repo:
        raise ModImportError("origin block is missing repo")
    if provider == PROVIDER_GITLAB:
        host = _origin_host(origin)
        return _gl.download_repo_zip(host, repo, ref, is_tag=is_tag,
                                     dest=dest, timeout=timeout)
    if "/" not in repo:
        raise ModImportError(f"origin.repo malformed: {repo!r}")
    owner, name = repo.split("/", 1)
    return _gh.download_repo_zip(owner, name, ref, is_tag=is_tag,
                                 dest=dest, timeout=timeout)


def browser_url(origin: dict) -> str:
    """Build a human-facing web URL for an origin block.

    Returns an empty string if the origin is malformed (GUI treats that as a
    no-op)."""
    repo = str(origin.get("repo") or "")
    if not repo:
        return ""
    provider = origin.get("type") or PROVIDER_GITHUB
    ref = str(origin.get("ref") or "")
    subdir = str(origin.get("subdir") or "")
    host = _origin_host(origin)
    if provider == PROVIDER_GITLAB:
        url = f"https://{host}/{repo}"
        if ref:
            url += f"/-/tree/{ref}"
            if subdir:
                url += f"/{subdir}"
        return url
    # github
    url = f"https://{host}/{repo}"
    if ref:
        url += f"/tree/{ref}"
        if subdir:
            url += f"/{subdir}"
    return url


def import_arg_for_origin(origin: dict) -> str:
    """Build a CLI argument string that would re-import an existing origin.

    GitHub origins round-trip as a bare ``owner/repo`` slug. GitLab origins
    round-trip as a full URL (the dispatcher needs a URL to pick GitLab)."""
    repo = str(origin.get("repo") or "")
    if not repo:
        return ""
    provider = origin.get("type") or PROVIDER_GITHUB
    if provider == PROVIDER_GITLAB:
        return f"https://{_origin_host(origin)}/{repo}"
    return repo
