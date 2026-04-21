"""verify — re-hash installed files on the chosen destination, report drift."""
from __future__ import annotations

from ..hashing import file_sha256
from ..profile import require_pz_install
from ..state import read_state


def run(args) -> int:
    p = args.profile
    install_to: str = args.install_to
    require_pz_install(p, install_to)
    state = read_state(p.state_file(install_to))
    if not state.installed:
        print(f"no installed state for {install_to}.")
        return 0
    print(f"verify {install_to}: {len(state.installed)} installed file(s)")
    drift = 0
    content_dir = p.content_dir_for(install_to)
    for e in state.installed:
        actual = file_sha256(content_dir / e.rel)
        if actual is None:
            print(f"  MISSING: {e.rel}")
            drift += 1
        elif actual != e.sha256:
            print(f"  DRIFT:   {e.rel}")
            drift += 1
    if drift == 0:
        print("  all files match recorded state")
        return 0
    print(f"  {drift} drifted file(s). Re-run `install --to {install_to} {' '.join(state.stack)}` to repair.")
    return 1
