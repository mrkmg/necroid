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
import shutil
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..errors import ModImportError
from ..core.mod import ModJson
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


@dataclass
class CommitResolution:
    sha: str
    is_tag: bool  # informational; codeload URL builder uses this


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


# --- Extraction -----------------------------------------------------------

def extract_archive(zip_path: Path, dest_dir: Path) -> Path:
    """Extract `zip_path` into `dest_dir`. Refuses entries that would escape
    the destination (zip-slip). Returns the wrapper directory inside dest_dir
    (GitHub archives always wrap content in `<owner>-<repo>-<sha>/`).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        if not names:
            raise ModImportError("archive is empty")
        for n in names:
            # Reject absolute paths and `..` traversal.
            np = Path(n)
            if np.is_absolute() or any(p == ".." for p in np.parts):
                raise ModImportError(f"archive contains unsafe entry: {n}")
        zf.extractall(dest_dir)

    # Identify wrapper dir.
    children = [p for p in dest_dir.iterdir() if not p.name.startswith(".")]
    dirs = [p for p in children if p.is_dir()]
    if len(dirs) != 1 or any(p.is_file() for p in children):
        raise ModImportError(
            "archive layout unexpected (no single top-level wrapper dir)"
        )
    return dirs[0]


# --- Multi-mod discovery --------------------------------------------------

@dataclass
class DiscoveredMod:
    """A `mod.json` found inside an extracted repo.

    `dirname` is the canonical Necroid mod-dir name — `<base>-<major>`. We
    derive it from the final path component of `subdir` if it parses, else
    from `mj.name` if that parses, else fall back to the raw subdir/name.
    Callers must verify `dirname` actually parses cleanly before treating
    it as a target.

    `mod_major` is the int extracted from `dirname`, or None if it did not
    parse. None means the mod is missing the required `-<digits>` suffix
    and cannot be imported.
    """
    subdir: str           # forward-slash, "" when at the repo root
    mj: ModJson
    src_path: Path = field(default_factory=Path)  # absolute dir containing mod.json
    dirname: str = ""     # canonical `<base>-<major>` (preserved from upstream)
    mod_major: Optional[int] = None


def discover_mods(extracted_root: Path) -> list[DiscoveredMod]:
    """Find every mod.json under `extracted_root`.

    Layouts supported (walked in this order, results merged):
      <root>/mod.json                       single-mod repo
      <root>/<name>/mod.json                depth 1 — flat container
      <root>/<container>/<name>/mod.json    depth 2 — e.g. mods/admin-xray-41/
      <root>/data/mods/<name>/mod.json      depth 3 — Necroid canonical layout
                                            (this repo + any fork shipping
                                            bundled mods that way)

    De-duped by subdir. Mixed-depth layouts are accepted; the caller can
    inspect each `DiscoveredMod.subdir` to flag oddities.
    """
    out: list[DiscoveredMod] = []

    root_mj = extracted_root / "mod.json"
    if root_mj.is_file():
        out.append(_load_discovered(extracted_root, ""))

    # Depth 1 + 2.
    for child in sorted(extracted_root.iterdir()):
        if not child.is_dir() or _is_skip_dir(child.name):
            continue
        mj_path = child / "mod.json"
        if mj_path.is_file():
            out.append(_load_discovered(extracted_root, child.name))
            continue
        for grand in sorted(child.iterdir()):
            if not grand.is_dir() or _is_skip_dir(grand.name):
                continue
            if (grand / "mod.json").is_file():
                out.append(_load_discovered(
                    extracted_root, f"{child.name}/{grand.name}"))

    # Necroid canonical layout: `data/mods/<name>/mod.json`. Always probed,
    # even when the depth-1/2 walk found other mods, so a repo can mix layouts.
    canonical = extracted_root / "data" / "mods"
    if canonical.is_dir():
        for child in sorted(canonical.iterdir()):
            if not child.is_dir() or _is_skip_dir(child.name):
                continue
            if (child / "mod.json").is_file():
                out.append(_load_discovered(
                    extracted_root, f"data/mods/{child.name}"))

    if not out:
        raise ModImportError(
            "repo contains no mod.json "
            "(looked at root, one/two levels deep, and data/mods/*)"
        )

    # De-dup by subdir.
    seen: dict[str, DiscoveredMod] = {}
    for dm in out:
        seen.setdefault(dm.subdir, dm)
    return list(seen.values())


def _load_discovered(root: Path, subdir: str) -> DiscoveredMod:
    from ..core.mod import parse_mod_dirname

    src = root if subdir == "" else (root / subdir)
    try:
        raw = json.loads((src / "mod.json").read_text(encoding="utf-8"))
        mj = ModJson.from_json(raw)
    except (OSError, json.JSONDecodeError, KeyError) as e:
        loc = subdir or "<root>"
        raise ModImportError(f"upstream mod.json at '{loc}' is not valid JSON / schema: {e}")

    # Canonical dirname rules:
    #   1. Trailing path component of subdir, IF it parses as `<base>-<major>`.
    #   2. mj.name, IF it parses as `<base>-<major>`.
    #   3. Whatever we have, with mod_major=None — the import preflight rejects
    #      this path with a clear "needs -<major> suffix" error.
    candidate_names: list[str] = []
    if subdir:
        candidate_names.append(subdir.rsplit("/", 1)[-1])
    if mj.name:
        candidate_names.append(mj.name)

    dirname = candidate_names[0] if candidate_names else ""
    mod_major = None
    for cand in candidate_names:
        parsed = parse_mod_dirname(cand)
        if parsed is not None:
            dirname = cand
            mod_major = parsed[1]
            break

    return DiscoveredMod(subdir=subdir, mj=mj, src_path=src,
                         dirname=dirname, mod_major=mod_major)


_SKIP_DIRS = frozenset({".git", ".github", "__pycache__", "node_modules"})


def _is_skip_dir(name: str) -> bool:
    return name in _SKIP_DIRS or name.startswith(".")


# --- File copy with skip rules --------------------------------------------

def copy_mod_tree(src: Path, dst: Path) -> None:
    """Copy a discovered mod tree (mod.json, patches/, README, …) into a
    fresh destination, skipping `.git*` and `.github/`. Destination must not
    exist."""
    if dst.exists():
        raise ModImportError(f"copy target already exists: {dst}")
    shutil.copytree(src, dst, ignore=_copy_ignore)


def _copy_ignore(_src: str, names: list[str]) -> set[str]:
    return {n for n in names if _is_skip_dir(n)}
