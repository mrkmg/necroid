"""argparse CLI. All subcommands delegate to `commands/*.run(args)`.

Global flags:
    --root PATH                 explicit workspace root (else: auto-discover from cwd)
    --gui                       launch the tkinter GUI instead of running a subcommand
    -server                     GUI-only shorthand: open the GUI with install-to=server selected

Per-command flags:
    init / resync-pristine:   --from {client,server}
    enter:                    --as   {client,server}
    install / uninstall / verify / list / status:  --to {client,server}
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from . import logging_util as log
from .config import read_config
from .errors import ConfigError, PzModderError
from .profile import find_root, load_profile, resolve_install_to, resolve_source
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
    test as test_cmd,
    uninstall as uninstall_cmd,
    verify as verify_cmd,
)


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

    s = sub.add_parser("new", help="create a new mod")
    s.add_argument("name")
    s.add_argument("--description", "-d", default="")
    s.add_argument("--client-only", dest="client_only", action="store_true",
                   help="mark the new mod as clientOnly (install allowed only to client PZ)")

    s = sub.add_parser("list", help="tabular view of all mods")
    s.add_argument("--to", dest="install_to", choices=("client", "server"), default=None,
                   help="destination to count patches for (default: config.defaultInstallTo)")

    s = sub.add_parser("status", help="working-tree divergence or per-mod patch applicability")
    s.add_argument("name", nargs="?", default=None)
    s.add_argument("--to", dest="install_to", choices=("client", "server"), default=None)

    s = sub.add_parser("enter", help="reset src/ and apply a stack of mods")
    s.add_argument("mods", nargs="+")
    s.add_argument("--as", dest="install_as", choices=("client", "server"), default=None,
                   help="destination variant to apply (default: config.defaultInstallTo)")

    s = sub.add_parser("capture", help="diff src/ vs pristine, rewrite the mod's patches")
    s.add_argument("name")
    s.add_argument("--as", dest="install_as", choices=("client", "server"), default=None,
                   help="fallback variant if no enter state is recorded")

    s = sub.add_parser("diff", help="concatenate a mod's patches to stdout")
    s.add_argument("name")

    sub.add_parser("reset", help="mirror src-pristine -> src, clear enter state")

    s = sub.add_parser("install", help="stack-additive install to a destination")
    s.add_argument("mods", nargs="+")
    s.add_argument("--to", dest="install_to", choices=("client", "server"), default=None,
                   help="install destination (default: config.defaultInstallTo)")

    s = sub.add_parser("uninstall", help="restore everything, or remove named mods and rebuild")
    s.add_argument("mods", nargs="*")
    s.add_argument("--to", dest="install_to", choices=("client", "server"), default=None)

    sub.add_parser("test", help="compile changed + new .java files in src/ (no install)")

    s = sub.add_parser("verify", help="re-hash installed files, report drift")
    s.add_argument("--to", dest="install_to", choices=("client", "server"), default=None)

    s = sub.add_parser("resync-pristine", help="after a PZ update: regenerate pristine, check mods")
    s.add_argument("--from", dest="from_install", choices=("client", "server"), default=None,
                   help="which install to re-seed from (default: config.workspaceSource)")
    s.add_argument("--to", dest="install_to", choices=("client", "server"), default=None,
                   help="install destination to use for mod applicability checks")

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
    "test": test_cmd.run,
    "verify": verify_cmd.run,
    "resync-pristine": resync_cmd.run,
}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    root = (args.root or find_root()).resolve()
    args.root = root

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

    # init doesn't require a fully-loaded Profile (config is being written).
    if args.command != "init":
        try:
            args.profile = load_profile(root, cfg=cfg) if cfg is not None else load_profile(root)
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
