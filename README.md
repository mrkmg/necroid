# PZ Mod Work

Cross-platform workspace for editing decompiled Project Zomboid classes and overwriting them in the Steam install. Supports both the **client** (Project Zomboid) and the **dedicated server** (Project Zomboid Dedicated Server).

**Only your local Steam install is ever modified** — nothing PZ-owned is distributed in this repo. A fresh clone contains the Python tool, mod patches, and docs; everything PZ-owned (jars, classes, decompiled sources) is reconstructed locally from your own Steam install by `pz-java-modder init`.

## First-time setup

Install the toolchain:

| Tool | Windows (`winget`) | macOS (`brew`) | Linux |
|---|---|---|---|
| Git | `winget install --id Git.Git -e` | `brew install git` | `apt install git` |
| JDK 17+ | `winget install EclipseAdoptium.Temurin.17.JDK` | `brew install --cask temurin@17` | `apt install openjdk-17-jdk` |
| Python 3.10+ | `winget install Python.Python.3.12` | (preinstalled) | (preinstalled or `apt install python3`) |

Own a Steam copy of Project Zomboid (client, dedicated server, or both).

```bash
git clone <this-repo> PZ-Mod-Work
cd PZ-Mod-Work
python -m pz_java_modder init                    # client (default)
python -m pz_java_modder --target server init    # optional: dedicated server
```

`init` does everything in one shot (≈1 min per target):

1. Locate the PZ install (Steam default path, `data/.mod-config.json`, or `--pz-install '...'`).
2. Check `git`, `java`, `javac`, `jar` are on PATH.
3. Download `data/tools/vineflower.jar` (Vineflower 1.11.1).
4. Copy PZ top-level `*.jar` → `data/<target>/libs/`.
5. Copy PZ class subtrees (`zombie`, `astar`, `com`, `de`, `fmod`, `javax`, `org`, `se`) → `data/<target>/classes-original/`.
6. Re-jar each subtree into `data/<target>/libs/classpath-originals/<name>.jar` for `-classpath` use.
7. Write `data/.mod-config.json`.
8. Decompile `classes-original/zombie` → `data/<target>/src-pristine/zombie` via Vineflower.
9. Scaffold `data/mods/` + `data/<target>/.mod-state.json`.

Pass `--force` to redo each step (e.g. after a PZ update — though `resync-pristine` is the fuller recipe).

## The short version

```bash
# CLI (devs, scripting)
pz-java-modder list                                  # all mods (tagged client/server)
pz-java-modder new my-mod -d "does a thing"          # scaffold mods/my-mod/
pz-java-modder enter my-mod                          # src/ ← pristine + my-mod's patches
# …edit under data/client/src/zombie/…
pz-java-modder capture my-mod                        # rewrite patches from working tree
pz-java-modder install my-mod                        # compile + atomic install
pz-java-modder uninstall                             # restore everything
pz-java-modder verify                                # re-hash installed files

# GUI (end users)
pz-java-modder --gui                                 # client
pz-java-modder --gui -server                         # dedicated server
```

During development, use the `python -m pz_java_modder` form from the repo root (or `pip install -e pz-java-modder/` to put `pz-java-modder` on PATH).

For distribution, run `python pz-java-modder/packaging/build_dist.py` to produce a `dist/` folder containing a self-contained binary plus the bundled mods directory — hand that to anyone who doesn't want to install Python themselves.

## Layout

Directories marked **(local-only)** are produced by `init` from your Steam install and excluded from git.

```
PZ-Mod-Work/
├── pz-java-modder/                   # Python source (tracked)
│   ├── pyproject.toml
│   ├── pz_java_modder/               # package
│   └── packaging/build_dist.py       # PyInstaller builder
├── data/
│   ├── .mod-config.json              # (local-only) clientPzInstall, serverPzInstall, defaultTarget
│   ├── mods/                         # tracked — the portable patch-set library
│   │   └── <name>/{mod.json, patches/}
│   ├── tools/                        # (local-only) vineflower.jar
│   ├── client/                       # (local-only) per-target PZ-sourced content
│   │   ├── src/                      # Vineflower output; reset by `enter`
│   │   ├── src-pristine/             # frozen pristine decompile
│   │   ├── classes-original/         # verbatim PZ classes
│   │   ├── libs/                     # verbatim PZ jars + classpath-originals/
│   │   ├── build/                    # javac output + staging
│   │   ├── .mod-state.json           # install tracking
│   │   └── .mod-enter.json           # current entered stack
│   └── server/                       # (local-only) same shape as client/
├── dist/                             # (local-only) output of build_dist.py
├── CLAUDE.md, README.md
└── .gitignore
```

Every mod is a directory of unified diffs against `src-pristine/`, plus a `mod.json` declaring `target: "client" | "server"`. The `patches/` subtree mirrors the package layout: e.g. `mods/lua-profiler/patches/zombie/Lua/Event.java.patch`. `.java.new` is a full replacement file for new classes; `.java.delete` marks a file for removal.

