"""reset — re-seed the currently entered mod's src-<mod>/ to pristine + patches.

Discards any in-progress edits in `src-<entered>/` and reapplies the mod's
patches on top of pristine. The enter state itself (which mod, install_as) is
preserved. To blow away the tree entirely, use `necroid clean`.
"""
from __future__ import annotations

import shutil

from ..util import logging_util as log
from ..errors import ConflictError
from ..util.fsops import mirror_tree
from ..core.profile import existing_subtrees
from ..build.stackapply import apply_stack
from ..core.state import read_enter


def run(args) -> int:
    p = args.profile
    es = read_enter(p.enter_file)
    if not es:
        raise SystemExit("no mod is entered — nothing to reset. Run `necroid enter <mod>` first.")

    subs = existing_subtrees(p.pristine)
    if not subs:
        raise SystemExit(f"src-pristine/ is empty at {p.pristine} (run `necroid init`)")

    target = p.src_for(es.mod)
    log.info(f"reset {es.mod} (as {es.install_as}): re-seed {target.name}/ from pristine + patches")
    if target.exists():
        shutil.rmtree(target)
    for sub in subs:
        mirror_tree(p.pristine / sub, target / sub)
    result = apply_stack(
        stack=[es.mod],
        work_dir=target,
        pristine_dir=p.pristine,
        mods_dir=p.mods_dir,
        scratch_root=p.build / "stage-scratch-enter",
        install_to=es.install_as,
    )
    if result.conflicts:
        log.error("CONFLICTS:")
        for cf in result.conflicts:
            print(f"  {cf.rel}  [{cf.type}]  mods: {', '.join(cf.mods)}")
        raise ConflictError([c.to_dict() for c in result.conflicts])
    log.success(f"reset {es.mod}: {len(result.touched)} file(s) re-applied.")
    return 0
