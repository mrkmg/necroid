"""Build a self-contained distributable.

Produces `<repo-root>/dist/`:
    necroid(.exe)
    mods/         (copied from <repo-root>/mods)
    data/
      tools/      (empty placeholder; first run self-extracts vineflower)
    README.txt

Also writes `<repo-root>/dist-archives/necroid-<version>-<platform>-<arch>.zip`
with the dist/ contents at the archive root — that's what CI uploads to releases.

Run from the repo root:
    python packaging/build_dist.py

Requires PyInstaller. Each platform builds locally (no cross-compile).
"""
from __future__ import annotations

import datetime as _dt
import json
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent                # packaging/
REPO_ROOT = HERE.parent                                # repo root

# Make `necroid` importable so we can read __version__ without running it.
sys.path.insert(0, str(REPO_ROOT))
from necroid import __version__ as NECROID_VERSION     # noqa: E402

DIST = REPO_ROOT / "dist"
PYI_WORK = REPO_ROOT / "build"                         # PyInstaller scratch (gitignored)
PYI_DIST = REPO_ROOT / "build" / "pyi-dist"            # PyInstaller raw output (gitignored)
ARCHIVES = REPO_ROOT / "dist-archives"                 # release zips (gitignored)


def platform_tag() -> tuple[str, str]:
    """Return (platform, arch) suffix pair, e.g. ('windows', 'x64')."""
    if sys.platform == "win32":
        plat = "windows"
    elif sys.platform == "darwin":
        plat = "macos"
    else:
        plat = "linux"
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch = "x64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        arch = machine or "unknown"
    return plat, arch


def build_macos_icns(assets_dir: Path) -> Path | None:
    """Generate assets/necroid-icon.icns from the committed PNGs.

    Uses Apple's `iconutil` (ships with Xcode command-line tools on every
    macOS GitHub runner). ImageMagick's ICNS coder is unusable on non-macOS
    hosts, so we avoid baking a committed .icns and regenerate per build.

    Returns the .icns path on success, or None if we're not on macOS / the
    tool chain is missing. Callers should treat None as "no app icon" and
    continue (matches how the Windows build behaves if .ico is missing).
    """
    if sys.platform != "darwin":
        return None
    if shutil.which("iconutil") is None or shutil.which("sips") is None:
        print("warning: iconutil/sips missing -- macOS build will have no app icon")
        return None

    source_full = assets_dir / "necroid-icon-256.png"
    source_skull = assets_dir / "necroid-mark-256.png"
    if not source_full.exists() or not source_skull.exists():
        print("warning: source PNGs missing -- skipping .icns generation")
        return None

    iconset = assets_dir / "necroid-icon.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir()

    # Apple's .iconset naming is exact: iconutil rejects unknown filenames.
    # Small sizes use skull-only (wordmark illegible); large sizes use full.
    # @2x variants at 32/64/256/512 cover Retina displays.
    frames = [
        ("icon_16x16.png",       16,   source_skull),
        ("icon_16x16@2x.png",    32,   source_skull),
        ("icon_32x32.png",       32,   source_skull),
        ("icon_32x32@2x.png",    64,   source_full),
        ("icon_128x128.png",     128,  source_full),
        ("icon_128x128@2x.png",  256,  source_full),
        ("icon_256x256.png",     256,  source_full),
        ("icon_256x256@2x.png",  512,  source_full),
        ("icon_512x512.png",     512,  source_full),
        ("icon_512x512@2x.png",  1024, source_full),
    ]
    for name, size, src in frames:
        subprocess.run(
            ["sips", "-z", str(size), str(size), str(src), "--out", str(iconset / name)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    icns_path = assets_dir / "necroid-icon.icns"
    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(icns_path)],
        check=True,
    )
    shutil.rmtree(iconset)
    print(f"Generated {icns_path}")
    return icns_path


def run_pyinstaller() -> Path:
    exe_name = "necroid"
    entry = REPO_ROOT / "necroid" / "__main__.py"
    tools_jar = REPO_ROOT / "data" / "tools" / "vineflower.jar"
    assets_dir = REPO_ROOT / "assets"
    mark_png = assets_dir / "necroid-mark-256.png"
    icon_full_png = assets_dir / "necroid-icon-256.png"
    icon_skull_png = assets_dir / "necroid-icon-skull-128.png"
    icon_ico = assets_dir / "necroid-icon.ico"
    icon_icns = build_macos_icns(assets_dir)
    # PZ-version probe source. Must end up alongside `necroid/` in the frozen
    # bundle so `pzversion._probe_source(package_dir())` resolves it.
    probe_java = REPO_ROOT / "necroid" / "java" / "NecroidGetPzVersion.java"

    # --add-data spec is `src<sep>dest`: ';' on Windows, ':' elsewhere.
    sep = ";" if sys.platform == "win32" else ":"
    add_data: list[str] = []
    if tools_jar.exists():
        add_data.extend(["--add-data", f"{tools_jar}{sep}data/tools"])
    for png in (mark_png, icon_full_png, icon_skull_png):
        if png.exists():
            add_data.extend(["--add-data", f"{png}{sep}assets"])
    if probe_java.exists():
        add_data.extend(["--add-data", f"{probe_java}{sep}necroid/java"])
    else:
        raise SystemExit(f"probe source missing: {probe_java}")

    icon_args: list[str] = []
    if sys.platform == "win32" and icon_ico.exists():
        icon_args = ["--icon", str(icon_ico)]
    elif sys.platform == "darwin" and icon_icns is not None and icon_icns.exists():
        icon_args = ["--icon", str(icon_icns)]

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", exe_name,
        "--noconfirm",
        "--clean",
        f"--workpath={PYI_WORK}",
        f"--distpath={PYI_DIST}",
        f"--specpath={PYI_WORK}",
        *icon_args,
        *add_data,
        str(entry),
    ]
    print("+", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT))
    if proc.returncode != 0:
        raise SystemExit(f"PyInstaller failed (exit {proc.returncode})")
    suffix = ".exe" if sys.platform == "win32" else ""
    return PYI_DIST / f"{exe_name}{suffix}"


