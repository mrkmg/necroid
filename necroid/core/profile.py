"""The Profile dataclass — shared workspace handle.

Necroid's heavy state (decompiled pristine, classes-original, libs, build
output, install-state caches, install-side manifests, mod-update cache,
ephemeral tmp dirs) all live inside the chosen PZ install at
`<pz_install>/necroid/`. The checkout's only local state is the pointer file
(`<repo>/data/.necroid-pointer.json`), the entered-mod record
(`<repo>/data/.mod-enter.json`), per-mod scratch trees (`<repo>/src-<mod>/`),
auto-fetched tools (`<repo>/data/tools/`), and the binary self-update cache
(`<repo>/data/.update-cache.json`).

The Profile carries both anchors:
    * `root`           — the checkout root (Python source, mods, tools, enter).
    * `pz_necroid_dir` — `<pz_workspace_source>/necroid/`. None for an
                         uninitialised checkout (no pointer written yet).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .config import ModConfig, pz_necroid_dir, read_config
from ..errors import ConfigError, NotInitialized


InstallTo = Literal["client", "server"]

# Every top-level class-file subtree PZ ships. These are all that `init` /
# `resync-pristine` mirror into `classes-original/` and decompile into
# `src-pristine/`. Kept in declaration order (zombie first — it's by far the
# largest and the most common edit target).
PZ_CLASS_SUBTREES: tuple[str, ...] = (
    "zombie", "astar", "com", "de", "fmod", "javax", "org", "se",
)

# Single fat jar shipped by PZ >=42 containing every class subtree. The
# launcher puts `./;projectzomboid.jar` on the classpath so a loose .class
# dropped under `<pz>/zombie/...` still overrides the jar entry — the install
# mechanism survives intact from 41. Only init-time seeding changes: for jar
# layout we extract entries into `classes-original/` instead of copying a
# loose tree.
PZ_FAT_JAR_NAME = "projectzomboid.jar"

# javac --release target per PZ major. PZ 41 ships JRE 17; PZ 42 ships JDK 25.
# Users must have a system JDK whose major is >= the target (PZ's bundled
# `jre64/` is runtime-only, no javac). Unknown majors fall back to 17 so
# pre-existing 41 workspaces and legacy configs keep compiling.
_JAVA_RELEASE_BY_MAJOR: dict[int, int] = {
    41: 17,
    42: 25,
}


def java_release_for_major(major: int) -> int:
    """Map a PZ major to the `javac --release N` target. Unknown majors fall
    back to 17 (the 41 target), which is safe for any legacy/unset workspace."""
    return _JAVA_RELEASE_BY_MAJOR.get(int(major), 17)


def detect_layout(content_dir: Path) -> str:
    """Decide whether a PZ install uses the fat-jar layout (>=42) or the
    loose class-tree layout (<=41). Returns `"jar"` or `"loose"`.

    The jar check wins if present — a fresh B42 install has an empty `zombie/`
    subtree (or one populated only by a previous Necroid install's leftover
    .class files), so looking at `zombie/` alone would misclassify.
    """
    if (content_dir / PZ_FAT_JAR_NAME).is_file():
        return "jar"
    return "loose"


def existing_subtrees(root: Path) -> list[str]:
    """Return the entries of PZ_CLASS_SUBTREES that exist under `root`.

    Used by commands that iterate the decompiled workspace (enter, reset,
    capture, status, test, install) so they tolerate partial workspaces —
    e.g. a user who hasn't re-run `init --force` after this change yet."""
    return [s for s in PZ_CLASS_SUBTREES if (root / s).is_dir()]


def find_root(start: Path | None = None) -> Path:
    """Walk up from `start` (default cwd) looking for workspace markers.
    Falls back to `start` if nothing matches.

    Frozen (PyInstaller) binaries anchor to the directory containing the
    executable so a user running `./necroid init` from `/opt/necroid/` does
    not walk up into `/opt/` and try to create `/opt/data`."""
    import sys
    if start is None and getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    p = (start or Path.cwd()).resolve()
    for candidate in (p, *p.parents):
        if (candidate / "data").is_dir() or (candidate / "mods").is_dir():
            return candidate
        # Source checkout: `necroid/` package dir with __init__.py — not just
        # any dir named `necroid` (the dist binary is a FILE named `necroid`
        # whose parent dir is also named `necroid`, which used to cause a
        # false match one level up).
        nec = candidate / "necroid"
        if nec.is_dir() and (nec / "__init__.py").is_file():
            return candidate
    return p


