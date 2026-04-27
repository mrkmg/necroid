"""External-tool discovery.

`java`, `javac`, and `jar` are ALWAYS resolved against a pinned bundled
Adoptium JDK under `<tools_dir>/jdk-bundled/`. PATH and the OS-level JDK
install roots (Adoptium / Microsoft / Zulu / Corretto / etc.) are NOT
consulted — Vineflower's decompile output depends on the JVM running it,
so a single pinned runtime is the only way to keep mod patches portable
across users.

`git` is still PATH-first, with auto-fetched MinGit on Windows as a
fallback. Git does not affect decompile output, so the strict pin
isn't needed there.

See `tools_fetch.BUNDLED_JDK_RELEASE` for the exact pin and the bumping
protocol.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from ..errors import ToolMissing


# Set by `cli.main()` once the workspace root is known. When set, `resolve()`
# can return cached / fetched binaries from `<tools_dir>/`. Stays None for
# callers that import this module without a workspace context (rare — the
# CLI binds it before any command runs).
_TOOLS_DIR: Path | None = None


def set_tools_dir(path: Path) -> None:
    """Bind the auto-fetch cache directory (typically `data/tools/`). Idempotent."""
    global _TOOLS_DIR
    _TOOLS_DIR = path


# Hints are only used for `git` now. `java`/`javac`/`jar` go through the
# bundled-JDK path which raises `ToolMissing` with its own pin-specific
# message (see `tools_fetch.ensure_bundled_jdk`).
_HINTS_WIN = {"git": "winget install --id Git.Git -e"}
_HINTS_MAC = {"git": "brew install git"}
_HINTS_LINUX = {"git": "sudo apt install git   (or dnf/pacman)"}


def _hint(name: str) -> str:
    if sys.platform == "win32":
        return _HINTS_WIN.get(name, "")
    if sys.platform == "darwin":
        return _HINTS_MAC.get(name, "")
    return _HINTS_LINUX.get(name, "")


_JDK_TOOLS = ("java", "javac", "jar")


def resolve(name: str) -> Path:
    """Return full path to an external tool or raise `ToolMissing`.

    `java`/`javac`/`jar` always come from the pinned bundled JDK
    (`tools_fetch.ensure_bundled_jdk`). PATH is ignored for these tools.

    `git` resolves PATH first, then the auto-fetched MinGit on Windows.
    """
    if name in _JDK_TOOLS:
        return _resolve_bundled_jdk_binary(name)

    # git path: PATH wins; on Windows fall back to auto-fetched MinGit.
    exe = shutil.which(name)
    if exe:
        return Path(exe)
    cached = _resolve_git_from_tools_dir()
    if cached:
        return cached
    fetched = _fetch_git()
    if fetched:
        return fetched
    raise ToolMissing(name, _hint(name))


def _require_tools_dir() -> Path:
    if _TOOLS_DIR is None:
        raise ToolMissing(
            "java",
            "internal error: tools dir not bound. `cli.main()` must call "
            "`tools.set_tools_dir(...)` before any command runs.",
        )
    return _TOOLS_DIR


def _resolve_bundled_jdk_binary(name: str) -> Path:
    from . import tools_fetch
    tools_dir = _require_tools_dir()
    jdk_home = tools_fetch.ensure_bundled_jdk(tools_dir)
    exe = _scan_jdk_for_binary(name, jdk_home)
    if exe is None:
        raise ToolMissing(
            name,
            f"bundled JDK at {jdk_home} does not contain `{name}` — extraction "
            f"may have been corrupted. Delete that directory and retry.",
        )
    return exe


def _resolve_git_from_tools_dir() -> Path | None:
    if _TOOLS_DIR is None:
        return None
    from . import tools_fetch
    return tools_fetch.fetched_git_exe(_TOOLS_DIR)


def _fetch_git() -> Path | None:
    if _TOOLS_DIR is None:
        return None
    from . import tools_fetch
    if sys.platform != "win32":
        return None
    return tools_fetch.ensure_portable_git(_TOOLS_DIR)


def _scan_jdk_for_binary(name: str, jdk_home: Path) -> Path | None:
    """Find `<name>` inside a JDK install dir.

    Adoptium archives extract to either `<jdk_home>/jdk-X.Y.Z+B/bin/<exe>`
    (Windows/Linux) or `<jdk_home>/jdk-X.Y.Z+B/Contents/Home/bin/<exe>`
    (macOS). Older shapes drop directly into `<jdk_home>/bin/<exe>` — we
    handle that too for robustness.
    """
    exe_name = f"{name}.exe" if sys.platform == "win32" else name
    if not jdk_home.is_dir():
        return None
    direct = jdk_home / "bin" / exe_name
    if direct.is_file():
        return direct
    for entry in jdk_home.iterdir():
        if not entry.is_dir():
            continue
        for rel in (Path("bin") / exe_name, Path("Contents") / "Home" / "bin" / exe_name):
            cand = entry / rel
            if cand.is_file():
                return cand
    return None


def check_all(names: list[str]) -> dict[str, Path]:
    """Resolve a list of tools, raising on the first missing."""
    return {n: resolve(n) for n in names}


_JAVAC_VER_RE = re.compile(r"javac\s+(\d+)")
_JAVA_VER_RE = re.compile(r'version\s+"(\d+)')


def _binary_major_version(exe: str, regex: re.Pattern[str], tool_name: str) -> int:
    try:
        proc = subprocess.run([exe, "-version"], capture_output=True, text=True, check=False)
    except OSError as e:
        raise ToolMissing(tool_name, f"{_hint(tool_name)} (exec failed: {e})")
    out = (proc.stderr or "") + "\n" + (proc.stdout or "")
    m = regex.search(out)
    if not m:
        return 0
    return int(m.group(1))


def javac_major_version(javac: Path | str | None = None) -> int:
    """Return the major version of `javac` (e.g. 17, 21, 25). Raises
    `ToolMissing` when javac is absent. Returns 0 when the version string
    can't be parsed."""
    exe = str(javac) if javac else str(resolve("javac"))
    return _binary_major_version(exe, _JAVAC_VER_RE, "javac")


