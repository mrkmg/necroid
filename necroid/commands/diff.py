"""diff — concatenate and print every patch for a mod."""
from __future__ import annotations

import sys

from ..mod import ensure_mod_exists, patch_items


def run(args) -> int:
    md = ensure_mod_exists(args.profile.mods_dir, args.name)
    items = patch_items(md)
    for it in items:
        print()
        print(f"=== {it.rel} [{it.kind}] ===")
        sys.stdout.flush()
        sys.stdout.buffer.write(it.file.read_bytes())
        sys.stdout.flush()
    return 0
