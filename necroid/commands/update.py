"""`necroid update` — self-update the packaged binary from GitHub Releases.

Thin UI layer over `necroid.updater`. The real work (HTTP, zip extraction,
binary swap, restart) lives there.
"""
from __future__ import annotations

from .. import __version__
from ..util import logging_util as log
from ..remote import updater
from ..errors import UpdateError


def run(args) -> int:
    # Hidden cleanup path: respawn after a successful update calls this to
    # unlink the `.old` sibling and exit. Must be a no-op outside that use.
    if getattr(args, "post_restart_cleanup", False):
        updater.cleanup_stale_old(args.root)
        return 0

    if getattr(args, "rollback", False):
        updater.rollback()  # os._exit on success
        return 0

    force = bool(getattr(args, "force", False))
    check_only = bool(getattr(args, "check", False))
    yes = bool(getattr(args, "yes", False))

    # Editable install -> friendly message, no network call.
    if not updater.is_frozen():
        log.info(
            f"this is a source / editable install (v{__version__}); "
            f"self-update is only available for the packaged binary."
        )
        log.info("to update: `git pull` (or `pip install -U` if installed from a wheel)")
        # Still honor --check: run the network check so scripts can discover
        # new versions even from an editable checkout.
        if check_only:
            release = updater.check_for_update(args.root, force=force, quiet=False)
            if release is None:
                log.info(f"on the latest version (v{__version__}).")
                return 0
            log.info(f"latest release: v{release.pretty_version}  {release.html_url}")
            return 1
        return 0

    release = updater.check_for_update(args.root, force=force, quiet=False)
    if release is None:
        log.info(f"on the latest version (v{__version__}).")
        return 0

    if check_only:
        log.info(
            f"update available: v{__version__} -> v{release.pretty_version}"
        )
        if release.html_url:
            log.info(f"notes: {release.html_url}")
        return 1

    try:
        updater.apply_update(release, yes=yes)  # os._exit on restart
    except UpdateError:
        raise
    return 0
