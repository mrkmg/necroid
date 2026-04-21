"""clean — delete per-mod editable working trees at the repo root.

Usage:
    necroid clean           # delete every src-*/ at the repo root
    necroid clean <mod>     # delete only src-<mod>/

If the currently entered mod's tree is removed, the enter state is cleared.
Pass `--yes` to skip the confirmation prompt.
"""
from __future__ import annotations

import shutil

from .. import logging_util as log
from ..state import clear_enter, read_enter


def _confirm(prompt: str) -> bool:
    try:
        ans = input(f"{prompt} [y/N] ").strip().lower()
    except EOFError:
        return False
    return ans in ("y", "yes")


def run(args) -> int:
    p = args.profile
    name: str | None = getattr(args, "mod", None)
    assume_yes: bool = bool(getattr(args, "yes", False))

    es = read_enter(p.enter_file)

    if name:
        target = p.src_for(name)
        if not target.exists():
            log.info(f"nothing to clean: {target} does not exist.")
            # Still clear stale enter state if it pointed at a missing dir.
            if es and es.mod == name:
                clear_enter(p.enter_file)
                log.info("cleared stale enter state.")
            return 0
        if not assume_yes and not _confirm(f"delete {target}?"):
            log.info("aborted.")
            return 1
        shutil.rmtree(target)
        log.success(f"removed {target}")
        if es and es.mod == name:
            clear_enter(p.enter_file)
            log.info("cleared enter state.")
        return 0

    # No name: nuke every src-*/.
    candidates = sorted(d for d in p.root.glob("src-*") if d.is_dir())
    if not candidates:
        log.info("nothing to clean: no src-*/ directories at the repo root.")
        if es:
            clear_enter(p.enter_file)
            log.info("cleared stale enter state.")
        return 0
    log.info("will delete:")
    for d in candidates:
        log.info(f"  {d}")
    if not assume_yes and not _confirm(f"delete {len(candidates)} director{'y' if len(candidates) == 1 else 'ies'}?"):
        log.info("aborted.")
        return 1
    for d in candidates:
        shutil.rmtree(d)
    log.success(f"removed {len(candidates)} director{'y' if len(candidates) == 1 else 'ies'}.")
    if es:
        clear_enter(p.enter_file)
        log.info("cleared enter state.")
    return 0
