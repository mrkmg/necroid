"""enter — reset src/, apply a stack of mods. Records .mod-enter.json.

clientOnly mods require a configured client PZ install. `--as` overrides the
postfix variant chosen (default = config.defaultInstallTo; forced to `client`
when any mod in the stack is clientOnly)."""
from __future__ import annotations

from .. import logging_util as log
from ..errors import ClientOnlyViolation, ConflictError
from ..fsops import mirror_tree
from ..mod import ensure_mod_exists, read_mod_json
from ..stackapply import apply_stack
from ..state import write_enter


def run(args) -> int:
    p = args.profile
    stack: list[str] = list(args.mods)
    if not stack:
        raise SystemExit("usage: necroid enter <mod1> [mod2 ...]")

    # Load every mod.json up front — detect clientOnly in the stack.
    has_client_only = False
    for name in stack:
        md = ensure_mod_exists(p.mods_dir, name)
        mj = read_mod_json(md)
        if mj.client_only:
            has_client_only = True
            if p.client_pz_install is None:
                raise ClientOnlyViolation(
                    f"mod '{name}' is clientOnly but no clientPzInstall is configured.\n"
                    f"    configure one: `necroid init --from client`."
                )

    install_as: str = args.install_as
    if has_client_only and install_as == "server":
        raise ClientOnlyViolation(
            f"stack contains a clientOnly mod; cannot enter with --as server."
        )

    log.info(f"enter [{', '.join(stack)}] (as {install_as}): reset src/ then apply patches")
    mirror_tree(p.pristine / "zombie", p.src / "zombie")
    result = apply_stack(
        stack=stack,
        work_dir=p.src,
        pristine_dir=p.pristine,
        mods_dir=p.mods_dir,
        scratch_root=p.build / "stage-scratch-enter",
        install_to=install_as,
    )
    if result.conflicts:
        log.error("CONFLICTS:")
        for cf in result.conflicts:
            print(f"  {cf.rel}  [{cf.type}]  mods: {', '.join(cf.mods)}")
        raise ConflictError([c.to_dict() for c in result.conflicts])
    write_enter(p.enter_file, stack, install_as=install_as)
    log.success(f"applied: {len(result.touched)} file(s). Edit under src/zombie/; run `capture` when done.")
    return 0
