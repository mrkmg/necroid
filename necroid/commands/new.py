"""new — create mods/<name>/ with mod.json + empty patches/."""
from __future__ import annotations

from .. import logging_util as log
from ..errors import ModAlreadyExists
from ..fsops import ensure_dir
from ..mod import new_mod_json, write_mod_json


def run(args) -> int:
    profile = args.profile
    name = args.name
    client_only = bool(getattr(args, "client_only", False))
    d = profile.mods_dir / name
    if d.exists():
        raise ModAlreadyExists(f"mod '{name}' already exists at {d}")
    ensure_dir(d)
    ensure_dir(d / "patches")
    mj = new_mod_json(name=name, description=args.description or "", client_only=client_only)
    write_mod_json(d, mj)
    log.success(f"created mod: {d}  (clientOnly={client_only})")
    return 0
