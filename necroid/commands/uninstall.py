"""uninstall — no args restores everything on the chosen destination; with args
removes named mods from that destination's stack and rebuilds."""
from __future__ import annotations

from .. import logging_util as log
from ..install import install_stack, uninstall_all
from ..state import read_state


def run(args) -> int:
    p = args.profile
    install_to: str = args.install_to
    names: list[str] = list(args.mods or [])
    state = read_state(p.state_file(install_to))

    if not names:
        uninstall_all(p, install_to)
        return 0

    current = list(state.stack)
    if not current:
        raise SystemExit(f"no mods installed to {install_to}; cannot remove [{', '.join(names)}]")
    missing = [n for n in names if n not in current]
    if missing:
        raise SystemExit(
            f"mod(s) not in {install_to} installed stack [{', '.join(current)}]: {', '.join(missing)}"
        )
    remainder = [n for n in current if n not in names]
    if not remainder:
        log.info(f"removing [{', '.join(names)}] empties the {install_to} stack — full uninstall")
        uninstall_all(p, install_to)
        return 0
    log.info(f"removing [{', '.join(names)}] from {install_to} stack [{', '.join(current)}] -> rebuilding [{', '.join(remainder)}]")
    install_stack(p, remainder, install_to=install_to)
    return 0
