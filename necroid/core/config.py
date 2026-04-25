"""`.mod-config.json` read/write + path expansion.

Schema (v1):
    {
      "version": 1,
      "clientPzInstall": "C:/Program Files (x86)/Steam/steamapps/common/ProjectZomboid",
      "serverPzInstall": "...Project Zomboid Dedicated Server",
      "defaultInstallTo": "client",
      "workspaceSource": "client",
      "workspaceMajor": 41,
      "workspaceVersion": "41.78.19",
      "workspaceLayout": "loose",
      "javaRelease": 17,
      "originalsDir": "workspace/classes-original"
    }

Lives at `<root>/data/.mod-config.json`. `defaultInstallTo`, `workspaceSource`,
`workspaceLayout`, `javaRelease`, and `originalsDir` are optional.

`workspaceLayout` records how the source PZ install stores Java classes.
`"loose"` (PZ build 41 and earlier) = a tree of `.class` files under
`<pz>/zombie/...`. `"jar"` (PZ build 42+) = a single fat `projectzomboid.jar`
at the install root. Detected at `init` time from install layout; determines
whether `classes-original/` is populated by mirror-copy (loose) or jar-extract
(jar), whether `libs/classpath-originals/` is rebuilt (loose) or skipped in
favor of using `projectzomboid.jar` directly (jar), and how `uninstall`
restores overwrites (copy from `classes-original/` for loose; delete the
loose override so the JVM falls back to the jar entry for jar — the PZ
launcher's classpath is `./;projectzomboid.jar` so loose trumps jar).
Legacy configs without this field default to `"loose"` on read.

`javaRelease` is the `javac --release N` target for mod sources. PZ 41
ships JRE 17; PZ 42 ships JDK 25 runtime and requires `--release 25` (the
user must have a system JDK 25+ — PZ's bundled `jre64/` has no javac).
Derived from `workspaceMajor` at `init` via the table in profile.py.
Legacy configs fall back to the same lookup on read.

`workspaceSource` records which PZ install seeded `data/workspace/`. Only matters
for `resync-pristine` (re-hydrates from the same source unless `--from` overrides).

`workspaceMajor` is the PZ major version the workspace is bound to (e.g. 41).
Mod dirs under `mods/` are suffixed with `-<major>` and filtered against
this value in every surface (list, status, install, GUI). Set at `init` time
from `workspaceVersion`. Changing it requires `resync-pristine --force-major-change`.

`workspaceVersion` is the full detected PZ version string (`PzVersion.__str__`,
e.g. `"41.78.19"`) at the time the workspace was seeded. Used for minor/patch
drift warnings at install time; never a hard gate.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import ConfigError

CONFIG_VERSION = 1

_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def expand_config_path(raw: str | None, root: Path) -> Path | None:
    """Resolve env vars (`$VAR`, `%VAR%`, `${VAR}`), `~`, relative-to-root. Returns
    absolute `Path` or None if `raw` is empty."""
    if not raw:
        return None
    s = os.path.expandvars(raw)  # handles $VAR and %VAR%
    s = _VAR_RE.sub(lambda m: os.environ.get(m.group(1), ""), s)
    s = os.path.expanduser(s)
    p = Path(s)
    if not p.is_absolute():
        p = root / p
    return p.resolve()


@dataclass
class ModConfig:
    version: int = CONFIG_VERSION
    client_pz_install: Path | None = None
    server_pz_install: Path | None = None
    default_install_to: str = "client"
    workspace_source: str = "client"
    workspace_major: int = 0
    workspace_version: str = ""
    workspace_fingerprint: str = ""  # opaque id; stamped into install-side manifest
    workspace_layout: str = ""       # "loose" (PZ <=41) or "jar" (PZ >=42); derived at init
    java_release: int = 0            # javac --release target; 0 = derive from workspace_major
    originals_dir_override: str = ""
    _raw: dict = field(default_factory=dict, repr=False)
    _path: Path | None = field(default=None, repr=False)

    @property
    def path(self) -> Path | None:
        return self._path

    def pz_install(self, install_to: str) -> Path | None:
        return self.client_pz_install if install_to == "client" else self.server_pz_install


def config_path(root: Path) -> Path:
    return root / "data" / ".mod-config.json"


def read_config(root: Path, required: bool = True) -> ModConfig:
    path = config_path(root)
    if not path.exists():
        if required:
            raise ConfigError(
                f"no config at {path}\n"
                f"    run `necroid init` to create it, or write one manually."
            )
        return ModConfig(_path=path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"malformed {path}: {e}")

    ver = raw.get("version", 1)
    if ver != CONFIG_VERSION:
        raise ConfigError(
            f"{path} is schema v{ver}; this tool requires v{CONFIG_VERSION}.\n"
            f"    re-run `necroid init` to rebuild it."
        )

    originals_raw = raw.get("originalsDir", "")
    if isinstance(originals_raw, dict):
        # Never valid in v3. Explicitly reject so users notice the schema shift.
        raise ConfigError(
            f"{path}: originalsDir must be a string in v{CONFIG_VERSION} (got object).\n"
            f"    re-run `necroid init` to regenerate."
        )

    try:
        workspace_major = int(raw.get("workspaceMajor", 0) or 0)
    except (TypeError, ValueError):
        raise ConfigError(f"{path}: workspaceMajor must be an integer")

    layout = str(raw.get("workspaceLayout", "") or "")
    if layout and layout not in ("loose", "jar"):
        raise ConfigError(f"{path}: workspaceLayout must be 'loose' or 'jar' (got {layout!r})")
    # Legacy configs (pre-B42) have no workspaceLayout field — default to "loose"
    # since every pre-B42 PZ install ships a loose class tree.
    if not layout:
        layout = "loose"

    try:
        java_release = int(raw.get("javaRelease", 0) or 0)
    except (TypeError, ValueError):
        raise ConfigError(f"{path}: javaRelease must be an integer")
    # Fall back to the major->release table if the legacy config didn't stamp one.
    if java_release <= 0:
        # Import is local to avoid a cycle: profile -> config -> profile.
        from .profile import java_release_for_major
        java_release = java_release_for_major(workspace_major)

    cfg = ModConfig(
        version=ver,
        client_pz_install=expand_config_path(raw.get("clientPzInstall"), root),
        server_pz_install=expand_config_path(raw.get("serverPzInstall"), root),
        default_install_to=raw.get("defaultInstallTo", "client"),
        workspace_source=raw.get("workspaceSource", "client"),
        workspace_major=workspace_major,
        workspace_version=str(raw.get("workspaceVersion", "") or ""),
        workspace_fingerprint=str(raw.get("workspaceFingerprint", "") or ""),
        workspace_layout=layout,
        java_release=java_release,
        originals_dir_override=str(originals_raw or ""),
        _raw=raw,
        _path=path,
    )
    return cfg


def write_config(root: Path, cfg: ModConfig) -> Path:
    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    obj: dict = {
        "version": CONFIG_VERSION,
        "clientPzInstall": str(cfg.client_pz_install).replace("\\", "/") if cfg.client_pz_install else "",
        "serverPzInstall": str(cfg.server_pz_install).replace("\\", "/") if cfg.server_pz_install else "",
        "defaultInstallTo": cfg.default_install_to,
        "workspaceSource": cfg.workspace_source,
        "workspaceMajor": int(cfg.workspace_major),
        "workspaceVersion": cfg.workspace_version,
        "workspaceFingerprint": cfg.workspace_fingerprint,
        "workspaceLayout": cfg.workspace_layout or "loose",
        "javaRelease": int(cfg.java_release) if cfg.java_release > 0 else 0,
    }
    if cfg.originals_dir_override:
        obj["originalsDir"] = cfg.originals_dir_override
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    cfg._path = path
    return path
