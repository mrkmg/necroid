<p align="center">
  <img src="assets/necroid.png" width="240" alt="Necroid"/>
</p>

<h1 align="center">Necroid</h1>

<p align="center"><em>Beyond Workshop.</em></p>

<p align="center">
  Java Mods for <strong>Project Zomboid</strong> that reach parts Steam Workshop can't.
</p>

---

## What is this?

You know how Steam Workshop is great, until you want change how radios work, have an admin X-ray that actually works, or a write a new mod that rewrites how the map loads? Workshop mods can only touch lua scripts and assets. They can't touch the Java engine underneath. Necroid can.

Necroid ships a bundle of Java-level mods for Project Zomboid plus a small app to install and uninstall them cleanly. Everything is reversible — you can always put the game back exactly how Steam shipped it.

<p align="center">
  <img src="assets/necroid-screenshot-v0.4.0.png" alt="Necroid GUI"/>
</p>

## Bundled mods

Each mod ships with its own README — click through for behaviour notes, in-game commands, and compatibility caveats.

| Mod | Client-only? | What it does |
|---|---|---|
| [admin-xray](data/mods/admin-xray-41/README.md) | yes | Staff render overrides — **F9** for LOS / wall-cutaway, **Shift+F9** for transparent trees. |
| [gravymod](data/mods/gravymod-41/README.md) | no | Adds various lua utils and commands. |
| [lua-profiler](data/mods/lua-profiler-41/README.md) | no | Per-mod Lua profiler with event/builtin/sample modes. Flame-graph output + mod/file filter. |
| [more-zoom](data/mods/more-zoom-41/README.md) | yes | Adds one extra zoom-out (300%) and one extra zoom-in (25%) level. |
| [multi-login](data/mods/multi-login-41/README.md) | no | Server-side: login queue admits N players concurrently instead of one at a time. Adds `MaxConcurrentLogins` (1–32, default 3). Depends on staff-priority. |
| [no-radio-fzzt](data/mods/no-radio-fzzt-41/README.md) | no | Disable all radio obfuscation (weather interference + distance falloff + scramble pipeline). Install to client or server. |
| [staff-priority](data/mods/staff-priority-41/README.md) | no | Staff (admin / moderator / overseer / gm / observer) skip ahead of VIPs and regular players in the login queue when the server is full. |
| [transparent-all](data/mods/transparent-all-41/README.md) | yes | Extends admin-xray's Shift+F9 to fade **every** static world object on screen — doors, windows, walls, mannequins, fences, fridges — not just trees. Depends on admin-xray. |
| [weather-flash-fix](data/mods/weather-flash-fix-41/README.md) | yes | Stops the 10-minute weather-resync flash when a Lua mod (e.g. Wasteland) is overriding client climate values. |

"Client-only" mods require a Project Zomboid **client** install and can only be installed to the client. Non-client-only mods can install to either the client or the Dedicated Server.

Some mods declare **dependencies** on other mods (e.g. `transparent-all` depends on `admin-xray`). Necroid pulls the full dependency closure into the install stack automatically — you don't need to tick the dep yourself, and the GUI will offer to tick / untick dependents when you toggle a dep.

In the Necroid GUI, click the **ⓘ** next to any mod to read its README without leaving the app.

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

On first launch, click **Set Up** in the top-right. Necroid finds your Steam install, makes a pristine copy of the vanilla Java classes, downloads the decompiler, and sets up the mod workspace. Takes about a minute. You only do this once (and again after a Project Zomboid update — the same button becomes **Update from Game**).

The mod list is **state-based**: the checkboxes automatically reflect what's currently installed on the chosen destination (client or server). Just tick what you want installed and untick what you don't:

- Check / uncheck mods to match your desired set.
- Click **Apply Changes**. Necroid works out the diff and installs anything added / uninstalls anything removed in a single atomic step.
- Launch Project Zomboid as usual.

The Status column previews the diff before you apply: `installed`, `pending add`, `pending remove`, `available`. **Revert** discards pending edits and snaps the checkboxes back to what's actually installed. Switching the **Install to** toggle (client ↔ server) re-reads state for that destination — client and server have independent stacks.

To roll back everything, uncheck every box and click **Apply Changes** — the game goes back to exactly how Steam shipped it.

