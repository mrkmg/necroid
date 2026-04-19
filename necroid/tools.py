"""External-tool discovery. Resolve git/java/javac/jar via PATH; produce
actionable install hints on failure per platform."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from .errors import ToolMissing


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
