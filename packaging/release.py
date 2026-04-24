#!/usr/bin/env python3
"""Bump version, commit, and tag a release.

Usage:
    python packaging/release.py <X.Y.Z>

Edits pyproject.toml and necroid/__init__.py to the given version, commits
the change to the current branch as "Release vX.Y.Z", and tags "vX.Y.Z".
Pushing is left to the caller.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
INIT_PY = REPO_ROOT / "necroid" / "__init__.py"

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def run(*args: str) -> str:
    r = subprocess.run(args, cwd=REPO_ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        die(f"{' '.join(args)} failed: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout.strip()


def replace_once(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    new_text, n = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if n != 1:
        die(f"could not find version line in {path}")
    path.write_text(new_text, encoding="utf-8")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        die("usage: release.py <X.Y.Z>")
    version = argv[1].lstrip("v")
    if not SEMVER_RE.match(version):
        die(f"invalid version {version!r}; expected X.Y.Z")

    tag = f"v{version}"

    status = run("git", "status", "--porcelain", "--untracked-files=no")
    if status:
        die("working tree not clean; commit or stash first")

    existing_tags = run("git", "tag", "--list", tag)
    if existing_tags:
        die(f"tag {tag} already exists")

    replace_once(
        PYPROJECT,
        r'^version = "\d+\.\d+\.\d+"$',
        f'version = "{version}"',
    )
    replace_once(
        INIT_PY,
        r'^__version__ = "\d+\.\d+\.\d+"$',
        f'__version__ = "{version}"',
    )

    run("git", "add", str(PYPROJECT.relative_to(REPO_ROOT)),
        str(INIT_PY.relative_to(REPO_ROOT)))
    run("git", "commit", "-m", f"Release {tag}")
    run("git", "tag", tag)

    print(f"released {tag}")
    print(f"push with: git push && git push origin {tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
