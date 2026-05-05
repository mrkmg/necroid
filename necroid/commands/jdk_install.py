"""`necroid jdk-install --archive <path>` — manual recovery for the
auto-fetched JDK when the Adoptium download is blocked (corp proxy, AV,
offline laptop). The user downloads the canonical Temurin archive themselves;
this command validates + extracts it into `data/tools/jdk-bundled/`.

Skips network entirely when `--no-verify` is passed; otherwise hits the
Adoptium checksum endpoint best-effort (skipped silently if unreachable —
the archive sniff still catches obvious garbage)."""
from __future__ import annotations

from pathlib import Path

from ..util import logging_util as log
from ..util import tools_fetch


def run(args) -> int:
    archive = Path(args.archive).expanduser().resolve()
    if not archive.is_file():
        log.error(f"archive not found: {archive}")
        return 2
    tools_dir: Path = args.root / "data" / "tools"
    log.step(f"installing pinned JDK from {archive.name}")
    try:
        target = tools_fetch.install_from_archive(
            archive, tools_dir, verify=not args.no_verify,
        )
    except (RuntimeError, OSError) as e:
        log.error(f"jdk-install failed: {e}")
        return 1
    log.success(f"JDK installed -> {target}")
    return 0
