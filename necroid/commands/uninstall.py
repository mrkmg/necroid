"""uninstall — no args restores everything on the chosen destination; with args
removes named mods from that destination's stack and rebuilds."""
from __future__ import annotations

from .. import logging_util as log
from ..config import read_config
from ..errors import ModNotFound, PzMajorMismatch
from ..install import install_stack, uninstall_all
from ..state import read_state
from ._resolve import resolve_mod


def run(args) -> int:
    p = args.profile
    install_to: str = args.install_to
    raw_names: list[str] = list(args.mods or [])
    state = read_state(p.state_file(install_to))

    if not raw_names:
        uninstall_all(p, install_to)
        return 0

    cfg = read_config(args.root, required=False)
    current = list(state.stack)
    # Accept bare names — resolve against workspace major first, then fall back
    # to matching entries already in the stack (so an orphaned state entry can
    # still be removed even after a major flip).
    names: list[str] = []
    for n in raw_names:
        try:
            names.append(resolve_mod(p.mods_dir, cfg.workspace_major, n))
        except (ModNotFound, PzMajorMismatch):
            if n in current:
                names.append(n)
            else:
                raise
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
