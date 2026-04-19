"""verify — re-hash installed files, report drift."""
from __future__ import annotations

from .. import logging_util as log
from ..hashing import file_sha256
from ..profile import require_pz_install
from ..state import read_state


def run(args) -> int:
    p = args.profile
    require_pz_install(p)
    state = read_state(p.state_file)
    if not state.installed:
        print("no installed state.")
        return 0
    print(f"verify {len(state.installed)} installed file(s)")
    drift = 0
    for e in state.installed:
        actual = file_sha256(p.content_dir / e.rel)
        if actual is None:
            print(f"  MISSING: {e.rel}")
            drift += 1
        elif actual != e.sha256:
            print(f"  DRIFT:   {e.rel}")
            drift += 1
    if drift == 0:
        print("  all files match recorded state")
        return 0
    print(f"  {drift} drifted file(s). Re-run `install {' '.join(state.stack)}` to repair.")
    return 1
