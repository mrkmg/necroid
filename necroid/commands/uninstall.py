"""uninstall — no args restores everything; with args removes named mods and rebuilds."""
from __future__ import annotations

from .. import logging_util as log
from ..install import install_stack, uninstall_all
from ..state import read_state


def run(args) -> int:
    p = args.profile
    names: list[str] = list(args.mods or [])
    state = read_state(p.state_file)

    if not names:
        uninstall_all(p)
        return 0

    current = list(state.stack)
    if not current:
        raise SystemExit(f"no mods installed; cannot remove [{', '.join(names)}]")
    missing = [n for n in names if n not in current]
    if missing:
        raise SystemExit(
            f"mod(s) not in installed stack [{', '.join(current)}]: {', '.join(missing)}"
        )
    remainder = [n for n in current if n not in names]
    if not remainder:
        log.info(f"removing [{', '.join(names)}] empties the stack — full uninstall")
        uninstall_all(p)
        return 0
    log.info(f"removing [{', '.join(names)}] from stack [{', '.join(current)}] -> rebuilding [{', '.join(remainder)}]")
    install_stack(p, remainder)
    return 0
