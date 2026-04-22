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
from ..config import read_config
from ..depgraph import effective_client_only, resolve_deps, validate_incompat
from ..errors import ClientOnlyViolation, ConflictError
from ..fsops import mirror_tree
from ..mod import ensure_mod_exists, read_mod_json
from ..profile import existing_subtrees
from ..stackapply import apply_stack
from ..state import write_enter
from ._resolve import resolve_mod


def run(args) -> int:
    p = args.profile
    cfg = read_config(args.root)
    name: str = resolve_mod(p.mods_dir, cfg.workspace_major, args.mod)

    md = ensure_mod_exists(p.mods_dir, name)
    mj = read_mod_json(md)

    # Resolve dep closure up-front — surfaces missing deps / cycles before
    # we touch the working tree, and drives clientOnly propagation.
    deps = resolve_deps(p.mods_dir, cfg.workspace_major, name)
    validate_incompat(p.mods_dir, cfg.workspace_major, [*deps, name])

    eff_client_only = effective_client_only(p.mods_dir, cfg.workspace_major, name)

    if eff_client_only and p.client_pz_install is None:
        reason = (
            f"mod '{name}' is clientOnly" if mj.client_only
            else f"mod '{name}' depends on a clientOnly mod"
        )
        raise ClientOnlyViolation(
            f"{reason} but no clientPzInstall is configured.\n"
            f"    configure one: `necroid init --from client`."
        )

    install_as: str = args.install_as
    if eff_client_only and install_as == "server":
        reason = (
            f"mod '{name}' is clientOnly" if mj.client_only
            else f"mod '{name}' depends on a clientOnly mod"
        )
        raise ClientOnlyViolation(
            f"{reason}; cannot enter with --as server."
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

    full_stack = [*deps, name]
    if deps:
        log.info(
            f"enter {name} (as {install_as}): seed {target.name}/ from pristine, "
            f"apply deps [{', '.join(deps)}] then {name}"
        )
    else:
        log.info(f"enter {name} (as {install_as}): seed {target.name}/ from pristine then apply patches")
    for sub in subs:
        mirror_tree(p.pristine / sub, target / sub)
    result = apply_stack(
        stack=full_stack,
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