## Modding workflow

PZ loads its Java classes from a loose class tree at the install root (`<steam>/common/ProjectZomboid/{zombie,astar,se,...}` for the client, `common/Project Zomboid Dedicated Server/...` for the server). The mod mechanism is: **compile your modified sources, then overwrite the `.class` files in the install.** PZ has no Java-mod loader reading jars from the Mods folder, so direct overwrite is the simplest working path.

`classes-original/` holds a pristine binary copy of every shipped `.class`; `src-pristine/` holds a pristine textual decompile. Install/uninstall use the binary copy; diff/capture/merge use the textual copy. All of this is per-target — editing a server class goes into `data/server/src/...`, installs to the dedicated-server install, and is tracked in `data/server/.mod-state.json`.

```bash
pz-java-modder new my-mod -d "..."                   # scaffold mods/my-mod/
pz-java-modder enter my-mod                          # reset src/, apply patches; edit in src/zombie/
pz-java-modder capture my-mod                        # rewrite patches from working tree
pz-java-modder install my-mod                        # stage + compile + copy to PZ install (needs write access)
pz-java-modder install mod-a mod-b                   # stack — 3-way merges, conflicts abort install
pz-java-modder uninstall                             # restore whatever the last install wrote
```

Install is atomic — staging or compile failures never touch the PZ install. Running Steam's "Verify Integrity of Game Files" will revert installed overrides — just re-install.

### Write access to the PZ install

- **Windows + Program Files:** run your terminal as Administrator. The GUI detects `PermissionError` and offers a "Relaunch as admin" button.
- **Linux / macOS:** the default Steam paths (`~/.steam/steam/...`, `~/Library/Application Support/Steam/...`) are user-writable. No elevation needed.

### Target-mismatch rules

- `install foo` / `enter foo` / `capture foo` / `diff foo` whose mod's `target` differs from the active profile → **hard error** (retry with `--target <other>`).
- `install` with no named mods → silently filters to active target.
- `list` / `status` show all mods; off-target rows are marked with a `*` prefix (e.g. `*server`).
- GUI in client mode **hides** server-target mods; server-launched GUI hides client ones.

## Don't try to compile the whole tree

Decompiled Java rarely round-trips cleanly (lambdas, generics erasure, obfuscation artifacts). The installer compiles only the files a mod touches. If you're calling `javac` by hand for a sanity check, pass only the specific files you edited.

`pz-java-modder` deliberately does not set `-sourcepath`: with a sourcepath, `javac` would try to recompile sibling decompiled files on demand and fail. Every non-modified symbol resolves from the original class jars in `libs/classpath-originals/`.

## After a PZ update

```bash
pz-java-modder resync-pristine                      # client
pz-java-modder --target server resync-pristine      # server
```

Re-runs the `init` flow with `--force` (refreshing `classes-original/`, `libs/`, `libs/classpath-originals/`, and `src-pristine/`), then re-fingerprints every mod against the new pristine. Any mod whose patches no longer apply is reported as STALE — re-enter and recapture those one at a time.

## Building a distributable

```bash
pip install pyinstaller
python pz-java-modder/packaging/build_dist.py
```

Produces `dist/pz-java-modder(.exe)` + `dist/data/mods/` + `dist/README.txt`. PyInstaller can't cross-compile; run the build on each OS you care about. Vineflower is bundled into the binary and self-extracts on first run — the end user only needs `git` and a JDK 17.

## What's tracked and what's not

Tracked:
- `pz-java-modder/` (Python source)
- `data/mods/` (patch-set library; the portable artifact)
- `CLAUDE.md`, `README.md`, `.gitignore`

Local-only (gitignored):
- `data/client/`, `data/server/` (per-target PZ-sourced content)
- `data/tools/*` (except `.gitkeep`)
- `data/.mod-config.json`
- `dist/`, `pz-java-modder/build/`, `pz-java-modder/dist/`
- Python caches

## Notes

- `classes-original/` is the binary source of truth for "vanilla" — `install`/`uninstall` use it to restore original `.class` files, and it's also re-jarred into `libs/classpath-originals/` so `javac -cp` can link against unmodified siblings.
- Non-`zombie` class trees (`astar`, `com`, `de`, `fmod`, `javax`, `org`, `se`) are re-jarred for classpath use but **not** decompiled by default. To mod one of those, run Vineflower against its `classes-original/<subtree>` directly — `init` currently only decompiles `zombie`.
- Java 17 target is enforced via `--release 17` so the output runs on PZ's bundled JRE 17.
- Older PowerShell-era workspaces are preserved at the `v1-final` git tag. The rewrite removed `mod.ps1`, `build.ps1`, and `lib/mod-lib.ps1`.
