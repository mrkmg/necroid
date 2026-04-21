# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

**Necroid** — a Project Zomboid mod manager, not a normal Java project. Source trees under `data/workspace/src/zombie/` are **decompiled** output from PZ's shipped class files (via Vineflower 1.11.1). The goal is: edit individual classes, recompile them targeting Java 17, then **overwrite the `.class` files directly in the PZ install**. PZ loads its Java classes from a loose class tree at the install root (`<steam>/common/ProjectZomboid/{zombie,astar,se,...}`), so replacing a `.class` file there is the mod mechanism. PZ does **not** have a Java-mod loader that picks up jars from the Mods folder — a jar-based approach would require writing our own classloader, which we aren't doing.

**Single shared workspace.** The client (`ProjectZomboid`, Steam app 108600) and dedicated server (`Project Zomboid Dedicated Server`, Steam app 380870, or `./pzserver/`) ship byte-identical Java class trees. Necroid therefore keeps **one** workspace at `data/workspace/{src,src-pristine,classes-original,libs,build}/`, seeded from whichever PZ install the user points `init` at (`--from client` or `--from server`). The chosen source is recorded in `config.workspaceSource`.

**Install destinations are per-invocation.** `necroid install <stack> --to client|server` chooses where the compiled `.class` files land. Each destination has its own state file: `data/.mod-state-client.json` and `data/.mod-state-server.json`. Both can coexist — a user may have, say, admin-xray installed to client and gravymod installed to server at the same time.

**Mods carry a `clientOnly` flag.** In `mod.json`, `clientOnly: true` means the mod requires a configured client PZ install and cannot be installed to the server (it relies on client-only rendering / input code). `clientOnly: false` (default) means the mod works against either destination. There is no per-mod "target" any more.

