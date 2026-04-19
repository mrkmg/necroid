"""The Profile dataclass — single switch point for client-vs-server work.

Every command takes a Profile. Every filesystem path a command needs is
derived from `root + target`; business logic never branches on target name.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .config import ModConfig, read_config
from .errors import ConfigError


Target = Literal["client", "server"]


def find_root(start: Path | None = None) -> Path:
    """Walk up from `start` (default cwd) looking for `data/` or `pz-java-modder/`.
    Falls back to `start` if nothing matches."""
    p = (start or Path.cwd()).resolve()
    for candidate in (p, *p.parents):
        if (candidate / "data").is_dir() or (candidate / "pz-java-modder").is_dir():
            return candidate
    return p


@dataclass(frozen=True)
class Profile:
    target: Target
    root: Path
    pz_install: Path            # PZ install root (what the user sees in Steam).
    data_dir: Path              # root / "data"
    originals_override: Path | None = None  # from config

    @property
    def content_dir(self) -> Path:
        """Directory that holds the top-level *.jar and class subtrees
        (zombie/, astar/, ...). For the client this is `pz_install` itself;
        for the dedicated server, it's `pz_install / "java"`."""
        return self.pz_install / "java" if self.target == "server" else self.pz_install

    # --- per-profile dirs ---
    @property
    def profile_dir(self) -> Path:   return self.data_dir / self.target
    @property
    def src(self) -> Path:           return self.profile_dir / "src"
    @property
    def pristine(self) -> Path:      return self.profile_dir / "src-pristine"
    @property
    def originals(self) -> Path:
        return self.originals_override or (self.profile_dir / "classes-original")
    @property
    def libs(self) -> Path:          return self.profile_dir / "libs"
    @property
    def classpath_originals(self) -> Path: return self.libs / "classpath-originals"
    @property
    def build(self) -> Path:         return self.profile_dir / "build"
    @property
    def classes_out(self) -> Path:   return self.build / "classes"
    @property
    def stage(self) -> Path:         return self.build / "stage-src"
    @property
    def state_file(self) -> Path:    return self.profile_dir / ".mod-state.json"
    @property
    def enter_file(self) -> Path:    return self.profile_dir / ".mod-enter.json"

    # --- shared ---
    @property
    def mods_dir(self) -> Path:      return self.data_dir / "mods"
    @property
    def tools_dir(self) -> Path:     return self.data_dir / "tools"
    @property
    def vineflower_jar(self) -> Path: return self.tools_dir / "vineflower.jar"


def load_profile(root: Path, target: Target | str, cfg: ModConfig | None = None, require_pz: bool = False) -> Profile:
    """Load a Profile. By default `pz_install` is optional — commands that
    actually need it (install, uninstall, verify, resync) re-validate via
    `require_pz_install(profile)` at point of use."""
    cfg = cfg or read_config(root)
    if target not in ("client", "server"):
        raise ConfigError(f"invalid target '{target}' (expected 'client' or 'server')")
    pz = cfg.pz_install(target)
    if pz is None and require_pz:
        field = "clientPzInstall" if target == "client" else "serverPzInstall"
        raise ConfigError(
            f"{field} not set in {cfg.path}\n"
            f"    re-run `pz-java-modder init --target {target}` or edit the config."
        )
    override_raw = cfg.originals_dir_override.get(target) if cfg.originals_dir_override else None
    override: Path | None = None
    if override_raw:
        from .config import expand_config_path
        override = expand_config_path(override_raw, root)
    return Profile(
        target=target,  # type: ignore[arg-type]
        root=root,
        pz_install=pz or Path(""),
        data_dir=root / "data",
        originals_override=override,
    )


def require_pz_install(profile: Profile) -> Path:
    """For install/uninstall/verify/resync — raise if the target's PZ install path
    isn't set or doesn't exist on disk."""
    if not profile.pz_install or str(profile.pz_install) == "":
        field = "clientPzInstall" if profile.target == "client" else "serverPzInstall"
        raise ConfigError(
            f"{field} not set in data/.mod-config.json\n"
            f"    re-run `pz-java-modder --target {profile.target} init` or edit the config."
        )
    if not profile.pz_install.exists():
        raise ConfigError(f"PZ install dir does not exist: {profile.pz_install}")
    return profile.pz_install


def resolve_target(cli_target: str | None, flag_server: bool, cfg: ModConfig | None = None) -> Target:
    """CLI target resolution:
       1. explicit --target wins
       2. --server / -server flag (GUI + CLI)
       3. config default_target
       4. 'client'
    """
    if cli_target:
        return cli_target  # type: ignore[return-value]
    if flag_server:
        return "server"
    if cfg and cfg.default_target in ("client", "server"):
        return cfg.default_target  # type: ignore[return-value]
    return "client"


def autodetect_server_install(client_install: Path | None, root: Path) -> Path | None:
    """Try to guess the Project Zomboid Dedicated Server install path.
       Checks: sibling under Steam's `common/`, then `<root>/pzserver`."""
    candidates: list[Path] = []
    if client_install:
        steam_common = client_install.parent
        candidates.append(steam_common / "Project Zomboid Dedicated Server")
    candidates.append(root / "pzserver")
    for c in candidates:
        if c.exists():
            return c.resolve()
    return None
