"""verify — re-hash installed files on the chosen destination, report drift
(both file-level SHA drift and PZ-version drift since last install)."""
from __future__ import annotations

from pathlib import Path

from ..errors import PzVersionDetectError
from ..hashing import file_sha256
from ..profile import require_pz_install
from ..pzversion import detect_pz_version
from ..state import read_state


def run(args) -> int:
    p = args.profile
    install_to: str = args.install_to
    require_pz_install(p, install_to)
    state = read_state(p.state_file(install_to))
    if not state.installed:
        print(f"no installed state for {install_to}.")
        return 0

    # PZ version drift.
    content_dir = p.content_dir_for(install_to)
    try:
        detected = str(detect_pz_version(
            content_dir, Path(__file__).resolve().parent.parent, p.root / "data"))
    except PzVersionDetectError as e:
        detected = None
        print(f"  (could not detect {install_to} install version: {e})")
    if detected and state.pz_version and detected != state.pz_version:
        print(f"  VERSION: installed against PZ {state.pz_version}, install is now PZ {detected} — re-install recommended")
    elif detected and state.pz_version:
        print(f"  version ok: PZ {detected}")

    print(f"verify {install_to}: {len(state.installed)} installed file(s)")
    drift = 0
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
