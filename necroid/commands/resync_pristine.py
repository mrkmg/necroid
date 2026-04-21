"""resync-pristine — after a PZ update, regenerate the shared workspace from
the source PZ install and flag mods whose patches no longer apply.

Guard: before the re-init copies the PZ install's class tree back into
`classes-original/`, any currently-installed stack on client or server is
rolled back to originals. Otherwise the modded `.class` files still sitting
in the PZ install would get adopted as the new pristine and every mod's
patches would start diffing against modded bytecode.
"""
from __future__ import annotations

import shutil
from argparse import Namespace

from .. import logging_util as log
from ..config import read_config
from ..errors import ConfigError, PzMajorMismatch, PzVersionDetectError
from ..fsops import empty_dir
from ..install import uninstall_all
from ..mod import list_mods, patch_items, pristine_snapshot, read_mod_json, write_mod_json
from ..patching import patched_theirs_file
from ..profile import require_pz_install
from ..pzversion import detect_pz_version
from ..state import read_state
from . import init as init_cmd


def _uninstall_active_stacks(profile) -> None:
    """Roll back both destinations' installed stacks (if any) before the
    pristine sources are refreshed. Raises if state says something is
    installed but the PZ install path isn't configured/present — we won't
    silently skip, since adopting modded classes as pristine would corrupt
    every mod in the library."""
    for dest in ("client", "server"):
        state = read_state(profile.state_file(dest))
        if not state.installed:
            continue
        log.step(
            f"guard: uninstall {dest} stack [{', '.join(state.stack)}] "
            f"({len(state.installed)} class file(s)) before resync"
        )
        try:
            require_pz_install(profile, dest)
        except ConfigError as e:
            raise ConfigError(
                f"cannot resync-pristine: {dest} has an installed stack but its PZ install "
                f"is unreachable. Roll it back manually, then retry.\n    {e}"
            )
        uninstall_all(profile, dest)


def run(args) -> int:
    p = args.profile
    source = args.source  # populated in cli.py from --from (or config.workspace_source)
    install_to = args.install_to  # used for postfix resolution during applicability check
    force_major = bool(getattr(args, "force_major_change", False))
    assume_yes = bool(getattr(args, "yes", False))

    # Major-change guard runs BEFORE the uninstall pre-flight — otherwise a
    # guard failure leaves the user without their installed stacks.
    src_install = p.pz_install(source)
    if src_install is None or not src_install.exists():
        raise ConfigError(
            f"{source}PzInstall is not configured or does not exist. "
            f"Run `necroid init --from {source}` first."
        )
    src_content = src_install / "java" if source == "server" else src_install

    cfg = read_config(args.root)
    try:
        detected = detect_pz_version(
            src_content,
            __import__("pathlib").Path(__file__).resolve().parent.parent,
            args.root / "data",
        )
    except PzVersionDetectError as e:
        raise ConfigError(f"could not detect PZ version at {src_content}: {e}")

    if cfg.workspace_major and detected.major != cfg.workspace_major and not force_major:
        raise PzMajorMismatch(
            f"workspace is bound to major {cfg.workspace_major}, but {source} install "
            f"is now PZ {detected}. Run with --force-major-change to re-bind the "
            f"workspace to major {detected.major} (this invalidates every mod's "
            f"patches against pristine — expect 3-way merge conflicts)."
        )
    if cfg.workspace_major and detected.major != cfg.workspace_major:
        log.warn(
            f"major change: workspace {cfg.workspace_major} → {detected.major}. "
            f"All major-{cfg.workspace_major} mods will filter out of default views; "
            f"re-enter and re-capture each one to port it."
        )

    _uninstall_active_stacks(p)

    log.info(f"resync-pristine [from={source}]: re-running init with --force")
    init_args = Namespace(
        root=args.root,
        source=source,
        pz_install=None,
        force=True,
        yes=True,                  # don't re-prompt; resync is non-interactive
        major=detected.major,      # explicit: match the detected install
    )
    init_cmd.run(init_args)
    cfg = read_config(args.root)

    log.step("checking mod patches against new pristine...")
    any_stale = False
    for name in list_mods(p.mods_dir, workspace_major=cfg.workspace_major):
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
