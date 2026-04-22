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


class ClientOnlyViolation(PzModderError):
    """Attempted to use a clientOnly mod without a configured client PZ install,
    or install such a mod to the server destination."""


class ConflictError(PzModderError):
    """Stack apply produced merge/new/patch conflicts; install aborted."""

    def __init__(self, conflicts: list):
        self.conflicts = conflicts
        lines = [f"  {c['rel']}  [{c['type']}]  mods: {', '.join(c['mods'])}" for c in conflicts]
        super().__init__("conflicts detected:\n" + "\n".join(lines))


class BuildError(PzModderError):
    """javac compile failure."""


class ModJsonError(PzModderError):
    """Malformed mod directory: bad patch file layout or inconsistent mod.json."""


class PzVersionDetectError(PzModderError):
    """Could not determine the PZ version of a given install.

    Raised when the detection probe fails to run, Core.class is missing from
    the target install, or the probe's stdout is not parseable. The cause is
    inlined in the message so the user knows which stage failed."""


class PzMajorMismatch(PzModderError):
    """Hard gate: a mod's major version (encoded in its dir name) does not
    match the workspace major, or a target PZ install's detected major does
    not match the workspace major."""


class ModDependencyMissing(PzModderError):
    """A mod declares a dependency that doesn't exist as a mod dir at the
    workspace major. Raised at enter/install/capture time, and also when
    uninstalling a mod would orphan a dependent mod that's still installed."""


class ModIncompatibility(PzModderError):
    """Two mods in the resolved stack declare each other (or one declares the
    other) as incompatible. Install / enter aborts before touching anything."""


class ModDependencyCycle(PzModderError):
    """Dependency graph has a cycle. Message includes the offending path."""
