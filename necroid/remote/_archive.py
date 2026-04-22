"""Provider-agnostic archive + mod-discovery helpers.

Used by ``necroid/remote/github.py`` and ``necroid/remote/gitlab.py``. Once an
archive is on disk, extraction, mod.json discovery, and copy-in are identical
regardless of where the zip came from.

Stdlib only.
"""
from __future__ import annotations

import json
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..errors import ModImportError
from ..core.mod import ModJson


# --- Shared dataclasses ---------------------------------------------------

@dataclass
class CommitResolution:
    """Result of resolving a branch / tag / sha to a concrete commit SHA."""
    sha: str
    is_tag: bool  # informational; consumed by per-provider archive URL builder


# --- Extraction -----------------------------------------------------------

def extract_archive(zip_path: Path, dest_dir: Path) -> Path:
    """Extract ``zip_path`` into ``dest_dir``. Refuses entries that would escape
    the destination (zip-slip). Returns the wrapper directory inside dest_dir
    (both GitHub and GitLab archives wrap content in a single top-level dir)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        if not names:
            raise ModImportError("archive is empty")
        for n in names:
            np = Path(n)
            if np.is_absolute() or any(p == ".." for p in np.parts):
                raise ModImportError(f"archive contains unsafe entry: {n}")
        zf.extractall(dest_dir)

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
    """A ``mod.json`` found inside an extracted repo.

    ``dirname`` is the canonical Necroid mod-dir name — ``<base>-<major>``. We
    derive it from the final path component of ``subdir`` if it parses, else
    from ``mj.name`` if that parses, else fall back to the raw subdir/name.
    Callers must verify ``dirname`` actually parses cleanly before treating
    it as a target.

    ``mod_major`` is the int extracted from ``dirname``, or None if it did not
    parse. None means the mod is missing the required ``-<digits>`` suffix
    and cannot be imported.
    """
    subdir: str           # forward-slash, "" when at the repo root
    mj: ModJson
    src_path: Path = field(default_factory=Path)  # absolute dir containing mod.json
    dirname: str = ""     # canonical `<base>-<major>` (preserved from upstream)
    mod_major: Optional[int] = None


def discover_mods(extracted_root: Path) -> list[DiscoveredMod]:
    """Find every mod.json under ``extracted_root``.

    The only supported layout is the canonical Necroid one:

      <root>/mods/<name>-<major>/mod.json

    Authors are expected to use ``necroid init`` in their repo, which
    scaffolds this layout directly. Anything else is rejected.
    """
    out: list[DiscoveredMod] = []

    canonical = extracted_root / "mods"
    if canonical.is_dir():
        for child in sorted(canonical.iterdir()):
            if not child.is_dir() or _is_skip_dir(child.name):
                continue
            if (child / "mod.json").is_file():
                out.append(_load_discovered(
                    extracted_root, f"mods/{child.name}"))

    if not out:
        raise ModImportError(
            "repo contains no mods/<name>/mod.json "
            "(the canonical Necroid layout — run `necroid init` in the repo)"
        )

    return out


def _load_discovered(root: Path, subdir: str) -> DiscoveredMod:
    from ..core.mod import parse_mod_dirname

    src = root if subdir == "" else (root / subdir)
    try:
        raw = json.loads((src / "mod.json").read_text(encoding="utf-8"))
        mj = ModJson.from_json(raw)
    except (OSError, json.JSONDecodeError, KeyError) as e:
        loc = subdir or "<root>"
        raise ModImportError(f"upstream mod.json at '{loc}' is not valid JSON / schema: {e}")

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
    fresh destination, skipping ``.git*`` and ``.github/``. Destination must
    not exist."""
    if dst.exists():
        raise ModImportError(f"copy target already exists: {dst}")
    shutil.copytree(src, dst, ignore=_copy_ignore)


def _copy_ignore(_src: str, names: list[str]) -> set[str]:
    return {n for n in names if _is_skip_dir(n)}
