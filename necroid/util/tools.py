"""External-tool discovery. Resolve git/java/javac/jar via PATH; produce
actionable install hints on failure per platform."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from ..errors import ToolMissing


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
    """Return full path to an external tool or raise ToolMissing with install hint."""
    exe = shutil.which(name)
    if not exe:
        raise ToolMissing(name, _hint(name))
    return Path(exe)


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
    best: tuple[int, Path] | None = None
    for cand in candidates:
        try:
            ver = version_fn(cand)
        except ToolMissing:
            continue
        if ver and ver >= target and (best is None or ver < best[0]):
            best = (ver, cand)

    if best is not None:
        from . import logging_util as log
        log.info(f"using JDK {best[0]} {name} at {best[1]} (PATH {name} is {have or '?'})")
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

    # Extra roots: either a parent of multiple JDKs, or a JDK home itself.
    for root in extra_roots:
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
