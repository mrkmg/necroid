"""Apply an ordered list of mods onto a working tree.

Fast path: plain `git apply` if the target file is still pristine for this
stack pass.
Fallback: 3-way merge via `git merge-file` when a prior mod already touched
the same file.

Conflict types:
  new-collision             two mods both create the same new file
  new-overwrites-existing   a .java.new would overwrite an existing file
  patch-missing-pristine    patch targets a path not in src-pristine
  patch-target-missing      an earlier mod deleted the target
  patch-does-not-apply-to-pristine  stale patch — fails at "theirs" generation
  merge-conflict            3-way merge produced conflict markers
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..util.fsops import empty_dir
from ..core.mod import PatchItem, ensure_mod_exists, patch_items
from .patching import git_apply_file, git_merge_file, patched_theirs_file


@dataclass
class Conflict:
    rel: str
    type: str
    mods: list[str]

    def to_dict(self) -> dict:
        return {"rel": self.rel, "type": self.type, "mods": list(self.mods)}


@dataclass
class ApplyResult:
    conflicts: list[Conflict] = field(default_factory=list)
    touched: dict[str, str] = field(default_factory=dict)   # rel -> last-mod-to-touch
    deletes: list[str] = field(default_factory=list)


def apply_stack(
    stack: list[str],
    work_dir: Path,
    pristine_dir: Path,
    mods_dir: Path,
    scratch_root: Path,
    install_to: str,
) -> ApplyResult:
    """Apply `stack` (ordered list of mod names) into `work_dir`, which already
       mirrors pristine. Mutates `work_dir` in place. Returns conflicts + touched map.

       `install_to` selects which destination's patches to use when a mod ships
       per-destination postfixed files."""
    result = ApplyResult()
    new_owner: dict[str, str] = {}

    empty_dir(scratch_root)
    try:
        for mod_name in stack:
            md = ensure_mod_exists(mods_dir, mod_name)
            items = patch_items(md, install_to)
            scratch = scratch_root / mod_name
            scratch.mkdir(parents=True, exist_ok=True)

            for it in items:
                if it.kind == "new":
                    _handle_new(it, work_dir, mod_name, new_owner, result)
                elif it.kind == "delete":
                    _handle_delete(it, work_dir, mod_name, result)
                elif it.kind == "patch":
                    _handle_patch(it, work_dir, pristine_dir, scratch, mod_name, result)
    finally:
        if scratch_root.exists():
            shutil.rmtree(scratch_root, ignore_errors=True)

    return result


def _handle_new(it: PatchItem, work_dir: Path, mod_name: str, new_owner: dict[str, str], r: ApplyResult) -> None:
    if it.rel in new_owner:
        r.conflicts.append(Conflict(it.rel, "new-collision", [new_owner[it.rel], mod_name]))
        return
    dst = work_dir / it.rel
    if dst.exists():
        r.conflicts.append(Conflict(it.rel, "new-overwrites-existing", [mod_name]))
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(it.file, dst)
    new_owner[it.rel] = mod_name
    r.touched[it.rel] = mod_name


def _handle_delete(it: PatchItem, work_dir: Path, mod_name: str, r: ApplyResult) -> None:
    dst = work_dir / it.rel
    if dst.exists():
        try:
            dst.unlink()
        except OSError:
            pass
    if it.rel not in r.deletes:
        r.deletes.append(it.rel)
    r.touched[it.rel] = mod_name


def _handle_patch(
    it: PatchItem,
    work_dir: Path,
    pristine_dir: Path,
    scratch: Path,
    mod_name: str,
    r: ApplyResult,
) -> None:
    target = work_dir / it.rel
    pristine_file = pristine_dir / it.rel
    if not pristine_file.exists():
        r.conflicts.append(Conflict(it.rel, "patch-missing-pristine", [mod_name]))
        return
    if not target.exists():
        r.conflicts.append(Conflict(it.rel, "patch-target-missing", [mod_name]))
        return

    # Fast path: apply the patch directly to the current work_dir state.
    # Covers both (a) target unchanged from pristine, and (b) a dependent
    # mod whose patch was authored against pristine + an already-applied
    # ancestor mod — in that case work_dir already contains the ancestor's
    # mutations, which is exactly the state the patch expects.
    if git_apply_file(it.file, work_dir, it.rel):
        r.touched[it.rel] = mod_name
        return
    # Fast path failed: fall back to a 3-way merge against pristine. This
    # handles independent mods that both modify the same file against plain
    # pristine (and thus neither side's patch applies to the other's mutated
    # work_dir directly).

    theirs = patched_theirs_file(pristine_dir, scratch, it.file, it.rel)
    if theirs is None:
        r.conflicts.append(Conflict(it.rel, "patch-does-not-apply-to-pristine", [mod_name]))
        return
    if not git_merge_file(target, pristine_file, theirs):
        prev = r.touched.get(it.rel, "(pristine)")
        r.conflicts.append(Conflict(it.rel, "merge-conflict", [prev, mod_name]))
        return
    r.touched[it.rel] = mod_name
