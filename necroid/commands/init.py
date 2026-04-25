"""init — bootstrap the shared workspace from one PZ install (client or server).

Since client and server ship byte-identical Java class trees, one workspace
serves both destinations. Pick whichever install you have via `--from`.

Two install layouts are supported:

- **loose** (PZ build 41 and earlier): classes live as a tree of `.class`
  files under `<pz>/zombie/...`. `init` mirrors that tree into
  `classes-original/` and rejars each subtree for `javac -cp`.
- **jar** (PZ build 42+): classes live inside a single fat
  `<pz>/projectzomboid.jar`. `init` extracts the jar into `classes-original/`
  (so the decompiler and hash-based restore paths keep seeing a loose tree)
  and skips the rejar step — the fat jar itself is on `javac -cp` via
  `workspace/libs/projectzomboid.jar`.

Steps:
    1. Resolve source PZ install (flag -> config -> autodetect -> default).
    2. Check external tools (java, jar, git; javac is version-checked later
       once we know the target release).
    3. Download tools/vineflower.jar.
    4. Copy PZ top-level *.jar  -> workspace/libs/
    5. Seed workspace/classes-original/  (loose: mirror; jar: extract).
    6. Rejar each subtree        -> workspace/libs/classpath-originals/<name>.jar
       (skipped on jar layout — fat jar is the classpath original).
    7. Detect PZ version via probe; confirm workspace major; derive layout +
       javac release; verify javac is new enough; write config.
    8. Decompile every present class subtree -> workspace/src-pristine/ (Vineflower).
    9. Scaffold mods/ and write a default .gitignore if none exists.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from ..util import logging_util as log
from ..util import procs
from ..core.config import ModConfig, config_path, read_config, write_config
from ..build.decompile import ensure_vineflower, decompile_all
from ..build.jar_extract import extract_fat_jar
from ..errors import ConfigError, PzVersionDetectError
from ..util.fsops import ensure_dir, mirror_tree
from ..util.hashing import file_sha256
from ..core.profile import (
    PZ_CLASS_SUBTREES,
    PZ_FAT_JAR_NAME,
    autodetect_server_install,
    detect_layout,
    java_release_for_major,
    load_profile,
)
from ..pz.pzversion import detect_pz_version
from ..pz.steam_discovery import discover_client_install
from ..paths import package_dir
from ..util.tools import check_all, require_javac_release, resolve


def _resolve_pz_install(source: str, existing: Path | None, flag: str | None, root: Path) -> Path:
    if flag:
        from ..core.config import expand_config_path
        p = expand_config_path(flag, root)
        if p is None:
            raise ConfigError(f"could not resolve --pz-install '{flag}'")
        return p
    if existing and existing.exists():
        log.info(f"using configured {source}PzInstall: {existing}")
        return existing

    # Steam-aware discovery (OS-specific roots + libraryfolders.vdf),
    # plus legacy fallbacks for the server (sibling-of-client, $ROOT/pzserver).
    if source == "client":
        guess = discover_client_install()
        if guess:
            return guess
    else:
        cfg = None
        try:
            cfg = read_config(root, required=False)
        except Exception:
            cfg = None
        client = cfg.client_pz_install if cfg else None
        guess = autodetect_server_install(client, root)
        if guess:
            log.info(f"autodetected server install: {guess}")
            return guess

    raise ConfigError(
        f"could not locate {source} PZ install.\n"
        f"    tried Steam registry / library folders for this OS.\n"
        f"    pass --pz-install '<path>' or edit data/.mod-config.json."
    )


def _copy_pz_jars(pz: Path, libs: Path, force: bool) -> None:
    ensure_dir(libs)
    src_jars = sorted(pz.glob("*.jar"))
    if not src_jars:
        raise ConfigError(f"no top-level .jar files under {pz} — is this the correct PZ install?")
    copied = skipped = 0
    for j in src_jars:
        dst = libs / j.name
        if dst.exists() and not force:
            if file_sha256(j) == file_sha256(dst):
                skipped += 1
                continue
        shutil.copy2(j, dst)
        copied += 1
    log.info(f"libs/: copied {copied}, unchanged {skipped} (total {len(src_jars)})")


def _copy_pz_classes(pz: Path, originals: Path, force: bool) -> None:
    ensure_dir(originals)
    for sub in PZ_CLASS_SUBTREES:
        src = pz / sub
        dst = originals / sub
        if not src.exists():
            log.warn(f"[missing] {src} — skipping")
            continue
        if dst.exists() and not force:
            log.info(f"[skip] classes-original/{sub} (use --force to refresh)")
            continue
        log.info(f"classes-original/{sub} <- {src}")
        # verify=True: resync-pristine sends us a possibly-modded install; we
        # must re-hash to detect files that look unchanged by mtime/size but
        # differ in content (e.g. Steam-reverted files with a close mtime).
        mirror_tree(src, dst, verify=True)


def _rejar_originals(originals: Path, out_jar_dir: Path, force: bool) -> None:
    ensure_dir(out_jar_dir)
    jar_exe = str(resolve("jar"))
    for sub in PZ_CLASS_SUBTREES:
        cls_dir = originals / sub
        if not cls_dir.exists():
            continue
        jar_path = out_jar_dir / f"{sub}.jar"
        if jar_path.exists() and not force:
            jar_mtime = jar_path.stat().st_mtime
            newest = max((p.stat().st_mtime for p in cls_dir.rglob("*") if p.is_file()), default=0)
            if newest <= jar_mtime:
                log.info(f"[skip] libs/classpath-originals/{sub}.jar (up to date)")
                continue
        log.info(f"libs/classpath-originals/{sub}.jar <- classes-original/{sub}")
        # Pre-delete: modern `jar` refuses to overwrite on rename-into-place.
        if jar_path.exists():
            jar_path.unlink()
        proc = procs.run([jar_exe, "cf", str(jar_path), sub], cwd=str(originals))
        if proc.returncode != 0:
            raise RuntimeError(f"jar failed for {sub} (exit {proc.returncode})")


def run(args) -> int:
    root: Path = args.root
    source: str = args.source  # populated in cli.py from --from (or default)
    
    # create data dirs
    ensure_dir(root / "data")
    ensure_dir(root / "data" / "tools")
    ensure_dir(root / "data" / "workspace")

    log.step(f"init [from={source}] — step 1/9: resolve PZ install path")
    cfg = read_config(root, required=False)

    existing = cfg.pz_install(source)
    pz = _resolve_pz_install(source, existing, args.pz_install, root)
    if not pz.exists():
        raise ConfigError(f"PZ install dir does not exist: {pz}")
    log.info(str(pz))

    # javac is version-gated later once we know the target release (PZ 42 needs JDK 25+).
    log.step("step 2/9: tools check (java, javac, jar, git)")
    found = check_all(["java", "javac", "jar", "git"])
    for name, path in found.items():
        log.info(f"{name}: {path}")

    log.step(f"step 3/9: vineflower.jar (v{__import__('necroid.build.decompile', fromlist=['VINEFLOWER_VERSION']).VINEFLOWER_VERSION})")
    ensure_vineflower(root / "data" / "tools", force=args.force)

    # Record this install path into the config.
    if source == "client":
        cfg.client_pz_install = pz
    else:
        cfg.server_pz_install = pz
    cfg.workspace_source = source

    profile = load_profile(root, cfg=cfg)

    content = profile.content_dir_for(source)
    if not content.exists():
        raise ConfigError(f"expected content dir does not exist: {content}")

    layout = detect_layout(content)
    log.info(f"detected install layout: {layout}"
             + (" (fat jar — PZ 42+)" if layout == "jar" else " (loose class tree — PZ <=41)"))

    log.step(f"step 4/9: copy PZ jars -> {profile.libs.relative_to(root)}")
    _copy_pz_jars(content, profile.libs, force=args.force)

    if layout == "jar":
        fat_jar = profile.libs / PZ_FAT_JAR_NAME
        if not fat_jar.is_file():
            # Should never happen — _copy_pz_jars copies every top-level .jar.
            raise ConfigError(
                f"workspace layout is 'jar' but {fat_jar} was not copied.\n"
                f"    is {content / PZ_FAT_JAR_NAME} present in the PZ install?"
            )
        log.step(f"step 5/9: extract {PZ_FAT_JAR_NAME} -> {profile.originals.relative_to(root)}")
        extract_fat_jar(fat_jar, profile.originals, PZ_CLASS_SUBTREES, force=args.force)

        log.step(f"step 6/9: rejar class trees -> {profile.classpath_originals.relative_to(root)} (skipped — fat jar serves as classpath original)")
    else:
        log.step(f"step 5/9: copy PZ class trees -> {profile.originals.relative_to(root)}")
        _copy_pz_classes(content, profile.originals, force=args.force)

        log.step(f"step 6/9: rejar class trees -> {profile.classpath_originals.relative_to(root)}")
        _rejar_originals(profile.originals, profile.classpath_originals, force=args.force)

    log.step(f"step 7/9: detect PZ version; write {config_path(root).relative_to(root)}")
    try:
        detected = detect_pz_version(content, package_dir(), root / "data")
    except PzVersionDetectError as e:
        raise ConfigError(
            f"could not detect PZ version at {content}: {e}\n"
            f"    workspace cannot be bound to a major without a version. Aborting."
        )
    log.info(f"detected PZ version: {detected}")

    chosen_major = _choose_workspace_major(detected.major, args)
    if chosen_major != detected.major:
        log.warn(
            f"workspace will be bound to major {chosen_major}, but source install is "
            f"PZ {detected}. This is advanced; compile correctness is not guaranteed."
        )
    cfg.workspace_major = int(chosen_major)
    cfg.workspace_version = str(detected)
    cfg.workspace_layout = layout
    cfg.java_release = java_release_for_major(chosen_major)
    if not cfg.workspace_fingerprint:
        cfg.workspace_fingerprint = _generate_fingerprint(root)
        log.info(f"workspace fingerprint: {cfg.workspace_fingerprint[:16]}…")

    # Enforce javac >= target release now that we know the target. A mismatch
    # is a hard error here (rather than surfacing later at first `install` /
    # `test`) because the rest of `init` ran expensive work assuming the
    # user would be able to compile against it.
    require_javac_release(cfg.java_release, hint_major=cfg.java_release)
    log.info(f"javac release target: {cfg.java_release}")

    write_config(root, cfg)
    log.info(
        f"wrote {config_path(root)}  "
        f"(workspaceMajor={cfg.workspace_major}, layout={cfg.workspace_layout}, "
        f"javaRelease={cfg.java_release})"
    )

    log.step(f"step 8/9: decompile class subtrees -> {profile.pristine.relative_to(root)}")
    libs_jars = sorted(profile.libs.glob("*.jar")) + sorted(profile.classpath_originals.glob("*.jar"))
    decompile_all(
        classes_orig=profile.originals,
        out_pristine_dir=profile.pristine,
        subtrees=list(PZ_CLASS_SUBTREES),
        libs_jars=libs_jars,
        vineflower_jar=profile.vineflower_jar,
        force=args.force,
    )

    log.step("step 9/9: scaffold mods/ + default .gitignore")
    ensure_dir(profile.mods_dir)
    _ensure_default_gitignore(root)

    log.success(f"init [from={source}] complete. Workspace bound to PZ {cfg.workspace_version} (major {cfg.workspace_major}).")
    log.info("next: `necroid new <mod-name>`  then  `capture <mod-name>`")
    return 0


DEFAULT_GITIGNORE = """\
# -----------------------------------------------------------------------------
# Necroid — local-only files. Your mods live at /mods/ and should be committed.
# Everything below is regenerated from your own PZ install via `necroid init`.
# -----------------------------------------------------------------------------

