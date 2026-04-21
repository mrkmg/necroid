"""Per-profile state + enter files."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# --- .mod-state.json -----------------------------------------------------

@dataclass
class InstalledEntry:
    rel: str            # forward-slash zombie/<...>.class path (under PZ install root)
    mod_origin: str     # which mod produced this class
    sha256: str         # uppercase hex

    def to_json(self) -> dict:
        return {"rel": self.rel, "modOrigin": self.mod_origin, "sha256": self.sha256}

    @staticmethod
    def from_json(o: dict) -> "InstalledEntry":
        return InstalledEntry(rel=o["rel"], mod_origin=o["modOrigin"], sha256=o["sha256"])


@dataclass
class ModState:
    version: int = 1
    stack: list[str] = field(default_factory=list)
    installed_at: str | None = None
    installed: list[InstalledEntry] = field(default_factory=list)
    pz_version: str | None = None   # full PZ version string recorded at install time

    def to_json(self) -> dict:
        return {
            "version": self.version,
            "stack": list(self.stack),
            "installedAt": self.installed_at,
            "installed": [e.to_json() for e in self.installed],
            "pzVersion": self.pz_version,
        }

    @staticmethod
    def from_json(o: dict) -> "ModState":
        pz = o.get("pzVersion")
        return ModState(
            version=int(o.get("version", 1)),
            stack=list(o.get("stack") or []),
            installed_at=o.get("installedAt"),
            installed=[InstalledEntry.from_json(e) for e in (o.get("installed") or [])],
            pz_version=str(pz) if pz else None,
        )


def read_state(state_file: Path) -> ModState:
    if not state_file.exists():
        return ModState()
    return ModState.from_json(json.loads(state_file.read_text(encoding="utf-8")))


def write_state(state_file: Path, state: ModState) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state.to_json(), indent=2) + "\n", encoding="utf-8")


def reset_state(state_file: Path) -> None:
    write_state(state_file, ModState())


# --- .mod-enter.json -----------------------------------------------------

@dataclass
class EnterState:
    mod: str
    entered_at: str
    install_as: str = "client"   # which destination's postfix variant was applied

    def to_json(self) -> dict:
        return {
            "mod": self.mod,
            "enteredAt": self.entered_at,
            "installAs": self.install_as,
        }


def read_enter(enter_file: Path) -> EnterState | None:
    """Read the entered-mod record.

    Single-mod schema: {"mod": "...", "enteredAt": "...", "installAs": "..."}.
    Legacy schema carried a `stack: [...]` list; a single-element legacy stack
    auto-migrates in-memory (caller should rewrite on next write). A multi-mod
    legacy stack is treated as invalid — enter-stacking is no longer supported.
    """
    if not enter_file.exists():
        return None
    o = json.loads(enter_file.read_text(encoding="utf-8"))
    mod = o.get("mod")
    if not mod:
        legacy_stack = list(o.get("stack") or [])
        if len(legacy_stack) == 1:
            mod = legacy_stack[0]
        else:
            # Unusable (empty or multi-mod). Caller treats as "nothing entered".
            return None
    return EnterState(
        mod=str(mod),
        entered_at=o.get("enteredAt", ""),
        install_as=o.get("installAs", "client"),
    )


def write_enter(enter_file: Path, mod: str, install_as: str = "client") -> EnterState:
    es = EnterState(mod=mod, entered_at=_utc_iso(), install_as=install_as)
    enter_file.parent.mkdir(parents=True, exist_ok=True)
    enter_file.write_text(json.dumps(es.to_json(), indent=2) + "\n", encoding="utf-8")
    return es


def clear_enter(enter_file: Path) -> None:
    if enter_file.exists():
        enter_file.unlink()


def utc_now_iso() -> str:
    return _utc_iso()
