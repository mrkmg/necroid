# pz-java-modder

Python source tree for the Project Zomboid Java mod manager. See the repo-root [README.md](../README.md) for user-facing docs and the [CLAUDE.md](../CLAUDE.md) for the workspace model.

## Dev setup

```bash
cd pz-java-modder
pip install -e .                # puts `pz-java-modder` on PATH
# or run from source without installing:
python -m pz_java_modder --help
```

Stdlib-only runtime (no deps in `pyproject.toml`). `pyinstaller` is only needed to build the distributable.

## Package layout

```
pz_java_modder/
├── __init__.py, __main__.py
├── cli.py                     # argparse entry
├── gui.py                     # tkinter GUI (launched via --gui)
├── profile.py                 # Profile dataclass (single target-switch point)
├── config.py                  # data/.mod-config.json I/O + path expansion
├── state.py                   # .mod-state.json / .mod-enter.json
├── mod.py                     # mod.json + patch enumeration + pristine snapshot
├── hashing.py                 # SHA256 helpers
├── fsops.py                   # mirror_tree (robocopy replacement), inner-class glob
├── tools.py                   # PATH discovery for git/java/javac/jar
├── errors.py                  # typed exceptions
├── logging_util.py            # stderr formatter
├── patching.py                # subprocess wrappers for git diff/apply/merge-file
├── decompile.py               # Vineflower download + decompile
├── buildjava.py               # javac wrapper
├── stackapply.py              # apply an ordered mod stack into a work tree (3-way aware)
├── install.py                 # atomic install orchestrator
└── commands/
    ├── init.py, new.py, list_cmd.py, status.py, enter.py, capture.py,
    ├── diff.py, reset.py, install_cmd.py, uninstall.py, verify.py,
    └── resync_pristine.py
```

Each `commands/*.py` exports a single `run(args) -> int`. `cli.py` and `gui.py` both dispatch to those — the GUI shells out to `python -m pz_java_modder` so a command crash can't kill the GUI.

## Building the distributable

```bash
pip install pyinstaller
python packaging/build_dist.py
```

Writes `<repo-root>/dist/` with the onefile binary, bundled mods, and a README. Per-platform local builds (no cross-compile).
