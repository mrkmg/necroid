"""install — stack-additive install of one or more mods to a chosen destination.

Args extend the currently-installed stack for the given `--to` destination
(deduped, order preserved); the worker `install_stack` rebuilds from the
merged stack atomically.
"""
from __future__ import annotations

from .. import logging_util as log
from ..errors import ClientOnlyViolation
from ..install import install_stack
from ..mod import ensure_mod_exists, read_mod_json
from ..state import read_state


def run(args) -> int:
    p = args.profile
    install_to: str = args.install_to
    names: list[str] = list(args.mods or [])
    if not names:
        raise SystemExit("usage: necroid install <mod1> [mod2 ...]  [--to client|server]")

    # Validate all named mods exist; preflight clientOnly rule.
    for name in names:
        md = ensure_mod_exists(p.mods_dir, name)
        mj = read_mod_json(md)
        if mj.client_only and install_to == "server":
            raise ClientOnlyViolation(
                f"mod '{name}' is clientOnly; cannot install to server.\n"
                f"    retry with `--to client`."
            )

    state = read_state(p.state_file(install_to))
    current = list(state.stack)
    merged = list(current)
    for n in names:
        if n not in merged:
            merged.append(n)

    added = [n for n in names if n not in current]
    if not added:
        log.info(f"{install_to} stack already contains [{', '.join(names)}] — rebuilding [{', '.join(merged)}]")
    elif not current:
        log.info(f"installing fresh {install_to} stack: [{', '.join(merged)}]")
    else:
        log.info(f"adding [{', '.join(added)}] to current {install_to} stack [{', '.join(current)}] -> [{', '.join(merged)}]")

    install_stack(p, merged, install_to=install_to)
    return 0
