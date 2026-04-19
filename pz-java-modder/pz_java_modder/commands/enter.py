"""enter — reset src/, apply a stack of mods. Records .mod-enter.json."""
from __future__ import annotations

from .. import logging_util as log
from ..errors import ConflictError, TargetMismatch
from ..fsops import mirror_tree
from ..mod import ensure_mod_exists, read_mod_json
from ..stackapply import apply_stack
from ..state import write_enter


def run(args) -> int:
    p = args.profile
    stack: list[str] = list(args.mods)
    if not stack:
        raise SystemExit("usage: pz-java-modder enter <mod1> [mod2 ...]")
    for name in stack:
        md = ensure_mod_exists(p.mods_dir, name)
        mj = read_mod_json(md)
        if mj.target != p.target:
            raise TargetMismatch(
                f"mod '{name}' targets {mj.target}; active profile is {p.target}\n"
                f"    retry with --target {mj.target}"
            )

    log.info(f"enter [{', '.join(stack)}]: reset src/ then apply patches")
    mirror_tree(p.pristine / "zombie", p.src / "zombie")
    result = apply_stack(
        stack=stack,
        work_dir=p.src,
        pristine_dir=p.pristine,
        mods_dir=p.mods_dir,
        scratch_root=p.build / "stage-scratch-enter",
    )
    if result.conflicts:
        log.error("CONFLICTS:")
        for cf in result.conflicts:
            print(f"  {cf.rel}  [{cf.type}]  mods: {', '.join(cf.mods)}")
        raise ConflictError([c.to_dict() for c in result.conflicts])
    write_enter(p.enter_file, stack)
    log.success(f"applied: {len(result.touched)} file(s). Edit under src/zombie/; run `capture` when done.")
    return 0
