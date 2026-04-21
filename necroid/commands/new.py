"""new — create mods/<base>-<major>/ with mod.json + empty patches/.

The mod dir always carries the workspace major as a suffix. Callers may:
    * pass a bare base ("admin-xray") — suffixed automatically.
    * pass a fully-qualified name ("admin-xray-41") — rejected unless the
      trailing `-<major>` matches the workspace major.
"""
from __future__ import annotations

from .. import logging_util as log
from ..config import read_config
from ..errors import ConfigError, ModAlreadyExists
from ..fsops import ensure_dir
from ..mod import mod_dirname, new_mod_json, parse_mod_dirname, write_mod_json


def run(args) -> int:
    profile = args.profile
    cfg = read_config(args.root)
    if not cfg.workspace_major:
        raise ConfigError(
            "workspace has no bound major. Run `necroid init` before creating mods."
        )
    ws_major = int(cfg.workspace_major)

    requested = args.name
    parsed = parse_mod_dirname(requested)
    if parsed is None:
        dirname = mod_dirname(requested, ws_major)
    else:
        base, req_major = parsed
        if req_major != ws_major:
            raise ConfigError(
                f"cannot create '{requested}': requested major {req_major} does not "
                f"match workspace major {ws_major}. Drop the suffix (pass '{base}') or "
                f"switch workspaces first."
            )
        dirname = requested

    client_only = bool(getattr(args, "client_only", False))
    d = profile.mods_dir / dirname
    if d.exists():
        raise ModAlreadyExists(f"mod '{dirname}' already exists at {d}")
    ensure_dir(d)
    ensure_dir(d / "patches")
    mj = new_mod_json(
        name=dirname,
        description=args.description or "",
        client_only=client_only,
        expected_version=cfg.workspace_version or "",
    )
    write_mod_json(d, mj)
    log.success(f"created mod: {d}  (clientOnly={client_only}, pz={mj.expected_version or '?'})")
    return 0
