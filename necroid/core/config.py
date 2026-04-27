"""Workspace config (lives inside the PZ install) + checkout pointer.

Necroid's state lives in `<pz_install>/necroid/`. The checkout's only link
to that workspace is a tiny pointer file at `<repo>/data/.necroid-pointer.json`
that records which PZ install holds the workspace. Multiple checkouts of
necroid pointing at the same PZ install share one workspace.

Pointer schema (v1, checkout-local — `data/.necroid-pointer.json`):
    {
      "version": 1,
      "pzInstall": "C:/Program Files (x86)/Steam/steamapps/common/ProjectZomboid"
    }

Workspace config schema (v1 — `<pz_install>/necroid/config.json`):
    {
      "version": 1,
      "clientPzInstall": "...",
      "serverPzInstall": "...",
      "defaultInstallTo": "client",
      "workspaceSource": "client",
      "workspaceMajor": 41,
      "workspaceVersion": "41.78.19",
      "workspaceLayout": "loose" | "jar",
      "javaRelease": 17,
      "originalsDir": ""
    }

Both `clientPzInstall` and `serverPzInstall` live in the workspace config so a
checkout that anchors at *either* install (via the pointer) can find the peer.

`workspaceSource` records which PZ install seeded the workspace — i.e. which
install dir holds `<pz>/necroid/workspace/`.

`workspaceLayout` records how the source PZ install stores Java classes.
`"loose"` (PZ build 41 and earlier) = a tree of `.class` files under
`<pz>/zombie/...`. `"jar"` (PZ build 42+) = a single fat `projectzomboid.jar`
at the install root. Detected at `init` time.

`javaRelease` is the `javac --release N` target. Derived from `workspaceMajor`.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import ConfigError, NotInitialized

CONFIG_VERSION = 1
POINTER_VERSION = 1

# Filenames live inside the PZ install's `necroid/` subdir.
POINTER_FILENAME = ".necroid-pointer.json"
WORKSPACE_DIRNAME = "necroid"
WORKSPACE_CONFIG_FILENAME = "config.json"

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


# ---------------------------------------------------------------------------
# pointer file (checkout-local)
# ---------------------------------------------------------------------------

def pointer_path(root: Path) -> Path:
    return root / "data" / POINTER_FILENAME


def read_pointer(root: Path) -> Path | None:
    """Return the PZ install path this checkout is anchored at, or None if no
    pointer file exists. Raises ConfigError if the pointer is malformed."""
    p = pointer_path(root)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ConfigError(f"malformed {p}: {e}")
    pz_raw = raw.get("pzInstall")
    if not pz_raw:
        raise ConfigError(f"{p}: pzInstall is empty")
    pz = expand_config_path(pz_raw, root)
    if pz is None:
        raise ConfigError(f"{p}: could not resolve pzInstall='{pz_raw}'")
    return pz


def write_pointer(root: Path, pz_install: Path) -> Path:
    p = pointer_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    obj = {
        "version": POINTER_VERSION,
        "pzInstall": str(pz_install).replace("\\", "/"),
    }
    p.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# workspace config (lives inside <pz>/necroid/)
# ---------------------------------------------------------------------------

def pz_necroid_dir(pz_install: Path) -> Path:
    """Where Necroid stores workspace state inside a PZ install."""
    return pz_install / WORKSPACE_DIRNAME


def workspace_config_path(pz_install: Path) -> Path:
    return pz_necroid_dir(pz_install) / WORKSPACE_CONFIG_FILENAME


# Legacy paths — used only to detect old-layout workspaces and refuse with a
# clear message. Never read for actual config data.
def legacy_config_path(root: Path) -> Path:
    return root / "data" / ".mod-config.json"


def legacy_workspace_dir(root: Path) -> Path:
    return root / "data" / "workspace"


@dataclass
class ModConfig:
    version: int = CONFIG_VERSION
    client_pz_install: Path | None = None
    server_pz_install: Path | None = None
    default_install_to: str = "client"
    workspace_source: str = "client"
    workspace_major: int = 0
    workspace_version: str = ""
    workspace_layout: str = ""       # "loose" (PZ <=41) or "jar" (PZ >=42)
    java_release: int = 0            # javac --release target; 0 = derive from workspace_major
    originals_dir_override: str = ""
    pz_install: Path | None = field(default=None, repr=False)   # the workspace anchor (from pointer)
    _raw: dict = field(default_factory=dict, repr=False)
    _path: Path | None = field(default=None, repr=False)

    @property
    def path(self) -> Path | None:
        return self._path

    def install_path(self, install_to: str) -> Path | None:
        return self.client_pz_install if install_to == "client" else self.server_pz_install


# config_path is kept for back-compat with callers that print the config
# location. Resolves to the workspace config under the pointer's PZ install,
# or the legacy path if no pointer exists yet (e.g. during init).
def config_path(root: Path) -> Path:
    pz = read_pointer(root)
    if pz is None:
        return legacy_config_path(root)
    return workspace_config_path(pz)


def assert_no_legacy_layout(root: Path) -> None:
    """Fail loudly if the checkout still carries old (pre-PZ-anchored) state.
    Workspace + config + state files now live under `<pz>/necroid/`."""
    legacy_files = [
        legacy_config_path(root),
        root / "data" / ".mod-state-client.json",
        root / "data" / ".mod-state-server.json",
        root / "data" / ".update-cache-mods.json",
    ]
    legacy_dirs = [legacy_workspace_dir(root)]
    found_files = [p for p in legacy_files if p.exists()]
    found_dirs = [p for p in legacy_dirs if p.exists()]
    if not found_files and not found_dirs:
        return
    items = "\n".join(f"    - {p}" for p in (found_dirs + found_files))
    raise ConfigError(
        "legacy Necroid workspace layout detected. The workspace + config + "
        "state files now live inside the PZ install at `<pz>/necroid/`.\n"
        "    Found:\n"
        f"{items}\n"
        "    If you have in-progress edits in any `src-<mod>/` tree, run "
        "`necroid capture <mod>` first to save them. Then delete the legacy "
        "paths above and re-run `necroid init` to bootstrap the new layout."
    )


def _parse_workspace_config(path: Path, raw: dict, ws_root: Path) -> ModConfig:
    ver = raw.get("version", 1)
    if ver != CONFIG_VERSION:
        raise ConfigError(
            f"{path} is schema v{ver}; this tool requires v{CONFIG_VERSION}.\n"
            f"    re-run `necroid init` to rebuild it."
        )

    originals_raw = raw.get("originalsDir", "")
    if isinstance(originals_raw, dict):
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
    if not layout:
        layout = "loose"

    try:
        java_release = int(raw.get("javaRelease", 0) or 0)
    except (TypeError, ValueError):
        raise ConfigError(f"{path}: javaRelease must be an integer")
    if java_release <= 0:
        from .profile import java_release_for_major
        java_release = java_release_for_major(workspace_major)

    return ModConfig(
        version=ver,
        client_pz_install=expand_config_path(raw.get("clientPzInstall"), ws_root),
        server_pz_install=expand_config_path(raw.get("serverPzInstall"), ws_root),
        default_install_to=raw.get("defaultInstallTo", "client"),
        workspace_source=raw.get("workspaceSource", "client"),
        workspace_major=workspace_major,
        workspace_version=str(raw.get("workspaceVersion", "") or ""),
        workspace_layout=layout,
        java_release=java_release,
        originals_dir_override=str(originals_raw or ""),
        _raw=raw,
        _path=path,
    )


def read_config(root: Path, required: bool = True) -> ModConfig:
    """Read the workspace config via the checkout's pointer.

    Refuses to silently fall back to legacy paths — if the checkout still has
    old-layout files, raises ConfigError directing the user to re-init.
    """
    assert_no_legacy_layout(root)
    pz = read_pointer(root)
    if pz is None:
        if required:
            raise NotInitialized(
                "no workspace pointer at "
                f"{pointer_path(root)}\n"
                "    run `necroid init` to bootstrap the workspace into a PZ install."
            )
        return ModConfig()

    cfg_path = workspace_config_path(pz)
    if not cfg_path.exists():
        if required:
            raise ConfigError(
                f"pointer says PZ install is at {pz}\n"
                f"    but {cfg_path} is missing.\n"
                f"    run `necroid init` to (re-)bootstrap the workspace."
            )
        cfg = ModConfig(_path=cfg_path)
        cfg.pz_install = pz
        return cfg

    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"malformed {cfg_path}: {e}")

    cfg = _parse_workspace_config(cfg_path, raw, ws_root=pz)
    cfg.pz_install = pz
    return cfg


def write_config(pz_install: Path, cfg: ModConfig) -> Path:
    """Write the workspace config into `<pz_install>/necroid/config.json`."""
    path = workspace_config_path(pz_install)
    path.parent.mkdir(parents=True, exist_ok=True)
    obj: dict = {
        "version": CONFIG_VERSION,
        "clientPzInstall": str(cfg.client_pz_install).replace("\\", "/") if cfg.client_pz_install else "",
        "serverPzInstall": str(cfg.server_pz_install).replace("\\", "/") if cfg.server_pz_install else "",
        "defaultInstallTo": cfg.default_install_to,
        "workspaceSource": cfg.workspace_source,
        "workspaceMajor": int(cfg.workspace_major),
        "workspaceVersion": cfg.workspace_version,
        "workspaceLayout": cfg.workspace_layout or "loose",
        "javaRelease": int(cfg.java_release) if cfg.java_release > 0 else 0,
    }
    if cfg.originals_dir_override:
        obj["originalsDir"] = cfg.originals_dir_override
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    cfg._path = path
    cfg.pz_install = pz_install
    return path
