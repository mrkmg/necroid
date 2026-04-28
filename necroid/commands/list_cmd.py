"""list — tabular view of mods with patch counts, clientOnly flag, PZ version.

Default: filters to the workspace major (mods whose dir ends `-<workspaceMajor>`).
Pass `--all` to include cross-major and unversioned legacy dirs, annotated."""
from __future__ import annotations

import json

from ..core.config import read_config
from ..core.mod import list_mods, mod_base_name, mod_major, patch_items, read_mod_json


_UNCATEGORIZED = "(uncategorized)"


def _cat_sort_key(cat: str) -> tuple[int, str]:
    # Uncategorized sinks to the bottom; real categories sort alphabetically.
    return (1, "") if cat == _UNCATEGORIZED else (0, cat)


def run(args) -> int:
    profile = args.profile
    install_to: str = args.install_to  # used purely for counting postfix-filtered patches
    show_all: bool = bool(getattr(args, "show_all", False))
    cat_filter: str | None = getattr(args, "category_filter", None)
    json_out: bool = bool(getattr(args, "json_out", False))
    if cat_filter is not None:
        cat_filter = cat_filter.strip().lower() or None

    cfg = read_config(args.root, required=False)
    ws_major = int(getattr(cfg, "workspace_major", 0) or 0)

    if show_all or ws_major == 0:
        mods = list_mods(profile.mods_dir, include_all=True)
    else:
        mods = list_mods(profile.mods_dir, workspace_major=ws_major)

    if not mods:
        if json_out:
            print(json.dumps({
                "schemaVersion": 1,
                "workspaceMajor": ws_major,
                "installTo": install_to,
                "showAll": show_all,
                "categoryFilter": cat_filter,
                "mods": [],
            }, indent=2))
            return 0
        if ws_major and not show_all:
            print(f"(no mods for PZ major {ws_major}. Pass `--all` to see cross-major dirs.)")
        else:
            print("(no mods defined; run `necroid new <name>`)")
        return 0

    # First pass: read mod.jsons, bucket by category.
    groups: dict[str, list[tuple[str, object]]] = {}
    for name in mods:
        try:
            mj = read_mod_json(profile.mods_dir / name)
        except Exception:
            continue
        cat = mj.category or _UNCATEGORIZED
        if cat_filter is not None and cat != cat_filter:
            continue
        groups.setdefault(cat, []).append((name, mj))

    if not groups:
        if json_out:
            print(json.dumps({
                "schemaVersion": 1,
                "workspaceMajor": ws_major,
                "installTo": install_to,
                "showAll": show_all,
                "categoryFilter": cat_filter,
                "mods": [],
            }, indent=2))
            return 0
        if cat_filter:
            print(f"(no mods in category '{cat_filter}')")
        return 0

    if json_out:
        entries: list[dict] = []
        for cat in sorted(groups.keys(), key=_cat_sort_key):
            for name, mj in sorted(groups[cat], key=lambda nm: mod_base_name(nm[0])):
                d = profile.mods_dir / name
                major = mod_major(name)
                base = mod_base_name(name)
                effective_to = "client" if mj.client_only else install_to
                try:
                    items = patch_items(d, effective_to)
                except Exception:
                    items = []
                n_p = sum(1 for i in items if i.kind == "patch")
                n_n = sum(1 for i in items if i.kind == "new")
                n_d = sum(1 for i in items if i.kind == "delete")
                entries.append({
                    "dirname": name,
                    "baseName": base,
                    "name": mj.name,
                    "modMajor": major,
                    "majorOk": (ws_major == 0) or (major == ws_major),
                    "category": mj.category or "",
                    "description": mj.description,
                    "version": mj.version,
                    "expectedVersion": mj.expected_version,
                    "clientOnly": mj.client_only,
                    "dependencies": list(mj.dependencies),
                    "incompatibleWith": list(mj.incompatible_with),
                    "patchCounts": {"patch": n_p, "new": n_n, "delete": n_d},
                })
        print(json.dumps({
            "schemaVersion": 1,
            "workspaceMajor": ws_major,
            "installTo": install_to,
            "showAll": show_all,
            "categoryFilter": cat_filter,
            "mods": entries,
        }, indent=2))
        return 0

    fmt = "{:<26} {:<10} {:<10} {:>5} {:>4} {:>4}  {:<20} {:<16} {}"
    hdr = fmt.format("MOD", "PZ", "CLI-ONLY", "PATCH", "NEW", "DEL",
                     "DEPS", "INCOMPATIBLE", "DESCRIPTION")

    print(hdr)
    print("-" * len(hdr))

    first = True
    for cat in sorted(groups.keys(), key=_cat_sort_key):
        # Section header for each category (skip when filter pins us to one).
        if cat_filter is None:
            if not first:
                print("")
            tail = max(0, len(hdr) - len(cat) - 6)
            print(f"== {cat} " + "=" * tail)
            first = False
        for name, mj in groups[cat]:
            d = profile.mods_dir / name
            major = mod_major(name)
            base = mod_base_name(name)

            # PZ column: prefer expectedVersion; else the dir major; else "—".
            if mj.expected_version:
                pz_col = mj.expected_version
            elif major is not None:
                pz_col = f"{major}.?"
            else:
                pz_col = "—"

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
            print(fmt.format(
                display_name[:26], pz_col[:10], tag, n_p, n_n, n_d,
                deps_col[:20], inc_col[:16], mj.description))

    if ws_major and show_all:
        print(f"\n(workspace bound to PZ major {ws_major}; non-matching mods are not installable.)")
    return 0