def copy_layout(binary: Path) -> None:
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)

    # Binary at top
    dest_binary = DIST / binary.name
    shutil.copy2(binary, dest_binary)
    # Preserve exec bit on POSIX (shutil.copy2 already does, but be explicit)
    if sys.platform != "win32":
        dest_binary.chmod(0o755)

    # Bundled mods. Stamp each mod.json with an `origin` block pointing back
    # at this repo so `necroid mod-update` refreshes bundled mods via the same
    # GitHub-fetch pipeline as user-imported ones. Without this, the only way
    # to get newer bundled mods would be to re-download the whole binary.
    src_mods = REPO_ROOT / "mods"
    if src_mods.exists():
        dst_mods = DIST / "mods"
        shutil.copytree(src_mods, dst_mods)
        stamp_bundled_origins(dst_mods)

    # Empty tools placeholder
    (DIST / "data" / "tools").mkdir(parents=True, exist_ok=True)
    (DIST / "data" / "tools" / ".gitkeep").write_text("", encoding="utf-8")

    readme = DIST / "README.txt"
    readme.write_text(
        "Necroid — Beyond Workshop\n"
        "=========================\n"
        "\n"
        "Java mod manager for Project Zomboid (client + dedicated server).\n"
        "\n"
        "Prereqs on the target machine: Git, JDK 17+ (javac, jar), Java runtime.\n"
        "\n"
        "First-run client setup:\n"
        "    ./necroid init\n"
        "\n"
        "First-run server setup:\n"
        "    ./necroid --target server init\n"
        "\n"
        "GUI (client):   ./necroid --gui\n"
        "GUI (server):   ./necroid --gui -server\n"
        "\n"
        "Bundled mods in mods/. Edit data/.mod-config.json to point at\n"
        "your PZ install paths (or run `init` and it'll autodetect).\n"
        "\n"
        "Updates + source: https://github.com/mrkmg/necroid\n",
        encoding="utf-8",
    )
    print(f"\nDist written to {DIST}")


BUNDLED_REPO = "mrkmg/necroid"
BUNDLED_REF = "main"


def stamp_bundled_origins(dist_mods_dir: Path) -> None:
    """Write an `origin` block into each bundled mod.json under `dist_mods_dir`.

    Pointing at `mrkmg/necroid` @ `main` means `necroid mod-update` works on
    bundled mods exactly like user-imported ones — the same archive download +
    version-compare loop. Bundled mods on-disk are left without a commitSha
    initially; the first `mod-update --check` resolves it.

    Skips any mod.json that already has an origin block (someone wired it up
    by hand, or this dist was built off another fork's repo). Idempotent —
    safe to re-run.
    """
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    stamped = 0
    for child in sorted(dist_mods_dir.iterdir()):
        mj_path = child / "mod.json"
        if not mj_path.is_file():
            continue
        try:
            data = json.loads(mj_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            print(f"warning: could not stamp origin on {mj_path} (unreadable)")
            continue
        if isinstance(data.get("origin"), dict) and data["origin"].get("repo"):
            continue  # respect existing origin
        # `subdir` is the path WITHIN the upstream repo where this mod's
        # mod.json lives. For bundled mods that's `mods/<dirname>`.
        data["origin"] = {
            "type": "github",
            "repo": BUNDLED_REPO,
            "ref": BUNDLED_REF,
            "subdir": f"mods/{child.name}",
            # Empty SHA on first ship — first `mod-update --check` populates it.
            "commitSha": "",
            "archiveUrl": (
                f"https://codeload.github.com/{BUNDLED_REPO}/zip/refs/heads/{BUNDLED_REF}"
            ),
            "importedAt": now,
            "upstreamVersion": str(data.get("version") or ""),
        }
        mj_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        stamped += 1
    print(f"stamped origin on {stamped} bundled mod(s) "
          f"(repo={BUNDLED_REPO}, ref={BUNDLED_REF})")


def write_archive() -> Path:
    """Zip the dist/ tree into dist-archives/necroid-<ver>-<plat>-<arch>.zip.

    Archive contents live at the root of the zip — unzip and run `./necroid`.
    """
    plat, arch = platform_tag()
    ARCHIVES.mkdir(parents=True, exist_ok=True)
    archive_name = f"necroid-v{NECROID_VERSION}-{plat}-{arch}.zip"
    archive_path = ARCHIVES / archive_name
    if archive_path.exists():
        archive_path.unlink()

    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(DIST.rglob("*")):
            if path.is_file():
                rel = path.relative_to(DIST)
                # Preserve exec bit for the necroid binary on POSIX so unzip
                # yields a runnable file without a chmod dance.
                info = zipfile.ZipInfo(str(rel).replace("\\", "/"))
                info.compress_type = zipfile.ZIP_DEFLATED
                mode = path.stat().st_mode
                info.external_attr = (mode & 0xFFFF) << 16
                with open(path, "rb") as f:
                    zf.writestr(info, f.read())
    print(f"Archive: {archive_path}")
    return archive_path


def main() -> int:
    binary = run_pyinstaller()
    if not binary.exists():
        raise SystemExit(f"expected binary missing: {binary}")
    copy_layout(binary)
    write_archive()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
