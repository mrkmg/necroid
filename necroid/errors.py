"""Typed exceptions. Keep messages user-facing (CLI and GUI both surface them)."""


class PzModderError(Exception):
    """Base for everything the CLI catches + pretty-prints."""


class ConfigError(PzModderError):
    """Missing / malformed / incomplete .mod-config.json."""


class NotInitialized(PzModderError):
    """Profile directory is missing or bootstrap hasn't been run."""


class ToolMissing(PzModderError):
    """Required external tool (git/java/javac/jar) not on PATH."""

    def __init__(self, name: str, hint: str = ""):
        super().__init__(f"{name} not found on PATH" + (f"\n    {hint}" if hint else ""))
        self.name = name
        self.hint = hint


class ModNotFound(PzModderError):
    """Named mod directory doesn't exist under data/mods/."""


class ModAlreadyExists(PzModderError):
    """Attempted to create a mod whose directory already exists."""


class TargetMismatch(PzModderError):
    """Named mod's target doesn't match active profile."""


class ConflictError(PzModderError):
    """Stack apply produced merge/new/patch conflicts; install aborted."""

    def __init__(self, conflicts: list):
        self.conflicts = conflicts
        lines = [f"  {c['rel']}  [{c['type']}]  mods: {', '.join(c['mods'])}" for c in conflicts]
        super().__init__("conflicts detected:\n" + "\n".join(lines))


class BuildError(PzModderError):
    """javac compile failure."""