# Shared workspace populated by `necroid init` from your PZ install.
/data/workspace/

# Per-mod editable working trees — `necroid enter <mod>` seeds one at /src-<mod>/.
/src-*/

# Downloaded tooling. tools/ dir itself is tracked via .gitkeep; contents are not.
/data/tools/*
!/data/tools/.gitkeep

# Local runtime state
/data/.mod-config.json
/data/.mod-enter.json
/data/.mod-state-client.json
/data/.mod-state-server.json
/data/.update-cache.json
/data/.update-cache-mods.json
/data/.import-tmp/
/data/.update-tmp/

# Build output (if you're building Necroid itself)
/dist/
/build/
"""


def _ensure_default_gitignore(root: Path) -> None:
    """Write a default `.gitignore` covering every Necroid-generated path.

    No-op when one already exists — never clobber a user's gitignore.
    This is what makes the 3rd-party dev flow one-step: drop necroid in
    your repo, `init`, and the local-only paths are already ignored.
    """
    gi = root / ".gitignore"
    if gi.exists():
        return
    gi.write_text(DEFAULT_GITIGNORE, encoding="utf-8")
    log.info(f"wrote {gi}")


def _generate_fingerprint(root: Path) -> str:
    """Per-workspace opaque id. Persistent across CLI invocations (written to
    config), unique per-init. Stamped into the install-side manifest so a
    second Necroid checkout can't silently reuse the same install.
    """
    import secrets
    from datetime import datetime, timezone
    from ..util.hashing import string_sha256
    seed = f"{root.resolve()}|{datetime.now(timezone.utc).isoformat()}|{secrets.token_hex(16)}"
    return string_sha256(seed).upper()


def _choose_workspace_major(detected_major: int, args) -> int:
    """Determine the workspace major to bind to.

    --major N           explicit override (advanced).
    --yes / non-tty     accept detected major silently.
    interactive tty     prompt to confirm; default=yes.
    """
    override = getattr(args, "major", None)
    if override is not None:
        return int(override)

    assume_yes = bool(getattr(args, "yes", False))
    if assume_yes or not sys.stdin.isatty():
        return int(detected_major)

    try:
        resp = input(
            f"Bind this workspace to PZ major {detected_major}? [Y/n]: "
        ).strip().lower()
    except EOFError:
        return int(detected_major)
    if resp in ("", "y", "yes"):
        return int(detected_major)
    raise ConfigError(
        "init aborted: workspace major not confirmed. Re-run with `--yes` or "
        "`--major N` if you know what you want."
    )
