"""init — bootstrap the shared workspace inside a PZ install (`<pz>/necroid/`).

Since client and server ship byte-identical Java class trees, one workspace
serves both destinations. Pick whichever install you have via `--from`. The
workspace lives inside the chosen install (`<pz>/necroid/workspace/`); a
small pointer file at `<repo>/data/.necroid-pointer.json` anchors the
checkout to that install. Multiple checkouts of necroid pointing at the same
PZ install share one workspace.

Two install layouts are supported:

- **loose** (PZ build 41 and earlier): classes live as a tree of `.class`
  files under `<pz>/zombie/...`. `init` mirrors that tree into
  `classes-original/` and rejars each subtree for `javac -cp`.
- **jar** (PZ build 42+): classes live inside a single fat
  `<pz>/projectzomboid.jar`. `init` extracts the jar into `classes-original/`
  and skips the rejar step — the fat jar itself is on `javac -cp`.

Steps:
    1. Refuse if a legacy (pre-PZ-anchored) layout is on disk.
    2. Resolve source PZ install (flag -> existing pointer -> autodetect).
    3. Check external tools (java, jar, git).
    4. Download tools/vineflower.jar.
    5. Copy PZ top-level *.jar  -> <pz>/necroid/workspace/libs/
    6. Seed classes-original/   (loose: mirror; jar: extract).
    7. Rejar each subtree -> classpath-originals/  (loose only).
    8. Detect PZ version, write workspace config, write pointer.
    9. Decompile every present class subtree -> src-pristine/.
   10. Scaffold mods/ + default .gitignore.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from ..util import logging_util as log
from ..util import procs
from ..core.config import (
    ModConfig,
    assert_no_legacy_layout,
    pz_necroid_dir,
    read_config,
    read_pointer,
    workspace_config_path,
    write_config,
    write_pointer,
)
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

    if source == "client":
        guess = discover_client_install()
        if guess:
            return guess
    else:
        # Reading peer install from config requires the config to exist already.
        # Pre-init we may not have one, so skip silently.
        client = None
        try:
            cfg = read_config(root, required=False)
            client = cfg.client_pz_install
        except Exception:
            pass
        guess = autodetect_server_install(client, root)
        if guess:
            log.info(f"autodetected server install: {guess}")
            return guess

    raise ConfigError(
        f"could not locate {source} PZ install.\n"
        f"    tried Steam registry / library folders for this OS.\n"
        f"    pass --pz-install '<path>'."
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
        if jar_path.exists():
            jar_path.unlink()
        proc = procs.run([jar_exe, "cf", str(jar_path), sub], cwd=str(originals))
        if proc.returncode != 0:
            raise RuntimeError(f"jar failed for {sub} (exit {proc.returncode})")


def run(args) -> int:
    root: Path = args.root
    source: str = args.source

    # Step 1: refuse to run on a legacy on-disk layout.
    assert_no_legacy_layout(root)

    ensure_dir(root / "data")
    ensure_dir(root / "data" / "tools")

    log.step(f"init [from={source}] — step 1/9: resolve PZ install path")

    # If a pointer already exists, prefer the install it names (a re-init
    # against the same install). Otherwise fall back to the configured peer
    # path or autodetect.
    existing_pz: Path | None = None
    try:
        existing_pz = read_pointer(root)
    except ConfigError:
        existing_pz = None

    # If the pointer's install matches the requested source, great. Otherwise
    # resolve fresh: the user may be re-anchoring the workspace to a different
    # install.
    pre_cfg = None
    if existing_pz is not None:
        try:
            pre_cfg = read_config(root, required=False)
        except Exception:
            pre_cfg = None

    candidate_existing: Path | None = None
    if pre_cfg is not None:
        candidate_existing = pre_cfg.install_path(source)

    pz = _resolve_pz_install(source, candidate_existing, args.pz_install, root)
    if not pz.exists():
        raise ConfigError(f"PZ install dir does not exist: {pz}")
    log.info(str(pz))

    log.step("step 2/9: tools check (java, javac, jar, git)")
    found = check_all(["java", "javac", "jar", "git"])
    for name, path in found.items():
        log.info(f"{name}: {path}")

    log.step(f"step 3/9: vineflower.jar (v{__import__('necroid.build.decompile', fromlist=['VINEFLOWER_VERSION']).VINEFLOWER_VERSION})")
    ensure_vineflower(root / "data" / "tools", force=args.force)

    # Build a fresh ModConfig for this init run. Read existing if present so
    # the peer install path is preserved.
    cfg = ModConfig()
    if pre_cfg is not None:
        cfg.client_pz_install = pre_cfg.client_pz_install
        cfg.server_pz_install = pre_cfg.server_pz_install
        cfg.default_install_to = pre_cfg.default_install_to
        cfg.workspace_source = pre_cfg.workspace_source
        cfg.originals_dir_override = pre_cfg.originals_dir_override

    if source == "client":
        cfg.client_pz_install = pz
    else:
        cfg.server_pz_install = pz
    cfg.workspace_source = source
    cfg.pz_install = pz

    # Write the pointer immediately so subsequent read_config calls during
    # init resolve correctly.
    pointer_p = write_pointer(root, pz)
    log.info(f"wrote pointer: {pointer_p}")

    profile = load_profile(root, cfg=cfg)

    content = profile.content_dir_for(source)
    if not content.exists():
        raise ConfigError(f"expected content dir does not exist: {content}")

    # Make sure the workspace dir exists before any subordinate step writes to it.
    ensure_dir(profile.pz_necroid_dir)
    ensure_dir(profile.workspace_dir)

    layout = detect_layout(content)
    log.info(f"detected install layout: {layout}"
             + (" (fat jar — PZ 42+)" if layout == "jar" else " (loose class tree — PZ <=41)"))

    log.step(f"step 4/9: copy PZ jars -> {profile.libs}")
    _copy_pz_jars(content, profile.libs, force=args.force)

    if layout == "jar":
        fat_jar = profile.libs / PZ_FAT_JAR_NAME
        if not fat_jar.is_file():
            raise ConfigError(
                f"workspace layout is 'jar' but {fat_jar} was not copied.\n"
                f"    is {content / PZ_FAT_JAR_NAME} present in the PZ install?"
            )
        log.step(f"step 5/9: extract {PZ_FAT_JAR_NAME} -> {profile.originals}")
        extract_fat_jar(fat_jar, profile.originals, PZ_CLASS_SUBTREES, force=args.force)

        log.step(f"step 6/9: rejar class trees -> {profile.classpath_originals} (skipped — fat jar serves as classpath original)")
    else:
        log.step(f"step 5/9: copy PZ class trees -> {profile.originals}")
        _copy_pz_classes(content, profile.originals, force=args.force)

        log.step(f"step 6/9: rejar class trees -> {profile.classpath_originals}")
        _rejar_originals(profile.originals, profile.classpath_originals, force=args.force)

    log.step(f"step 7/9: detect PZ version; write {workspace_config_path(pz)}")
    try:
        detected = detect_pz_version(content, package_dir(), profile.data_dir)
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

    require_javac_release(cfg.java_release, hint_major=cfg.java_release)
    log.info(f"javac release target: {cfg.java_release}")

    write_config(pz, cfg)
    log.info(
        f"wrote {workspace_config_path(pz)}  "
        f"(workspaceMajor={cfg.workspace_major}, layout={cfg.workspace_layout}, "
        f"javaRelease={cfg.java_release})"
    )

    log.step(f"step 8/9: decompile class subtrees -> {profile.pristine}")
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

    log.success(
        f"init [from={source}] complete. "
        f"Workspace at {pz_necroid_dir(pz)}. "
        f"Bound to PZ {cfg.workspace_version} (major {cfg.workspace_major})."
    )
    log.info("next: `necroid new <mod-name>`  then  `capture <mod-name>`")
    return 0


DEFAULT_GITIGNORE = """\
# -----------------------------------------------------------------------------
# Necroid — local-only files. Your mods live at /mods/ and should be committed.
# Workspace state (decompiled pristine, classes-original, install caches) lives
# inside the PZ install at `<pz>/necroid/`, not here in the checkout.
# -----------------------------------------------------------------------------

# Per-mod editable working trees — `necroid enter <mod>` seeds one at /src-<mod>/.
/src-*/

# Downloaded tooling. tools/ dir itself is tracked via .gitkeep; contents are not.
/data/tools/*
!/data/tools/.gitkeep

# Local checkout state
/data/.necroid-pointer.json
/data/.mod-enter.json
/data/.update-cache.json

# Legacy paths from pre-PZ-anchored Necroid (in case anyone migrates a checkout
# that still has them on disk). Safe to keep ignored.
/data/workspace/
/data/.mod-config.json
/data/.mod-state-client.json
/data/.mod-state-server.json
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
    """
    gi = root / ".gitignore"
    if gi.exists():
        return
    gi.write_text(DEFAULT_GITIGNORE, encoding="utf-8")
    log.info(f"wrote {gi}")


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