def java_major_version(java: Path | str | None = None) -> int:
    """Return the major version of a `java` runtime (e.g. 17, 21, 25)."""
    exe = str(java) if java else str(resolve("java"))
    return _binary_major_version(exe, _JAVA_VER_RE, "java")


def require_javac_release(target_release: int, *, hint_major: int | None = None) -> Path:
    """Return the bundled javac. Asserts the bundled JDK is new enough for
    `target_release` (always true for supported PZ majors — see
    `tools_fetch.BUNDLED_JDK_MAJOR`).

    The `hint_major` parameter is preserved for call-site compatibility but
    no longer used: the bundled JDK pin makes the version-gating moot."""
    _ = hint_major
    return _require_jdk_release("javac", int(target_release))


def require_java_release(target_release: int, *, extra_roots: tuple[Path, ...] = ()) -> Path:
    """Return the bundled java runtime. `extra_roots` is preserved for
    call-site compatibility (e.g. pzversion passes PZ's bundled `jre64/`)
    but is ignored — the pinned JDK 25 runtime can load any class-file
    version PZ has shipped."""
    _ = extra_roots
    return _require_jdk_release("java", int(target_release))


def _require_jdk_release(name: str, target_release: int) -> Path:
    from . import tools_fetch
    if target_release > tools_fetch.BUNDLED_JDK_MAJOR:
        raise ToolMissing(
            name,
            f"requested JDK {target_release}+, but bundled pin is "
            f"{tools_fetch.BUNDLED_JDK_RELEASE} (major {tools_fetch.BUNDLED_JDK_MAJOR}). "
            f"Bump `BUNDLED_JDK_RELEASE` in `necroid/util/tools_fetch.py`.",
        )
    return resolve(name)
