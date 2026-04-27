"""mod-update — refresh imported mods from their source repos.

    necroid mod-update                    # check + refresh every imported mod
    necroid mod-update <name>             # one mod
    necroid mod-update <name> --include-peers   # also any mods sharing (repo, ref)
    necroid mod-update --check             # dry-run, populate update cache
    necroid mod-update --json --check      # machine-readable for the GUI

Groups targets by (repo, ref) so a single archive download serves N peer mods.
On `--check`, persists results to <pz>/necroid/update-cache-mods.json (24h TTL).
Refuses any mod that is currently entered.
"""
from __future__ import annotations

import json
import shutil
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..util import logging_util as log
from ..core.config import read_config
from ..errors import ConfigError, ModNotFound, ModUpdateError
from ..util.fsops import ensure_dir
from ..remote._archive import (
    DiscoveredMod,
    copy_mod_tree,
    discover_mods,
    extract_archive,
)
from ..remote._providers import (
    PROVIDER_GITHUB,
    PROVIDER_GITLAB,
    download_origin_zip,
    resolve_origin_sha,
)
from ..core.mod import (
    list_mods,
    mod_base_name,
    mod_dirname,
    parse_mod_dirname,
    read_mod_json,
    read_origin,
    has_origin,
    write_mod_json,
    write_origin,
)
from ..core.state import read_enter, utc_now_iso
from ..remote.updater import parse_version


CACHE_TTL_SECONDS = 24 * 3600


@dataclass
class _Target:
    dirname: str
    mj_path: Path
    origin: dict


def run(args) -> int:
    profile = args.profile
    cfg = read_config(args.root)
    if not cfg.workspace_major:
        raise ConfigError(
            "workspace has no bound major. Run `necroid init` before updating mods."
        )
    ws_major = int(cfg.workspace_major)

    check_only = bool(getattr(args, "check_only", False))
    force = bool(getattr(args, "force", False))
    include_peers = bool(getattr(args, "include_peers", False))
    json_out = bool(getattr(args, "json", False))
    name_arg: Optional[str] = getattr(args, "name", None)

    enter_state = read_enter(profile.enter_file)

    # --- Build target list ---
    targets = _collect_targets(profile.mods_dir, ws_major, name_arg, include_peers)
    if not targets:
        if name_arg:
            raise ModUpdateError(
                f"mod '{name_arg}' has no recorded origin — was it imported?"
            )
        log.info("no imported mods found (nothing to update)")
        if json_out:
            print(json.dumps({"results": []}, indent=2))
        return 0

    # --- Group by (provider, host, repo, ref) ---
    # ``host`` is "" for github (canonical host implied). Including provider +
    # host in the key prevents cross-host GitLab peers from being lumped
    # together when they happen to share a ``namespace/project`` path.
    groups: dict[tuple[str, str, str, str], list[_Target]] = {}
    for t in targets:
        key = (
            t.origin.get("type") or PROVIDER_GITHUB,
            str(t.origin.get("host") or ""),
            t.origin.get("repo", ""),
            t.origin.get("ref", ""),
        )
        groups.setdefault(key, []).append(t)

    results: list[dict] = []
    tmp_root = profile.tmp_dir / "update-tmp"
    if tmp_root.exists():
        shutil.rmtree(tmp_root, ignore_errors=True)

    try:
        for (provider, host, repo, ref), group in groups.items():
            results.extend(_process_group(
                provider=provider, host=host, repo=repo, ref=ref, group=group,
                profile=profile, ws_major=ws_major,
                enter_state=enter_state,
                check_only=check_only, force=force,
                tmp_root=tmp_root,
            ))
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    # --- Persist cache (always, on --check OR full updates — both reflect the
    # latest known upstream state) ---
    _write_cache(profile.update_cache_mods_file, results)

    # --- Output ---
    if json_out:
        print(json.dumps({"results": results}, indent=2))
    _summarize(results, check_only)

    return 0


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------

def _collect_targets(mods_dir: Path, ws_major: int,
                     name_arg: Optional[str], include_peers: bool) -> list[_Target]:
    if name_arg:
        dirname = _resolve_named(mods_dir, ws_major, name_arg)
        mj = read_mod_json(mods_dir / dirname)
        if not has_origin(mj):
            raise ModUpdateError(
                f"mod '{dirname}' has no recorded origin — was it imported?"
            )
        primary = _Target(dirname=dirname, mj_path=mods_dir / dirname,
                          origin=read_origin(mj) or {})
        out = [primary]
        if include_peers:
            for other in list_mods(mods_dir, workspace_major=ws_major):
                if other == dirname:
                    continue
                try:
                    other_mj = read_mod_json(mods_dir / other)
                except Exception:
                    continue
                o = read_origin(other_mj)
                if not o:
                    continue
                if (o.get("repo") == primary.origin.get("repo")
                        and o.get("ref") == primary.origin.get("ref")):
                    out.append(_Target(dirname=other, mj_path=mods_dir / other,
                                       origin=o))
        return out

    out = []
    for d in list_mods(mods_dir, workspace_major=ws_major):
        try:
            mj = read_mod_json(mods_dir / d)
        except Exception:
            continue
        if has_origin(mj):
            out.append(_Target(dirname=d, mj_path=mods_dir / d,
                               origin=read_origin(mj) or {}))
    return out


