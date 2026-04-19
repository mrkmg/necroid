"""mod.json and patch enumeration.

Schema (v2):
    {
      "name": "lua-profiler",
      "target": "client" | "server",    # default "client" if absent
      "description": "...",
      "version": "0.2.0",
      "createdAt": "...",
      "updatedAt": "...",
      "pristineSnapshot": "<sha256>"
    }

Patch extensions:
  <rel>.java.patch  - unified diff vs src-pristine/<rel>.java
  <rel>.java.new    - full file content to create
  <rel>.java.delete - (zero-byte marker) delete <rel>.java
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .errors import ModNotFound
from .hashing import file_sha256, string_sha256
from .state import utc_now_iso


Target = Literal["client", "server"]
Kind = Literal["patch", "new", "delete"]


@dataclass
class ModJson:
    name: str
    target: Target = "client"
    description: str = ""
    version: str = "0.1.0"
    created_at: str = ""
    updated_at: str = ""
    pristine_snapshot: str = ""
    _extra: dict = field(default_factory=dict, repr=False)

    def to_json(self) -> dict:
        o = {
            "name": self.name,
            "target": self.target,
            "description": self.description,
            "version": self.version,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "pristineSnapshot": self.pristine_snapshot,
        }
        o.update(self._extra)
        return o

    @staticmethod
    def from_json(o: dict) -> "ModJson":
        known = {"name", "target", "description", "version", "createdAt", "updatedAt", "pristineSnapshot"}
        extra = {k: v for k, v in o.items() if k not in known}
        tgt = o.get("target", "client")
        if tgt not in ("client", "server"):
            tgt = "client"
        return ModJson(
            name=o["name"],
            target=tgt,  # type: ignore[arg-type]
            description=o.get("description", "") or "",
            version=o.get("version", "0.1.0"),
            created_at=o.get("createdAt", ""),
            updated_at=o.get("updatedAt", ""),
            pristine_snapshot=o.get("pristineSnapshot", "") or "",
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


def read_mod_json(md: Path) -> ModJson:
    path = md / "mod.json"
    if not path.exists():
        raise ModNotFound(f"mod.json not found in {md}")
    return ModJson.from_json(json.loads(path.read_text(encoding="utf-8")))


def write_mod_json(md: Path, mj: ModJson) -> None:
    (md / "mod.json").write_text(json.dumps(mj.to_json(), indent=2) + "\n", encoding="utf-8")


def new_mod_json(name: str, description: str = "", target: Target = "client") -> ModJson:
    now = utc_now_iso()
    return ModJson(
        name=name, target=target, description=description, version="0.1.0",
        created_at=now, updated_at=now, pristine_snapshot="",
    )


def patch_items(md: Path) -> list[PatchItem]:
    patches = md / "patches"
    if not patches.exists():
        return []
    out: list[PatchItem] = []
    for p in sorted(patches.rglob("*")):
        if not p.is_file():
            continue
        rel_full = p.relative_to(patches).as_posix()
        if rel_full.endswith(".java.patch"):
            out.append(PatchItem(rel=rel_full[:-len(".patch")], kind="patch", file=p))
        elif rel_full.endswith(".java.new"):
            out.append(PatchItem(rel=rel_full[:-len(".new")], kind="new", file=p))
        elif rel_full.endswith(".java.delete"):
            out.append(PatchItem(rel=rel_full[:-len(".delete")], kind="delete", file=p))
    return out


def list_mods(mods_dir: Path) -> list[str]:
    if not mods_dir.exists():
        return []
    return sorted(p.name for p in mods_dir.iterdir() if p.is_dir())


def pristine_snapshot(pristine_dir: Path, items: list[PatchItem]) -> str:
    """Concat per-touched-file SHA256s (sorted by rel), then hash. Stable across reruns."""
    parts: list[str] = []
    for it in sorted(items, key=lambda x: x.rel):
        p = pristine_dir / it.rel
        h = file_sha256(p) or "ABSENT"
        parts.append(f"{it.rel}|{h}")
    return string_sha256("\n".join(parts))
