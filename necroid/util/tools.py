"""External-tool discovery. Resolve git/java/javac/jar via PATH; produce
actionable install hints on failure per platform."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from ..errors import ToolMissing


# Set by `cli.main()` (and `init`) once the workspace root is known. When set,
# `resolve()` and `_find_jdk_binary()` will consult `<tools_dir>/jdk-*/` and
# `<tools_dir>/git/` as fallbacks, and fetch from upstream on first miss.
# Stays None for callers that import this module without a workspace context;
# behavior degrades back to the pre-auto-fetch path.
_TOOLS_DIR: Path | None = None


def set_tools_dir(path: Path) -> None:
    """Bind the auto-fetch cache directory (typically `data/tools/`). Idempotent."""
    global _TOOLS_DIR
    _TOOLS_DIR = path


_HINTS_WIN = {
    "git":   "winget install --id Git.Git -e",
    "java":  "winget install --id EclipseAdoptium.Temurin.17.JDK",
    "javac": "winget install --id EclipseAdoptium.Temurin.17.JDK",
    "jar":   "winget install --id EclipseAdoptium.Temurin.17.JDK",
}
_HINTS_MAC = {
    "git":   "brew install git",
    "java":  "brew install --cask temurin@17",
    "javac": "brew install --cask temurin@17",
    "jar":   "brew install --cask temurin@17",
}
_HINTS_LINUX = {
    "git":   "sudo apt install git   (or dnf/pacman)",
    "java":  "sudo apt install openjdk-17-jdk",
    "javac": "sudo apt install openjdk-17-jdk",
    "jar":   "sudo apt install openjdk-17-jdk",
}


def _hint(name: str) -> str:
    if sys.platform == "win32":
        return _HINTS_WIN.get(name, "")
    if sys.platform == "darwin":
        return _HINTS_MAC.get(name, "")
    return _HINTS_LINUX.get(name, "")


def resolve(name: str) -> Path:
    """Return full path to an external tool or raise ToolMissing with install hint.

    PATH wins. If PATH has nothing and `set_tools_dir()` has been called, fall
    back to the auto-fetched copy under `<tools_dir>/`:
      - `git` (Windows only): downloaded MinGit at `<tools_dir>/git/cmd/git.exe`.
      - `java` / `javac` / `jar`: scan `<tools_dir>/jdk-*/` for any extracted JDK.
    On a fresh install the fetch is performed lazily; subsequent calls hit the
    on-disk cache. macOS/Linux have no portable git distribution — `resolve("git")`
    falls through to `ToolMissing` with the usual hint.
    """
    exe = shutil.which(name)
    if exe:
        return Path(exe)

    cached = _resolve_from_tools_dir(name)
    if cached:
        return cached
    fetched = _fetch_and_resolve(name)
    if fetched:
        return fetched
    raise ToolMissing(name, _hint(name))


def _resolve_from_tools_dir(name: str) -> Path | None:
    if _TOOLS_DIR is None:
        return None
    from . import tools_fetch
    if name == "git":
        return tools_fetch.fetched_git_exe(_TOOLS_DIR)
    if name in ("java", "javac", "jar"):
        return _scan_jdk_for_binary(name, tools_fetch.fetched_jdk_roots(_TOOLS_DIR))
    return None


def _fetch_and_resolve(name: str) -> Path | None:
    if _TOOLS_DIR is None:
        return None
    from . import tools_fetch
    if name == "git" and sys.platform == "win32":
        return tools_fetch.ensure_portable_git(_TOOLS_DIR)
    if name in ("java", "javac", "jar"):
        # Plain (non-version-gated) callers — e.g. `init` step 2's check_all,
        # or `resolve("jar")` from `_copy_pz_jars`. We don't know the caller's
        # target release here; fetch the highest PZ-major release we know
        # about (25), which satisfies every supported workspace. The
        # version-gated path picks the lowest qualifying JDK on disk anyway,
        # so this download is reused rather than duplicated later.
        from ..core.profile import _JAVA_RELEASE_BY_MAJOR  # type: ignore[attr-defined]
        target_major = max(_JAVA_RELEASE_BY_MAJOR.values())
        root = tools_fetch.ensure_portable_jdk(_TOOLS_DIR, target_major)
        if root is None:
            return None
        return _scan_jdk_for_binary(name, (root,))
    return None


def _scan_jdk_for_binary(name: str, jdk_roots: tuple[Path, ...]) -> Path | None:
    """Find `<name>` inside any of the given fetched-JDK parent dirs. Walks one
    level (Adoptium archives extract to e.g. `jdk-25.0.1+9/bin/<name>`)."""
    exe_name = f"{name}.exe" if sys.platform == "win32" else name
    for root in jdk_roots:
        if not root.is_dir():
            continue
        direct = root / "bin" / exe_name
        if direct.is_file():
            return direct
        for entry in root.iterdir():
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
    # Both `java -version` and `javac -version` use stderr on older JDKs and
    # stdout on newer — check both.
    out = (proc.stderr or "") + "\n" + (proc.stdout or "")
    m = regex.search(out)
    if not m:
        return 0
    return int(m.group(1))


def javac_major_version(javac: Path | str | None = None) -> int:
    """Return the major version of `javac` (e.g. 17, 21, 25). Raises
    `ToolMissing` when javac is absent. Returns 0 when the version string
    can't be parsed — callers decide how strict to be."""
    exe = str(javac) if javac else str(resolve("javac"))
    return _binary_major_version(exe, _JAVAC_VER_RE, "javac")