Uninstall restores originals from `data/workspace/classes-original/` (verbatim copy of the install's class tree) — that directory is the single source of truth for "what the vanilla class looks like", and **must not be edited**.

Writing to `C:\Program Files (x86)\...` requires an elevated shell. If Steam "Verify Integrity of Game Files" is run, it will revert any installed overrides — just re-run `necroid install <stack> --to <dest>` afterwards.

Mods are diff-based: each mod is a directory of unified diffs under `data/mods/<name>/patches/`, authored against the frozen pristine decompile at `data/workspace/src-pristine/`. Multiple mods touching the same file combine via 3-way merge at install time. See `necroid --help`.

**Branding:** Name = Necroid. Tagline = "Beyond Workshop". Palette = Charcoals + Bone (see `necroid/gui.py` `PALETTE` dict). Brand assets live in `assets/`; `assets/necroid.png` is the 1024² source, and derived icons (`necroid-mark-256.png`, `necroid-icon-256.png`, `necroid-icon.ico`) are regenerated via `bash assets/build-assets.sh` (requires ImageMagick; end users don't need it).

**Distribution model:** the repo is git-tracked for sharing with other modders, but nothing PZ-owned ships through git. `.gitignore` excludes `data/workspace/`, `data/tools/vineflower.jar`, `data/.mod-config.json`, `data/.mod-enter.json`, `data/.mod-state-*.json`, `dist/`, `build/`, and Python caches. On a fresh clone, `necroid init` reconstructs every local-only directory from the user's own Steam install — they must own a copy of PZ. Only `necroid/` (Python source), `packaging/`, `assets/`, `data/mods/` (the patch-set library), and docs are tracked. Releases ship via GitHub Releases at `github.com/mrkmg/necroid` — tag, run `packaging/build_dist.py`, zip `dist/`, attach to the tagged release.

## Tool: `necroid`

Python 3.10+ (stdlib only — tkinter, subprocess, hashlib, urllib, json). Cross-platform (Windows / Linux / macOS). Two entry points:

- **CLI** — full feature set. Developers and automation use this.
- **GUI** (tkinter) — simplified end-user surface: Init/Resync, Install, Uninstall. Launch with `--gui`. Themed charcoal/bone; logo + window icon load from `assets/`.

External requirements on PATH: `git`, `java` (17+), `javac` (17+), `jar` (ships with JDK). `init` downloads Vineflower itself.

Run from the repo root:

```bash
# one-time bootstrap (seeds the shared workspace from either install):
python -m necroid init                      # --from client (the default if configured)
python -m necroid init --from server        # or bootstrap from the dedicated server

# day-to-day:
python -m necroid list                      # tabular mod inventory, with Client-only? column
python -m necroid status                    # working tree vs pristine + both install states
python -m necroid status my-mod             # per-mod patch applicability
python -m necroid verify --to client        # re-hash client-installed files
python -m necroid resync-pristine           # after a PZ update

# GUI:
python -m necroid --gui                     # single window; install-to toggle in the header
python -m necroid --gui -server             # same window, initial install-to=server
```

Install editable (`pip install -e .`) to put `necroid` on PATH as a bare command. The packaged distributable from `packaging/build_dist.py` also uses the bare name `necroid` (no `python -m`).

Per-command flags:

- `init` / `resync-pristine`: `--from {client,server}` picks the PZ install to seed from. Default comes from `config.workspaceSource`, then falls back to whichever install is configured, then `client`.
- `install` / `uninstall` / `verify` / `list` / `status`: `--to {client,server}` chooses the install destination / state file / counting lens. Default from `config.defaultInstallTo`.
- `enter`: `--as {client,server}` picks which per-destination postfix variant to apply when the mod ships one. Default is `config.defaultInstallTo`; forced to `client` if any mod in the stack is `clientOnly`.

Install is **atomic**: stages against pristine, compiles via `javac`, restores the previous install to originals, then copies new classes. A conflict during staging or a compile error leaves the PZ install untouched. Inner classes (`Outer$Inner.class`) are globbed automatically — a mod lists source changes, not class enumerations.

There are **no tests and no linter** for the PZ-decompiled code — it's decompiled output, not hand-written. The `javac` compile step is the only correctness gate. The Python tool itself is stdlib-only and also has no test suite yet.

### Creating a new mod

1. `necroid new my-mod --description "..."` — scaffolds `data/mods/my-mod/mod.json` + empty `patches/`. Add `--client-only` if the mod touches client-only code.
2. `necroid enter my-mod` — mirrors pristine into `data/workspace/src/` and applies my-mod's patches (none yet for a fresh mod). Working tree is now "entered" on my-mod (recorded in `data/.mod-enter.json`, including `installAs`).
3. Edit files under `data/workspace/src/zombie/`. Only touch files you intend to ship — every diff vs pristine becomes a patch.
4. `necroid capture my-mod` — diffs `workspace/src/` against `workspace/src-pristine/` and writes `.java.patch` / `.java.new` / `.java.delete` under `data/mods/my-mod/patches/`. Safe to run repeatedly.
5. `necroid test` — javac-only compile of the currently-entered working tree into `data/workspace/build/classes/`. No install, no staging, no PZ-install writes. Fastest way to catch compile errors before touching the game. Run it anytime between edits.
6. `necroid install my-mod --to client` — compile + install; play-test.

### Updating an existing mod

1. `necroid enter my-mod` — resets `workspace/src/` and reapplies my-mod's patches so the working tree matches the mod's current state. Do this even if you think `src/` is already correct — only way to guarantee a clean baseline.
2. Edit under `data/workspace/src/zombie/`.
3. `necroid capture my-mod` — rewrites the patch set. Patches for files you reverted to pristine drop out automatically.
4. For a stack (`enter mod-a mod-b`): captures always write to the **last** mod in the entered stack. To edit an upstream mod, re-enter with it last, or enter it alone.
5. Stale mods after a PZ update: `necroid status my-mod` reports whether each patch still applies. If stale, `enter` the mod (expect 3-way merge conflict markers in `src/`), resolve by hand, then `capture`.

### Installing / uninstalling

- `necroid install my-mod --to client` — stage against pristine, compile, roll back the prior `client` install, copy new `.class` files into the client PZ install. Drop `--to` to use `config.defaultInstallTo`.
- `necroid install mod-a mod-b --to server` — stack multiple mods via 3-way merge against pristine. Order matters for conflict resolution; conflicts abort the install.
- `necroid uninstall --to <dest>` — restore every class file the last install on `<dest>` wrote back to its `classes-original/` version.
- `necroid uninstall my-mod --to <dest>` — remove one from that destination's stack and rebuild the rest.
- `necroid verify --to <dest>` — re-hash installed files against `data/.mod-state-<dest>.json`.
- `necroid test` — compile the entered working tree via javac into `data/workspace/build/classes/` without installing. Green here means `install` will compile; runtime correctness is still on the play-test.
- Client and server state are independent — you can install one stack to client and a different one to server simultaneously.
- Installing a different stack to the same destination implicitly uninstalls the prior one — no manual uninstall needed before switching.
- Steam "Verify Integrity of Game Files" silently reverts overrides. Re-run `install` to restore.

### clientOnly rules

- `install --to server` on a stack containing any `clientOnly: true` mod → **hard error** (`ClientOnlyViolation`). Retry with `--to client`.
- `enter` on a stack containing a `clientOnly: true` mod when `clientPzInstall` is unset → **hard error**. Configure the client install (`necroid init --from client`) or drop `clientOnly`.
- `enter <stack> --as server` when the stack contains a `clientOnly: true` mod → **hard error**.
- `list` / `status` never hide mods. The `Client-only?` column (list) or `clientOnly:` line (status per-mod) is the marker.
- GUI shows all mods. When install-to = server, clientOnly rows gray out and can't be checked; flipping the header toggle back to client re-enables them.

## Critical build constraints

- **Only pass modified files to `javac`** (the `install` flow does this automatically). Compiling all ~1601 decompiled files produces thousands of errors — decompiled Java doesn't round-trip cleanly (lambdas, generics erasure, obfuscation artifacts). The install overwrites individual `.class` files, so compiling the changed files only is correct.
- `buildjava.javac_compile` deliberately **does not pass `-sourcepath`**. With a sourcepath, javac would try to recompile sibling decompiled files on demand. Every non-modified symbol resolves from the original class jars in `data/workspace/libs/classpath-originals/`.
- Java target is **17** (`javac --release 17`). PZ bundles JRE 17 (`jre64/`).
- `data/workspace/build/classes/` is the javac output; `data/workspace/build/stage-src/` is the ephemeral staging tree for each install. Both safe to delete.

## Directory roles

- `necroid/` — Python package (CLI, GUI, commands, install orchestrator). Flat layout at repo root.
- `packaging/build_dist.py` — PyInstaller builder; writes `<repo-root>/dist/`.
- `assets/` — brand assets. `necroid.png` (source 1024²), `necroid-mark-256.png` (GUI header skull), `necroid-icon-256.png` (window icon), `necroid-icon.ico` (Windows exe icon), `build-assets.sh` (ImageMagick regen).
- `pyproject.toml` — project metadata; script entry point `necroid = "necroid.cli:main"`.
- `data/` — all PZ-sourced + runtime content.
- `data/.mod-config.json` — `clientPzInstall`, `serverPzInstall`, `defaultInstallTo`, `workspaceSource`. Schema v3. Local-only.
- `data/mods/<name>/` — each mod: `mod.json` (with `clientOnly`) + `patches/` containing `.java.patch` / `.java.new` / `.java.delete`. **Tracked**; the portable artifact.
- `data/tools/vineflower.jar` — downloaded by `init`. Local-only.
- `data/workspace/src/{zombie,astar,com,de,fmod,javax,org,se}/` — decompiled Java, editable. `enter` resets and patches, `capture` reads back. Every class subtree PZ ships is decompiled, so mods can touch any of them (e.g. `se/krka/kahlua/...` for Lua-interpreter changes).
- `data/workspace/src-pristine/<same subtrees>/` — **frozen** pristine decompile. Populated by `init`; refreshed by `resync-pristine`.
- `data/workspace/classes-original/` — verbatim class-file copies from the Steam install. Reference and restore source; **do not edit**.
- `data/workspace/libs/` — every jar from the PZ install used to seed the workspace.
- `data/workspace/libs/classpath-originals/` — the `classes-original/` subtrees repackaged as jars for `javac -cp`.
- `data/workspace/build/classes/` — javac output mirroring `zombie/...`.
- `data/workspace/build/stage-src/` — ephemeral install-staging tree.
- `data/.mod-state-client.json` / `data/.mod-state-server.json` — per-destination runtime manifest of what the last install to that destination wrote; used by `uninstall --to <dest>`.
- `data/.mod-enter.json` — the mod stack the working tree is currently "entered" on, plus the `installAs` destination used when applying postfix variants.
- `build/` — PyInstaller scratch + raw output. Local-only.
- `dist/` — produced by `packaging/build_dist.py`: self-contained binary + `data/mods/`. Local-only; zipped and shipped via GitHub Releases.

## When a PZ update lands

Run `necroid resync-pristine` (one pass — workspace is shared). Before refreshing pristine sources, any installed stack on client or server is uninstalled first — otherwise the modded `.class` files in the PZ install would get copied back into `classes-original/` and adopted as the new pristine, contaminating every mod's diffs. If a destination has installed state but its PZ install is unreachable, resync aborts rather than silently skipping. After the guard, it re-runs the `init` flow with `--force` against `config.workspaceSource` (refreshing `classes-original/`, `libs/`, `libs/classpath-originals/`, and every `src-pristine/<subtree>/`), then re-fingerprints every mod against the new pristine. Mods whose patches no longer apply are flagged STALE — `enter` them one at a time, resolve conflicts in `src/`, then `capture`.

Vineflower writes files declaring `package <subtree>;` into its output root (not a nested folder) because each `classes-original/<subtree>/` *is* the package root. `decompile_subtree` therefore decompiles into a tmp dir and renames it into `src-pristine/<subtree>/` as the final step — that's not a bug. Each subtree is decompiled in its own Vineflower invocation.

## Building the distributable

```bash
pip install pyinstaller
python packaging/build_dist.py
```

Produces `dist/necroid(.exe)` + `dist/data/mods/` + `dist/README.txt`. PyInstaller does not cross-compile; build on each target OS you need. Vineflower is bundled into the binary and self-extracts on first run. The derived PNG assets (`necroid-mark-256.png`, `necroid-icon-256.png`) are bundled via `--add-data` and resolved at runtime via `necroid/assets.py` (which handles both dev and `sys._MEIPASS` frozen mode). On Windows, `necroid-icon.ico` is also baked into the `.exe` via PyInstaller's `--icon` flag.

## Things that look like bugs but aren't

- Many `.java` files contain `new Float(...)` / `new Double(...)` deprecation warnings and `sun.misc.Unsafe` warnings. These are in PZ's original bytecode — leave them alone unless the file you're modding is one of them.
- Fully-qualified names like `zombie.BaseAmbientStreamManager` inside files already in `package zombie` are a Vineflower quirk, not an error.
- Inner classes in `classes-original/` appear as `Outer$Inner.class` (~2980 class files for the client) but decompile to inner-class declarations inside ~1601 outer `.java` files. The counts match.
