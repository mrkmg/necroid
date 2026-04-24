"""argparse CLI. All subcommands delegate to `commands/*.run(args)`.

Global flags:
    --root PATH                 explicit workspace root (else: auto-discover from cwd)
    --gui                       launch the tkinter GUI instead of running a subcommand
    -server                     GUI-only shorthand: open the GUI with install-to=server selected

Per-command flags:
    init / resync-pristine:   --from {client,server}
    enter:                    --as   {client,server}
    install / uninstall / verify / list / status:  --to {client,server}
    uninstall:                --cascade
    new:                      --depends-on MOD / --incompatible-with MOD
    deps:                     show <mod> | add|remove <mod> --requires|--conflicts MOD
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .util import logging_util as log
from .core.config import read_config
from .errors import ConfigError, PzModderError
from .core.profile import find_root, load_profile, resolve_install_to, resolve_source
from .commands import (
    capture as capture_cmd,
    clean as clean_cmd,
    deps_cmd,
    diff as diff_cmd,
    doctor as doctor_cmd,
    enter as enter_cmd,
    import_cmd,
    init as init_cmd,
    install_cmd,
    list_cmd,
    mod_update as mod_update_cmd,
    new as new_cmd,
    reset as reset_cmd,
    resync_pristine as resync_cmd,
    status as status_cmd,
    test as test_cmd,
    uninstall as uninstall_cmd,
    update as update_cmd,
    verify as verify_cmd,
)
from .remote import updater


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="necroid",
        description="Necroid — Beyond Workshop. Diff-based Java mod manager for Project Zomboid (client and dedicated server).",
    )
    p.add_argument("-V", "--version", action="version",
                   version=f"necroid {__version__}")
    p.add_argument("--root", type=Path, default=None,
                   help="workspace root (default: auto-discover upward from cwd)")
    p.add_argument("--gui", action="store_true",
                   help="launch the GUI instead of running a subcommand")
    # GUI-only shorthand. Accepted as a top-level flag so `necroid --gui -server`
    # still works. For non-GUI commands it's silently ignored.
    p.add_argument("-server", dest="gui_server", action="store_true",
                   help=argparse.SUPPRESS)

    sub = p.add_subparsers(dest="command", metavar="<command>")

    s = sub.add_parser("init", help="bootstrap the shared workspace from a PZ install")
    s.add_argument("--from", dest="from_install", choices=("client", "server"), default=None,
                   help="which PZ install to seed the workspace from (default: config.workspaceSource or 'client')")
    s.add_argument("--pz-install", default=None, help="override PZ install path")
    s.add_argument("--force", action="store_true", help="redo steps even if they look up-to-date")
    s.add_argument("--yes", "-y", action="store_true",
                   help="accept the detected PZ major and auto-migrate legacy mod dirs without prompting")
    s.add_argument("--major", type=int, default=None,
                   help="override the detected workspace major (advanced; normally leave unset)")

    s = sub.add_parser("new", help="create a new mod")
    s.add_argument("name")
    s.add_argument("--description", "-d", default="")
    s.add_argument("--client-only", dest="client_only", action="store_true",
                   help="mark the new mod as clientOnly (install allowed only to client PZ)")
    s.add_argument("--depends-on", dest="deps", action="append", default=[],
                   metavar="MOD",
                   help="declare a dependency on another mod (repeatable; bare mod name)")
    s.add_argument("--incompatible-with", dest="incompat", action="append", default=[],
                   metavar="MOD",
                   help="declare an incompatibility with another mod (repeatable; bare mod name)")

    s = sub.add_parser("list", help="tabular view of all mods")
    s.add_argument("--to", dest="install_to", choices=("client", "server"), default=None,
                   help="destination to count patches for (default: config.defaultInstallTo)")
    s.add_argument("--all", dest="show_all", action="store_true",
                   help="show mods for every PZ major (default: filter to workspaceMajor)")

    s = sub.add_parser("status", help="working-tree divergence or per-mod patch applicability")
    s.add_argument("name", nargs="?", default=None)
    s.add_argument("--to", dest="install_to", choices=("client", "server"), default=None)

    s = sub.add_parser("enter", help="seed src-<mod>/ from pristine + patches and mark it entered")
    s.add_argument("mod", help="mod name (only one at a time; use `install` for stacks)")
    s.add_argument("--as", dest="install_as", choices=("client", "server"), default=None,
                   help="destination variant to apply (default: config.defaultInstallTo)")
    s.add_argument("--force", action="store_true",
                   help="re-seed src-<mod>/ even if it already exists (discards local edits)")

    s = sub.add_parser("capture", help="diff src-<mod>/ vs pristine, rewrite the mod's patches")
    s.add_argument("name")
    s.add_argument("--as", dest="install_as", choices=("client", "server"), default=None,
                   help="fallback variant if no enter state is recorded")

    s = sub.add_parser("diff", help="concatenate a mod's patches to stdout")
    s.add_argument("name")

    sub.add_parser("reset", help="re-seed the entered mod's src-<mod>/ from pristine + patches")

    s = sub.add_parser("clean", help="delete per-mod src-*/ working trees at the repo root")
    s.add_argument("mod", nargs="?", default=None,
                   help="specific mod to clean; omit to clean every src-*/")
    s.add_argument("--yes", "-y", action="store_true",
                   help="skip the confirmation prompt")

    s = sub.add_parser("install", help="stack-additive install to a destination")
    s.add_argument("mods", nargs="+")
    s.add_argument("--to", dest="install_to", choices=("client", "server"), default=None,
                   help="install destination (default: config.defaultInstallTo)")
    s.add_argument("--replace", action="store_true",
                   help="replace the destination's stack with the given mods exactly "
                        "(default: additive — merges into the existing stack)")
    s.add_argument("--adopt-install", dest="adopt_install", action="store_true",
                   help="adopt a PZ install whose manifest was written by a different "
                        "Necroid workspace (fingerprint mismatch). Use when you've cloned "
                        "or moved the workspace dir; rare.")

    s = sub.add_parser("uninstall", help="restore everything, or remove named mods and rebuild")
    s.add_argument("mods", nargs="*")
    s.add_argument("--to", dest="install_to", choices=("client", "server"), default=None)
    s.add_argument("--cascade", action="store_true",
                   help="also remove any installed mods that transitively depend on the named ones")

    s = sub.add_parser("deps", help="view or edit a mod's dependency / incompatibility lists")
    deps_sub = s.add_subparsers(dest="deps_action", metavar="<action>")
    deps_sub.required = True

    ds = deps_sub.add_parser("show", help="print a mod's dependencies + incompatibleWith")
    ds.add_argument("mod")

    da = deps_sub.add_parser("add", help="add a dependency or incompatibility")
    da.add_argument("mod")
    g = da.add_mutually_exclusive_group(required=True)
    g.add_argument("--requires", metavar="OTHER",
                   help="bare name of a mod this one depends on")
    g.add_argument("--conflicts", metavar="OTHER",
                   help="bare name of a mod this one is incompatible with")

    dr = deps_sub.add_parser("remove", help="remove a dependency or incompatibility")
    dr.add_argument("mod")
    g = dr.add_mutually_exclusive_group(required=True)
    g.add_argument("--requires", metavar="OTHER",
                   help="bare name of a declared dependency to drop")
    g.add_argument("--conflicts", metavar="OTHER",
                   help="bare name of a declared incompatibility to drop")

    sub.add_parser("test", help="compile changed + new .java files in the entered mod's src-<mod>/ (no install)")

    s = sub.add_parser("verify", help="re-hash installed files, report drift")
    s.add_argument("--to", dest="install_to", choices=("client", "server"), default=None)

    s = sub.add_parser("doctor", help="read-only audit of install state + remediation hints")
    s.add_argument("--to", dest="install_to", choices=("client", "server"), default=None)

    s = sub.add_parser("update", help="check GitHub Releases and self-update the binary")
    s.add_argument("--check", action="store_true",
                   help="only check for a newer release; do not download or apply")
    s.add_argument("--force", action="store_true",
                   help="bypass the 24h check cache")
    s.add_argument("--yes", "-y", action="store_true",
                   help="apply without the interactive confirm prompt")
    s.add_argument("--rollback", action="store_true",
                   help="restore the previous binary from necroid.old[.exe]")
    s.add_argument("--post-restart-cleanup", dest="post_restart_cleanup",
                   action="store_true", help=argparse.SUPPRESS)

    s = sub.add_parser("import",
                       help="pull mods from a GitHub or GitLab repo into mods/")
    s.add_argument("repo",
                   help="owner/repo, a github.com URL, or a GitLab URL "
                        "(including self-hosted; optionally /tree/<ref>)")
    s.add_argument("--ref", default=None,
                   help="branch, tag, or commit SHA (default: repo's default branch)")
    s.add_argument("--mod", dest="mod_selectors", action="append", default=[],
                   metavar="SELECTOR",
                   help="select a mod from the repo by subdir or bare name (repeatable)")
    s.add_argument("--all", dest="select_all", action="store_true",
                   help="import every discovered mod that matches the workspace major")
    s.add_argument("--include-all-majors", dest="include_all_majors", action="store_true",
                   help="also import mods for non-current PZ majors (filtered out of "
                        "list/install until you switch workspaces; rare)")
    s.add_argument("--list", dest="list_only", action="store_true",
                   help="discover and list mods only; do not import")
    s.add_argument("--json", action="store_true",
                   help="emit machine-readable discovery output (use with --list)")
    s.add_argument("--name", dest="name_override", default=None,
                   help="override the mod dir base (only when importing exactly one mod)")
    s.add_argument("--force", action="store_true",
                   help="overwrite existing target dirs")

    s = sub.add_parser("mod-update",
                       help="check / update imported mods from their source repos")
    s.add_argument("name", nargs="?", default=None,
                   help="optional bare or fully-qualified mod name (default: every imported mod)")
    s.add_argument("--check", dest="check_only", action="store_true",
                   help="dry-run: report what would update; populate the cache")
    s.add_argument("--force", action="store_true",
                   help="apply even if upstream is older or same version")
    s.add_argument("--include-peers", dest="include_peers", action="store_true",
                   help="when a name is given, also update siblings sharing the same (repo, ref)")
    s.add_argument("--json", action="store_true",
                   help="emit machine-readable per-mod result objects to stdout")

    s = sub.add_parser("resync-pristine", help="after a PZ update: regenerate pristine, check mods")
    s.add_argument("--from", dest="from_install", choices=("client", "server"), default=None,
                   help="which install to re-seed from (default: config.workspaceSource)")
    s.add_argument("--to", dest="install_to", choices=("client", "server"), default=None,
                   help="install destination to use for mod applicability checks")
    s.add_argument("--force-major-change", action="store_true",
                   help="allow the workspace major to change (invalidates existing mods' patches)")
    s.add_argument("--force-version-drift", action="store_true",
                   help="proceed even when Steam has rewritten some installed files with a "
                        "different PZ version's vanilla. Drifted files are NOT restored from "
                        "`classes-original/`; Steam's current bytes become the new pristine "
                        "(every mod will be flagged for re-capture).")
    s.add_argument("--force-orphans", action="store_true",
                   help="proceed even when the install contains class files that are in "
                        "neither the install-side manifest nor `classes-original/`. They "
                        "will be adopted into the new pristine.")
    s.add_argument("--adopt-install", dest="adopt_install", action="store_true",
                   help="accept an install-side manifest written by a different workspace "
                        "fingerprint (cloned / moved workspace).")
    s.add_argument("--yes", "-y", action="store_true",
                   help="skip confirmation prompts")

    return p


_HANDLERS = {
    "init": init_cmd.run,
    "new": new_cmd.run,
    "list": list_cmd.run,
    "status": status_cmd.run,
    "enter": enter_cmd.run,
    "capture": capture_cmd.run,
    "diff": diff_cmd.run,
    "reset": reset_cmd.run,
    "clean": clean_cmd.run,
    "install": install_cmd.run,
    "uninstall": uninstall_cmd.run,
    "test": test_cmd.run,
    "verify": verify_cmd.run,
    "doctor": doctor_cmd.run,
    "resync-pristine": resync_cmd.run,
    "deps": deps_cmd.run,
    "update": update_cmd.run,
    "import": import_cmd.run,
    "mod-update": mod_update_cmd.run,
}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    root = (args.root or find_root()).resolve()
    args.root = root

    # Sweep any leftover `.old` binary from a prior self-update.
    updater.cleanup_stale_old(root)

    if not args.command and not args.gui:
        args.gui = True

    if args.gui:
        cfg = None
        try:
            cfg = read_config(root, required=False)
        except ConfigError:
            cfg = None
        initial_to = "server" if getattr(args, "gui_server", False) else resolve_install_to(None, cfg)
        from .gui import launch
        return launch(root=root, initial_install_to=initial_to)

    cfg = None
    try:
        cfg = read_config(root, required=False)
    except ConfigError:
        cfg = None

    # init / resync-pristine: resolve source
    if args.command in ("init", "resync-pristine"):
        args.source = resolve_source(getattr(args, "from_install", None), cfg)
    # Commands that need install_to: resolve it (may be None on the Namespace).
    if hasattr(args, "install_to"):
        args.install_to = resolve_install_to(args.install_to, cfg)
    if hasattr(args, "install_as"):
        args.install_as = resolve_install_to(args.install_as, cfg)

    # init + update don't require a fully-loaded Profile. `init` is writing
    # the config; `update` only touches the sibling binary + the update cache,
    # and should work from a fresh install that hasn't been initialized yet.
    if args.command not in ("init", "update"):
        try:
            args.profile = load_profile(root, cfg=cfg) if cfg is not None else load_profile(root)
        except PzModderError as e:
            log.error(str(e))
            return 2
    else:
        args.profile = None

    handler = _HANDLERS[args.command]
    try:
        code = int(handler(args) or 0)
    except PzModderError as e:
        log.error(str(e))
        return 1
    except KeyboardInterrupt:
        log.error("interrupted")
        return 130

    # Opportunistic "update available" notice. No-op when running `update`
    # itself, when NECROID_NO_UPDATE_CHECK is set, when stderr is not a TTY,
    # or when the command failed (no point dumping extra output on error).
    if code == 0 and args.command != "update":
        try:
            updater.emit_opportunistic_notice(root)
        except Exception:
            pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
