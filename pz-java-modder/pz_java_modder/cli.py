"""argparse CLI. All subcommands delegate to `commands/*.run(args)`.

Global flags:
    --target {client,server}    override target (else: --server, else config default, else client)
    --server / -server          alias for --target server
    --root PATH                 explicit workspace root (else: auto-discover from cwd)
    --gui                       launch the tkinter GUI instead of running a subcommand

Target resolution (highest wins):
    --target > --server / -server > config.defaultTarget > "client"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import logging_util as log
from .config import read_config
from .errors import ConfigError, PzModderError
from .profile import find_root, load_profile, resolve_target
from .commands import (
    capture as capture_cmd,
    diff as diff_cmd,
    enter as enter_cmd,
    init as init_cmd,
    install_cmd,
    list_cmd,
    new as new_cmd,
    reset as reset_cmd,
    resync_pristine as resync_cmd,
    status as status_cmd,
    uninstall as uninstall_cmd,
    verify as verify_cmd,
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pz-java-modder",
        description="Diff-based Java mod manager for Project Zomboid (client and dedicated server).",
    )
    p.add_argument("--target", choices=("client", "server"), default=None,
                   help="target profile (default: config.defaultTarget or 'client')")
    # GUI convention: -server (single dash) flips to server mode when launching GUI.
    p.add_argument("--server", "-server", dest="flag_server", action="store_true",
                   help="alias for --target server")
    p.add_argument("--root", type=Path, default=None,
                   help="workspace root (default: auto-discover upward from cwd)")
    p.add_argument("--gui", action="store_true",
                   help="launch the GUI instead of running a subcommand")

    sub = p.add_subparsers(dest="command", metavar="<command>")

    s = sub.add_parser("init", help="bootstrap a profile (client or server)")
    s.add_argument("--pz-install", default=None, help="override PZ install path")
    s.add_argument("--force", action="store_true", help="redo steps even if they look up-to-date")

    s = sub.add_parser("new", help="create a new mod")
    s.add_argument("name")
    s.add_argument("--description", "-d", default="")

    sub.add_parser("list", help="tabular view of all mods")

    s = sub.add_parser("status", help="working-tree divergence or per-mod patch applicability")
    s.add_argument("name", nargs="?", default=None)

    s = sub.add_parser("enter", help="reset src/ and apply a stack of mods")
    s.add_argument("mods", nargs="+")

    s = sub.add_parser("capture", help="diff src/ vs pristine, rewrite the mod's patches")
    s.add_argument("name")

    s = sub.add_parser("diff", help="concatenate a mod's patches to stdout")
    s.add_argument("name")

    sub.add_parser("reset", help="mirror src-pristine -> src, clear enter state")

    s = sub.add_parser("install", help="stack-additive install")
    s.add_argument("mods", nargs="+")

    s = sub.add_parser("uninstall", help="restore everything, or remove named mods and rebuild")
    s.add_argument("mods", nargs="*")

    sub.add_parser("verify", help="re-hash installed files, report drift")
    sub.add_parser("resync-pristine", help="after a PZ update: regenerate pristine, check mods")

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
    "install": install_cmd.run,
    "uninstall": uninstall_cmd.run,
    "verify": verify_cmd.run,
    "resync-pristine": resync_cmd.run,
}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    root = (args.root or find_root()).resolve()
    args.root = root

    # No subcommand and no explicit --gui: launch the GUI. The tool is primarily
    # for end users; CLI power users pass a subcommand.
    if not args.command and not args.gui:
        args.gui = True

    if args.gui:
        from .gui import launch
        return launch(root=root, target=resolve_target(args.target, args.flag_server))

    # Resolve target. For init, config may not exist yet — read_config is optional.
    cfg = None
    try:
        cfg = read_config(root, required=False)
    except ConfigError:
        cfg = None
    target = resolve_target(args.target, args.flag_server, cfg)
    args.target = target

    # init doesn't require a fully-loaded Profile (config is being written).
    # All other commands need one.
    if args.command != "init":
        try:
            args.profile = load_profile(root, target, cfg=cfg)
        except PzModderError as e:
            log.error(str(e))
            return 2
    else:
        args.profile = None

    handler = _HANDLERS[args.command]
    try:
        return int(handler(args) or 0)
    except PzModderError as e:
        log.error(str(e))
        return 1
    except KeyboardInterrupt:
        log.error("interrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
