"""Constants shared across the GUI: palette, status-line labels, dialog titles.

Pure data — no Tk, no subprocess, no side effects — so any gui module can
import these without pulling in the whole controller.
"""
from __future__ import annotations

from typing import Literal


InstallTo = Literal["client", "server"]


# Charcoal + Bone palette — sampled from the brand mark.
PALETTE = {
    "char_900":  "#1F1F22",
    "char_700":  "#2B2B30",
    "char_500":  "#3D3D44",
    "char_300":  "#5A5A63",
    "bone":      "#EDE6D3",
    "bone_dim":  "#C7BFA8",
    "accent":    "#8FA68E",
    "warn":      "#D9A441",
    "error":     "#C86060",
}


# Maps CLI `==> step …` substrings to user-facing wording. Matched by
# substring (first hit wins), so ordering here is only cosmetic.
STEP_FRIENDLY = {
    "stage source": "Preparing files…",
    "compile": "Compiling Java classes…",
    "restore prior": "Restoring previous mods…",
    "copy class files to": "Writing to Project Zomboid…",
    "resolve PZ install path": "Looking for Project Zomboid…",
    "tools check": "Checking Java + Git…",
    "vineflower.jar": "Downloading decompiler…",
    "copy PZ jars": "Copying game libraries…",
    "copy PZ class trees": "Copying game classes…",
    "rejar class trees": "Repackaging classes…",
    "write data/.mod-config": "Saving settings…",
    "decompile class subtrees": "Decompiling game code (this takes a while)…",
    "scaffold mods": "Finishing setup…",
    "checking mod patches": "Re-checking mod patches…",
    "downloading": "Downloading update…",
    "extracting binary": "Extracting update…",
    "swapping binary": "Installing update…",
}


# Dialog title per CLI subcommand, for the failure messagebox.
CMD_FAILURE_TITLE = {
    "install": "Apply changes failed",
    "uninstall": "Apply changes failed",
    "apply": "Apply changes failed",
    "init": "Setup failed",
    "resync-pristine": "Update failed",
    "update": "Self-update failed",
}
