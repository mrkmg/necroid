"""test — detect changed files in src/ vs src-pristine/ and try to compile them.

Dry-run build: same file-selection logic as `capture`, but instead of writing
patches it hands the modified + new .java files to javac. No staging, no install,
no side effects on the PZ install.
"""
from __future__ import annotations

from pathlib import Path

from .. import buildjava
from .. import logging_util as log
from ..errors import BuildError
from ..hashing import file_sha256


def run(args) -> int:
    p = args.profile

    src_zombie = p.src / "zombie"
    pristine_zombie = p.pristine / "zombie"
    if not src_zombie.exists():
        raise SystemExit(f"src/zombie/ not found at {src_zombie}")
    if not pristine_zombie.exists():
        raise SystemExit(f"src-pristine/zombie/ not found at {pristine_zombie} (run `necroid init`)")

    changed: list[Path] = []
    new_files: list[Path] = []

    for java in sorted(src_zombie.rglob("*.java")):
        if not java.is_file():
            continue
        rel = "zombie/" + java.relative_to(src_zombie).as_posix()
        pristine_file = p.pristine / rel
        if not pristine_file.exists():
            new_files.append(java)
            log.info(f"new:  {rel}")
            continue
        if file_sha256(java) == file_sha256(pristine_file):
            continue
        changed.append(java)
        log.info(f"mod:  {rel}")

    files = changed + new_files
    if not files:
        log.info("no changed or new .java files — nothing to test.")
        return 0

    log.step(f"compile {len(files)} file(s) (test build, output -> {p.classes_out})")
    try:
        buildjava.javac_compile(
            files=files,
            libs=p.libs,
            classpath_originals=p.classpath_originals,
            out_dir=p.classes_out,
            clean=True,
        )
    except BuildError as e:
        log.error(str(e))
        return 1

    log.success(f"test build OK: {len(files)} file(s) compiled.")
    return 0
