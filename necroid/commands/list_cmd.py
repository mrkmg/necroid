"""list — tabular view of mods with patch counts, clientOnly flag, PZ version.

Default: filters to the workspace major (mods whose dir ends `-<workspaceMajor>`).
Pass `--all` to include cross-major and unversioned legacy dirs, annotated."""
from __future__ import annotations

from ..config import read_config
from ..mod import list_mods, mod_base_name, mod_major, patch_items, read_mod_json


def run(args) -> int:
    profile = args.profile
    install_to: str = args.install_to  # used purely for counting postfix-filtered patches
    show_all: bool = bool(getattr(args, "show_all", False))

    cfg = read_config(args.root, required=False)
    ws_major = int(getattr(cfg, "workspace_major", 0) or 0)

    if show_all or ws_major == 0:
        mods = list_mods(profile.mods_dir, include_all=True)
    else:
        mods = list_mods(profile.mods_dir, workspace_major=ws_major)

    if not mods:
        if ws_major and not show_all:
            print(f"(no mods for PZ major {ws_major}. Pass `--all` to see cross-major dirs.)")
        else:
            print("(no mods defined; run `necroid new <name>`)")
        return 0

    hdr = "{:<26} {:<10} {:<10} {:>5} {:>4} {:>4}  {:<20} {:<16} {}".format(
        "MOD", "PZ", "CLI-ONLY", "PATCH", "NEW", "DEL", "DEPS", "INCOMPATIBLE", "DESCRIPTION")
    print(hdr)
    print("-" * len(hdr))
    for name in mods:
        d = profile.mods_dir / name
        try:
            mj = read_mod_json(d)
        except Exception:
            continue
        major = mod_major(name)
        base = mod_base_name(name)

        # PZ column: prefer expectedVersion; else the dir major; else "—".
        if mj.expected_version:
            pz_col = mj.expected_version
        elif major is not None:
            pz_col = f"{major}.?"
        else:
            pz_col = "—"

        # Compatibility tag for --all view.
        display_name = base
        if show_all:
            if major is None:
                display_name = f"{name} (unversioned)"
            elif ws_major and major != ws_major:
                display_name = f"{name} (PZ {major})"

        effective_to = "client" if mj.client_only else install_to
        try:
            items = patch_items(d, effective_to)
        except Exception:
            items = []
        n_p = sum(1 for i in items if i.kind == "patch")
        n_n = sum(1 for i in items if i.kind == "new")
        n_d = sum(1 for i in items if i.kind == "delete")
        tag = "yes" if mj.client_only else "no"
        deps_col = ",".join(mj.dependencies) if mj.dependencies else "—"
        inc_col = ",".join(mj.incompatible_with) if mj.incompatible_with else "—"
        print("{:<26} {:<10} {:<10} {:>5} {:>4} {:>4}  {:<20} {:<16} {}".format(
            display_name[:26], pz_col[:10], tag, n_p, n_n, n_d,
            deps_col[:20], inc_col[:16], mj.description))
    if ws_major and show_all:
        print(f"\n(workspace bound to PZ major {ws_major}; non-matching mods are not installable.)")
    return 0
