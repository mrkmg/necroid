"""diff — concatenate and print every patch for a mod.

Groups output by shared / client-only / server-only when any postfix variants exist.
"""
from __future__ import annotations

import sys

from ..mod import (
    INSTALL_DESTINATIONS,
    ensure_mod_exists,
    parse_patch_filename,
    read_mod_json,
)


def _dump(rel: str, kind: str, path) -> None:
    print()
    print(f"=== {rel} [{kind}] ===")
    sys.stdout.flush()
    sys.stdout.buffer.write(path.read_bytes())
    sys.stdout.flush()


def run(args) -> int:
    md = ensure_mod_exists(args.profile.mods_dir, args.name)
    _mj = read_mod_json(md)   # load-check (raises on legacy schema)
    shared: list[tuple[str, str, object]] = []
    client_only: list[tuple[str, str, object]] = []
    server_only: list[tuple[str, str, object]] = []
    patches = md / "patches"
    if patches.exists():
        for p in sorted(patches.rglob("*")):
            if not p.is_file():
                continue
            rel_full = p.relative_to(patches).as_posix()
            parsed = parse_patch_filename(rel_full)
            if parsed is None:
                continue
            rel, kind, applies = parsed
            if applies == frozenset(INSTALL_DESTINATIONS):
                shared.append((rel, kind, p))
            elif applies == frozenset(("client",)):
                client_only.append((rel, kind, p))
            else:
                server_only.append((rel, kind, p))
    # Common case: only shared files. Skip the section headers.
    if shared and not client_only and not server_only:
        for rel, kind, p in shared:
            _dump(rel, kind, p)
        return 0
    for label, bucket in (("shared", shared), ("client-only", client_only), ("server-only", server_only)):
        if not bucket:
            continue
        print()
        print(f"### {label} ###")
        for rel, kind, p in bucket:
            _dump(rel, kind, p)
    return 0