@dataclass(frozen=True)
class Profile:
    root: Path                              # checkout dir (Python, mods, tools, enter)
    pz_necroid_dir: Path | None = None      # <pz_workspace_source>/necroid/  (None pre-init)
    client_pz_install: Path | None = None
    server_pz_install: Path | None = None
    originals_override: Path | None = None
    workspace_layout: str = "loose"
    java_release: int = 17
    workspace_major: int = 0

    # --- destinations ---
    def pz_install(self, install_to: str) -> Path | None:
        return self.client_pz_install if install_to == "client" else self.server_pz_install

    def content_dir_for(self, install_to: str) -> Path:
        """Where class files are laid out inside the chosen PZ install. Client
        puts them at the install root; the dedicated server nests them under
        `<install>/java/`."""
        pz = self.pz_install(install_to)
        if pz is None:
            return Path("")
        return pz / "java" if install_to == "server" else pz

    def fat_jar_for(self, install_to: str) -> Path | None:
        """Path to `projectzomboid.jar` for the given destination, or None if
        the workspace is loose-layout (PZ <=41)."""
        if self.workspace_layout != "jar":
            return None
        content = self.content_dir_for(install_to)
        if not content or str(content) == "":
            return None
        return content / PZ_FAT_JAR_NAME

    def install_necroid_dir(self, install_to: str) -> Path | None:
        """Where the install-side manifest for `install_to` lives:
        `<pz_install>/necroid/`. Note this is the install ROOT, not the
        content dir — both client and server keep their manifest at
        `<install>/necroid/install-manifest.json` for symmetry."""
        pz = self.pz_install(install_to)
        if pz is None:
            return None
        return pz_necroid_dir(pz)

    # --- checkout-local paths ---
    @property
    def data_dir(self) -> Path:        return self.root / "data"
    @property
    def mods_dir(self) -> Path:        return self.root / "mods"
    @property
    def tools_dir(self) -> Path:       return self.data_dir / "tools"
    @property
    def vineflower_jar(self) -> Path:  return self.tools_dir / "vineflower.jar"
    @property
    def enter_file(self) -> Path:      return self.data_dir / ".mod-enter.json"
    @property
    def update_cache_binary(self) -> Path:
        """Self-update cache for the binary itself — checkout-local because
        the binary lives next to the checkout, not inside any PZ install."""
        return self.data_dir / ".update-cache.json"

    def src_for(self, mod_name: str) -> Path:
        """Per-mod editable working tree, rooted at the checkout root."""
        return self.root / f"src-{mod_name}"

    # --- workspace paths (anchored in the PZ install) ---
    def _ws_root(self) -> Path:
        if self.pz_necroid_dir is None:
            raise NotInitialized(
                "no workspace pointer — run `necroid init` to bootstrap the workspace."
            )
        return self.pz_necroid_dir

    @property
    def workspace_dir(self) -> Path:   return self._ws_root() / "workspace"
    @property
    def pristine(self) -> Path:        return self.workspace_dir / "src-pristine"
    @property
    def originals(self) -> Path:
        return self.originals_override or (self.workspace_dir / "classes-original")
    @property
    def libs(self) -> Path:                return self.workspace_dir / "libs"
    @property
    def classpath_originals(self) -> Path: return self.libs / "classpath-originals"
    @property
    def build(self) -> Path:               return self.workspace_dir / "build"
    @property
    def classes_out(self) -> Path:         return self.build / "classes"
    @property
    def stage(self) -> Path:               return self.build / "stage-src"

    def state_file(self, install_to: str) -> Path:
        return self._ws_root() / f"state-{install_to}.json"

    @property
    def update_cache_mods_file(self) -> Path:
        return self._ws_root() / "update-cache-mods.json"

    @property
    def tmp_dir(self) -> Path:
        """Workspace-side scratch dir for import + mod-update archive extraction."""
        return self._ws_root() / "tmp"


