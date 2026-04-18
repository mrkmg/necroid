# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A **Project Zomboid mod workspace**, not a normal Java project. The source tree under `src/zombie/` is **decompiled** output from PZ's shipped class files (via Vineflower 1.11.1). The goal is: edit individual classes, recompile them targeting Java 17, then **overwrite the `.class` files directly in the Steam install**. PZ loads its Java classes from a loose class tree at the install root (`<steam>/common/ProjectZomboid/{zombie,astar,se,...}`), so replacing a `.class` file there is the mod mechanism. PZ does **not** have a Java-mod loader that picks up jars from the Mods folder — a jar-based approach would require writing our own classloader, which we aren't doing.

Uninstall restores originals from `classes-original/` (our pristine copy of the install's class tree) — that directory is the single source of truth for "what the vanilla class looks like", and **must not be edited**.

Writing to `C:\Program Files (x86)\...` requires an elevated PowerShell. If Steam "Verify Integrity of Game Files" is run, it will revert any installed overrides — just run `./mod.ps1 install <stack>` afterwards to re-apply.

Mods are diff-based: each mod is a directory of unified diffs under `mods/<name>/patches/`, authored against a frozen pristine decompile at `src-pristine/`. Multiple mods touching the same file combine via 3-way merge at install time. See `./mod.ps1 help`.

**Distribution model:** this repo is git-tracked for sharing with other modders, but nothing PZ-owned ships through git. `.gitignore` excludes `classes-original/`, `libs/`, `src/`, `src-pristine/`, `tools/vineflower.jar`, `build/`, and local runtime files (`.mod-config.json`, `.mod-state.json`, `.mod-enter.json`). On a fresh clone, `./mod.ps1 init` reconstructs every local-only directory from the user's own Steam install — they must own a copy of PZ. Only scripts, mod patches under `mods/`, and docs are tracked.

## Build commands

Use PowerShell — this is a Windows-only workspace.

Compile modified sources (produces `build/classes/zombie/...`):
```powershell
./build.ps1 src/zombie/Lua/Event.java src/zombie/Lua/LuaProfiler.java
```

### Mod workflow (`mod.ps1`)

Requires `git.exe` on PATH (Git for Windows). Writes to Program Files need an elevated PowerShell. All mod lifecycle operations go through `mod.ps1` — do not hand-edit `patches/` or `.mod-state.json`.

Reference commands:

```powershell
./mod.ps1 init                           # one-time: re-decompile into src-pristine/
./mod.ps1 list                           # show all mods
./mod.ps1 status                         # working tree vs pristine; installed stack
./mod.ps1 status my-mod                  # do my-mod's patches still apply against current pristine?
./mod.ps1 verify                         # re-hash installed files against .mod-state.json
./mod.ps1 resync-pristine                # after a PZ update: regenerate src-pristine/, flag stale mods
```

Install is atomic: it stages against pristine, compiles, restores the previous install to original, then copies new classes. A conflict during staging or a compile error leaves the PZ install untouched. Inner classes (`Outer$Inner.class`) are globbed automatically — a mod lists source changes, not class enumerations.

There are **no tests and no linter** — this is decompiled output, not hand-written code. The compile step is the only correctness gate.

#### Creating a new mod

1. `./mod.ps1 new my-mod -Description "..."` — scaffolds `mods/my-mod/` with `mod.json` and empty `patches/`.
2. `./mod.ps1 enter my-mod` — resets `src/zombie/` to pristine and applies my-mod's patches (none yet for a fresh mod). The working tree is now "entered" on my-mod (recorded in `.mod-enter.json`).
3. Edit files under `src/zombie/`. Only touch files you intend to ship — every diff vs pristine becomes a patch.
4. Compile what you changed to sanity-check: `./build.ps1 src/zombie/Foo/Bar.java ...`. Pass only modified files; see Critical build constraints above.
5. `./mod.ps1 capture my-mod` — diffs `src/` against `src-pristine/` and writes `.java.patch` / `.java.new` / `.java.delete` files under `mods/my-mod/patches/`. Safe to run repeatedly; it rewrites the patch set from current working tree.
6. Install to test in-game (see below).

#### Updating an existing mod

1. `./mod.ps1 enter my-mod` — resets `src/` and reapplies my-mod's existing patches so the working tree matches the mod's current state. Do this even if you think `src/` is already correct — it's the only way to guarantee a clean baseline.
2. Edit under `src/zombie/` as needed.
3. `./mod.ps1 capture my-mod` — rewrites the patch set. Patches for files you reverted to pristine are dropped automatically.
4. For a stack (`./mod.ps1 enter mod-a mod-b`): captures always write to the **last** mod in the entered stack. To edit an upstream mod, re-enter with it last, or enter it alone.
5. Stale mods after a PZ update: `./mod.ps1 status my-mod` reports whether each patch still applies. If any are stale, enter the mod (expect 3-way merge conflict markers in `src/`), resolve them by hand, then `capture` to rewrite.

#### Installing / uninstalling

- `./mod.ps1 install my-mod` — stage against pristine, compile via `build.ps1 -Clean`, roll back any prior install to originals, then copy new `.class` files into the PZ install. Atomic: any staging conflict or compile error leaves the Steam install untouched.
- `./mod.ps1 install mod-a mod-b` — same, but stacks multiple mods via 3-way merge against pristine. Order matters for conflict resolution; conflicts abort the install.
- `./mod.ps1 uninstall` — restores every `.class` file the last install wrote back to its `classes-original/` version. All-or-nothing; driven by `.mod-state.json`.
- `./mod.ps1 verify` — re-hashes installed files against `.mod-state.json`. Use after suspecting Steam integrity-verify clobbered the install, or to confirm a clean uninstall.
- Add `-DryRun` to `install` / `uninstall` to print planned copies without touching disk.
- Installing a different mod stack implicitly uninstalls the prior stack first — you don't need to uninstall manually before switching.
- Steam "Verify Integrity of Game Files" silently reverts overrides. Re-run `./mod.ps1 install <stack>` to restore.

## Critical build constraints

- **Only pass files you actually modified to `build.ps1`.** Running with no files (script errors out, intentionally) or against all ~1601 decompiled files produces thousands of errors — decompiled Java doesn't round-trip cleanly (lambdas, generics erasure, obfuscation artifacts). The install overwrites individual `.class` files, so compiling single files is the correct pattern.
- `build.ps1` deliberately **does not pass `-sourcepath`**. With a sourcepath, javac would try to recompile sibling decompiled files on demand. Instead, every non-modified symbol resolves from the original class jars in `libs/classpath-originals/`.
- Java target is **17** (`javac --release 17`). PZ bundles JRE 17 (`jre64/`); system JDK here is Temurin 21, which cross-compiles to 17 via `--release`.
- `build/classes/` is cumulative — re-running `build.ps1` with different files adds to it without clearing. `mods.ps1 install` only copies the class globs listed for the target package, so cumulative build output is fine (and convenient for building multiple packages before installing).

## Directory roles

- `src/zombie/` — decompiled Java, editable. Working copy that `mod.ps1 enter` resets and patches, and `mod.ps1 capture` reads back.
- `src-pristine/zombie/` — **frozen** pristine decompile, never edited. Populated by `mod.ps1 init`; refreshed by `mod.ps1 resync-pristine` after a PZ update. Single textual source of truth for "vanilla".
- `classes-original/` — verbatim class-file copies from the Steam install. Reference and classpath source; do **not** edit. Single binary source of truth for "vanilla".
- `libs/` — every jar from the PZ install.
- `libs/classpath-originals/` — the `classes-original/` subtrees (zombie, astar, com, de, fmod, javax, org, se) repackaged as jars so `javac -cp` can read them. Regenerate by re-jarring if `classes-original/` changes.
- `tools/vineflower.jar` — decompiler. Downloaded by `mod.ps1 init` (not tracked in git).
- `build/classes/` — javac output mirroring the `zombie/...` package layout. Source for install copy.
- `build/stage-src/` — ephemeral staging tree rebuilt on every `mod.ps1 install`. Safe to delete.
- `mods/<name>/` — each mod: `mod.json` + `patches/` with `.java.patch` / `.java.new` / `.java.delete`.
- `lib/mod-lib.ps1` — shared helpers dot-sourced by `mod.ps1`.
- `.mod-config.json` — `pzInstallDir` + `originalsDir`. Replaces the non-package fields of legacy `mods.json`.
- `.mod-state.json` — runtime: what `.class` files the last install wrote, so `uninstall` can undo.
- `.mod-enter.json` — ephemeral: records which mod stack the working tree is currently "entered" on.

## When a PZ update lands

Run `./mod.ps1 resync-pristine`. This re-runs the full `init` flow with `-Force` (refreshing `classes-original/`, `libs/`, `libs/classpath-originals/`, and `src-pristine/zombie/`), then re-fingerprints every mod against the new pristine. Mods whose patches no longer apply are flagged STALE — enter them one at a time, resolve conflicts in `src/`, then `capture` to rewrite the patches.

Vineflower writes the `zombie` package at the root of its output folder (files declare `package zombie;` but land directly in the output root), so `_InitStep_Decompile` in [mod.ps1](mod.ps1) moves that output into `src-pristine/zombie/` as the final step — that's not a bug.

## Things that look like bugs but aren't

- Many `.java` files contain `new Float(...)` / `new Double(...)` deprecation warnings and `sun.misc.Unsafe` warnings. These are in PZ's original bytecode — leave them alone unless the file you're modding is one of them.
- Fully-qualified names like `zombie.BaseAmbientStreamManager` inside files already in `package zombie` are a Vineflower quirk, not an error.
- Inner classes in `classes-original/` appear as `Outer$Inner.class` (2980 class files) but decompile to inner-class declarations inside 1601 outer `.java` files. The counts match.
