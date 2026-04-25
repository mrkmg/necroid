"""test — detect changed files in src-<entered>/ vs src-pristine/ and try to compile them.

Dry-run build: same file-selection logic as `capture`, but instead of writing
patches it hands the modified + new .java files to javac. No staging, no install,
no side effects on the PZ install.
"""
from __future__ import annotations

from pathlib import Path

from ..build import buildjava
from ..util import logging_util as log
from ..errors import BuildError
from ..util.hashing import file_sha256
from ..core.profile import existing_subtrees
from ..core.state import read_enter


def run(args) -> int:
    p = args.profile

    es = read_enter(p.enter_file)
    if not es:
        raise SystemExit("no mod is entered — run `necroid enter <mod>` first.")
    src_dir = p.src_for(es.mod)
    if not src_dir.exists():
        raise SystemExit(
            f"entered mod '{es.mod}' has no working tree at {src_dir}. "
            f"Run `necroid enter {es.mod} --force` to re-seed."
        )

    subs = existing_subtrees(p.pristine)
    if not subs:
        raise SystemExit(f"src-pristine/ is empty at {p.pristine} (run `necroid init`)")

    changed: list[Path] = []
    new_files: list[Path] = []

    for sub in subs:
        src_sub = src_dir / sub
        if not src_sub.exists():
            continue
        for java in sorted(src_sub.rglob("*.java")):
            if not java.is_file():
                continue
            rel = f"{sub}/" + java.relative_to(src_sub).as_posix()
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

    log.step(f"compile {len(files)} file(s) from {src_dir.name}/ (test build, output -> {p.classes_out})")
    try:
        buildjava.javac_compile(
            files=files,
            libs=p.libs,
            classpath_originals=p.classpath_originals,
            out_dir=p.classes_out,
            clean=True,
            java_release=int(p.java_release or 17),
        )
    except BuildError as e:
        log.error(str(e))
        return 1

    log.success(f"test build OK: {len(files)} file(s) compiled.")
    return 0
