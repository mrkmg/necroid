"""new — create mods/<base>-<major>/ with mod.json + empty patches/.

The mod dir always carries the workspace major as a suffix. Callers may:
    * pass a bare base ("admin-xray") — suffixed automatically.
    * pass a fully-qualified name ("admin-xray-41") — rejected unless the
      trailing `-<major>` matches the workspace major.
"""
from __future__ import annotations

from ..util import logging_util as log
from ..core.config import read_config
from ..errors import ConfigError, ModAlreadyExists, ModNotFound, PzMajorMismatch
from ..util.fsops import ensure_dir
from ..core.mod import mod_base_name, mod_dirname, new_mod_json, parse_mod_dirname, write_mod_json
from ._resolve import resolve_mod


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
    raw_deps: list[str] = list(getattr(args, "deps", []) or [])
    raw_incompat: list[str] = list(getattr(args, "incompat", []) or [])

    # Store as bare names. We validate that each referenced mod currently
    # exists at the workspace major, but only warn on a miss — the referenced
    # mod may be authored later; hard validation is at enter/install time.
    def _normalise(names: list[str], label: str) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for n in names:
            bare = mod_base_name(n)
            if bare == mod_base_name(dirname):
                raise ConfigError(f"mod cannot list itself in {label}: {n}")
            if bare in seen:
                continue
            seen.add(bare)
            try:
                resolve_mod(profile.mods_dir, ws_major, bare)
            except (ModNotFound, PzMajorMismatch) as e:
                log.warn(f"{label} '{bare}' doesn't resolve yet: {e}")
            out.append(bare)
        return out

    dependencies = _normalise(raw_deps, "--depends-on")
    incompatible_with = _normalise(raw_incompat, "--incompatible-with")

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
        dependencies=dependencies,
        incompatible_with=incompatible_with,
    )
    write_mod_json(d, mj)
    extras = []
    if dependencies:
        extras.append(f"deps={dependencies}")
    if incompatible_with:
        extras.append(f"incompatible={incompatible_with}")
    extras_str = f"  {' '.join(extras)}" if extras else ""
    log.success(
        f"created mod: {d}  (clientOnly={client_only}, pz={mj.expected_version or '?'})"
        f"{extras_str}"
    )
    return 0
