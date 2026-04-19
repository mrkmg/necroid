"""init — bootstrap a profile (client or server).

Steps:
    1. Resolve PZ install (flag -> config -> autodetect).
    2. Check external tools (java, javac, jar, git).
    3. Download tools/vineflower.jar.
    4. Copy PZ top-level *.jar  -> <profile>/libs/
    5. Copy PZ class subtrees    -> <profile>/classes-original/
    6. Rejar each subtree        -> <profile>/libs/classpath-originals/<name>.jar
    7. Write data/.mod-config.json.
    8. Decompile classes-original/zombie -> <profile>/src-pristine/zombie (Vineflower).
    9. Scaffold data/mods/ and <profile>/.mod-state.json.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from .. import logging_util as log
from ..config import ModConfig, config_path, read_config, write_config
from ..decompile import ensure_vineflower, decompile_zombie
from ..errors import ConfigError
from ..fsops import ensure_dir, mirror_tree
from ..hashing import file_sha256
from ..profile import Profile, autodetect_server_install, load_profile
from ..state import ModState, write_state
from ..tools import check_all, resolve


PZ_CLASS_SUBTREES = ("zombie", "astar", "com", "de", "fmod", "javax", "org", "se")
DEFAULT_CLIENT_INSTALL_WIN = Path(r"C:\Program Files (x86)\Steam\steamapps\common\ProjectZomboid")


def _resolve_pz_install(target: str, existing: Path | None, flag: str | None, root: Path) -> Path:
    if flag:
        from ..config import expand_config_path
        p = expand_config_path(flag, root)
        if p is None:
            raise ConfigError(f"could not resolve --pz-install '{flag}'")
        return p
    if existing and existing.exists():
        log.info(f"using configured {target}PzInstall: {existing}")
        return existing
    if target == "client" and DEFAULT_CLIENT_INSTALL_WIN.exists():
        log.info(f"using default PZ install: {DEFAULT_CLIENT_INSTALL_WIN}")
        return DEFAULT_CLIENT_INSTALL_WIN
    if target == "server":
        # Try autodetect off the client install (if known), then $ROOT/pzserver.
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
        f"could not locate {target} PZ install.\n"
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
        mirror_tree(src, dst)


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
        # Modern `jar` tools (JDK 17+) write to a tmp file then rename into place,
        # which fails with FileAlreadyExistsException if the target already exists.
        # Pre-delete to make the operation idempotent.
        if jar_path.exists():
            jar_path.unlink()
        proc = subprocess.run([jar_exe, "cf", str(jar_path), sub], cwd=str(originals))
        if proc.returncode != 0:
            raise RuntimeError(f"jar failed for {sub} (exit {proc.returncode})")


def run(args) -> int:
    root: Path = args.root
    target: str = args.target

    log.step(f"init [{target}] — step 1/9: resolve PZ install path")
    # Pre-read config (non-required) so we can carry over the other target's install.
    try:
        cfg = read_config(root, required=False)
    except ConfigError:
        cfg = ModConfig(_path=config_path(root))

    existing = cfg.pz_install(target)
    pz = _resolve_pz_install(target, existing, args.pz_install, root)
    if not pz.exists():
        raise ConfigError(f"PZ install dir does not exist: {pz}")
    log.info(str(pz))

    log.step("step 2/9: tools check (java, javac, jar, git)")
    found = check_all(["java", "javac", "jar", "git"])
    for name, path in found.items():
        log.info(f"{name}: {path}")

    log.step(f"step 3/9: vineflower.jar (v{__import__('pz_java_modder.decompile', fromlist=['VINEFLOWER_VERSION']).VINEFLOWER_VERSION})")
    ensure_vineflower(root / "data" / "tools", force=args.force)

    # Build the profile now that PZ install is known.
    if target == "client":
        cfg.client_pz_install = pz
    else:
        cfg.server_pz_install = pz

    profile = load_profile(root, target, cfg=cfg)

    content = profile.content_dir
    if not content.exists():
        raise ConfigError(f"expected content dir does not exist: {content}")
    log.step(f"step 4/9: copy PZ jars -> {profile.libs.relative_to(root)}")
    _copy_pz_jars(content, profile.libs, force=args.force)

    log.step(f"step 5/9: copy PZ class trees -> {profile.originals.relative_to(root)}")
    _copy_pz_classes(content, profile.originals, force=args.force)

    log.step(f"step 6/9: rejar class trees -> {profile.classpath_originals.relative_to(root)}")
    _rejar_originals(profile.originals, profile.classpath_originals, force=args.force)

    log.step(f"step 7/9: write {config_path(root).relative_to(root)}")
    write_config(root, cfg)
    log.info(f"wrote {config_path(root)}")

    log.step(f"step 8/9: decompile zombie -> {profile.pristine.relative_to(root)}")
    libs_jars = sorted(profile.libs.glob("*.jar")) + sorted(profile.classpath_originals.glob("*.jar"))
    decompile_zombie(
        classes_orig=profile.originals,
        out_pristine_dir=profile.pristine,
        libs_jars=libs_jars,
        vineflower_jar=profile.vineflower_jar,
        force=args.force,
    )

    log.step("step 9/9: scaffold mods/ + state")
    ensure_dir(profile.mods_dir)
    if not profile.state_file.exists():
        write_state(profile.state_file, ModState())
        log.info(f"created {profile.state_file}")

    log.success(f"init [{target}] complete.")
    log.info(f"next: pz-java-modder --target {target} new <mod-name>  then  capture <mod-name>")
    return 0