def java_major_version(java: Path | str | None = None) -> int:
    """Return the major version of a `java` runtime (e.g. 17, 21, 25)."""
    exe = str(java) if java else str(resolve("java"))
    return _binary_major_version(exe, _JAVA_VER_RE, "java")


def _find_jdk_binary(
    name: str,
    *,
    target_major: int,
    version_fn,
    extra_roots: tuple[Path, ...] = (),
) -> Path:
    """Resolve a JDK binary (`java`, `javac`, ...) whose major >= target_major.

    1. Try PATH first. If new enough, use it.
    2. Otherwise scan well-known JDK install roots (plus any `extra_roots`
       supplied by the caller — used to add e.g. PZ's bundled `jre64/`).
    3. Pick the *lowest* qualifying major: closer to what PZ ships, less
       drift from new language/runtime features.
    4. Raise ToolMissing if nothing usable is found.

    Returns the absolute path. Callers should pass that path to subprocess
    invocations rather than relying on PATH — a usable JDK on disk shouldn't
    be defeated by a stale shell.
    """
    target = int(target_major)
    exe = resolve(name)
    have = 0
    try:
        have = version_fn(exe)
    except ToolMissing:
        pass
    if have and have >= target:
        return exe

    candidates = _discover_jdk_binaries(name, extra_roots=extra_roots)
    best = _pick_lowest_qualifying(candidates, version_fn, target)
    if best is not None:
        from . import logging_util as log
        log.info(f"using JDK {best[0]} {name} at {best[1]} (PATH {name} is {have or '?'})")
        return best[1]

    # Last resort: download a portable Temurin JDK matching the target.
    if _TOOLS_DIR is not None:
        from . import tools_fetch
        fetched_root = tools_fetch.ensure_portable_jdk(_TOOLS_DIR, target)
        if fetched_root is not None:
            candidates = _discover_jdk_binaries(name, extra_roots=(fetched_root,))
            best = _pick_lowest_qualifying(candidates, version_fn, target)
            if best is not None:
                from . import logging_util as log
                log.info(f"using portable JDK {best[0]} {name} at {best[1]}")
                return best[1]

    extra = f" PATH {name} is {have}; " if have else " "
    if candidates:
        seen = ", ".join(sorted({str(p.parent.parent) for p in candidates}))
        extra += f"scanned: {seen}."
    raise ToolMissing(
        name,
        f"need JDK {target}+ for `{name}` (none found).{extra} "
        f"{_hint_for_release(target)}",
    )


def require_javac_release(target_release: int, *, hint_major: int | None = None) -> Path:
    """Resolve javac whose major >= `target_release`. See `_find_jdk_binary`."""
    return _find_jdk_binary(
        "javac",
        target_major=int(target_release),
        version_fn=javac_major_version,
    )


