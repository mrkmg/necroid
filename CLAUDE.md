# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A **Project Zomboid mod workspace**, not a normal Java project. Per-profile source trees under `data/<target>/src/zombie/` are **decompiled** output from PZ's shipped class files (via Vineflower 1.11.1). The goal is: edit individual classes, recompile them targeting Java 17, then **overwrite the `.class` files directly in the PZ install**. PZ loads its Java classes from a loose class tree at the install root (`<steam>/common/ProjectZomboid/{zombie,astar,se,...}`), so replacing a `.class` file there is the mod mechanism. PZ does **not** have a Java-mod loader that picks up jars from the Mods folder — a jar-based approach would require writing our own classloader, which we aren't doing.

The tool supports **two targets**:
- `client` — `ProjectZomboid` install (Steam app 108600).
- `server` — `Project Zomboid Dedicated Server` install (Steam app 380870) or a local `./pzserver/` copy.

Each target has its own `data/<target>/{src,src-pristine,classes-original,libs,build}/` tree and its own `.mod-state.json`. Mods declare `target: "client" | "server"` in `mod.json` (default `client`).

Uninstall restores originals from `data/<target>/classes-original/` (pristine copy of the install's class tree) — that directory is the single source of truth for "what the vanilla class looks like", and **must not be edited**.

Writing to `C:\Program Files (x86)\...` requires an elevated shell. If Steam "Verify Integrity of Game Files" is run, it will revert any installed overrides — just re-run `pz-java-modder install <stack>` afterwards.

Mods are diff-based: each mod is a directory of unified diffs under `data/mods/<name>/patches/`, authored against the frozen pristine decompile at `data/<target>/src-pristine/`. Multiple mods touching the same file combine via 3-way merge at install time. See `pz-java-modder --help`.

**Distribution model:** the repo is git-tracked for sharing with other modders, but nothing PZ-owned ships through git. `.gitignore` excludes `data/client/`, `data/server/`, `data/tools/vineflower.jar`, `data/.mod-config.json`, `dist/`, and Python caches. On a fresh clone, `pz-java-modder init` reconstructs every local-only directory from the user's own Steam install — they must own a copy of PZ. Only `pz-java-modder/` (Python source), `data/mods/` (the patch-set library), and docs are tracked.

## Tool: `pz-java-modder`

Python 3.10+ (stdlib only — tkinter, subprocess, hashlib, urllib, json). Cross-platform (Windows / Linux / macOS). Two entry points:

- **CLI** — full feature set. Developers and automation use this.
- **GUI** (tkinter) — simplified end-user surface: Init/Resync, Install, Uninstall. Launch with `--gui`.

External requirements on PATH: `git`, `java` (17+), `javac` (17+), `jar` (ships with JDK). `init` downloads Vineflower itself.

Run from the repo root:

```bash
# one-time bootstrap (client target is default):
python -m pz_java_modder init
python -m pz_java_modder --target server init     # separately for server

# day-to-day:
python -m pz_java_modder list                     # tabular mod inventory
python -m pz_java_modder status                   # working tree vs pristine + installed stack
python -m pz_java_modder status my-mod            # per-mod patch applicability
python -m pz_java_modder verify                   # re-hash installed files
python -m pz_java_modder resync-pristine          # after a PZ update

# GUI:
python -m pz_java_modder --gui                    # client GUI
python -m pz_java_modder --gui -server            # server GUI
```

The packaged distributable uses the bare name `pz-java-modder` (no `python -m`).

All target-aware commands accept `--target {client,server}`; default resolves from `data/.mod-config.json` `defaultTarget` (falls back to `client`). `-server` (single-dash) is a shorthand for `--target server` — useful for GUI launchers.

Install is **atomic**: stages against pristine, compiles via `javac`, restores the previous install to originals, then copies new classes. A conflict during staging or a compile error leaves the PZ install untouched. Inner classes (`Outer$Inner.class`) are globbed automatically — a mod lists source changes, not class enumerations.

There are **no tests and no linter** for the PZ-decompiled code — it's decompiled output, not hand-written. The `javac` compile step is the only correctness gate. The Python tool itself is stdlib-only and also has no test suite yet.

### Creating a new mod

1. `pz-java-modder new my-mod --description "..."` — scaffolds `data/mods/my-mod/mod.json` + empty `patches/`. Target comes from the active profile (use `--target server` to create a server mod).
2. `pz-java-modder enter my-mod` — mirrors pristine into `data/<target>/src/` and applies my-mod's patches (none yet for a fresh mod). Working tree is now "entered" on my-mod (recorded in `data/<target>/.mod-enter.json`).
3. Edit files under `data/<target>/src/zombie/`. Only touch files you intend to ship — every diff vs pristine becomes a patch.
4. `pz-java-modder capture my-mod` — diffs `src/` against `src-pristine/` and writes `.java.patch` / `.java.new` / `.java.delete` under `data/mods/my-mod/patches/`. Safe to run repeatedly.
5. `pz-java-modder install my-mod` — compile + install; play-test.

### Updating an existing mod

1. `pz-java-modder enter my-mod` — resets `src/` and reapplies my-mod's patches so the working tree matches the mod's current state. Do this even if you think `src/` is already correct — only way to guarantee a clean baseline.
2. Edit under `data/<target>/src/zombie/`.
3. `pz-java-modder capture my-mod` — rewrites the patch set. Patches for files you reverted to pristine drop out automatically.
4. For a stack (`enter mod-a mod-b`): captures always write to the **last** mod in the entered stack. To edit an upstream mod, re-enter with it last, or enter it alone.
5. Stale mods after a PZ update: `pz-java-modder status my-mod` reports whether each patch still applies. If stale, `enter` the mod (expect 3-way merge conflict markers in `src/`), resolve by hand, then `capture`.

### Installing / uninstalling

- `pz-java-modder install my-mod` — stage against pristine, compile, roll back prior install, copy new `.class` files into the PZ install.
- `pz-java-modder install mod-a mod-b` — stack multiple mods via 3-way merge against pristine. Order matters for conflict resolution; conflicts abort the install.
- `pz-java-modder uninstall` — restore every class file the last install wrote back to its `classes-original/` version.
- `pz-java-modder uninstall my-mod` — remove one from the stack and rebuild the rest.
- `pz-java-modder verify` — re-hash installed files against `.mod-state.json`.
- Installing a different stack implicitly uninstalls the prior stack — no manual uninstall needed before switching.
- Steam "Verify Integrity of Game Files" silently reverts overrides. Re-run `install` to restore.

### Target-mismatch rules

- `install my-mod`, `enter my-mod`, `capture my-mod`, `diff my-mod` with a mod whose `target` differs from the active profile → **hard error** (retry with `--target <other>`).
- `install` with no named mods → silent filter; no-named `uninstall` behaves identically.
- `list` / `status` (no-arg) show all mods; off-target rows are marked `*client` or `*server`.
- GUI in `client` mode hides server-target mods entirely; server-launched GUI hides client ones.

## Critical build constraints

- **Only pass modified files to `javac`** (the `install` flow does this automatically). Compiling all ~1601 decompiled files produces thousands of errors — decompiled Java doesn't round-trip cleanly (lambdas, generics erasure, obfuscation artifacts). The install overwrites individual `.class` files, so compiling the changed files only is correct.
- `buildjava.javac_compile` deliberately **does not pass `-sourcepath`**. With a sourcepath, javac would try to recompile sibling decompiled files on demand. Every non-modified symbol resolves from the original class jars in `data/<target>/libs/classpath-originals/`.
- Java target is **17** (`javac --release 17`). PZ bundles JRE 17 (`jre64/`).
- `data/<target>/build/classes/` is the javac output; `stage-src/` is the ephemeral staging tree for each install. Both safe to delete.

## Directory roles

- `pz-java-modder/` — Python source tree. `pyproject.toml`, `pz_java_modder/`, `packaging/build_dist.py`.
- `data/` — all PZ-sourced + runtime content.
- `data/.mod-config.json` — `clientPzInstall`, `serverPzInstall`, `defaultTarget`. Local-only.
- `data/mods/<name>/` — each mod: `mod.json` (with `target`) + `patches/` containing `.java.patch` / `.java.new` / `.java.delete`. **Tracked**; the portable artifact.
- `data/tools/vineflower.jar` — downloaded by `init`. Local-only.
- `data/<target>/src/zombie/` — decompiled Java, editable per profile. `enter` resets and patches, `capture` reads back.
- `data/<target>/src-pristine/zombie/` — **frozen** pristine decompile. Populated by `init`; refreshed by `resync-pristine`.
- `data/<target>/classes-original/` — verbatim class-file copies from the Steam install. Reference and restore source; **do not edit**.
- `data/<target>/libs/` — every jar from the PZ install.
- `data/<target>/libs/classpath-originals/` — the `classes-original/` subtrees repackaged as jars for `javac -cp`.
- `data/<target>/build/classes/` — javac output mirroring `zombie/...`.
- `data/<target>/build/stage-src/` — ephemeral install-staging tree.
- `data/<target>/.mod-state.json` — per-profile runtime manifest of what the last install wrote; used by `uninstall`.
- `data/<target>/.mod-enter.json` — per-profile: the mod stack the working tree is currently "entered" on.
- `dist/` — produced by `pz-java-modder/packaging/build_dist.py`: self-contained binary + `data/mods/`. Local-only.

## When a PZ update lands

Run `pz-java-modder resync-pristine` (per target). This re-runs the `init` flow with `--force` (refreshing `classes-original/`, `libs/`, `libs/classpath-originals/`, and `src-pristine/zombie/`), then re-fingerprints every mod against the new pristine. Mods whose patches no longer apply are flagged STALE — `enter` them one at a time, resolve conflicts in `src/`, then `capture`.

Vineflower writes files declaring `package zombie;` into its output root (not a nested `zombie/` folder), so `decompile_zombie` moves that output into `src-pristine/zombie/` as the final step — that's not a bug.

## Building the distributable

```bash
pip install pyinstaller
python pz-java-modder/packaging/build_dist.py
```

Produces `dist/pz-java-modder(.exe)` + `dist/data/mods/` + `dist/README.txt`. PyInstaller does not cross-compile; build on each target OS you need. Vineflower is bundled into the binary and self-extracts on first run.

## Things that look like bugs but aren't

- Many `.java` files contain `new Float(...)` / `new Double(...)` deprecation warnings and `sun.misc.Unsafe` warnings. These are in PZ's original bytecode — leave them alone unless the file you're modding is one of them.
- Fully-qualified names like `zombie.BaseAmbientStreamManager` inside files already in `package zombie` are a Vineflower quirk, not an error.
- Inner classes in `classes-original/` appear as `Outer$Inner.class` (~2980 class files for the client) but decompile to inner-class declarations inside ~1601 outer `.java` files. The counts match.
