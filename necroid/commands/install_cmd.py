"""install — stack-additive install of one or more mods to a chosen destination.

Args extend the currently-installed stack for the given `--to` destination
(deduped, order preserved); the worker `install_stack` rebuilds from the
merged stack atomically.
"""
from __future__ import annotations

from ..util import logging_util as log
from ..core.config import read_config
from ..core.depgraph import expand_stack, validate_incompat
from ..errors import ClientOnlyViolation
from ..build.install import install_stack
from ..core.mod import ensure_mod_exists, read_mod_json
from ..core.state import read_state
from ._resolve import resolve_mod


def run(args) -> int:
    p = args.profile
    install_to: str = args.install_to
    replace: bool = bool(getattr(args, "replace", False))
    raw_names: list[str] = list(args.mods or [])
    if not raw_names:
        raise SystemExit("usage: necroid install <mod1> [mod2 ...]  [--to client|server] [--replace]")

    cfg = read_config(args.root)
    user_names = [resolve_mod(p.mods_dir, cfg.workspace_major, n) for n in raw_names]

    # Pull in every transitive dep, topo order (deps before dependents).
    names = expand_stack(p.mods_dir, cfg.workspace_major, user_names)
    pulled_in = [n for n in names if n not in user_names]
    if pulled_in:
        log.info(f"pulling in deps: [{', '.join(pulled_in)}]")

    # Validate the full resolved stack — either side's incompat declaration
    # wins.
    validate_incompat(p.mods_dir, cfg.workspace_major, names)

    # Preflight clientOnly rule across the full resolved stack (covers deps).
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

    if replace:
        # Exact-replace semantics — the GUI's "state-based" apply relies on this
        # so unchecked mods actually leave the stack. Dedupe but preserve order.
        seen: set[str] = set()
        merged: list[str] = []
        for n in names:
            if n not in seen:
                seen.add(n)
                merged.append(n)
        added = [n for n in merged if n not in current]
        removed = [n for n in current if n not in seen]
        if merged == current:
            log.info(f"{install_to} stack unchanged: [{', '.join(merged)}] — rebuilding")
        else:
            parts: list[str] = []
            if added:
                parts.append(f"+[{', '.join(added)}]")
            if removed:
                parts.append(f"-[{', '.join(removed)}]")
            log.info(
                f"replace {install_to} stack [{', '.join(current)}] -> "
                f"[{', '.join(merged)}]  {' '.join(parts)}"
            )
    else:
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

    # Re-validate the full merged stack — additive merges can surface a
    # conflict between a newly-added mod and one already installed.
    validate_incompat(p.mods_dir, cfg.workspace_major, merged)

    install_stack(p, merged, install_to=install_to,
                  adopt_install=bool(getattr(args, "adopt_install", False)))
    return 0
