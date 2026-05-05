"""config — show or update workspace config fields.

Usage:
    necroid config show
    necroid config set client-pz-install '<path>'
    necroid config set server-pz-install '<path>'
    necroid config set default-install-to client|server
    necroid config unset server-pz-install
    necroid config unset client-pz-install

Edits the workspace config at `<pz>/necroid/config.json` via the existing
read_config / write_config helpers. Path values are validated to exist and
to fingerprint as a real PZ install before being saved.

For first-time setup (no workspace yet), use `necroid init --pz-install <path>`.
"""
from __future__ import annotations

from pathlib import Path

from ..util import logging_util as log
from ..core.config import expand_config_path, read_config, write_config
from ..errors import ConfigError
from ..pz.steam_discovery import fingerprint_pz_install


_PATH_FIELDS = {"client-pz-install", "server-pz-install"}
_CHOICE_FIELDS = {"default-install-to": ("client", "server")}
_ALL_FIELDS = _PATH_FIELDS | set(_CHOICE_FIELDS)


def _print_show(cfg) -> int:
    log.info(f"config: {cfg.path}")
    log.info(f"  workspace anchor (pointer): {cfg.pz_install}")
    log.info(f"  clientPzInstall: {cfg.client_pz_install or '(unset)'}")
    log.info(f"  serverPzInstall: {cfg.server_pz_install or '(unset)'}")
    log.info(f"  defaultInstallTo: {cfg.default_install_to}")
    log.info(f"  workspaceSource: {cfg.workspace_source}")
    log.info(f"  workspaceMajor: {cfg.workspace_major}")
    log.info(f"  workspaceVersion: {cfg.workspace_version}")
    log.info(f"  workspaceLayout: {cfg.workspace_layout}")
    log.info(f"  javaRelease: {cfg.java_release}")
    return 0


def _resolve_and_validate_path(value: str, root: Path) -> Path:
    p = expand_config_path(value, root)
    if p is None:
        raise ConfigError(f"could not resolve path '{value}'")
    if not p.exists():
        raise ConfigError(f"path does not exist: {p}")
    if not p.is_dir():
        raise ConfigError(f"not a directory: {p}")
    if not fingerprint_pz_install(p):
        raise ConfigError(
            f"path does not look like a Project Zomboid install: {p}\n"
            f"    expected one of: projectzomboid.jar, ProjectZomboid64.exe, "
            f"projectzomboid64.sh, or a `zombie/` subdir."
        )
    return p


def _layout_for(pz: Path) -> str:
    if (pz / "projectzomboid.jar").is_file():
        return "jar"
    return "loose"


def _do_set(cfg, field: str, value: str, root: Path) -> int:
    if field in _PATH_FIELDS:
        new_path = _resolve_and_validate_path(value, root)
        if field == "client-pz-install":
            old = cfg.client_pz_install
            cfg.client_pz_install = new_path
            new_layout = _layout_for(new_path)
            if cfg.workspace_layout and new_layout != cfg.workspace_layout:
                log.warn(
                    f"workspaceLayout is '{cfg.workspace_layout}' but {new_path} "
                    f"looks like '{new_layout}'.\n"
                    f"    this likely means the new install is a different PZ major; "
                    f"run `necroid resync-pristine` to rebuild the workspace."
                )
        else:
            old = cfg.server_pz_install
            cfg.server_pz_install = new_path
        anchor = cfg.pz_install or new_path
        write_config(anchor, cfg)
        log.success(f"set {field}: {old or '(unset)'} -> {new_path}")
        return 0

    if field in _CHOICE_FIELDS:
        choices = _CHOICE_FIELDS[field]
        if value not in choices:
            raise ConfigError(
                f"{field} must be one of: {', '.join(choices)} (got {value!r})"
            )
        old = cfg.default_install_to
        cfg.default_install_to = value
        anchor = cfg.pz_install
        if anchor is None:
            raise ConfigError("no workspace anchor; run `necroid init` first")
        write_config(anchor, cfg)
        log.success(f"set {field}: {old} -> {value}")
        return 0

    raise ConfigError(
        f"unknown field {field!r}. valid fields: {', '.join(sorted(_ALL_FIELDS))}"
    )


def _do_unset(cfg, field: str) -> int:
    if field == "client-pz-install":
        old = cfg.client_pz_install
        cfg.client_pz_install = None
    elif field == "server-pz-install":
        old = cfg.server_pz_install
        cfg.server_pz_install = None
    else:
        raise ConfigError(
            f"cannot unset {field!r}. unsettable fields: client-pz-install, server-pz-install"
        )
    anchor = cfg.pz_install
    if anchor is None:
        raise ConfigError("no workspace anchor; run `necroid init` first")
    write_config(anchor, cfg)
    log.success(f"unset {field} (was {old or '(unset)'})")
    return 0


def run(args) -> int:
    root: Path = args.root
    action = args.config_action

    cfg = read_config(root, required=True)

    if action == "show":
        return _print_show(cfg)
    if action == "set":
        return _do_set(cfg, args.field, args.value, root)
    if action == "unset":
        return _do_unset(cfg, args.field)
    raise ConfigError(f"unknown config action: {action}")
