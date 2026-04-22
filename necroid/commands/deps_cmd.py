"""deps — edit a mod's `dependencies` / `incompatibleWith` lists.

Subcommands:
    necroid deps show    <mod>
    necroid deps add     <mod> --requires  <other>
    necroid deps add     <mod> --conflicts <other>
    necroid deps remove  <mod> --requires  <other>
    necroid deps remove  <mod> --conflicts <other>

Each `add --requires` runs a dry-run cycle-detection against the in-memory
graph so the edit is rejected before it can corrupt mod.json. Names are
stored bare; resolution happens at enter/install/capture time.
"""
from __future__ import annotations

from ..util import logging_util as log
from ..core.config import read_config
from ..core.depgraph import resolve_deps
from ..errors import (
    ConfigError,
    ModDependencyCycle,
    ModNotFound,
    PzMajorMismatch,
)
from ..core.mod import ensure_mod_exists, mod_base_name, read_mod_json, write_mod_json
from ._resolve import resolve_mod


def _load(args) -> tuple:
    """Return (profile, cfg, mod_dir, mod_json, target_bare)."""
    p = args.profile
    cfg = read_config(args.root)
    if not cfg.workspace_major:
        raise ConfigError("workspace has no bound major. Run `necroid init` first.")
    name = resolve_mod(p.mods_dir, cfg.workspace_major, args.mod)
    md = ensure_mod_exists(p.mods_dir, name)
    mj = read_mod_json(md)
    return p, cfg, md, mj, name


def _validate_other(profile, ws_major: int, other_raw: str, self_name: str) -> str:
    """Resolve + bare-ify an `--requires` / `--conflicts` argument.
    Warn (don't fail) if the referenced mod doesn't exist yet — same as
    `necroid new --depends-on`."""
    bare = mod_base_name(other_raw)
    if bare == mod_base_name(self_name):
        raise ConfigError(f"mod cannot reference itself: {bare}")
    try:
        resolve_mod(profile.mods_dir, ws_major, bare)
    except (ModNotFound, PzMajorMismatch) as e:
        log.warn(f"target '{bare}' doesn't resolve yet: {e}")
    return bare


def run(args) -> int:
    action = args.deps_action
    if action == "show":
        return _show(args)
    if action == "add":
        return _add(args)
    if action == "remove":
        return _remove(args)
    raise SystemExit(f"unknown deps action: {action}")


def _show(args) -> int:
    _p, _cfg, _md, mj, _name = _load(args)
    deps = mj.dependencies or []
    inc = mj.incompatible_with or []
    print(f"{mj.name}:")
    print(f"  dependencies    : {', '.join(deps) if deps else '(none)'}")
    print(f"  incompatibleWith: {', '.join(inc) if inc else '(none)'}")
    return 0


def _add(args) -> int:
    p, cfg, md, mj, name = _load(args)
    requires = getattr(args, "requires", None)
    conflicts = getattr(args, "conflicts", None)
    if not requires and not conflicts:
        raise SystemExit("usage: necroid deps add <mod> --requires <other> | --conflicts <other>")

    if requires:
        other = _validate_other(p, cfg.workspace_major, requires, name)
        if other in mj.dependencies:
            log.info(f"{mj.name}: already depends on '{other}' — no change")
            return 0
        # Dry-run cycle check by staging the edit in memory.
        mj.dependencies = [*mj.dependencies, other]
        write_mod_json(md, mj)
        try:
            resolve_deps(p.mods_dir, cfg.workspace_major, name)
        except ModDependencyCycle as e:
            # Roll back the stage and re-raise typed.
            mj.dependencies = [d for d in mj.dependencies if d != other]
            write_mod_json(md, mj)
            raise ModDependencyCycle(f"refused: {e}") from None
        log.success(f"{mj.name}: added dependency '{other}'")
        return 0

    if conflicts:
        other = _validate_other(p, cfg.workspace_major, conflicts, name)
        if other in mj.incompatible_with:
            log.info(f"{mj.name}: already incompatible with '{other}' — no change")
            return 0
        mj.incompatible_with = [*mj.incompatible_with, other]
        write_mod_json(md, mj)
        log.success(f"{mj.name}: added incompatibility '{other}'")
        return 0

    return 0


def _remove(args) -> int:
    p, cfg, md, mj, name = _load(args)
    requires = getattr(args, "requires", None)
    conflicts = getattr(args, "conflicts", None)
    if not requires and not conflicts:
        raise SystemExit("usage: necroid deps remove <mod> --requires <other> | --conflicts <other>")

    if requires:
        target = mod_base_name(requires)
        if target not in mj.dependencies:
            log.info(f"{mj.name}: not a dependency: '{target}' — no change")
            return 0
        mj.dependencies = [d for d in mj.dependencies if d != target]
        write_mod_json(md, mj)
        log.success(f"{mj.name}: removed dependency '{target}'")
        return 0

    if conflicts:
        target = mod_base_name(conflicts)
        if target not in mj.incompatible_with:
            log.info(f"{mj.name}: not incompatible with '{target}' — no change")
            return 0
        mj.incompatible_with = [d for d in mj.incompatible_with if d != target]
        write_mod_json(md, mj)
        log.success(f"{mj.name}: removed incompatibility '{target}'")
        return 0

    return 0