def load_profile(root: Path, cfg: ModConfig | None = None) -> Profile:
    """Load the shared Profile. Reads the pointer + workspace config; both PZ
    install paths are optional (commands that need to write re-check via
    `require_pz_install(profile, install_to)`)."""
    cfg = cfg if cfg is not None else read_config(root, required=False)
    override: Path | None = None
    if cfg.originals_dir_override:
        from .config import expand_config_path
        override = expand_config_path(cfg.originals_dir_override, root)

    layout = cfg.workspace_layout or "loose"
    release = cfg.java_release if cfg.java_release > 0 else java_release_for_major(cfg.workspace_major)

    ws_dir: Path | None = None
    if cfg.pz_install is not None:
        ws_dir = pz_necroid_dir(cfg.pz_install)

    return Profile(
        root=root,
        pz_necroid_dir=ws_dir,
        client_pz_install=cfg.client_pz_install,
        server_pz_install=cfg.server_pz_install,
        originals_override=override,
        workspace_layout=layout,
        java_release=release,
        workspace_major=int(cfg.workspace_major),
    )


def require_pz_install(profile: Profile, install_to: str) -> Path:
    """For install/uninstall/verify — raise if the given destination's PZ install
    path isn't set or doesn't exist on disk."""
    pz = profile.pz_install(install_to)
    field = "clientPzInstall" if install_to == "client" else "serverPzInstall"
    if pz is None or str(pz) == "":
        raise ConfigError(
            f"{field} not set in workspace config\n"
            f"    re-run `necroid init --from {install_to}` or edit the config."
        )
    if not pz.exists():
        raise ConfigError(f"PZ install dir does not exist: {pz}")
    return pz


def resolve_install_to(cli_to: str | None, cfg: ModConfig | None = None) -> InstallTo:
    """CLI install-destination resolution:
       1. explicit --to wins
       2. config.default_install_to
       3. 'client'
    """
    if cli_to:
        if cli_to not in ("client", "server"):
            raise ConfigError(f"invalid --to '{cli_to}' (expected 'client' or 'server')")
        return cli_to  # type: ignore[return-value]
    if cfg and cfg.default_install_to in ("client", "server"):
        return cfg.default_install_to  # type: ignore[return-value]
    return "client"


def resolve_source(cli_from: str | None, cfg: ModConfig | None = None) -> InstallTo:
    """CLI workspace-source resolution for `init` and `resync-pristine`:
       1. explicit --from wins
       2. config.workspace_source
       3. 'client' if clientPzInstall is set, else 'server' if serverPzInstall is set
       4. 'client'
    """
    if cli_from:
        if cli_from not in ("client", "server"):
            raise ConfigError(f"invalid --from '{cli_from}' (expected 'client' or 'server')")
        return cli_from  # type: ignore[return-value]
    if cfg and cfg.workspace_source in ("client", "server"):
        return cfg.workspace_source  # type: ignore[return-value]
    if cfg:
        if cfg.client_pz_install:
            return "client"
        if cfg.server_pz_install:
            return "server"
    return "client"


def autodetect_server_install(client_install: Path | None, root: Path) -> Path | None:
    """Try to guess the Project Zomboid Dedicated Server install path.
       Checks, in order:
         1. Steam-aware discovery (registry + libraryfolders.vdf, per OS).
         2. Sibling of the client install under Steam's `common/`.
         3. `<root>/pzserver`."""
    from ..pz.steam_discovery import discover_server_install
    guess = discover_server_install()
    if guess:
        return guess

    candidates: list[Path] = []
    if client_install:
        steam_common = client_install.parent
        candidates.append(steam_common / "Project Zomboid Dedicated Server")
    candidates.append(root / "pzserver")
    for c in candidates:
        if c.exists():
            return c.resolve()
    return None