If something drifted (e.g. Steam ran a "Verify Integrity of Game Files" pass and reverted everything), the checkboxes will still show what Necroid thinks is installed. Click **Apply Changes** once to re-install, or uncheck and re-check a mod to force a reapply.

## Updates

Necroid checks GitHub Releases for a newer version once a day in the background.

- **GUI**: a yellow banner appears below the header when an update is available — click **Install Update** and Necroid downloads the new binary, swaps it in place, and closes. Re-open it to start using the new version. Click **Dismiss** to ignore the banner for the current session.
- **CLI**: a one-line `update available: vX.Y.Z → vA.B.C (run: necroid update)` notice prints after any command (silenced when stderr is redirected — scripts stay clean). Run `necroid update` to apply, `necroid update --check` to just check, or `necroid update --rollback` to swap back to the previous binary if something went wrong. Set `NECROID_NO_UPDATE_CHECK=1` to disable background checks entirely.

Self-update only works for the packaged binary you downloaded from the [Releases page](https://github.com/mrkmg/necroid/releases). If you installed from source (`pip install -e .`), use `git pull` instead. On Windows, if Necroid lives under `C:\Program Files`, the update needs an elevated shell — run as administrator. Your installed mods, your Project Zomboid install, and `data/.mod-config.json` are never touched by the updater; only the `necroid` binary itself is replaced.

## Troubleshooting

- **"javac not found"** — install JDK 17+ (see the table above) and restart Necroid.
- **Permission errors on Windows** — close Necroid, right-click **necroid.exe** → **Run as administrator**.
- **Mods disappeared after a Steam update** — expected. Steam's "Verify Integrity of Game Files" silently reverts everything. Click **Apply Changes** in Necroid to push your stack back into the install (uncheck + re-check one mod first if the button is disabled — Necroid's state file still matches what Steam overwrote).
- **Mod marked STALE after a Project Zomboid update** — the game changed underneath the mod. Click **Update from Game**, then Apply Changes to reinstall your stack. If the mod still won't apply, wait for an updated release.
- **Wrong Project Zomboid install path detected** — edit `data/.mod-config.json` in the Necroid folder and set `clientPzInstall` to your actual install path.

## Config file

`data/.mod-config.json` is written by `necroid init` and stores your install paths plus a couple of defaults. You only need to edit it by hand if auto-detection picked the wrong Steam install or you want to change the default destination.

```json
{
  "version": 3,
  "clientPzInstall": "C:/Program Files (x86)/Steam/steamapps/common/ProjectZomboid",
  "serverPzInstall": "C:/Program Files (x86)/Steam/steamapps/common/Project Zomboid Dedicated Server",
  "defaultInstallTo": "client",
  "workspaceSource": "client"
}
```

- `clientPzInstall` / `serverPzInstall` — absolute paths to each PZ install. Leave either empty (`""`) if you don't have it. Forward slashes on all OSes. `~`, `$VAR`, `%VAR%`, and `${VAR}` are expanded.
- `defaultInstallTo` — `client` or `server`. Used when you omit `--to` on `install` / `uninstall` / `verify` / `list` / `status`.
- `workspaceSource` — `client` or `server`. Which install seeded `data/workspace/`. `resync-pristine` re-hydrates from this unless you pass `--from`.
- `originalsDir` *(optional)* — override for the verbatim class-copy directory. Leave unset unless you know why you need it.

---

## For server operators

Necroid also supports the **Project Zomboid Dedicated Server** (Steam app `380870`, or a local `./pzserver/` install). One shared workspace serves both — install destination is chosen per-install with `--to client|server` (default from `data/.mod-config.json` `defaultInstallTo`).

If you only have the dedicated server, bootstrap the workspace from it:

```bash
./necroid init --from server
```

Then install / uninstall / verify against whichever destination you care about:

```bash
./necroid list --to server
./necroid install my-non-clientonly-mod --to server
./necroid uninstall --to server
./necroid --gui -server                   # GUI opens with install-to = server selected
```

Mods flagged `clientOnly: true` cannot install to the server — they need the game's rendering path. Everything else installs to either.

## CLI reference

Most people never need this — install mods from the GUI and move on. If you're automating, scripting, or running headless on a dedicated-server box, here's the full surface:

