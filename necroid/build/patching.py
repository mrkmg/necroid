"""Git-subprocess wrappers for diff / apply / merge-file.

All I/O is bytes — Python text-mode auto-translates newlines on Windows and
would corrupt LF-only patches against LF-only source. Patches are always
written with `\\n` line endings.

Critical workaround (preserved from PS): when `git apply` runs in a tree
that's nested inside a git repo, it consults the outer index and silently
drops patches whose targets aren't in the index. Stripping `diff --git` and
`index ...` lines forces git to fall back to plain `---`/`+++` path
resolution against CWD.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from ..util.tools import resolve


_GIT_ENV = ("-c", "core.autocrlf=false", "-c", "core.safecrlf=false")


def _git() -> str:
    return str(resolve("git"))


def git_diff_no_index(pristine: Path, working: Path, rel_path: str) -> bytes | None:
    """Run `git diff --no-index` between two files, return patch bytes with
       `a/<rel_path>` / `b/<rel_path>` headers. Returns None if files are identical.

       Exit codes: 0 = same, 1 = differ (expected), >=2 = error."""
    proc = subprocess.run(
        [
            _git(), *_GIT_ENV, "diff", "--no-index", "--no-color",
            "--no-renames", "-U3", "--", str(pristine), str(working),
        ],
        capture_output=True,
    )
    if proc.returncode >= 2:
        raise RuntimeError(f"git diff failed for {rel_path}: {proc.stderr.decode('utf-8', errors='replace')}")
    raw = proc.stdout
    if not raw:
        return None
    a_hdr = f"a/{rel_path}".encode("utf-8")
    b_hdr = f"b/{rel_path}".encode("utf-8")
    out: list[bytes] = []
    for line in raw.splitlines():
        if line.startswith(b"diff --git "):
            out.append(b"diff --git " + a_hdr + b" " + b_hdr)
        elif line.startswith(b"--- "):
            out.append(b"--- " + a_hdr)
        elif line.startswith(b"+++ "):
            out.append(b"+++ " + b_hdr)
        else:
            out.append(line)
    return b"\n".join(out) + b"\n"


def git_apply_file(patch_file: Path, work_dir: Path, rel_path: str) -> bool:
    """Apply a patch with `git apply`. Strips `diff --git` and `index` lines
       so git won't consult a parent repo's index. Returns True on success."""
    raw = patch_file.read_bytes()
    stripped_lines: list[bytes] = []
    for line in raw.splitlines():
        if line.startswith(b"diff --git "):
            continue
        if line.startswith(b"index ") and _looks_like_index_line(line):
            continue
        stripped_lines.append(line)
    stripped = b"\n".join(stripped_lines) + b"\n"

    with tempfile.NamedTemporaryFile(delete=False, suffix=".patch") as tmp:
        tmp.write(stripped)
        tmp_path = Path(tmp.name)
    try:
        proc = subprocess.run(
            [_git(), "-c", "core.autocrlf=false", "apply", "--whitespace=nowarn", "--", str(tmp_path)],
            cwd=str(work_dir),
            capture_output=True,
        )
        return proc.returncode == 0
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def _looks_like_index_line(line: bytes) -> bool:
    # git's index line is "index <hex>..<hex>[ <mode>]"
    if not line.startswith(b"index "):
        return False
    rest = line[len(b"index "):].split(b" ", 1)[0]
    return b".." in rest


def git_merge_file(current: Path, base: Path, incoming: Path) -> bool:
    """In-place 3-way merge. Exit 0 = clean, >0 = conflicts written in-place."""
    proc = subprocess.run(
        [_git(), "merge-file", "-L", "current", "-L", "base", "-L", "incoming",
         str(current), str(base), str(incoming)],
        capture_output=True,
    )
    return proc.returncode == 0


def patched_theirs_file(pristine_dir: Path, scratch_dir: Path, patch_file: Path, rel_path: str) -> Path | None:
    """Copy pristine/<rel> to scratch/<rel>, apply patch in scratch dir. Returns
       the patched file path, or None if apply failed / pristine is missing."""
    src = pristine_dir / rel_path
    if not src.exists():
        return None
    dst = scratch_dir / rel_path
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())
    if git_apply_file(patch_file, scratch_dir, rel_path):
        return dst
    return None
