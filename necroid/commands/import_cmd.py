"""import — pull mods from a GitHub or GitLab repo into mods/<base>-<major>/.

The upstream repo is expected to follow the canonical Necroid layout:
`<repo-root>/mods/<base>-<major>/mod.json`. Mods are identified by their
canonical `<base>-<major>` dirname (same shape as locally-authored ones).
Per-major variants coexist: a repo may carry `admin-xray-41/` and
`admin-xray-42/` side by side, and an import filters to the workspace's
bound major by default.

    necroid import owner/repo                          # GitHub, bare slug
    necroid import https://gitlab.com/ns/proj          # GitLab, full URL
    necroid import owner/repo --list                   # discover only (text)
    necroid import owner/repo --list --json            # discover only (machine-readable, GUI)
    necroid import owner/repo --all                    # import every mod that matches workspace major
    necroid import owner/repo --mod foo                # bare base resolves against workspace major
    necroid import owner/repo --mod foo-41             # exact dirname (must match workspace major)
    necroid import owner/repo --include-all-majors --all
        # also pull mods for non-current majors (rare; e.g. preparing for a PZ migration)

Pre-flight validates the entire selection before any writes. Per-mod commit
goes through `<target>.new` then atomic rename, so a failure mid-loop leaves
already-committed peers intact.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..util import logging_util as log
from ..core.config import read_config
from ..errors import ConfigError, ModImportError
from ..util.fsops import ensure_dir
from ..remote.github import (
    DiscoveredMod,
    copy_mod_tree,
    discover_mods,
    extract_archive,
    parse_github_ref,
)
from ..remote import github as _gh_mod
from ..remote import gitlab as _gl_mod
from ..remote._providers import (
    PROVIDER_GITHUB,
    PROVIDER_GITLAB,
    detect_provider,
)
from ..core.mod import (
    list_mods,
    mod_base_name,
    parse_mod_dirname,
    write_mod_json,
    write_origin,
)
from ..core.state import utc_now_iso


def run(args) -> int:
    profile = args.profile
    cfg = read_config(args.root)
    if not cfg.workspace_major:
        raise ConfigError(
            "workspace has no bound major. Run `necroid init` before importing mods."
        )
    ws_major = int(cfg.workspace_major)

    # --- Resolve repo + ref (provider-dispatched) ---
    provider = detect_provider(args.repo)
    if provider == PROVIDER_GITLAB:
        gl = _gl_mod.parse_gitlab_ref(args.repo)
        provider_host: str | None = gl.host
        repo_full = gl.project_path
        ref_from_url = gl.ref_from_url
        ref = args.ref or ref_from_url or _gl_mod.resolve_default_branch(
            gl.host, gl.project_path)
        log.step(f"resolving {repo_full}@{ref} (gitlab {gl.host})")
        sha_info = _gl_mod.resolve_commit_sha(gl.host, gl.project_path, ref)
    else:
        gh = parse_github_ref(args.repo)
        provider_host = None  # github.com implied; not stored in origin
        repo_full = f"{gh.owner}/{gh.repo}"
        ref_from_url = gh.ref_from_url
        ref = args.ref or ref_from_url or _gh_mod.resolve_default_branch(
            gh.owner, gh.repo)
        log.step(f"resolving {repo_full}@{ref}")
        sha_info = _gh_mod.resolve_commit_sha(gh.owner, gh.repo, ref)
    log.info(f"commit {sha_info.sha[:7]} ({'tag' if sha_info.is_tag else 'branch/sha'})")

    # --- Download + extract ---
    tmp_root = profile.data_dir / ".import-tmp"
    if tmp_root.exists():
        shutil.rmtree(tmp_root, ignore_errors=True)
    ensure_dir(tmp_root)
    try:
        zip_stem = repo_full.replace("/", "-")
        zip_path = tmp_root / f"{zip_stem}-{sha_info.sha[:12]}.zip"
        if provider == PROVIDER_GITLAB:
            url = _gl_mod.download_repo_zip(
                gl.host, gl.project_path, ref,
                is_tag=sha_info.is_tag, dest=zip_path,
            )
        else:
            url = _gh_mod.download_repo_zip(
                gh.owner, gh.repo, ref,
                is_tag=sha_info.is_tag, dest=zip_path,
            )
        log.info(f"downloaded {zip_path.name}")
        extract_dir = tmp_root / "x"
        wrapper = extract_archive(zip_path, extract_dir)
        log.info(f"extracted into {wrapper.name}/")

        discovered = discover_mods(wrapper)

        # --- --list / --json: discover-only ---
        if getattr(args, "list_only", False):
            if getattr(args, "json", False):
                _emit_json_discovery(discovered, ws_major)
            else:
                _emit_text_discovery(repo_full, ref, discovered, ws_major)
            return 0

        # --- Selection ---
        selected = _resolve_selection(discovered, args, repo_full, ws_major)

        # --- Pre-flight (no writes) ---
        _preflight(selected, ws_major, profile.mods_dir, args)

        # --- Commit per-mod ---
        committed: list[str] = []
        try:
            for dm in selected:
                _commit_one(
                    dm=dm, profile=profile,
                    provider=provider, host=provider_host,
                    repo_full=repo_full, ref=ref,
                    sha=sha_info.sha, archive_url_str=url,
                )
                committed.append(dm.dirname)
                log.success(
                    f"  + {dm.dirname}  v{dm.mj.version}  "
                    f"(from {dm.subdir or '<root>'})"
                )
        except Exception:
            if committed:
                log.warn(
                    f"{len(committed)} mod(s) committed before failure: "
                    f"{', '.join(committed)}"
                )
            raise

        # --- Resolve-warn dependencies once full peer set is visible ---
        # Only check at the workspace's major — other-major variants are
        # filtered out of list/install anyway and unblock a different toolchain.
        installed_bases = {mod_base_name(n) for n
                           in list_mods(profile.mods_dir, workspace_major=ws_major)}
        for dm in selected:
            if dm.mod_major != ws_major:
                continue
            for dep in dm.mj.dependencies:
                bare = mod_base_name(dep)
                if bare not in installed_bases:
                    log.warn(
                        f"{dm.dirname}: dependency '{bare}' not installed at "
                        f"major {ws_major} — install it before `enter`/`install`"
                    )

        log.success(
            f"imported {len(committed)} mod(s) from {repo_full}@{ref} "
            f"({sha_info.sha[:7]})"
        )
        return 0
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Discovery output
# ---------------------------------------------------------------------------

def _emit_text_discovery(repo_full: str, ref: str,
                         discovered: list[DiscoveredMod], ws_major: int) -> None:
    print(f"{repo_full} @ {ref} — {len(discovered)} mod(s):")
    for dm in discovered:
        loc = dm.subdir or "<root>"
        ev = (dm.mj.expected_version or "").strip()
        if dm.mod_major is None:
            major_note = "  [! no -<major> suffix on dirname/name]"
        elif dm.mod_major != ws_major:
            major_note = f"  [! PZ major {dm.mod_major}, workspace is {ws_major}]"
        else:
            major_note = ""
        co = " clientOnly" if dm.mj.client_only else ""
        print(f"  - {loc}: {dm.dirname or dm.mj.name} v{dm.mj.version} "
              f"(PZ {ev or '?'}){co}{major_note}")


def _emit_json_discovery(discovered: list[DiscoveredMod], ws_major: int) -> None:
    payload = {
        "mods": [
            {
                "subdir": dm.subdir,
                "dirname": dm.dirname,
                "name": dm.mj.name,
                "baseName": mod_base_name(dm.dirname or dm.mj.name),
                "modMajor": dm.mod_major,
                "version": dm.mj.version,
                "description": dm.mj.description,
                "clientOnly": dm.mj.client_only,
                "expectedVersion": dm.mj.expected_version,
                "majorOk": dm.mod_major == ws_major,
                "dependencies": list(dm.mj.dependencies),
                "incompatibleWith": list(dm.mj.incompatible_with),
            }
            for dm in discovered
        ],
        "workspaceMajor": ws_major,
    }
    print(json.dumps(payload, indent=2))


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def _resolve_selection(discovered: list[DiscoveredMod], args, repo_full: str,
                       ws_major: int) -> list[DiscoveredMod]:
    """Pick which discovered mods to import.

    A discovered mod can be selected by:
      - exact subdir          ("mods/admin-xray-41")
      - exact dirname         ("admin-xray-41")
      - bare base name        ("admin-xray" — resolves against workspace major)

    Bare-name selectors only resolve against mods whose major matches the
    workspace (so `--mod admin-xray` against a repo carrying both 41 and 42
    variants picks the one for your workspace). Fully-qualified selectors
    match any major; if the matched mod's major doesn't match the workspace,
    pre-flight rejects it (unless `--include-all-majors` is set).
    """
    selectors: list[str] = list(getattr(args, "mod_selectors", None) or [])
    select_all = bool(getattr(args, "select_all", False))
    include_all_majors = bool(getattr(args, "include_all_majors", False))

    def _all_in_scope() -> list[DiscoveredMod]:
        if include_all_majors:
            return list(discovered)
        return [dm for dm in discovered if dm.mod_major == ws_major]

    if select_all:
        scope = _all_in_scope()
        if not scope:
            avail = _availability_line(discovered)
            raise ModImportError(
                f"--all matched no mods at workspace major {ws_major}.\n"
                f"  available: {avail}\n"
                f"  pass --include-all-majors to import every variant"
            )
        return scope

    if selectors:
        matched: list[DiscoveredMod] = []
        unmatched: list[str] = []
        for sel in selectors:
            parsed = parse_mod_dirname(sel)
            if parsed is not None:
                # Fully-qualified `<base>-<major>` selector.
                hits = [dm for dm in discovered
                        if dm.dirname == sel or dm.subdir == sel
                        or dm.subdir.endswith("/" + sel)]
            else:
                # Bare base — auto-suffix to workspace major.
                target_dir = f"{sel}-{ws_major}"
                hits = [dm for dm in discovered
                        if dm.dirname == target_dir
                        or dm.subdir.endswith("/" + target_dir)
                        or dm.subdir == target_dir]
            if not hits:
                unmatched.append(sel)
            for h in hits:
                if h not in matched:
                    matched.append(h)
        if unmatched:
            avail = _availability_line(discovered)
            raise ModImportError(
                f"--mod selectors did not match any discovered mod at "
                f"workspace major {ws_major}: {', '.join(unmatched)}\n"
                f"  available: {avail}"
            )
        return matched

    # No --all, no --mod, no --list.
    in_scope = _all_in_scope()
    if len(in_scope) == 1:
        return in_scope

    avail = _availability_line(discovered)
    if not in_scope:
        raise ModImportError(
            f"repo {repo_full} contains {len(discovered)} mod(s), "
            f"none for PZ major {ws_major}.\n  available: {avail}\n"
            f"  pass --include-all-majors to import a non-matching variant"
        )
    raise ModImportError(
        f"repo {repo_full} contains {len(in_scope)} mods at PZ "
        f"major {ws_major}; pass --all or --mod <name> [--mod ...]\n"
        f"  available: {avail}"
    )


def _availability_line(discovered: list[DiscoveredMod]) -> str:
    return ", ".join(
        f"{(dm.subdir or '<root>')} ({dm.dirname or dm.mj.name}@{dm.mj.version})"
        for dm in discovered
    )


# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------

def _preflight(selected: list[DiscoveredMod], ws_major: int,
               mods_dir: Path, args) -> None:
    """Validate the entire selection before any writes.

    Targets are the upstream's canonical `<base>-<major>` dirname — never
    re-suffixed. `--name` rebases the dir base while preserving the mod's
    own major (so `--name foo` against `admin-xray-41` becomes `foo-41`,
    not `foo-<workspaceMajor>`).
    """
    name_override = getattr(args, "name_override", None)
    force = bool(getattr(args, "force", False))
    include_all_majors = bool(getattr(args, "include_all_majors", False))

    if name_override and len(selected) != 1:
        raise ModImportError("--name only valid when importing exactly one mod")

    # 1. Must have a parseable major suffix.
    no_suffix = [dm for dm in selected if dm.mod_major is None]
    if no_suffix:
        violators = "\n  - ".join(
            f"{(dm.subdir or '<root>')}: dirname '{dm.dirname or dm.mj.name}' "
            f"has no -<major> suffix"
            for dm in no_suffix
        )
        raise ModImportError(
            "Necroid mod dirs must end in -<PZ-major> (e.g. admin-xray-41). "
            "Upstream does not follow this convention:\n  - " + violators
        )

    # 2. Major-vs-workspace check (skippable with --include-all-majors).
    if not include_all_majors:
        wrong = [dm for dm in selected if dm.mod_major != ws_major]
        if wrong:
            violators = "\n  - ".join(
                f"{(dm.subdir or '<root>')} ({dm.dirname}): PZ major "
                f"{dm.mod_major}, workspace is {ws_major}"
                for dm in wrong
            )
            raise ModImportError(
                f"PZ major mismatch on {len(wrong)} selected mod(s):\n  - "
                + violators
                + "\n  pick a matching variant from the same repo, or pass "
                  "--include-all-majors to import anyway (the mod will be "
                  "filtered out of list/install until you switch workspaces)."
            )

    # 3. Resolve final dirname per selected mod.
    for dm in selected:
        if name_override:
            override = name_override.strip()
            parsed = parse_mod_dirname(override)
            if parsed is None:
                # Bare base — preserve the mod's own major.
                dm.dirname = f"{override}-{dm.mod_major}"
            else:
                # Fully-qualified — must match the mod's own major.
                if parsed[1] != dm.mod_major:
                    raise ModImportError(
                        f"--name '{override}' has major {parsed[1]} but mod "
                        f"is for major {dm.mod_major}; pass bare base or fix major"
                    )
                dm.dirname = override
        # else: dm.dirname already set by discover_mods.

    # 4. Target collisions within the selection.
    seen: dict[str, DiscoveredMod] = {}
    for dm in selected:
        if dm.dirname in seen:
            other = seen[dm.dirname]
            raise ModImportError(
                f"two selected mods resolve to the same target dir "
                f"'{dm.dirname}' "
                f"(conflict: {other.subdir or '<root>'} vs {dm.subdir or '<root>'})"
            )
        seen[dm.dirname] = dm

    # 5. Existing target conflicts.
    if not force:
        existing = [dm.dirname for dm in selected
                    if (mods_dir / dm.dirname).exists()]
        if existing:
            lines = "\n  - ".join(str(mods_dir / d) for d in existing)
            raise ModImportError(
                "target dir(s) already exist; pass --force to overwrite:\n  - " + lines
            )


# ---------------------------------------------------------------------------
# Per-mod commit
# ---------------------------------------------------------------------------

def _commit_one(*, dm: DiscoveredMod, profile,
                provider: str, host: str | None,
                repo_full: str, ref: str, sha: str,
                archive_url_str: str) -> None:
    """Atomically install one discovered mod into profile.mods_dir.

    Strategy: copy upstream tree into <target>.new, rewrite mod.json there,
    then rmtree old target (if any) and rename .new -> target. Each mod is
    its own atomic unit; partial failure across multiple mods leaves earlier
    successes intact.
    """
    target = profile.mods_dir / dm.dirname
    staging = profile.mods_dir / (dm.dirname + ".new")

    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)

    copy_mod_tree(dm.src_path, staging)

    # Re-stamp mod.json: canonical name + origin block.
    mj = dm.mj
    mj.name = dm.dirname
    now = utc_now_iso()
    write_origin(
        mj,
        type=provider,
        host=host,
        repo=repo_full,
        ref=ref,
        subdir=dm.subdir,
        commitSha=sha,
        archiveUrl=archive_url_str,
        importedAt=now,
        upstreamVersion=mj.version,
    )
    mj.updated_at = now
    write_mod_json(staging, mj)

    if target.exists():
        shutil.rmtree(target)
    staging.rename(target)
