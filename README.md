<p align="center">
  <img src="assets/necroid.png" width="240" alt="Necroid"/>
</p>

<h1 align="center">Necroid</h1>

<p align="center"><em>Beyond Workshop.</em></p>

<p align="center">
  Mods for <strong>Project Zomboid</strong> that reach parts Steam Workshop can't.
</p>

---

## What is this?

You know how Steam Workshop is great, until you want the zombies to see further, or the admin X-ray to actually work, or a new mod that rewrites how the map loads? Workshop mods can only touch scripts and assets. They can't touch the Java engine underneath. Necroid can.

Necroid ships a bundle of Java-level mods for Project Zomboid plus a small app to install and uninstall them cleanly. Everything is reversible — you can always put the game back exactly how Steam shipped it.

## Install

1. Install these one-time prerequisites:

   | Tool | Windows (`winget`) | macOS (`brew`) | Linux |
   |---|---|---|---|
   | Git | `winget install --id Git.Git -e` | `brew install git` | `apt install git` |
   | JDK 17+ | `winget install EclipseAdoptium.Temurin.17.JDK` | `brew install --cask temurin@17` | `apt install openjdk-17-jdk` |

2. Download the latest release for your OS from <https://github.com/mrkmg/necroid/releases> and unzip anywhere you like.
3. Double-click **necroid** (or `necroid.exe` on Windows). The Necroid window opens.

   > **Windows:** if Project Zomboid is installed under `C:\Program Files (x86)\`, right-click **necroid.exe** → **Run as administrator**. That path is read-only otherwise, and Necroid needs to write new class files there.

## Using Necroid

On first launch, click **Init / Resync** in the top-right. Necroid finds your Steam install, makes a pristine copy of the vanilla Java classes, downloads the decompiler, and sets up the mod workspace. Takes about a minute. You only do this once (and again after a Project Zomboid update).

Then:

- Check the boxes next to the mods you want.
- Click **Install**.
- Launch Project Zomboid as usual.

To roll back, click **Uninstall** — the game goes back to exactly how Steam shipped it.

The mod list updates automatically. If something drifted (e.g. Steam ran a "Verify Integrity of Game Files" pass and reverted everything), just click **Install** again.

## Troubleshooting

- **"javac not found"** — install JDK 17+ (see the table above) and restart Necroid.
- **Permission errors on Windows** — close Necroid, right-click **necroid.exe** → **Run as administrator**.
- **Mods disappeared after a Steam update** — expected. Steam's "Verify Integrity of Game Files" silently reverts everything. Click **Install** in Necroid again.
- **Mod marked STALE after a Project Zomboid update** — the game changed underneath the mod. Click **Init / Resync**, then reinstall your mods. If the mod still won't apply, wait for an updated release.
- **Wrong Project Zomboid install path detected** — edit `data/.mod-config.json` in the Necroid folder and set `clientPzInstall` to your actual install path.

---

## For server operators

Necroid also supports the **Project Zomboid Dedicated Server** (Steam app `380870`, or a local `./pzserver/` install). Each target (client vs. server) has its own workspace, its own install state, and its own set of mods. A mod is authored for one target; the tool hard-errors if you try to apply a client mod to a server install (or vice-versa).

Bootstrap the server profile alongside the client (or instead of it):

```bash
./necroid --target server init
```

Then run any command with `--target server`, or the single-dash shorthand `-server`:

```bash
./necroid --target server list
./necroid --target server install my-server-mod
./necroid --gui -server                   # GUI in server mode
```

All the client troubleshooting applies equally to the server profile.

## CLI reference

Most people never need this — install mods from the GUI and move on. If you're automating, scripting, or running headless on a dedicated-server box, here's the full surface:

```bash
necroid list                         # all mods (off-target rows tagged *client / *server)
necroid install <mod1> [mod2 ...]    # compile + install, stacking multiple mods
necroid uninstall                    # restore everything
necroid uninstall <mod>              # remove one from the stack, rebuild the rest
necroid status                       # working tree vs pristine + installed stack
necroid status <mod>                 # per-mod patch applicability
necroid verify                       # re-hash installed files to detect drift
necroid resync-pristine              # after a PZ update: refresh the vanilla baseline
necroid new <name> -d "..."          # scaffold a new mod
necroid enter <mod1> [mod2 ...]      # reset working tree, apply a mod stack for editing
necroid capture <mod>                # diff working tree vs pristine, rewrite patches
necroid diff <mod>                   # print a mod's patches to stdout
necroid reset                        # mirror pristine -> working tree, clear enter state
```

All target-aware commands accept `--target {client,server}` (or `-server` shorthand). Default comes from `data/.mod-config.json` `defaultTarget`, falling back to `client`.

---

## For developers

### Repo layout

```
necroid/                              # this repo
├── pyproject.toml
├── necroid/                          # Python package (CLI, GUI, commands)
├── packaging/build_dist.py           # PyInstaller builder
├── assets/                           # brand assets (logo, derived icons)
├── data/
│   ├── mods/                         # tracked — the portable patch-set library
│   │   └── <name>/{mod.json, patches/}
│   ├── .mod-config.json              # local-only, written by `init`
│   ├── tools/vineflower.jar          # local-only
│   ├── client/                       # local-only, per-target PZ-sourced content
│   │   ├── src/                      # editable working tree (reset by `enter`)
│   │   ├── src-pristine/             # frozen pristine decompile
│   │   ├── classes-original/         # verbatim PZ classes
│   │   ├── libs/                     # PZ jars + classpath-originals/
│   │   ├── build/                    # javac output + staging
│   │   ├── .mod-state.json
│   │   └── .mod-enter.json
│   └── server/                       # same shape as client/
├── dist/                             # local-only, output of build_dist.py
├── CLAUDE.md, README.md
└── .gitignore
```

Local-only directories are reconstructed from the user's own Steam install by `necroid init`. Nothing PZ-owned ships through git.

### Dev setup

```bash
git clone https://github.com/mrkmg/necroid
cd necroid
pip install -e .                      # puts `necroid` on PATH
necroid init
```

During development, `python -m necroid` works equivalently from the repo root.

### Authoring a mod

```bash
necroid new my-mod -d "does a thing"     # scaffold data/mods/my-mod/
necroid enter my-mod                     # reset src/, apply patches
# ...edit under data/<target>/src/zombie/...
necroid capture my-mod                   # rewrite patches from working tree
necroid install my-mod                   # compile + install; play-test
```

For a stack (`enter mod-a mod-b`), captures always write to the **last** mod in the entered stack. To edit an upstream mod in a stack, re-enter with it last, or enter it alone.

After a PZ update, run `necroid resync-pristine` and `enter` each STALE mod to resolve the new conflicts.

### Building a release

Tag-driven. GitHub Actions ([.github/workflows/release.yml](.github/workflows/release.yml)) builds on Windows/Linux/macOS runners and publishes the release:

```bash
# 1. Bump the version in BOTH files (they must match or CI fails):
#       necroid/__init__.py    -> __version__ = "X.Y.Z"
#       pyproject.toml         -> version = "X.Y.Z"
# 2. Commit, tag, push:
git commit -am "Release vX.Y.Z"
git tag vX.Y.Z
git push && git push --tags
```

The workflow fans out to five runners and attaches five zips to the release:

- `necroid-vX.Y.Z-windows-x64.zip`
- `necroid-vX.Y.Z-linux-x64.zip`
- `necroid-vX.Y.Z-linux-arm64.zip`
- `necroid-vX.Y.Z-macos-x64.zip` (Intel Macs)
- `necroid-vX.Y.Z-macos-arm64.zip` (Apple Silicon)

Each zip unpacks to `necroid(.exe)` + `data/mods/` + `README.txt`. Release notes are auto-generated from commits since the previous tag.

For a local build on the current OS (no tag, no release):

```bash
pip install pyinstaller
python packaging/build_dist.py
# produces dist/necroid(.exe) + dist/data/mods/ + dist/README.txt
# plus dist-archives/necroid-vX.Y.Z-<platform>-<arch>.zip
```

PyInstaller does not cross-compile — `build_dist.py` produces only the current-OS binary. Vineflower is bundled into the binary; at runtime `data/tools/vineflower.jar` auto-downloads if missing. End users only need Git and a JDK 17.

macOS builds are unsigned; users will see a Gatekeeper warning on first launch (right-click → Open to bypass). Apple Developer ID signing / notarization is not in scope yet.

### Regenerating brand assets

Source of truth: `assets/necroid.png`. Derived files (`necroid-mark-256.png`, `necroid-icon-256.png`, `necroid-icon.ico`) are committed. To regenerate after a logo edit:

```bash
bash assets/build-assets.sh           # requires ImageMagick (`magick` on PATH)
```

End users and the release build do **not** need ImageMagick.

### Architecture deep-dive

See [CLAUDE.md](CLAUDE.md) for: directory roles, install atomicity, javac constraints, target-mismatch rules, the PZ update flow, and what looks like bugs but isn't (decompiler quirks).

### License

[Unlicense](https://unlicense.org) — public domain.
