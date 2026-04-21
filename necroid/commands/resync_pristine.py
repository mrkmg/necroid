"""resync-pristine — after a PZ update, regenerate the shared workspace from
the source PZ install and flag mods whose patches no longer apply."""
from __future__ import annotations

import shutil
from argparse import Namespace

from .. import logging_util as log
from ..fsops import empty_dir
from ..mod import list_mods, patch_items, pristine_snapshot, read_mod_json, write_mod_json
from ..patching import patched_theirs_file
from . import init as init_cmd


def run(args) -> int:
    p = args.profile
    source = args.source  # populated in cli.py from --from (or config.workspace_source)
    install_to = args.install_to  # used for postfix resolution during applicability check
    log.info(f"resync-pristine [from={source}]: re-running init with --force")
    init_args = Namespace(
        root=args.root,
        source=source,
        pz_install=None,
        force=True,
    )
    init_cmd.run(init_args)

    log.step("checking mod patches against new pristine...")
    any_stale = False
    for name in list_mods(p.mods_dir):
        md = p.mods_dir / name
        mj = read_mod_json(md)
        # For applicability checking, use the effective install destination;
        # clientOnly mods are always checked against the client variant.
        effective_to = "client" if mj.client_only else install_to
        items = patch_items(md, effective_to)
        scratch = p.build / f"resync-scratch-{name}"
        empty_dir(scratch)
        try:
            stale: list[str] = []
            for it in items:
                if it.kind != "patch":
                    continue
                theirs = patched_theirs_file(p.pristine, scratch, it.file, it.rel)
                if theirs is None:
                    stale.append(it.rel)
            if not stale:
                mj.pristine_snapshot = pristine_snapshot(p.pristine, items)
                write_mod_json(md, mj)
                log.info(f"{name}: OK ({len(items)} item(s), snapshot refreshed)")
            else:
                any_stale = True
                log.warn(f"{name}: STALE — re-enter and re-capture manually")
                for s in stale:
                    log.warn(f"    - {s}")
        finally:
            if scratch.exists():
                shutil.rmtree(scratch, ignore_errors=True)
    return 1 if any_stale else 0
