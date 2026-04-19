"""reset — mirror src-pristine/zombie -> src/zombie, clear enter state."""
from __future__ import annotations

from .. import logging_util as log
from ..fsops import mirror_tree
from ..state import clear_enter


def run(args) -> int:
    p = args.profile
    log.info(f"reset: {p.src}/zombie <- {p.pristine}/zombie")
    mirror_tree(p.pristine / "zombie", p.src / "zombie")
    clear_enter(p.enter_file)
    log.success("done.")
    return 0
