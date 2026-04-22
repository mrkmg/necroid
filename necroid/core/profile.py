"""The Profile dataclass — shared workspace handle.

There is now a single workspace rooted at `data/workspace/`. The Profile
carries the root and the resolved client/server PZ install paths (either may
be empty). Install destination is chosen per-invocation (`install_to`) and
is not stored on the Profile.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .config import ModConfig, read_config
from ..errors import ConfigError


InstallTo = Literal["client", "server"]

# Every top-level class-file subtree PZ ships. These are all that `init` /
# `resync-pristine` mirror into `classes-original/` and decompile into
# `src-pristine/`. Kept in declaration order (zombie first — it's by far the
# largest and the most common edit target).
PZ_CLASS_SUBTREES: tuple[str, ...] = (
    "zombie", "astar", "com", "de", "fmod", "javax", "org", "se",
)


def existing_subtrees(root: Path) -> list[str]:
    """Return the entries of PZ_CLASS_SUBTREES that exist under `root`.

    Used by commands that iterate the decompiled workspace (enter, reset,
    capture, status, test, install) so they tolerate partial workspaces —
    e.g. a user who hasn't re-run `init --force` after this change yet."""
    return [s for s in PZ_CLASS_SUBTREES if (root / s).is_dir()]


def find_root(start: Path | None = None) -> Path:
    """Walk up from `start` (default cwd) looking for `data/` or `necroid/`.
    Falls back to `start` if nothing matches."""
    p = (start or Path.cwd()).resolve()
    for candidate in (p, *p.parents):
        if (candidate / "data").is_dir() or (candidate / "necroid").is_dir():
            return candidate
    return p


@dataclass(frozen=True)
class Profile:
    root: Path
    data_dir: Path
    client_pz_install: Path | None = None
    server_pz_install: Path | None = None
    originals_override: Path | None = None

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

    # --- workspace dirs (shared) ---
    @property
    def workspace_dir(self) -> Path: return self.data_dir / "workspace"
    @property
    def src(self) -> Path:
        # Legacy single-tree path. Kept so any stray caller still resolves,
        # but no command uses it any more — each mod has its own src-<name>/
        # tree at the repo root (see `src_for`). Safe to remove once external
        # callers are gone.
        return self.workspace_dir / "src"
    def src_for(self, mod_name: str) -> Path:
        """Per-mod editable working tree, rooted at the repo root.
        `enter` populates it; `capture`/`test`/`status`/`reset` read/write it."""
        return self.root / f"src-{mod_name}"
    @property
    def pristine(self) -> Path:      return self.workspace_dir / "src-pristine"
    @property
    def originals(self) -> Path:
        return self.originals_override or (self.workspace_dir / "classes-original")
    @property
    def libs(self) -> Path:          return self.workspace_dir / "libs"
    @property
    def classpath_originals(self) -> Path: return self.libs / "classpath-originals"
    @property
    def build(self) -> Path:         return self.workspace_dir / "build"
    @property
    def classes_out(self) -> Path:   return self.build / "classes"
    @property
    def stage(self) -> Path:         return self.build / "stage-src"
    @property
    def enter_file(self) -> Path:    return self.data_dir / ".mod-enter.json"

    def state_file(self, install_to: str) -> Path:
        return self.data_dir / f".mod-state-{install_to}.json"

    # --- shared ---
    @property
    def mods_dir(self) -> Path:      return self.root / "mods"
    @property
    def tools_dir(self) -> Path:     return self.data_dir / "tools"
    @property
    def vineflower_jar(self) -> Path: return self.tools_dir / "vineflower.jar"


def load_profile(root: Path, cfg: ModConfig | None = None) -> Profile:
    """Load the shared Profile. Neither PZ install is required up front — install /
    uninstall / verify re-check with `require_pz_install(profile, install_to)` when
    they actually need to write."""
    cfg = cfg or read_config(root)
    override: Path | None = None
    if cfg.originals_dir_override:
        from .config import expand_config_path
        override = expand_config_path(cfg.originals_dir_override, root)
    return Profile(
        root=root,
        data_dir=root / "data",
        client_pz_install=cfg.client_pz_install,
        server_pz_install=cfg.server_pz_install,
        originals_override=override,
    )


def require_pz_install(profile: Profile, install_to: str) -> Path:
    """For install/uninstall/verify — raise if the given destination's PZ install
    path isn't set or doesn't exist on disk."""
    pz = profile.pz_install(install_to)
    field = "clientPzInstall" if install_to == "client" else "serverPzInstall"
    if pz is None or str(pz) == "":
        raise ConfigError(
            f"{field} not set in data/.mod-config.json\n"
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
