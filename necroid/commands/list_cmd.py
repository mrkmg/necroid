"""list — tabular view of all mods with patch counts + clientOnly flag."""
from __future__ import annotations

from ..mod import list_mods, patch_items, read_mod_json


def run(args) -> int:
    profile = args.profile
    install_to: str = args.install_to  # used purely for counting postfix-filtered patches
    mods = list_mods(profile.mods_dir)
    if not mods:
        print("(no mods defined; run `necroid new <name>`)")
        return 0
    hdr = "{:<24} {:<10} {:>5} {:>4} {:>4}  {}".format(
        "MOD", "CLI-ONLY", "PATCH", "NEW", "DEL", "DESCRIPTION")
    print(hdr)
    print("-" * len(hdr))
    for name in mods:
        d = profile.mods_dir / name
        try:
            mj = read_mod_json(d)
        except Exception:
            continue
        effective_to = "client" if mj.client_only else install_to
        try:
            items = patch_items(d, effective_to)
        except Exception:
            items = []
        n_p = sum(1 for i in items if i.kind == "patch")
        n_n = sum(1 for i in items if i.kind == "new")
        n_d = sum(1 for i in items if i.kind == "delete")
        tag = "yes" if mj.client_only else "no"
        print("{:<24} {:<10} {:>5} {:>4} {:>4}  {}".format(
            name, tag, n_p, n_n, n_d, mj.description))
    return 0
