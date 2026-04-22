"""Mod-name resolution: accept a bare base (`admin-xray`) or a fully-qualified
dir name (`admin-xray-41`), return the canonical on-disk dir name, and enforce
the workspace-major gate.

All CLI commands that take mod names route through `resolve_mod` so the gate
is consistent and cross-major dirs (including legacy unsuffixed ones) never
slip into install / enter / capture / uninstall flows."""
from __future__ import annotations

from pathlib import Path

from ..errors import ModNotFound, PzMajorMismatch
from ..core.mod import list_mods, mod_dirname, parse_mod_dirname


def resolve_mod(mods_dir: Path, workspace_major: int, user_name: str) -> str:
    """Resolve `user_name` to a canonical mod dir name under `mods_dir`.

    Accepts either form:
      * bare base ("admin-xray") — must resolve to `<base>-<workspace_major>`.
      * fully-qualified ("admin-xray-41") — major MUST equal workspace_major.

    Raises:
      * `ModNotFound` when no matching dir exists.
      * `PzMajorMismatch` when a fully-qualified name names a dir whose major
        does not match the workspace, or when only cross-major variants exist
        for a bare base.
    """
    if not user_name or "/" in user_name or "\\" in user_name:
        raise ModNotFound(f"invalid mod name: {user_name!r}")

    workspace_major = int(workspace_major)
    parsed = parse_mod_dirname(user_name)
    present = set(list_mods(mods_dir, include_all=True))

    if parsed is not None:
        # Fully-qualified: exact-match dir, major must agree.
        base, major = parsed
        if major != workspace_major:
            if user_name in present:
                raise PzMajorMismatch(
                    f"mod '{user_name}' is for PZ {major}; workspace is bound to PZ "
                    f"{workspace_major}. Use `resync-pristine --force-major-change` to "
                    f"switch workspaces, or install the `{base}-{workspace_major}` variant "
                    f"if one exists."
                )
            raise ModNotFound(f"mod '{user_name}' not found under {mods_dir}")
        if user_name not in present:
            raise ModNotFound(f"mod '{user_name}' not found under {mods_dir}")
        return user_name

    # Bare base: look for `<base>-<workspace_major>`.
    canonical = mod_dirname(user_name, workspace_major)
    if canonical in present:
        return canonical

    # Surface a friendlier error when a cross-major sibling exists.
    incompatible = [d for d in present
                    if (p := parse_mod_dirname(d)) is not None and p[0] == user_name]
    if incompatible:
        raise PzMajorMismatch(
            f"no '{user_name}' mod compatible with PZ {workspace_major}; "
            f"found incompatible: {', '.join(sorted(incompatible))}."
        )
    # Legacy unversioned dir? Nudge the user to re-init.
    if user_name in present:
        raise ModNotFound(
            f"mod dir '{user_name}' exists but has no `-<major>` suffix — "
            f"run `necroid init` to migrate legacy mod dirs, or rename it to "
            f"'{canonical}'."
        )
    raise ModNotFound(f"no mod '{user_name}' under {mods_dir}")
