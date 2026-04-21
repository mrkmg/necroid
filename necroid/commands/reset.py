"""reset — mirror every pristine class subtree -> src/, clear enter state."""
from __future__ import annotations

from .. import logging_util as log
from ..fsops import mirror_tree
from ..profile import existing_subtrees
from ..state import clear_enter


def run(args) -> int:
    p = args.profile
    subs = existing_subtrees(p.pristine)
    if not subs:
        raise SystemExit(f"src-pristine/ is empty at {p.pristine} (run `necroid init`)")
    log.info(f"reset: {p.src}/ <- {p.pristine}/  ({', '.join(subs)})")
    for sub in subs:
        mirror_tree(p.pristine / sub, p.src / sub)
    clear_enter(p.enter_file)
    log.success("done.")
    return 0
