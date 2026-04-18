# PZ Mod Work

Local workspace for editing decompiled Project Zomboid classes and overwriting them in the Steam install. **Only your local Steam install is ever modified** — nothing PZ-owned is distributed in this repo. A fresh clone contains only scripts, mod patches, and docs; everything PZ-owned (jars, classes, decompiled sources) is reconstructed locally from your own Steam install by `./mod.ps1 init`.

## First-time setup (fresh clone)

Requirements — install via [winget](https://learn.microsoft.com/windows/package-manager/winget/):

```powershell
winget install EclipseAdoptium.Temurin.21.JDK
winget install Git.Git
```

…then open a **new** PowerShell so `java.exe`, `jar.exe`, and `git.exe` are on PATH. Own a Steam copy of Project Zomboid.

```powershell
git clone <this-repo> PZ-Mod-Work
cd PZ-Mod-Work
./mod.ps1 init
```

`init` does everything in one shot (≈1 min):

1. Locates the PZ install (`-PzInstallDir '...'` to override the default Steam path).
2. Downloads `tools/vineflower.jar` (Vineflower 1.11.1).
3. Copies PZ top-level `*.jar` → `libs/`.
4. Copies PZ class subtrees (`zombie`, `astar`, `com`, `de`, `fmod`, `javax`, `org`, `se`) → `classes-original/`.
5. Re-jars each subtree into `libs/classpath-originals/<name>.jar` for `-classpath` use.
6. Writes `.mod-config.json`.
7. Decompiles `classes-original/zombie` → `src-pristine/zombie` via Vineflower.
8. Scaffolds `mods/` + `.mod-state.json`.

Re-run with `-Force` to redo each step (e.g. after a PZ update; `./mod.ps1 resync-pristine` is the fuller recipe that also re-fingerprints mods).

## Layout

Directories marked **(local-only)** are produced by `./mod.ps1 init` from your Steam install and excluded from git — they are never distributed.

```
PZ-Mod-Work/
├── tools/                      # (local-only) vineflower.jar — downloaded by init
├── libs/                       # (local-only) verbatim PZ jars
│   └── classpath-originals/    # (local-only) class subtrees re-jarred for javac -cp
├── classes-original/           # (local-only) verbatim PZ class trees
├── src/                        # (local-only) Vineflower output; reset by `mod.ps1 enter`
├── src-pristine/               # (local-only) frozen pristine decompile; never hand-edited
├── build/                      # (local-only) javac output + staging
├── mods/
│   └── <name>/                 # mod.json + patches/ (.java.patch / .java.new / .java.delete)
├── lib/
│   └── mod-lib.ps1             # shared helpers
├── build.ps1                   # javac wrapper
├── mod.ps1                     # diff-based mod manager
├── .mod-config.json            # (local-only) pzInstallDir, originalsDir
├── .mod-state.json             # (local-only) install tracking
└── .mod-enter.json             # (local-only) current entered stack
```

## Toolchain

- Decompiler: **Vineflower 1.11.1** (`tools/vineflower.jar`)
- JDK: system Temurin 21 (`javac --release 17` targets Java 17 bytecode — PZ ships JRE 17)
- Build script: `build.ps1` (PowerShell 7+)

## Modding workflow

PZ loads its Java classes from a loose class tree at the install root (`<steam>/common/ProjectZomboid/{zombie,astar,se,...}`). The mod mechanism is: **compile your modified sources, then overwrite the `.class` files in the install**. PZ does not have a Java-mod loader that reads jars from the Mods folder, so this direct-overwrite approach is the simplest working path. `classes-original/` holds a pristine binary copy of every shipped `.class`; `src-pristine/` holds a pristine textual decompile. Install / uninstall use the binary copy; diff / capture / merge use the textual copy.

Each mod is a directory of unified diffs against `src-pristine/`. Requires `git.exe` on PATH.

```powershell
./mod.ps1 init                                     # one-time: re-decompile into src-pristine/
./mod.ps1 new my-mod -Description "..."
./mod.ps1 enter my-mod                             # reset src/, apply patches; edit in src/zombie/
./mod.ps1 capture my-mod                           # diff src/ vs src-pristine/ into mods/my-mod/patches/
./mod.ps1 install my-mod                           # stage + compile + copy to PZ install (elevated)
./mod.ps1 install mod-a mod-b                      # stack — 3-way merges, conflicts abort install
./mod.ps1 uninstall                                # restore whatever the last install wrote
```

Install is atomic — staging or compile failures never touch the PZ install. Running Steam's "Verify Integrity of Game Files" will revert installed overrides — just re-install.

For raw compile (no install), `build.ps1` still works:
```powershell
./build.ps1 src/zombie/Foo.java src/zombie/bar/Baz.java
# -> build/classes/zombie/Foo.class, build/classes/zombie/bar/Baz.class
```

### Don't try to compile the whole tree
Decompiled Java rarely round-trips cleanly (lambdas, generics erasure, obfuscation artifacts). Compile only what you touched. `build.ps1` errors out if you pass no files.

## Why `-sourcepath` is **not** used

If `javac` sees a source tree on `-sourcepath`, it will try to recompile siblings on demand, not just load them from the classpath. Since most decompiled files don't recompile without touch-up, that fails immediately. Instead the build script passes **only** the requested `.java` files and resolves every other symbol from the original class jars on `-classpath`.

## After a PZ update

```powershell
./mod.ps1 resync-pristine
```

Re-runs the full `init` flow with `-Force` (refreshing `classes-original/`, `libs/`, `libs/classpath-originals/`, and `src-pristine/`), then re-fingerprints every mod against the new pristine. Any mod whose patches no longer apply is reported as STALE — re-enter and recapture those one at a time.

## Notes

- `classes-original/` is the binary source of truth for "vanilla" — `install`/`uninstall` use it to restore original `.class` files, and it's also re-jarred into `libs/classpath-originals/` so `javac -cp` can link against unmodified siblings.
- Non-`zombie` class trees (`astar`, `com`, `de`, `fmod`, `javax`, `org`, `se`) are re-jarred for classpath use but **not** decompiled by default. To mod one of those too, run Vineflower against its `classes-original/<subtree>` directly — `init` currently only decompiles `zombie`.
- Java 17 target is enforced via `--release 17` so the output runs on PZ's bundled JRE 17.
- Nothing PZ-owned is tracked in git — `.gitignore` excludes `classes-original/`, `libs/`, `src/`, `src-pristine/`, `tools/vineflower.jar`, and `build/`. See `./mod.ps1 init` above to reconstitute them from your Steam install.
