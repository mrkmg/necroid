"""mod.json and patch enumeration.

Schema (v6):
    {
      "name": "lua-profiler",
      "clientOnly": false,                          # default false
      "description": "...",
      "version": "0.2.0",
      "expectedVersion": "41.78.19",                # PZ version at last capture
      "createdAt": "...",
      "updatedAt": "...",
      "pristineSnapshot": "<sha256>",
      "dependencies": ["other-mod"],                # bare names; default []
      "incompatibleWith": ["rival-mod"]             # bare names; default []
    }

`dependencies` and `incompatibleWith` both hold **bare** mod names (no
`-<major>` suffix). They're resolved against the workspace major at
enter/install/capture time via `_resolve.resolve_mod`. This matches the
CLI's bare-name ergonomics and survives workspace rebinding across PZ
majors transparently.

clientOnly mods require a configured client PZ install and may only be
installed to the client destination.

`expectedVersion` is the full PZ version string (from `PzVersion.__str__`)
at the time of the last successful `new` or `capture` against this mod. It
parses to a major version that MUST agree with the mod dir's `-<major>`
suffix (a loader-level cross-check). Only the minor/patch are allowed to
drift — and only with a soft warning at install time.

Mod dir names encode the PZ major: `mods/<base>-<major>/` (e.g.
`admin-xray-41`). The suffix is authoritative for compatibility filtering.
Legacy unsuffixed dirs are migrated at `init` time.

Patch file naming (inside `patches/`):
    <rel>.java.patch   - unified diff vs src-pristine/<rel>.java
    <rel>.java.new     - full file content to create
    <rel>.java.delete  - zero-byte marker: delete <rel>.java

Each patch file may optionally be keyed to an install destination:
    <rel>.java.patch            - shared (applies to whichever destination)
    <rel>.java.patch.client     - client-destination-only
    <rel>.java.patch.server     - server-destination-only
    <rel>.java.new.client       - client-destination-only new file
    <rel>.java.delete.server    - server-destination-only delete marker
    ...etc.

clientOnly=true mods may not carry a .server-postfixed file — patch_items
raises a clear error if one is found.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ..errors import ModJsonError, ModNotFound
from ..util.hashing import file_sha256, string_sha256
from .state import utc_now_iso


Kind = Literal["patch", "new", "delete"]
INSTALL_DESTINATIONS: tuple[str, str] = ("client", "server")

# Mod dir names: `<base>-<major>`, where `<major>` is one-or-more digits.
# Greedy on the base so `foo-bar-41` parses to base=`foo-bar`, major=41.
_MOD_DIRNAME_RE = re.compile(r"^(?P<base>.+)-(?P<major>\d+)$")


@dataclass
class ModJson:
    name: str
    client_only: bool = False
    description: str = ""
    category: str = ""
    version: str = "0.1.0"
    expected_version: str = ""   # PZ version string at last capture, e.g. "41.78.19"
    created_at: str = ""
    updated_at: str = ""
    pristine_snapshot: str = ""
    dependencies: list[str] = field(default_factory=list)       # bare mod names
    incompatible_with: list[str] = field(default_factory=list)  # bare mod names
    _extra: dict = field(default_factory=dict, repr=False)

    def to_json(self) -> dict:
        o = {
            "name": self.name,
            "clientOnly": self.client_only,
            "description": self.description,
            "category": self.category,
            "version": self.version,
            "expectedVersion": self.expected_version,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "pristineSnapshot": self.pristine_snapshot,
            "dependencies": list(self.dependencies),
            "incompatibleWith": list(self.incompatible_with),
        }
        o.update(self._extra)
        return o

    @staticmethod
    def from_json(o: dict) -> "ModJson":
        known = {"name", "clientOnly", "description", "category",
                 "version", "expectedVersion",
                 "createdAt", "updatedAt", "pristineSnapshot",
                 "dependencies", "incompatibleWith"}
        extra = {k: v for k, v in o.items() if k not in known}
        deps_raw = o.get("dependencies") or []
        inc_raw = o.get("incompatibleWith") or []
        return ModJson(
            name=o["name"],
            client_only=bool(o.get("clientOnly", False)),
            description=o.get("description", "") or "",
            category=(o.get("category", "") or "").strip().lower(),
            version=o.get("version", "0.1.0"),
            expected_version=str(o.get("expectedVersion", "") or ""),
            created_at=o.get("createdAt", ""),
            updated_at=o.get("updatedAt", ""),
            pristine_snapshot=o.get("pristineSnapshot", "") or "",
            dependencies=[str(x) for x in deps_raw if isinstance(x, str)],
            incompatible_with=[str(x) for x in inc_raw if isinstance(x, str)],
            _extra=extra,
        )


@dataclass
class PatchItem:
    rel: str         # forward-slash rel path, e.g. zombie/Lua/Event.java
    kind: Kind
    file: Path       # absolute path to the .java.patch / .java.new / .java.delete


def mod_dir(mods_dir: Path, name: str) -> Path:
    return mods_dir / name


def ensure_mod_exists(mods_dir: Path, name: str) -> Path:
    d = mod_dir(mods_dir, name)
    if not d.is_dir():
        raise ModNotFound(f"mod '{name}' not found at {d}")
    return d


# --- Mod-dir-name parsing (major version suffix) --------------------------

def parse_mod_dirname(dirname: str) -> tuple[str, int] | None:
    """Split a mod dir name into (base, major) or return None if the dir name
    has no `-<digits>` suffix. Greedy on the base — the last `-<digits>` wins.

    Examples:
        parse_mod_dirname("admin-xray-41") -> ("admin-xray", 41)
        parse_mod_dirname("foo")           -> None
        parse_mod_dirname("v1-client-42")  -> ("v1-client", 42)
    """
    m = _MOD_DIRNAME_RE.match(dirname)
    if not m:
        return None
    return m.group("base"), int(m.group("major"))


def mod_dirname(base: str, major: int) -> str:
    """Join a base and major into a canonical mod dir name."""
    return f"{base}-{int(major)}"


def mod_base_name(dirname: str) -> str:
    """Return the display base for a mod dir name (no `-<major>` suffix).
    Falls back to the full dirname for unversioned (legacy) dirs."""
    parsed = parse_mod_dirname(dirname)
    return parsed[0] if parsed else dirname


def mod_major(dirname: str) -> int | None:
    """Return the major version encoded in a mod dir name, or None."""
    parsed = parse_mod_dirname(dirname)
    return parsed[1] if parsed else None


def read_mod_json(md: Path) -> ModJson:
    path = md / "mod.json"
    if not path.exists():
        raise ModNotFound(f"mod.json not found in {md}")
    mj = ModJson.from_json(json.loads(path.read_text(encoding="utf-8")))

    # Cross-check: if the dir name encodes a major AND expectedVersion is set,
    # they must agree on major. Catches hand-edit mistakes.
    dir_parsed = parse_mod_dirname(md.name)
    if dir_parsed and mj.expected_version:
        try:
            from ..pz.pzversion import PzVersion
            ev = PzVersion.parse(mj.expected_version)
        except Exception:
            # Soft: unparseable expected_version is surfaced as a warning elsewhere,
            # not a blocker here — read_mod_json must not explode on stale data.
            return mj
        if ev.major != dir_parsed[1]:
            raise ModJsonError(
                f"mod '{md.name}': expectedVersion '{mj.expected_version}' "
                f"is major {ev.major}, but dir suffix is -{dir_parsed[1]}. "
                f"Fix one or the other."
            )
    return mj


def write_mod_json(md: Path, mj: ModJson) -> None:
    (md / "mod.json").write_text(json.dumps(mj.to_json(), indent=2) + "\n", encoding="utf-8")


def new_mod_json(name: str, description: str = "", client_only: bool = False,
                 expected_version: str = "",
                 category: str = "",
                 dependencies: list[str] | None = None,
                 incompatible_with: list[str] | None = None) -> ModJson:
    now = utc_now_iso()
    return ModJson(
        name=name, client_only=client_only, description=description,
        category=(category or "").strip().lower(),
        version="0.1.0",
        expected_version=expected_version,
        created_at=now, updated_at=now, pristine_snapshot="",
        dependencies=list(dependencies or []),
        incompatible_with=list(incompatible_with or []),
    )


_KIND_SUFFIXES: tuple[tuple[str, Kind], ...] = (
    (".java.patch", "patch"),
    (".java.new", "new"),
    (".java.delete", "delete"),
)


def parse_patch_filename(rel_full: str) -> tuple[str, Kind, frozenset[str]] | None:
    """Parse a path under patches/. Returns (rel, kind, applies_to) or None
    if the filename isn't a patch file.

    applies_to:
      - frozenset({"client", "server"}) for shared (no postfix)
      - frozenset({"client"}) for *.client postfix
      - frozenset({"server"}) for *.server postfix
    """
    base = rel_full
    postfix = ""
    for t in INSTALL_DESTINATIONS:
        if base.endswith("." + t):
            postfix = t
            base = base[: -(len(t) + 1)]
            break
    for suf, kind in _KIND_SUFFIXES:
        if base.endswith(suf):
            rel = base[: -len(suf)] + ".java"
            applies = frozenset((postfix,)) if postfix else frozenset(INSTALL_DESTINATIONS)
            return rel, kind, applies
    return None


def prune_empty_dirs(root: Path) -> None:
    """Remove empty directories under `root` (bottom-up). `root` itself kept."""
    if not root.exists():
        return
    for p in sorted(root.rglob("*"), key=lambda x: len(x.parts), reverse=True):
        if p.is_dir() and p != root:
            try:
                p.rmdir()
            except OSError:
                pass


def patch_items(md: Path, install_to: str) -> list[PatchItem]:
    """Enumerate patch items for a mod, filtered for a given install destination.

    - clientOnly mods may not carry any `.server`-postfixed file → hard error.
    - Postfixed file overrides the shared file for the matching destination.
      Presence of both shared and a matching postfix for the chosen destination
      is a hard error.
    """
    if install_to not in INSTALL_DESTINATIONS:
        raise ModJsonError(f"invalid install_to '{install_to}' (expected 'client' or 'server')")

    mj = read_mod_json(md)
    patches = md / "patches"
    if not patches.exists():
        return []

    out: list[PatchItem] = []
    seen: dict[tuple[str, Kind], PatchItem] = {}
    for p in sorted(patches.rglob("*")):
        if not p.is_file():
            continue
        rel_full = p.relative_to(patches).as_posix()
        parsed = parse_patch_filename(rel_full)
        if parsed is None:
            continue
        rel, kind, applies = parsed
        is_postfixed = len(applies) == 1
        if is_postfixed and mj.client_only and "server" in applies:
            raise ModJsonError(
                f"mod '{mj.name}' is clientOnly but contains a server-postfixed "
                f"patch file: {rel_full}. Remove the .server postfix or drop clientOnly."
            )
        if install_to not in applies:
            continue
        key = (rel, kind)
        if key in seen:
            raise ModJsonError(
                f"mod '{mj.name}': ambiguous patch for {rel} [{kind}] — "
                f"both {seen[key].file.name} and {p.name} apply to install_to={install_to}"
            )
        item = PatchItem(rel=rel, kind=kind, file=p)
        seen[key] = item
        out.append(item)
    return out


def list_mods(mods_dir: Path, workspace_major: int | None = None,
              include_all: bool = False) -> list[str]:
    """List mod dir names under `mods_dir`, sorted.

    Default (no args): returns every immediate subdirectory — same as before.
    With `workspace_major`: filters to dirs whose `-<major>` suffix matches
    (unsuffixed legacy dirs are excluded). `include_all=True` overrides the
    filter and returns every subdirectory regardless of suffix."""
    if not mods_dir.exists():
        return []
    all_dirs = sorted(p.name for p in mods_dir.iterdir() if p.is_dir())
    if include_all or workspace_major is None:
        return all_dirs
    return [d for d in all_dirs if mod_major(d) == int(workspace_major)]


def pristine_snapshot(pristine_dir: Path, items: list[PatchItem]) -> str:
    """Concat per-touched-file SHA256s (sorted by rel), then hash. Stable across reruns."""
    parts: list[str] = []
    for it in sorted(items, key=lambda x: x.rel):
        p = pristine_dir / it.rel
        h = file_sha256(p) or "ABSENT"
        parts.append(f"{it.rel}|{h}")
    return string_sha256("\n".join(parts))


# --- Origin metadata (imported-from-GitHub) ------------------------------
#
# Stored as a top-level "origin" object inside mod.json, riding through the
# `_extra` dict on ModJson. No dataclass field — keeps the schema forward-
# compat and means a hand-written mod (no origin) round-trips identically.

_ORIGIN_KEY = "origin"
_ORIGIN_REQUIRED_FIELDS = (
    "type", "repo", "ref", "subdir", "commitSha",
    "archiveUrl", "importedAt", "upstreamVersion",
)


def has_origin(mj: ModJson) -> bool:
    """True if this mod was imported (has an origin block we can update from)."""
    o = mj._extra.get(_ORIGIN_KEY)
    return isinstance(o, dict) and bool(o.get("repo"))


def read_origin(mj: ModJson) -> dict | None:
    """Return the origin block as a plain dict, or None if absent.

    Does not validate field completeness — callers that need strict origins
    should check has_origin() and individual fields. Returns a copy to avoid
    accidental mutation of `_extra`."""
    o = mj._extra.get(_ORIGIN_KEY)
    if not isinstance(o, dict):
        return None
    return dict(o)


def write_origin(mj: ModJson, *, type: str = "github",
                 repo: str, ref: str, subdir: str, commitSha: str,
                 archiveUrl: str, importedAt: str, upstreamVersion: str,
                 host: str | None = None) -> None:
    """Set / replace the origin block on `mj` in place. Caller must persist via
    write_mod_json afterward.

    `host` is optional and stored only when truthy — absent means the provider's
    canonical host (github.com for github). GitLab origins must supply `host`
    (e.g. "gitlab.com" or a self-hosted instance)."""
    out: dict = {
        "type": type,
        "repo": repo,
        "ref": ref,
        "subdir": subdir,
        "commitSha": commitSha,
        "archiveUrl": archiveUrl,
        "importedAt": importedAt,
        "upstreamVersion": upstreamVersion,
    }
    if host:
        out["host"] = host
    mj._extra[_ORIGIN_KEY] = out
