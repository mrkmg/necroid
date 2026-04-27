"""PZ version detection.

Strategy: compile a small Java probe (`necroid/java/NecroidGetPzVersion.java`)
on first use, then invoke it with `-cp <cache>:<pz_install>` — the probe uses
reflection to read `zombie.core.Core.gameVersion` (a `GameVersion` object whose
`toString()` returns `"MAJOR.MINOR[SUFFIX]"`) and `zombie.core.Core.buildVersion`
(an int), printing `"<major>.<minor>[<suffix>].<build>"` on stdout (e.g.
`"41.78.19"`).

This avoids bytecode parsing entirely: PZ's own code tells us its version. The
probe's compiled .class is cached under `data/tools/pz-version-probe/`, keyed
by the source .java's sha256 so stub updates force a recompile.

Install-path note: the client lays its class tree at the install root, while
the dedicated server nests it under `<install>/java/`. Callers should pass the
_content_ directory — `Profile.content_dir_for(install_to)` — not the raw
install root.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from ..util import logging_util as log
from ..util import procs
from ..errors import PzVersionDetectError
from ..util.hashing import file_sha256
from ..util.tools import require_java_release, resolve


_PROBE_JAVA_REL = Path("java") / "NecroidGetPzVersion.java"
_PROBE_CLASS_NAME = "NecroidGetPzVersion"
_OUT_RE = re.compile(r"^(\d+)\.(\d+)([^.\d][^.]*)?\.(\d+)$")


@dataclass(frozen=True)
class PzVersion:
    major: int
    minor: int
    suffix: str   # optional trailing non-numeric tag on GameVersion.toString() — usually ""
    patch: int    # Core.buildVersion (e.g. 19 for "41.78.19")

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}{self.suffix}.{self.patch}"

    @staticmethod
    def parse(s: str) -> "PzVersion":
        m = _OUT_RE.match(s.strip())
        if not m:
            raise PzVersionDetectError(f"unparseable PZ version string: {s!r}")
        return PzVersion(
            major=int(m.group(1)),
            minor=int(m.group(2)),
            suffix=m.group(3) or "",
            patch=int(m.group(4)),
        )


def _probe_source(necroid_pkg_dir: Path) -> Path:
    return necroid_pkg_dir / _PROBE_JAVA_REL


def _probe_cache_dir(data_dir: Path) -> Path:
    return data_dir / "tools" / "pz-version-probe"


def _probe_class_file(data_dir: Path) -> Path:
    return _probe_cache_dir(data_dir) / f"{_PROBE_CLASS_NAME}.class"


def _stamp_file(data_dir: Path) -> Path:
    return _probe_cache_dir(data_dir) / ".source-sha256"


def ensure_probe_compiled(necroid_pkg_dir: Path, data_dir: Path) -> Path:
    """Compile the probe into `data/tools/pz-version-probe/` if the cached
    .class is missing or its source hash has changed. Returns the cache dir."""
    src = _probe_source(necroid_pkg_dir)
    if not src.is_file():
        raise PzVersionDetectError(
            f"probe source missing: {src}. This is a bug in the necroid package."
        )

    cache_dir = _probe_cache_dir(data_dir)
    class_file = _probe_class_file(data_dir)
    stamp = _stamp_file(data_dir)
    src_hash = file_sha256(src) or ""

    if class_file.is_file() and stamp.is_file():
        try:
            if stamp.read_text(encoding="utf-8").strip() == src_hash:
                return cache_dir
        except OSError:
            pass  # fall through to recompile

    cache_dir.mkdir(parents=True, exist_ok=True)
    javac = str(resolve("javac"))
    log.info(f"compiling PZ-version probe -> {cache_dir}")
    cmd = [javac, "--release", "17", "-encoding", "UTF-8", "-d", str(cache_dir), str(src)]
    proc = procs.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise PzVersionDetectError(
            f"probe compile failed (exit {proc.returncode}):\n{proc.stderr.strip()}"
        )
    try:
        stamp.write_text(src_hash, encoding="utf-8")
    except OSError:
        pass  # not fatal; worst case we recompile next call
    return cache_dir


def _classfile_required_jdk(class_bytes: bytes) -> int:
    """Inspect the first 8 bytes of a `.class` file and return the minimum
    JDK major needed to load it.

    Class file format: bytes 0..3 = magic 0xCAFEBABE, bytes 4..5 = minor,
    bytes 6..7 = major (big-endian). Major = JDK + 44 (so 61 = JDK 17,
    65 = JDK 21, 69 = JDK 25). 0 on parse failure — caller falls back to
    the PATH java / lowest available.
    """
    if len(class_bytes) < 8 or class_bytes[:4] != b"\xCA\xFE\xBA\xBE":
        return 0
    classfile_major = (class_bytes[6] << 8) | class_bytes[7]
    if classfile_major < 45:
        return 0
    return classfile_major - 44


def _read_core_classfile_head(loose_core: Path | None, fat_jar: Path | None) -> bytes:
    """Read the first 8 bytes of `zombie/core/Core.class` from whichever
    source exists. Returns empty bytes on any failure (caller treats as
    "version unknown")."""
    try:
        if loose_core and loose_core.is_file():
            with open(loose_core, "rb") as fp:
                return fp.read(8)
        if fat_jar and fat_jar.is_file():
            import zipfile
            with zipfile.ZipFile(fat_jar, "r") as zf:
                with zf.open("zombie/core/Core.class", "r") as fp:
                    return fp.read(8)
    except (OSError, KeyError):
        pass
    return b""


def detect_pz_version(content_dir: Path, necroid_pkg_dir: Path, data_dir: Path) -> PzVersion:
    """Run the probe against `content_dir` (the directory containing the
    `zombie/core/Core.class` tree) and return the parsed PzVersion.

    Pass `Profile.content_dir_for(install_to)` here, not the raw install root —
    the dedicated server puts its class tree under `<install>/java/`.
    """
    if not content_dir.exists():
        raise PzVersionDetectError(f"PZ content dir does not exist: {content_dir}")

    # Two layouts: loose tree (PZ <=41, `zombie/core/Core.class` on disk) or
    # fat jar (PZ >=42, classes inside `<content>/projectzomboid.jar`). The
    # probe just needs Core on the classpath — let the filesystem tell us
    # which form to feed it.
    loose_core = content_dir / "zombie" / "core" / "Core.class"
    fat_jar = content_dir / "projectzomboid.jar"
    if loose_core.is_file():
        core_source = str(content_dir)
    elif fat_jar.is_file():
        core_source = str(fat_jar)
    else:
        raise PzVersionDetectError(
            f"neither zombie/core/Core.class nor projectzomboid.jar found under "
            f"{content_dir} — is this a Project Zomboid install?"
        )

    cache_dir = ensure_probe_compiled(necroid_pkg_dir, data_dir)

    # Pick a `java` runtime new enough to load Core.class. PZ 42 bytecode is
    # class-file v69 (needs JDK 25); the bundled pinned JDK satisfies every
    # PZ major we support. We still read Core's class-file version so a
    # future PZ that targets JDK > bundled triggers a clear ToolMissing
    # ("bump BUNDLED_JDK_RELEASE") instead of a runtime UnsupportedClassVersionError.
    head = _read_core_classfile_head(
        loose_core if loose_core.is_file() else None,
        fat_jar if fat_jar.is_file() else None,
    )
    needed_jdk = _classfile_required_jdk(head)
    if needed_jdk > 0:
        java = str(require_java_release(needed_jdk))
    else:
        java = str(resolve("java"))
    cp = os.pathsep.join([str(cache_dir), core_source])
    cmd = [java, "-cp", cp, _PROBE_CLASS_NAME]
    proc = procs.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise PzVersionDetectError(
            f"probe invocation failed (exit {proc.returncode}) against {content_dir}:\n"
            f"stderr: {proc.stderr.strip()}\n"
            f"stdout: {proc.stdout.strip()}"
        )
    out = proc.stdout.strip()
    if not out:
        raise PzVersionDetectError(
            f"probe produced no output against {content_dir}. stderr: {proc.stderr.strip()}"
        )
    return PzVersion.parse(out)
