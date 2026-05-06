# AGENTS.md

Two flows live here: working on Necroid (the tool) and working on Necroid mods.

For deep context (install layouts, manifest schema, reconciliation matrix, Steam asymmetry, build constraints, directory roles, what-looks-like-bugs-but-isn't) → [`docs/architecture.md`](docs/architecture.md). Read it before touching install/uninstall/resync internals or anything that writes to the PZ install.

## Working on Necroid (the tool)

Necroid is a Project Zomboid mod manager written in Python. The tool itself; this section is for developing it.

- **Python 3.10+, stdlib only.** No third-party deps. `tkinter`, `subprocess`, `hashlib`, `urllib`, `json`, `zipfile` are fair game; `requests`/`pyyaml`/anything in `pip` is not.
- **Cross-platform: Windows / Linux / macOS.** Don't introduce PowerShell-only or POSIX-only shims. Use `pathlib`, `subprocess` with arg lists, `shutil`, `os.path`. Auto-fetched portable tools (JDK, MinGit on Win) live in `data/tools/`; see `necroid/util/tools.py` + `tools_fetch.py`.
- **Layout.** Flat at repo root:
  - `necroid/` — package. Entry: `necroid/cli.py:main` (script `necroid` per `pyproject.toml`). GUI: `necroid/gui.py`. Subpackages: `commands/`, `core/`, `util/`. Top-level modules: `github.py`, `pzversion.py`, `assets.py`, etc.
  - `packaging/build_dist.py` — PyInstaller builder.
  - `assets/` — brand assets.
  - `mods/<base>-<major>/` — bundled patch sets (the portable artifact).
  - `.githooks/pre-commit` — blocks `src-*/` commits. Wired by `necroid init` via `git config core.hooksPath .githooks`. On a fresh clone without `init`, run that command yourself.
- **Run from source.** `python -m necroid <cmd>` from repo root, or `pip install -e .` then `necroid <cmd>`.
- **No tests, no linter.** The Python tool currently has no automated gate — verify changes manually (run the CLI / GUI against a real PZ install). The `javac` compile step (via `necroid test` / `install`) is the only gate for the Java side; that's deliberate, decompiled Java doesn't round-trip.
- **Build the distributable.**
  ```bash
  pip install pyinstaller
  python packaging/build_dist.py
  ```
  Produces `dist/necroid(.exe)` + `dist/mods/` + `dist/README.txt`. PyInstaller doesn't cross-compile — build on each target OS. Vineflower is bundled and self-extracts on first run.
- **External tool requirements.** `git`, `java` (17+), `javac` (17+), `jar`. Necroid auto-fetches Temurin JDK + (on Windows) MinGit when missing; macOS/Linux git is left to the OS package manager.
- **Don't change install/uninstall/resync semantics without reading [`docs/architecture.md`](docs/architecture.md).** The install-side manifest, hash-aware uninstall, reconciliation matrix, and per-file audit are load-bearing — Steam's file management is asymmetric and easy to break.

## Working on Necroid Mods

Mods are unified diffs against a frozen decompile of PZ. Live at `mods/<base>-<major>/` (e.g. `admin-xray-41`). Patches under `patches/` as `.java.patch` / `.java.new` / `.java.delete`. Architecture details (decompile model, layouts, manifest, Steam interactions) → [`docs/architecture.md`](docs/architecture.md).

### Create a new mod

When being asked to plan a new mod, you should first run the `new mod-name` and `enter mod-name` commands, **before any research**, so you can get a src-my-mod folder you can use to do your research.

```bash
necroid new my-mod --description "..." [--client-only] [--category utility] [--depends-on dep1]
necroid enter my-mod                # seeds src-my-mod/ from pristine
# edit files under src-my-mod/zombie/...
necroid test                        # javac-only, fast feedback loop
necroid capture my-mod              # diff src-my-mod/ → mods/my-mod-<major>/patches/
necroid install my-mod --to client  # compile + install; play-test
```

When iterating on a mod the user is play-testing, after each successful `necroid test` also run `necroid capture <mod>` then `necroid install <mod> --to client` so the live install reflects the latest edit.

### Update an existing mod

```bash
necroid enter my-mod          # preserves src-my-mod/ if present; --force to re-seed
# edit src-my-mod/...
necroid capture my-mod
necroid install my-mod --to client
# when complete
necroid clean
git add mods/my-mod-*
git commit -m "Update my-mod: <summary of changes>"
```

- Only one mod can be entered at a time. Switching mods (`necroid enter other-mod`) is non-destructive — `src-my-mod/` stays on disk.
- `necroid clean [<mod>]` deletes `src-*/` trees. `necroid reset` re-seeds the entered mod from pristine + patches (discards local edits, keeps enter state).
- Patches that revert files back to pristine drop out of `capture` automatically — no manual cleanup.

### Stack mods + relationships

- Stacking is **install-time** only: `necroid install mod-a mod-b --to client` (3-way merge against pristine; conflicts abort).
- You can't enter multiple mods. Each mod's patches are authored against pristine, not against an upstream mod.
- Declare relationships in `mod.json` (or via `necroid new --depends-on X --incompatible-with Y` / `necroid deps add|remove`):
  - `dependencies: ["other-mod"]` — bare names. Applied first at enter; `capture` diffs against pristine + applied deps; install topo-orders.
  - `incompatibleWith: ["rival"]` — either side rejects the stack with `ModIncompatibility`.
- `necroid uninstall <mod> --cascade` removes dependents from the installed stack instead of erroring.

### After a PZ update

```bash
necroid doctor --to client          # read-only diagnosis first — explains why a resync would abort
necroid resync-pristine             # one pass; --force-major-change for 41→42
                                    # --force-version-drift if Steam patch overwrote installed files
                                    # --force-orphans if mod-touched subtrees have unknown files
```

For each stale mod after resync: `necroid enter <mod> --force` (or `enter` then `reset`), resolve conflict markers in `src-<mod>/`, then `capture`. `resync-pristine` deliberately does **not** rebase existing `src-*/` trees.

### Import / publish from GitHub

- **Import:** `necroid import owner/repo [--list] [--all] [--mod admin-xray] [--ref branch]`. Discovery requires the canonical layout `mods/<name>-<major>/mod.json` at repo root.
- **Refresh:** `necroid mod-update [name] [--check] [--include-peers]`. The `origin` block in `mod.json` makes a mod eligible — bundled mods get one stamped at dist time.
- **Per-major variants are sibling dirs**, not branches: `mods/admin-xray-41/` and `mods/admin-xray-42/` coexist on the same branch. Authors maintain both for as long as they care to.
- **Publish:** drop Necroid into a repo, `necroid init`, develop under `mods/`, commit `README.md` + `mods/`. That's it. Users `necroid import owner/repo`.

### `clientOnly` rules

- `mod.json: "clientOnly": true` → mod requires a configured client PZ install; cannot be installed to the server.
- `install --to server` on a stack containing any clientOnly mod → hard error (`ClientOnlyViolation`).
- `enter` requires `clientPzInstall` configured if any mod in the stack is clientOnly.
- A dep's clientOnly propagates: dependent's effective clientOnly is true if any transitive dep is.

### Install / uninstall basics

- `necroid install my-mod --to client|server` — atomic: stage, compile, roll back prior install on that destination, copy new classes. A conflict or compile error leaves the PZ install untouched.
- `necroid uninstall --to <dest>` — restore (loose) or delete (jar) every file the last install wrote, hash-aware. Also deletes the install-side manifest.
- `necroid uninstall my-mod --to <dest>` — drop one from the installed stack and rebuild the rest.
- Installing a different stack to the same destination implicitly uninstalls the prior one.
- Client and server state are independent — different stacks on each is fine.
- Steam "Verify Integrity of Game Files" silently reverts overrides on `loose` layout. Re-run `install`, or `doctor --to <dest>` first for a per-file report.

Bare mod names (`admin-xray`) resolve to `<base>-<workspaceMajor>`. Fully-qualified names (`admin-xray-41`) must match the workspace major or the command errors with `PzMajorMismatch`.
