"""Build a self-contained distributable.

Produces `<repo-root>/dist/`:
    pz-java-modder(.exe)
    data/
      mods/       (copied from <repo-root>/data/mods)
      tools/      (empty placeholder; first run self-extracts vineflower)
    README.txt

Run from the `pz-java-modder/` dir:
    python packaging/build_dist.py

Requires PyInstaller. Each platform builds locally (no cross-compile).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent                # pz-java-modder/packaging
PZ_MODDER = HERE.parent                                # pz-java-modder/
REPO_ROOT = PZ_MODDER.parent                           # repo root

DIST = REPO_ROOT / "dist"
PYI_WORK = PZ_MODDER / "build"                         # PyInstaller scratch (gitignored)
PYI_DIST = PZ_MODDER / "dist"                          # PyInstaller raw output (gitignored)


def run_pyinstaller() -> Path:
    exe_name = "pz-java-modder"
    entry = PZ_MODDER / "pz_java_modder" / "__main__.py"
    tools_jar = REPO_ROOT / "data" / "tools" / "vineflower.jar"

    # --add-data spec is `src<sep>dest`: ';' on Windows, ':' elsewhere.
    sep = ";" if sys.platform == "win32" else ":"
    add_data = []
    if tools_jar.exists():
        add_data.extend(["--add-data", f"{tools_jar}{sep}data/tools"])

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", exe_name,
        "--noconfirm",
        "--clean",
        f"--workpath={PYI_WORK}",
        f"--distpath={PYI_DIST}",
        f"--specpath={PYI_WORK}",
        *add_data,
        str(entry),
    ]
    print("+", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(PZ_MODDER))
    if proc.returncode != 0:
        raise SystemExit(f"PyInstaller failed (exit {proc.returncode})")
    suffix = ".exe" if sys.platform == "win32" else ""
    return PYI_DIST / f"{exe_name}{suffix}"


def copy_layout(binary: Path) -> None:
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)

    # Binary at top
    shutil.copy2(binary, DIST / binary.name)

    # Bundled mods
    src_mods = REPO_ROOT / "data" / "mods"
    if src_mods.exists():
        shutil.copytree(src_mods, DIST / "data" / "mods")

    # Empty tools placeholder
    (DIST / "data" / "tools").mkdir(parents=True, exist_ok=True)
    (DIST / "data" / "tools" / ".gitkeep").write_text("", encoding="utf-8")

    readme = DIST / "README.txt"
    readme.write_text(
        "PZ Java Modder — distributable build\n"
        "====================================\n"
        "\n"
        "Prereqs on the target machine: Git, JDK 17+ (javac, jar), Java runtime.\n"
        "\n"
        "First-run client setup:\n"
        "    ./pz-java-modder init\n"
        "\n"
        "First-run server setup:\n"
        "    ./pz-java-modder --target server init\n"
        "\n"
        "GUI (client):   ./pz-java-modder --gui\n"
        "GUI (server):   ./pz-java-modder --gui -server\n"
        "\n"
        "Bundled mods in data/mods/. Edit data/.mod-config.json to point at\n"
        "your PZ install paths (or run `init` and it'll autodetect).\n",
        encoding="utf-8",
    )
    print(f"\nDist written to {DIST}")


def main() -> int:
    binary = run_pyinstaller()
    if not binary.exists():
        raise SystemExit(f"expected binary missing: {binary}")
    copy_layout(binary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