def _resolve_named(mods_dir: Path, ws_major: int, name: str) -> str:
    """Accept bare base or fully-qualified dirname, like other commands."""
    parsed = parse_mod_dirname(name)
    if parsed is None:
        candidate = mod_dirname(name, ws_major)
    else:
        candidate = name
    if not (mods_dir / candidate).is_dir():
        raise ModNotFound(f"mod '{name}' not found at {mods_dir / candidate}")
    return candidate


# ---------------------------------------------------------------------------
# Per-group processing
# ---------------------------------------------------------------------------

def _process_group(*, provider: str, host: str, repo: str, ref: str,
                   group: list[_Target],
                   profile, ws_major: int, enter_state,
                   check_only: bool, force: bool,
                   tmp_root: Path) -> list[dict]:
    if not repo or not ref:
        return [_result(t, status="error",
                        message="origin block is missing repo/ref",
                        upstream_version=None, upstream_sha=None)
                for t in group]

    # Shape validation: github always needs ``owner/repo``; gitlab needs at
    # least one ``/`` for ``namespace/project`` and tolerates more segments.
    if "/" not in repo:
        return [_result(t, status="error",
                        message=f"origin.repo malformed: {repo!r}",
                        upstream_version=None, upstream_sha=None)
                for t in group]

    host_note = f" ({provider} {host})" if host else ""
    log.step(f"checking {repo}@{ref}{host_note} ({len(group)} mod(s))")

    # All mods in this group share the same origin shape — use the first one
    # to drive the dispatch.
    origin_for_dispatch = group[0].origin
    try:
        sha_info = resolve_origin_sha(origin_for_dispatch, ref)
    except Exception as e:
        return [_result(t, status="error",
                        message=str(e),
                        upstream_version=None, upstream_sha=None)
                for t in group]
    new_sha = sha_info.sha
    log.info(f"upstream commit {new_sha[:7]}")

    # Skip download entirely if every mod is already at this SHA + not --force.
    all_current = (not force) and all(
        t.origin.get("commitSha") == new_sha for t in group
    )
    if all_current:
        results = []
        for t in group:
            local_v = read_mod_json(t.mj_path).version
            log.info(f"  {t.dirname}: up to date (v{local_v} @ {new_sha[:7]})")
            results.append(_result(
                t, status="up-to-date", message="up to date",
                upstream_version=local_v, upstream_sha=new_sha,
            ))
        return results

    # Need the archive — fetch once.
    ensure_dir(tmp_root)
    group_stem = repo.replace("/", "-")
    group_tmp = tmp_root / f"{group_stem}-{new_sha[:12]}"
    if group_tmp.exists():
        shutil.rmtree(group_tmp, ignore_errors=True)
    ensure_dir(group_tmp)
    zip_path = group_tmp / "archive.zip"
    try:
        url = download_origin_zip(origin_for_dispatch, ref,
                                   is_tag=sha_info.is_tag, dest=zip_path)
        wrapper = extract_archive(zip_path, group_tmp / "x")
        upstream = discover_mods(wrapper)
    except Exception as e:
        return [_result(t, status="error", message=str(e),
                        upstream_version=None, upstream_sha=new_sha)
                for t in group]

    by_subdir: dict[str, DiscoveredMod] = {dm.subdir: dm for dm in upstream}

    results: list[dict] = []
    for t in group:
        results.append(_process_one(
            t=t, by_subdir=by_subdir, profile=profile, ws_major=ws_major,
            enter_state=enter_state, new_sha=new_sha,
            archive_url_str=url, provider=provider, host=host,
            repo=repo, ref=ref,
            check_only=check_only, force=force,
        ))
    return results


