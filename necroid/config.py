"""`.mod-config.json` read/write + path expansion.

Schema (v3):
    {
      "version": 3,
      "clientPzInstall": "C:/Program Files (x86)/Steam/steamapps/common/ProjectZomboid",
      "serverPzInstall": "...Project Zomboid Dedicated Server",
      "defaultInstallTo": "client",
      "workspaceSource": "client",
      "originalsDir": "workspace/classes-original"
    }

Lives at `<root>/data/.mod-config.json`. `defaultInstallTo`, `workspaceSource`,
and `originalsDir` are optional.

`workspaceSource` records which PZ install seeded `data/workspace/`. Only matters
for `resync-pristine` (re-hydrates from the same source unless `--from` overrides).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigError

CONFIG_VERSION = 3

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
            f"    re-run `necroid init` (this tool migrated to a single shared workspace)."
        )

    originals_raw = raw.get("originalsDir", "")
    if isinstance(originals_raw, dict):
        # Never valid in v3. Explicitly reject so users notice the schema shift.
        raise ConfigError(
            f"{path}: originalsDir must be a string in v{CONFIG_VERSION} (got object).\n"
            f"    re-run `necroid init` to regenerate."
        )

    cfg = ModConfig(
        version=ver,
        client_pz_install=expand_config_path(raw.get("clientPzInstall"), root),
        server_pz_install=expand_config_path(raw.get("serverPzInstall"), root),
        default_install_to=raw.get("defaultInstallTo", "client"),
        workspace_source=raw.get("workspaceSource", "client"),
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
    }
    if cfg.originals_dir_override:
        obj["originalsDir"] = cfg.originals_dir_override
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    cfg._path = path
    return path