```bash
necroid list                         # all mods (Client-only? column)
necroid install <mod1> [mod2 ...]    # compile + install, stacking multiple mods
necroid uninstall                    # restore everything for the chosen destination
necroid uninstall <mod>              # remove one from the stack, rebuild the rest
necroid status                       # working tree vs pristine + installed stacks (client + server)
necroid status <mod>                 # per-mod patch applicability
necroid verify                       # re-hash installed files to detect drift
necroid resync-pristine              # after a PZ update: refresh the vanilla baseline
necroid update                       # check + self-update from GitHub Releases (packaged binary only)
necroid update --check               # check only, don't download
necroid update --rollback            # swap back to the previous binary
necroid new <name> -d "..." [--client-only]  # scaffold a new mod
necroid enter <mod1> [mod2 ...]      # reset working tree, apply a mod stack for editing
necroid capture <mod>                # diff working tree vs pristine, rewrite patches
necroid diff <mod>                   # print a mod's patches to stdout
necroid reset                        # mirror pristine -> working tree, clear enter state
```

Per-command flags:

- `init` / `resync-pristine` take `--from {client,server}` (which PZ install seeds the shared workspace).
- `install` / `uninstall` / `verify` / `list` / `status` take `--to {client,server}` (install destination).
- `enter` takes `--as {client,server}` (postfix variant to apply when the mod ships per-destination variants — rare).

Defaults come from `data/.mod-config.json` (`defaultInstallTo`, `workspaceSource`), falling back to `client`.

---

---

## Why is this different?

Necroid is a diff-based mod manager. It works by making a pristine copy of the vanilla Java classes, then applying mods as patches on top. To uninstall, it just deletes the patched classes and copies the pristine ones back in. No file-level patching, no bytecode rewriting, no classloader shenanigans.

Necroid does not ship any Project Zomboid files, bytecode, or decompiled sources. This ensures Necroid is legally safe and provides a very easy way to update these mods for small version changes in PZ. When PZ updates, just refresh the pristine baseline and re-apply the patches. If the update is small, the patches will mostly apply cleanly with a few manual tweaks. If the update is large, the patches won't apply at all, but you can still use them as a reference for what changed and how to fix it.. then make a PR with the fixes :-)

Finally, it's safer for end users. No random .class files downloaded from the internet that you just have to trust. All mods in necroid are source-code, reviewable, and built locally on your machine.

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
│   ├── .mod-enter.json               # local-only: currently entered mod + install_as
│   ├── .mod-state-client.json        # local-only: last install to client destination
│   ├── .mod-state-server.json        # local-only: last install to server destination
│   ├── tools/vineflower.jar          # local-only
│   └── workspace/                    # local-only, one shared PZ-sourced workspace
│       ├── src-pristine/             # frozen pristine decompile
│       ├── classes-original/         # verbatim PZ classes (identical client/server)
│       ├── libs/                     # PZ jars + classpath-originals/
│       └── build/                    # javac output + staging
├── src-<modname>/                    # local-only, per-mod editable tree (one per entered mod)
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
necroid new my-mod -d "does a thing"     # scaffold data/mods/my-mod/ (add --client-only if it is)
necroid enter my-mod                     # seed src-my-mod/ from pristine + patches (or preserve if exists)
# ...edit under src-my-mod/zombie/...
necroid capture my-mod                   # rewrite patches from working tree
necroid test                             # javac-only compile, no install (fast sanity check)
necroid install my-mod --to client       # compile + install; play-test
necroid clean my-mod                     # (optional) delete src-my-mod/ when done
```

Only one mod is entered at a time. Each mod gets its own `src-<name>/` tree at the repo root, so switching between mods via `necroid enter other-mod` preserves in-progress edits on the previous tree. Use `necroid clean` (with or without a mod name) to delete trees, and `necroid reset` to re-seed the currently entered mod's tree from pristine + patches. Stacking (`install mod-a mod-b …`) still works for install-time composition — only `enter` is single-mod.

After a PZ update, run `necroid resync-pristine` and `enter` each STALE mod (or `reset` it) to resolve the new conflicts.

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

See [CLAUDE.md](CLAUDE.md) for: directory roles, install atomicity, javac constraints, clientOnly rules, the PZ update flow, and what looks like bugs but isn't (decompiler quirks).

### License

[Unlicense](https://unlicense.org) — public domain.