def _process_one(*, t: _Target, by_subdir: dict, profile, ws_major: int,
                 enter_state, new_sha: str, archive_url_str: str,
                 provider: str, host: str,
                 repo: str, ref: str, check_only: bool, force: bool) -> dict:
    subdir = t.origin.get("subdir", "")
    dm = by_subdir.get(subdir)
    if dm is None:
        msg = (f"upstream no longer contains '{subdir or '<root>'}' "
               f"(mod removed/moved in repo?)")
        log.warn(f"  {t.dirname}: {msg}")
        return _result(t, status="error", message=msg,
                       upstream_version=None, upstream_sha=new_sha)

    if enter_state and enter_state.mod == t.dirname:
        msg = ("currently entered; run `necroid clean` "
               "(or capture + clean) before updating")
        log.warn(f"  {t.dirname}: {msg}")
        return _result(t, status="entered", message=msg,
                       upstream_version=dm.mj.version, upstream_sha=new_sha)

    # No major-vs-workspace check: the local dirname encodes the major and
    # never changes during update. If upstream's mod.json.expected_version
    # disagrees with the dirname suffix, read_mod_json catches it as an
    # authoring error.

    local_mj = read_mod_json(t.mj_path)
    local_v = local_mj.version
    new_v = dm.mj.version
    cmp = (parse_version(new_v) > parse_version(local_v))
    if not cmp and not force:
        log.info(f"  {t.dirname}: up to date (v{local_v} @ {new_sha[:7]})")
        # Refresh the local origin block's commitSha so subsequent runs can
        # short-circuit the archive download. Bundled mods ship with an empty
        # commitSha — the first up-to-date check populates it here.
        if t.origin.get("commitSha") != new_sha:
            write_origin(
                local_mj,
                type=provider, host=host or None,
                repo=repo, ref=ref, subdir=subdir,
                commitSha=new_sha,
                archiveUrl=t.origin.get("archiveUrl") or archive_url_str,
                importedAt=t.origin.get("importedAt") or utc_now_iso(),
                upstreamVersion=new_v,
            )
            try:
                write_mod_json(t.mj_path, local_mj)
            except OSError:
                pass  # advisory; cache still records the SHA
        return _result(t, status="up-to-date", message="up to date",
                       upstream_version=new_v, upstream_sha=new_sha)

    if check_only:
        log.info(f"  {t.dirname}: would update v{local_v} -> v{new_v} @ {new_sha[:7]}")
        return _result(t, status="outdated",
                       message=f"would update v{local_v} -> v{new_v}",
                       upstream_version=new_v, upstream_sha=new_sha)

    # --- Apply ---
    target = profile.mods_dir / t.dirname
    staging = profile.mods_dir / (t.dirname + ".new")
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    copy_mod_tree(dm.src_path, staging)

    # Compose new mod.json — preserve some local fields, take others from upstream.
    new_mj = dm.mj
    new_mj.name = t.dirname  # canonical dir-derived name
    new_mj.pristine_snapshot = local_mj.pristine_snapshot
    new_mj.expected_version = local_mj.expected_version
    new_mj.created_at = local_mj.created_at
    now = utc_now_iso()
    new_mj.updated_at = now
    write_origin(
        new_mj,
        type=provider, host=host or None,
        repo=repo, ref=ref, subdir=subdir,
        commitSha=new_sha, archiveUrl=archive_url_str,
        importedAt=now, upstreamVersion=new_v,
    )
    write_mod_json(staging, new_mj)

    if local_mj.client_only != new_mj.client_only:
        log.warn(f"  {t.dirname}: clientOnly {local_mj.client_only} -> {new_mj.client_only}")

    if target.exists():
        shutil.rmtree(target)
    staging.rename(target)

    log.success(f"  {t.dirname}: v{local_v} -> v{new_v} @ {new_sha[:7]}")
    return _result(t, status="updated",
                   message=f"updated v{local_v} -> v{new_v}",
                   upstream_version=new_v, upstream_sha=new_sha)


# ---------------------------------------------------------------------------
# Cache + result shape
# ---------------------------------------------------------------------------

def _result(t: _Target, *, status: str, message: str,
            upstream_version: Optional[str], upstream_sha: Optional[str]) -> dict:
    local_v = ""
    try:
        local_v = read_mod_json(t.mj_path).version
    except Exception:
        pass
    return {
        "name": t.dirname,
        "status": status,             # updated|outdated|up-to-date|entered|error
        "message": message,
        "localVersion": local_v,
        "upstreamVersion": upstream_version,
        "upstreamSha": upstream_sha,
        "checkedAt": utc_now_iso(),
        "repo": t.origin.get("repo"),
        "ref": t.origin.get("ref"),
        "subdir": t.origin.get("subdir", ""),
    }


def _write_cache(path: Path, results: list[dict]) -> None:
    """Merge results into the on-disk update cache. We keep stale entries for
    mods that weren't part of this run (so a `mod-update <one>` doesn't blow
    away the prior 'check all' state for the others)."""
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
    by_name = dict(existing.get("mods") or {})
    for r in results:
        by_name[r["name"]] = {
            "checkedAt": r["checkedAt"],
            "localVersion": r["localVersion"],
            "upstreamVersion": r["upstreamVersion"],
            "upstreamSha": r["upstreamSha"],
            "status": r["status"],
            "message": r["message"],
        }
    out = {"version": 1, "mods": by_name}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    except OSError:
        # Cache is advisory — never fail the command because of it.
        pass


def read_cache(path: Path) -> dict:
    """Public reader for the GUI."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _summarize(results: list[dict], check_only: bool) -> None:
    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    parts = [f"{k}={v}" for k, v in sorted(counts.items())]
    verb = "checked" if check_only else "processed"
    log.success(f"{verb} {len(results)} mod(s): {', '.join(parts) if parts else '(none)'}")


