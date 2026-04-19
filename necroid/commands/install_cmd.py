"""install — stack-additive install of one or more mods.

Args extend the currently-installed stack (deduped, order preserved); the
worker `install_stack` rebuilds from the merged stack atomically.
"""
from __future__ import annotations

from .. import logging_util as log
from ..errors import TargetMismatch
from ..install import install_stack
from ..mod import ensure_mod_exists, read_mod_json
from ..state import read_state


def run(args) -> int:
    p = args.profile
    names: list[str] = list(args.mods or [])
    if not names:
        raise SystemExit("usage: necroid install <mod1> [mod2 ...]")

    # Validate all named mods exist and target matches active profile.
    for name in names:
        md = ensure_mod_exists(p.mods_dir, name)
        mj = read_mod_json(md)
        if mj.target != p.target:
            raise TargetMismatch(
                f"mod '{name}' targets {mj.target}; active profile is {p.target}\n"
                f"    retry with --target {mj.target}"
            )

    state = read_state(p.state_file)
    current = list(state.stack)
    merged = list(current)
    for n in names:
        if n not in merged:
            merged.append(n)

    added = [n for n in names if n not in current]
    if not added:
        log.info(f"stack already contains [{', '.join(names)}] — rebuilding [{', '.join(merged)}]")
    elif not current:
        log.info(f"installing fresh stack: [{', '.join(merged)}]")
    else:
        log.info(f"adding [{', '.join(added)}] to current stack [{', '.join(current)}] -> [{', '.join(merged)}]")

    install_stack(p, merged)
    return 0
