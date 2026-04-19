"""list — tabular view of all mods with patch counts + target tag."""
from __future__ import annotations

from ..mod import list_mods, patch_items, read_mod_json


def run(args) -> int:
    profile = args.profile
    mods = list_mods(profile.mods_dir)
    if not mods:
        print("(no mods defined; run `necroid new <name>`)")
        return 0
    hdr = "{:<24} {:<8} {:>5} {:>4} {:>4}  {}".format(
        "MOD", "TARGET", "PATCH", "NEW", "DEL", "DESCRIPTION")
    print(hdr)
    print("-" * len(hdr))
    for name in mods:
        d = profile.mods_dir / name
        try:
            mj = read_mod_json(d)
        except Exception:
            continue
        items = patch_items(d)
        n_p = sum(1 for i in items if i.kind == "patch")
        n_n = sum(1 for i in items if i.kind == "new")
        n_d = sum(1 for i in items if i.kind == "delete")
        tag = mj.target
        if tag != profile.target:
            tag = f"*{tag}"  # off-target
        print("{:<24} {:<8} {:>5} {:>4} {:>4}  {}".format(
            name, tag, n_p, n_n, n_d, mj.description))
    return 0
