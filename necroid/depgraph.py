"""Dependency + incompatibility graph for mods.

Mod.json carries two bare-name lists:
  * `dependencies`       — mods that must be applied *before* this one.
  * `incompatibleWith`   — mods that cannot coexist with this one in a stack.

This module resolves bare names against the workspace major and builds a
topologically-ordered closure. Deps are applied before their dependents
(stackapply consumes an ordered list); incompatibilities are symmetric
(either side's declaration is enough to reject a pairing).

All public functions accept bare or fully-qualified names interchangeably —
they're canonicalised through `commands._resolve.resolve_mod` at the edge.
"""
from __future__ import annotations

from pathlib import Path

from .errors import (
    ModDependencyCycle,
    ModDependencyMissing,
    ModIncompatibility,
    ModNotFound,
    PzMajorMismatch,
)
from .mod import ensure_mod_exists, read_mod_json
from .commands._resolve import resolve_mod


def _canonical(mods_dir: Path, ws_major: int, name: str, *, context: str = "") -> str:
    """Canonicalise a single user-supplied name. A dep/incompat pointing at a
    non-existent or wrong-major mod surfaces as ModDependencyMissing — the
    caller needs a typed error, not the raw ModNotFound/PzMajorMismatch."""
    try:
        return resolve_mod(mods_dir, int(ws_major), name)
    except (ModNotFound, PzMajorMismatch) as e:
        prefix = f"{context}: " if context else ""
        raise ModDependencyMissing(f"{prefix}{e}") from None


def _canonicalise_many(mods_dir: Path, ws_major: int, names: list[str],
                       *, context: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for n in names:
        c = _canonical(mods_dir, ws_major, n, context=context)
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _read_edges(mods_dir: Path, ws_major: int, name: str) -> tuple[list[str], list[str]]:
    """Return (deps, incompat) for one mod — canonical names, deduped."""
    md = ensure_mod_exists(mods_dir, name)
    mj = read_mod_json(md)
    deps = _canonicalise_many(
        mods_dir, ws_major, mj.dependencies,
        context=f"mod '{name}' dependency"
    )
    inc = _canonicalise_many(
        mods_dir, ws_major, mj.incompatible_with,
        context=f"mod '{name}' incompatibleWith"
    )
    if name in deps:
        raise ModDependencyCycle(f"mod '{name}' lists itself as a dependency")
    return deps, inc


def resolve_deps(mods_dir: Path, ws_major: int, name: str) -> list[str]:
    """Transitive deps of `name` in topological order (dependencies first,
    dependents last). Excludes `name` itself. Raises ModDependencyCycle if
    the graph contains a cycle reachable from `name`.
    """
    name = _canonical(mods_dir, ws_major, name)

    # DFS with three-colour marking for cycle detection.
    # colour: 0 = unseen, 1 = on stack, 2 = done
    colour: dict[str, int] = {}
    order: list[str] = []
    path: list[str] = []

    def visit(n: str) -> None:
        c = colour.get(n, 0)
        if c == 2:
            return
        if c == 1:
            # Cycle — locate it in `path` and format nicely.
            idx = path.index(n)
            cycle = " -> ".join(path[idx:] + [n])
            raise ModDependencyCycle(f"dependency cycle: {cycle}")
        colour[n] = 1
        path.append(n)
        deps, _inc = _read_edges(mods_dir, ws_major, n)
        for d in deps:
            visit(d)
        path.pop()
        colour[n] = 2
        order.append(n)

    visit(name)
    # Drop `name` itself; caller passes it separately.
    return [m for m in order if m != name]


def expand_stack(mods_dir: Path, ws_major: int,
                 user_stack: list[str]) -> list[str]:
    """Return `user_stack` with every transitive dep hoisted before its
    dependents. Stable: a dep appears immediately before its first dependent
    (in iteration order); user-supplied order between independent mods is
    preserved; duplicates are collapsed.
    """
    canonical_user = _canonicalise_many(
        mods_dir, ws_major, user_stack, context="install stack"
    )
    out: list[str] = []
    placed: set[str] = set()
    for name in canonical_user:
        # Deps come first (in their own topo order), then the mod itself.
        chain = resolve_deps(mods_dir, ws_major, name) + [name]
        for n in chain:
            if n not in placed:
                out.append(n)
                placed.add(n)
    return out


def validate_incompat(mods_dir: Path, ws_major: int,
                      stack: list[str]) -> None:
    """Raise ModIncompatibility if any pair in `stack` is declared
    incompatible by either side. `stack` may be bare or canonical names."""
    canonical = _canonicalise_many(
        mods_dir, ws_major, stack, context="incompat validation"
    )
    # Build a symmetric incompat set.
    edges: dict[str, set[str]] = {n: set() for n in canonical}
    for n in canonical:
        _deps, inc = _read_edges(mods_dir, ws_major, n)
        for i in inc:
            edges.setdefault(n, set()).add(i)
            edges.setdefault(i, set()).add(n)
    # Check every unordered pair.
    present = set(canonical)
    for a in canonical:
        for b in edges.get(a, ()):
            if b in present and a < b:
                raise ModIncompatibility(
                    f"mods '{a}' and '{b}' are declared incompatible "
                    f"and cannot be installed together"
                )


def reverse_dependents(mods_dir: Path, ws_major: int, name: str,
                       *, within: list[str]) -> list[str]:
    """Of the mods in `within`, which transitively depend on `name`? Used by
    uninstall to detect orphaning. Returns canonical names in `within`'s
    iteration order."""
    target = _canonical(mods_dir, ws_major, name)
    canon_within = _canonicalise_many(
        mods_dir, ws_major, within, context="dependent lookup"
    )
    out: list[str] = []
    for candidate in canon_within:
        if candidate == target:
            continue
        try:
            closure = resolve_deps(mods_dir, ws_major, candidate)
        except (ModDependencyMissing, ModDependencyCycle):
            # Skip broken nodes — uninstall shouldn't fail just because an
            # unrelated mod's graph is busted.
            continue
        if target in closure:
            out.append(candidate)
    return out


def effective_client_only(mods_dir: Path, ws_major: int, name: str) -> bool:
    """True if `name` or any transitive dep has `clientOnly=true`. A dep's
    client_only propagates to its dependents because the applied stack in
    `src-<name>/` (or at install time) contains the dep's client-only code."""
    target = _canonical(mods_dir, ws_major, name)
    mj_target = read_mod_json(ensure_mod_exists(mods_dir, target))
    if mj_target.client_only:
        return True
    for d in resolve_deps(mods_dir, ws_major, target):
        mj = read_mod_json(ensure_mod_exists(mods_dir, d))
        if mj.client_only:
            return True
    return False