def require_java_release(target_release: int, *, extra_roots: tuple[Path, ...] = ()) -> Path:
    """Resolve a `java` runtime whose major >= `target_release`. See `_find_jdk_binary`.

    `extra_roots` lets callers add candidate JDK roots beyond the standard
    OS-level scan — pzversion uses this to include `<pz>/jre64/` so the
    JRE PZ ships with itself is always considered (it's guaranteed to match
    the install's bytecode version)."""
    return _find_jdk_binary(
        "java",
        target_major=int(target_release),
        version_fn=java_major_version,
        extra_roots=extra_roots,
    )


def _pick_lowest_qualifying(
    candidates: list[Path],
    version_fn,
    target: int,
) -> tuple[int, Path] | None:
    best: tuple[int, Path] | None = None
    for cand in candidates:
        try:
            ver = version_fn(cand)
        except ToolMissing:
            continue
        if ver and ver >= target and (best is None or ver < best[0]):
            best = (ver, cand)
    return best


def _discover_jdk_binaries(
    name: str,
    *,
    extra_roots: tuple[Path, ...] = (),
) -> list[Path]:
    """Return candidate `<name>` executables (e.g. `javac`, `java`) found in
    well-known JDK install roots, plus any `extra_roots` the caller supplies.
    Order is irrelevant — the caller picks by version. Empty list if nothing
    matches.

    `extra_roots` are treated two ways: as either a JDK install root (we look
    for `<root>/<entry>/bin/<exe>`) or as a single JDK home (we look for
    `<root>/bin/<exe>`). PZ's bundled `<pz>/jre64/` is the latter shape.
    """
    exe_name = f"{name}.exe" if sys.platform == "win32" else name
    roots: list[Path] = []
    if sys.platform == "win32":
        roots += [
            Path("C:/Program Files/Eclipse Adoptium"),
            Path("C:/Program Files/Java"),
            Path("C:/Program Files/Microsoft"),
            Path("C:/Program Files/Zulu"),
            Path("C:/Program Files/Amazon Corretto"),
            Path("C:/Program Files/BellSoft"),
            Path("C:/Program Files/Semeru"),
        ]
    elif sys.platform == "darwin":
        roots += [Path("/Library/Java/JavaVirtualMachines")]
    else:
        roots += [Path("/usr/lib/jvm"), Path("/usr/java"), Path("/opt")]

    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            if not entry.is_dir():
                continue
            # macOS JDKs nest under `Contents/Home/`; everywhere else `bin/`
            # is one level down.
            for rel in (Path("bin") / exe_name, Path("Contents") / "Home" / "bin" / exe_name):
                cand = entry / rel
                if cand.is_file():
                    found.append(cand)
                    break

    # Auto-fetch cache: any previously-downloaded `tools_dir/jdk-*/` parents.
    auto_roots: tuple[Path, ...] = ()
    if _TOOLS_DIR is not None:
        from . import tools_fetch
        auto_roots = tools_fetch.fetched_jdk_roots(_TOOLS_DIR)

    # Extra roots: either a parent of multiple JDKs, or a JDK home itself.
    for root in (*extra_roots, *auto_roots):
        if not root.is_dir():
            continue
        # Direct JDK home? <root>/bin/<exe>
        direct = root / "bin" / exe_name
        if direct.is_file():
            found.append(direct)
            continue
        # Otherwise walk one level down looking for JDK homes.
        for entry in root.iterdir():
            if not entry.is_dir():
                continue
            for rel in (Path("bin") / exe_name, Path("Contents") / "Home" / "bin" / exe_name):
                cand = entry / rel
                if cand.is_file():
                    found.append(cand)
                    break
    return found


def _hint_for_release(major: int) -> str:
    """Install-hint override that substitutes the right JDK major into the
    stock per-OS hint. Falls back to the base hint when no major-specific
    package is known."""
    if sys.platform == "win32":
        return f"winget install --id EclipseAdoptium.Temurin.{major}.JDK"
    if sys.platform == "darwin":
        return f"brew install --cask temurin@{major}"
    # Linux package names vary by distro; point at the family rather than
    # pretending to know the exact package.
    return f"install a JDK {major}+ (e.g. openjdk-{major}-jdk on Debian/Ubuntu)"
