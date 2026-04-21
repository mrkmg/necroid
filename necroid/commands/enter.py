"""enter — prepare a per-mod editable working tree at `src-<mod>/` (repo root).

Single-mod only — enter-stacking was removed; use `install` for stacks.

clientOnly mods require a configured client PZ install. `--as` overrides the
postfix variant applied (default = config.defaultInstallTo; forced to `client`
for a clientOnly mod).

If `src-<mod>/` already exists, `enter` *preserves* its contents (so switching
between mods doesn't wipe in-progress edits). Pass `--force` to re-seed from
pristine + patches, discarding any local edits. Use `necroid clean` to delete
per-mod src dirs entirely.
"""
from __future__ import annotations

import shutil

from .. import logging_util as log
from ..errors import ClientOnlyViolation, ConflictError
from ..fsops import mirror_tree
from ..mod import ensure_mod_exists, read_mod_json
from ..profile import existing_subtrees
from ..stackapply import apply_stack
from ..state import write_enter


def run(args) -> int:
    p = args.profile
    name: str = args.mod

    md = ensure_mod_exists(p.mods_dir, name)
    mj = read_mod_json(md)

    if mj.client_only and p.client_pz_install is None:
        raise ClientOnlyViolation(
            f"mod '{name}' is clientOnly but no clientPzInstall is configured.\n"
            f"    configure one: `necroid init --from client`."
        )

    install_as: str = args.install_as
    if mj.client_only and install_as == "server":
        raise ClientOnlyViolation(
            f"mod '{name}' is clientOnly; cannot enter with --as server."
        )

    target = p.src_for(name)

    if target.exists() and not args.force:
        log.info(f"enter {name} (as {install_as}): preserving existing working tree at {target}")
        log.info("  (pass --force to re-seed from pristine + patches)")
        write_enter(p.enter_file, name, install_as=install_as)
        log.success(f"entered '{name}'. Edit under {target.name}/; run `capture` when done.")
        return 0

    if target.exists() and args.force:
        log.info(f"--force: wiping {target} before re-seeding")
        shutil.rmtree(target)

    subs = existing_subtrees(p.pristine)
    if not subs:
        raise SystemExit(f"src-pristine/ is empty at {p.pristine} (run `necroid init`)")

    log.info(f"enter {name} (as {install_as}): seed {target.name}/ from pristine then apply patches")
    for sub in subs:
        mirror_tree(p.pristine / sub, target / sub)
    result = apply_stack(
        stack=[name],
        work_dir=target,
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
    write_enter(p.enter_file, name, install_as=install_as)
    log.success(f"applied: {len(result.touched)} file(s). Edit under {target.name}/; run `capture` when done.")
    return 0
