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
    """Named mod directory doesn't exist under mods/."""


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


class UpdateError(PzModderError):
    """Self-updater failed: network, asset missing, permission, or a malformed
    release zip. Raised by `necroid update`. Opportunistic background checks
    swallow these silently (`quiet=True` paths in `updater`)."""


class ModImportError(PzModderError):
    """`necroid import` failed: bad URL, network, no mod.json found upstream,
    PZ-major mismatch, target dir collision, or selection mismatch."""


class ModUpdateError(PzModderError):
    """`necroid mod-update` failed: missing origin, mod is currently entered,
    upstream subdir vanished, or network/parse failure."""


class InstallManifestMissing(PzModderError):
    """Local cache says the install has a stack, but the install-side manifest
    (`<pz>/.necroid-install.json`) is absent. Indicates the PZ install was
    wiped or reinstalled by Steam out from under us. Resync/verify treat this
    as a "stack cleared" signal; install refuses to proceed without confirmation."""


class InstallFingerprintMismatch(PzModderError):
    """The install-side manifest exists but was written by a different Necroid
    workspace (fingerprint does not match this workspace). Another checkout or
    clone of Necroid is managing this PZ install. Pass `--adopt-install` to
    take ownership (and implicitly invalidate the other workspace's state)."""


class InstallManifestTampered(PzModderError):
    """The install-side manifest is unreadable, malformed, or its schema is not
    supported by this Necroid version."""


class PristineDrift(PzModderError):
    """A file under `classes-original/` no longer hashes to the value recorded
    when a mod was installed — something has silently modified pristine. Uninstall
    refuses to restore from an unverified source. Requires `resync-pristine`
    (with appropriate force flag) to recover."""


class InstallVersionDrift(PzModderError):
    """The live PZ install contains files that are neither the version we wrote
    nor the vanilla we recorded at install — Steam has rewritten them with a
    different version's vanilla (integrity verify or a patch landed). Resync
    refuses to adopt this as the new pristine; `--force-version-drift` switches
    to the "trust-Steam" strategy and marks every mod as needing recapture."""


class OrphanInstalledFile(PzModderError):
    """A class file exists under a mod-touched subtree in the PZ install that
    is not in either `classes-original/` or the install-side manifest. Most
    likely cause: a prior Necroid run crashed between deploy and manifest-write,
    or the user hand-patched the install outside Necroid."""
