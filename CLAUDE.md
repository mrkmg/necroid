# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

**Necroid** — a Project Zomboid mod manager, not a normal Java project. Source trees under `data/workspace/src/zombie/` are **decompiled** output from PZ's shipped class files (via Vineflower 1.11.1). The goal is: edit individual classes, recompile them targeting Java 17, then **overwrite the `.class` files directly in the PZ install**. PZ loads its Java classes from a loose class tree at the install root (`<steam>/common/ProjectZomboid/{zombie,astar,se,...}`), so replacing a `.class` file there is the mod mechanism. PZ does **not** have a Java-mod loader that picks up jars from the Mods folder — a jar-based approach would require writing our own classloader, which we aren't doing.

**Single shared workspace.** The client (`ProjectZomboid`, Steam app 108600) and dedicated server (`Project Zomboid Dedicated Server`, Steam app 380870, or `./pzserver/`) ship byte-identical Java class trees. Necroid therefore keeps **one** workspace at `data/workspace/{src,src-pristine,classes-original,libs,build}/`, seeded from whichever PZ install the user points `init` at (`--from client` or `--from server`). The chosen source is recorded in `config.workspaceSource`.

**Install destinations are per-invocation.** `necroid install <stack> --to client|server` chooses where the compiled `.class` files land. Each destination has its own state file: `data/.mod-state-client.json` and `data/.mod-state-server.json`. Both can coexist — a user may have, say, admin-xray installed to client and gravymod installed to server at the same time.

**Mods carry a `clientOnly` flag.** In `mod.json`, `clientOnly: true` means the mod requires a configured client PZ install and cannot be installed to the server (it relies on client-only rendering / input code). `clientOnly: false` (default) means the mod works against either destination. There is no per-mod "target" any more.

**Workspace is bound to one PZ major version.** Every PZ major (41, 42, ...) decompiles to an incompatible source tree — a 41-authored patch set cannot apply to 42 pristine and vice versa. Necroid handles this by binding a workspace to exactly one major at `init` time (detected via a tiny Java probe that reads `zombie.core.Core.gameVersion` + `Core.buildVersion` via reflection — see `necroid/pzversion.py`). The bound major is stored in `config.workspaceMajor`; the full version string (e.g. `"41.78.19"`) is stored in `config.workspaceVersion`.

**Mod dirs encode their PZ major in the name.** `mods/<base>-<major>/` — e.g. `admin-xray-41`. The `-<major>` suffix is authoritative: `list`, `status`, `install`, `enter`, and the GUI filter mods against `workspaceMajor` and refuse to touch incompatible variants. `necroid install admin-xray` resolves bare bases against the workspace major (so `admin-xray` → `admin-xray-41` if that's what the workspace is bound to). `mod.json` additionally stamps `expectedVersion` (the full PZ version at the time of last `capture`) for soft minor/patch-drift warnings — the dir suffix is the hard gate, `expectedVersion` is the recapture hint.

**Major changes require an explicit opt-in.** `necroid resync-pristine` refuses to silently re-bind the workspace to a different major when the source install has moved (e.g. 41 → 42). Pass `--force-major-change` to acknowledge that every existing mod's patches will need to be re-captured against the new pristine. The old-major mod dirs are left on disk (filtered out of default views); re-enter and re-capture each to port.

Uninstall restores originals from `data/workspace/classes-original/` (verbatim copy of the install's class tree) — that directory is the single source of truth for "what the vanilla class looks like", and **must not be edited**.

Writing to `C:\Program Files (x86)\...` requires an elevated shell. If Steam "Verify Integrity of Game Files" is run, it will revert any installed overrides — just re-run `necroid install <stack> --to <dest>` afterwards (or `necroid doctor --to <dest>` first to see exactly what Steam changed).

**Steam's file management is asymmetric — the integrity system has strong implications for this tool.** Steam's Verify / patch-update flows only touch files that appear in Steam's manifest for the current PZ build. That means:

- Files Necroid **overwrote** (e.g. `zombie/core/Core.class`) may be rewritten by Steam back to vanilla — either the *same* vanilla (Verify on an unchanged build) or a *different-version* vanilla (patch update). Necroid can't tell those apart without hash evidence.
- Files Necroid **added** (e.g. `zombie/gravymod/GravyMain.class` compiled from a mod's `.java.new`) are not in Steam's manifest, so Steam leaves them alone forever. They can outlive uninstalls, PZ reinstalls, and major-version changes if Necroid state ever gets lost.

This is the root reason for the install-side manifest (below): without an authoritative record stamped into the install, a `resync-pristine` can't distinguish "Steam reverted my overwrite to the new-version vanilla" (dangerous — would poison `classes-original/`) from "Steam left everything alone" (safe). See the **Install-side manifest** and **When a PZ update lands** sections.

Mods are diff-based: each mod is a directory of unified diffs under `mods/<name>/patches/`, authored against the frozen pristine decompile at `data/workspace/src-pristine/`. Multiple mods touching the same file combine via 3-way merge at install time. See `necroid --help`.

**Mods can declare relationships** via `mod.json`:

- `dependencies: ["admin-xray"]` — list of bare names (no `-<major>` suffix; resolved against the workspace major at command time). At `enter` time the dep's patches are applied first and the dependent's edits sit on top; at `capture` time the diff is taken against **pristine + applied deps** (built in an ephemeral `data/workspace/build/capture-baseline/<mod>/` tree), so the dependent's own `patches/` contain only what it adds *beyond* its deps. At `install` time the full dep closure is expanded in topo order (deps before dependents). A dependent's effective `clientOnly` is true if any transitive dep is `clientOnly`.
- `incompatibleWith: ["rival-mod"]` — list of bare names. Either side's declaration is enough to reject a stack; `install`, `enter`, and the GUI all fail fast with `ModIncompatibility`.

CLI surface for relationships: `necroid new --depends-on X --incompatible-with Y`, `necroid deps show <mod>`, `necroid deps add|remove <mod> --requires|--conflicts <other>`, `necroid uninstall <mod> --cascade` (cascades to any dependents still in the installed stack rather than erroring). GUI auto-pulls deps on check, prompts "also uncheck N dependents?" on uncheck, and refuses an incompatible check with a flashed tooltip. Relationship errors all inherit `PzModderError`: `ModDependencyMissing`, `ModIncompatibility`, `ModDependencyCycle`.

**Mods can be imported from GitHub repos and refreshed in place.** Discovery only accepts the canonical Necroid layout: `<root>/mods/<name>-<major>/mod.json`. That's what `necroid init` scaffolds in every author's repo, so every published Necroid mod looks the same. Other shapes (single-mod-at-root, flat-container, arbitrary nesting) are rejected with a clear error pointing at the canonical layout. The mod's canonical `<base>-<major>` dirname is **preserved verbatim** from upstream — never re-suffixed against the workspace. Per-major variants (`admin-xray-41` and `admin-xray-42`) coexist in the same repo on the same branch, and import filters to mods matching the workspace major by default (`--include-all-majors` to override). Bare-name `--mod` selectors (`--mod admin-xray`) auto-resolve to `<base>-<workspaceMajor>`; fully-qualified selectors (`--mod admin-xray-42`) match exactly and trigger pre-flight rejection on major mismatch. An `origin` block is stamped into `_extra` (rides the existing forward-compat dict in `mod.py:ModJson`):

```json
"origin": {
  "type": "github", "repo": "owner/name", "ref": "main",
  "subdir": "mods/admin-xray",
  "commitSha": "<40-hex>", "archiveUrl": "https://codeload.github.com/...",
  "importedAt": "<ISO-8601 UTC>", "upstreamVersion": "0.3.1"
}
```

`subdir` is always `mods/<dirname>` (the canonical layout). The presence of an `origin` block is what `necroid mod-update` uses to decide which mods are eligible for refresh. CLI surface: `necroid import <repo> [--ref] [--mod ...] [--all] [--list] [--json] [--name] [--force] [--include-all-majors]` and `necroid mod-update [name] [--check] [--force] [--include-peers] [--json]`. Errors: `ModImportError`, `ModUpdateError` (both inherit `PzModderError`). Implementation: `necroid/github.py` (URL parse, SHA resolve, archive fetch, `discover_mods()`), `necroid/commands/import_cmd.py`, `necroid/commands/mod_update.py`. Network: `urllib` only — no `git clone`, no extra deps. ≤2 GitHub REST calls per import (`/repos/{o}/{r}` only when `--ref` absent for the default branch, then `/commits/{ref}` for SHA); the archive itself is codeload, which is not REST-rate-limited. Per-mod commit is atomic via `<target>.new` rename so a failure mid-loop leaves earlier successes intact. `mod-update` groups targets by `(repo, ref)` so one archive download serves N peer mods. `mod-update` does **not** re-check the mod major — the dirname is the source of truth and never changes during refresh. A mod that is currently `enter`-ed is refused for update — clean first.

**Per-major variants are siblings, not branches.** A repo can ship `admin-xray-41/` and `admin-xray-42/` on the same `main` branch. When PZ moves from 41 → 42, mod authors keep the 41 dir untouched and add a sibling 42 dir; users on each major pull the appropriate variant via `mod-update` against the same `(repo, ref)`. This avoids the per-game-major branching tax and keeps fixes shipping for old majors as long as the author cares to maintain them.

`mod-update --check` writes results into `data/.update-cache-mods.json` (24h TTL, schema v1, gitignored). The GUI reads this cache to decorate the Version column with `⬆ <new>` badges and to drive the "N updates available" status-strip chip; the cache file is the single source of truth for "is upstream newer". The GUI also runs `mod-update --check` from the **Check Updates** header button and from the per-mod right-click menu.

**Bundled mods participate in the same `mod-update` flow.** `packaging/build_dist.py:stamp_bundled_origins` walks the `mods/` tree being copied into the dist and stamps each mod's `mod.json` with `origin = {repo: "mrkmg/necroid", ref: "main", subdir: "mods/<dirname>", commitSha: "", ...}`. The empty `commitSha` is by design — the first `mod-update --check` resolves the real SHA and writes it back to the local `mod.json` (in the up-to-date branch of `_process_one`, mirroring the apply branch). Subsequent runs short-circuit the archive download via the SHA-equality check in `_process_group`. The source-tree `mods/*/mod.json` files are **not** stamped — they have no origin, so source installs (`pip install -e .`) treat them as local; only the dist-shipped variants carry the origin block. Users can "unbind" a bundled mod by deleting its `origin` block — it then behaves like a hand-authored mod and is ignored by `mod-update`. Stamp logic is idempotent and skips any mod that already has an origin (so re-runs and forks don't clobber).

**Install-side manifest is the authoritative record.** `<pz_install>/.necroid-install.json` (or `<pz_install>/java/.necroid-install.json` for the dedicated server) is written by every `necroid install` and deleted by every full `necroid uninstall`. It's the source of truth for "what has Necroid done to this install". The per-destination local file `data/.mod-state-<dest>.json` is now a **cache** — fast to read for CLI/GUI, but the install-side manifest wins on any disagreement.

The manifest lets Necroid detect four categories of tampering that the old state-only model could not:

1. **Steam Verify / patch-update** — the install file's bytes differ from both `writtenSha256` (what we wrote) and `originalSha256` (what was there when we installed). Classified as `NEW_VERSION_DRIFT`. Resync refuses to silently adopt unless `--force-version-drift` is passed.
2. **PZ reinstall / Steam uninstall+reinstall** — the manifest is gone but local cache says a stack is installed. Classified as `WIPED` (if no recorded files exist on disk) or `LEGACY_UNMIGRATED` (if they do — i.e. the install predates the install-side manifest, treated as a soft migration path). The latter is expected on any workspace upgraded from pre-v2 Necroid; the first install/uninstall after upgrade seeds the manifest.
3. **Two-workspace collision** — a second Necroid checkout (or a cloned / moved workspace) installing to the same PZ install stamps a different `workspace.fingerprint`. Any command that needs to write refuses unless `--adopt-install` is passed, which takes ownership and invalidates the other workspace's state.
4. **Manual tamper / orphaned files** — `.class` files under a mod-touched subtree that are in neither the manifest nor `classes-original/` (user hand-patched, or a prior crash between deploy and state-write). The orphan scan catches these.

Manifest schema v1 (written by `necroid/core/install_manifest.py`):

```json
{
  "schemaVersion": 1,
  "workspace": {
    "fingerprint": "<hex from config.workspaceFingerprint>",
    "workspaceDir": "C:\\path\\to\\PZ-Mod-Work",
    "workspaceMajor": 41
  },
  "destination": "client",
  "pzVersionAtInstall": "41.78.19",
  "installedAt": "<ISO UTC>",
  "stack": [{"dirname": "admin-xray-41", "version": "0.3.1"}],
  "files": [
    {"rel": "zombie/Foo.class",
     "writtenSha256": "<hex>",
     "originalSha256": "<hex>|null",
     "wasAdded": false,
     "modOrigin": "admin-xray-41"}
  ]
}
```

**Workspace fingerprint** (`config.workspaceFingerprint`, a sha256 over workspaceDir + timestamp + random salt) is minted once at `init` and persists across CLI invocations. Workspaces upgraded from pre-v2 Necroid get a fingerprint stamped on the first install via the `_ensure_workspace_fingerprint` path in `install.py` — no user action needed. The fingerprint is what distinguishes two different Necroid checkouts pointing at the same PZ install; the install-side manifest carries it so the comparison is possible at any later command.

**Reconciliation matrix** (`install_manifest.reconcile()`): read at the start of `install` / `uninstall` / `verify` / `doctor` / `resync-pristine`. Returns one of `CLEAN`, `FIRST_TIME`, `WIPED`, `LEGACY_UNMIGRATED`, `FINGERPRINT_MISMATCH`, `CACHE_STALE`, `TAMPERED`. Callers decide whether each is a hard error, a warning, or auto-healed (e.g. `CACHE_STALE` refreshes the local cache from the install-side manifest transparently).

**Per-file audit** (`install_manifest.audit_manifest_files()`): hashes every file the manifest claims we installed and classifies each as `STILL_MODDED` / `REVERTED_TO_OLD_VANILLA` / `NEW_VERSION_DRIFT` / `MISSING` / `ADDED_UNTOUCHED` / `ADDED_TAMPERED`. The audit is the mechanism behind `verify`, `doctor`, and the `resync-pristine` pre-flight.

**State schema v2** (`data/.mod-state-<dest>.json`): adds `writtenSha256` (renamed from `sha256`), `originalSha256`, `wasAdded`, and `workspaceFingerprint`. Old v1 entries are migrated transparently on read: `writtenSha256` falls back to `sha256`; `wasAdded` / `originalSha256` default to conservative values. First install after upgrade rewrites at v2 and seeds the install-side manifest.

**Branding:** Name = Necroid. Tagline = "Beyond Workshop". Palette = Charcoals + Bone (see `necroid/gui.py` `PALETTE` dict). Brand assets live in `assets/`; `assets/necroid.png` is the 1024² source, and derived icons (`necroid-mark-256.png`, `necroid-icon-256.png`, `necroid-icon.ico`) are regenerated via `bash assets/build-assets.sh` (requires ImageMagick; end users don't need it).

**Distribution model:** the repo is git-tracked for sharing with other modders, but nothing PZ-owned ships through git. `.gitignore` excludes `data/workspace/`, `data/tools/vineflower.jar`, `data/.mod-config.json`, `data/.mod-enter.json`, `data/.mod-state-*.json`, `dist/`, `build/`, and Python caches. On a fresh clone, `necroid init` reconstructs every local-only directory from the user's own Steam install — they must own a copy of PZ. Only `necroid/` (Python source), `packaging/`, `assets/`, `mods/` (the patch-set library at the repo root), and docs are tracked. Releases ship via GitHub Releases at `github.com/mrkmg/necroid` — tag, run `packaging/build_dist.py`, zip `dist/`, attach to the tagged release. **3rd-party mod authors use the same layout**: drop `necroid` at the root of their own repo, `necroid init` (which scaffolds `mods/` and writes a default `.gitignore`), develop under `mods/`, commit `README.md` + `mods/`. Users then `necroid import owner/repo` and the canonical `mods/<name>/mod.json` layout is picked up automatically.

## Tool: `necroid`

Python 3.10+ (stdlib only — tkinter, subprocess, hashlib, urllib, json). Cross-platform (Windows / Linux / macOS). Two entry points:

- **CLI** — full feature set. Developers and automation use this.
- **GUI** (tkinter) — simplified end-user surface: Set Up / Update from Game, and a state-based checkbox list with a single **Apply Changes** button (plus **Revert** to drop pending edits). Checkboxes auto-seed from `data/.mod-state-<dest>.json` on load and on every destination flip; Apply Changes diffs the user's selection against the installed stack and shells out to `necroid install <desired> --to <dest>` (or `necroid uninstall --to <dest>` when the selection is empty). Header also has **Import…** (two-stage discover-then-select modal that shells `necroid import --list --json` then `necroid import …`) and **Check Updates** (shells `necroid mod-update --check`). Mod table includes Origin (`⤓` glyph for imported) and Version columns; outdated imports show a `⬆ <new>` badge. A per-row right-click menu exposes Check / Update / Update with peers / Reimport / Show origin / Open on GitHub. The status strip surfaces an "N updates available" chip when the update cache shows outdated mods. Launch with `--gui`. Themed charcoal/bone; logo + window icon load from `assets/`.

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
python -m necroid verify --to client        # manifest reconcile + per-file audit + orphan scan
python -m necroid doctor --to client        # read-only diagnosis with remediation hints
python -m necroid resync-pristine           # after a PZ update

# import + update from GitHub:
python -m necroid import owner/repo --list             # discover mods in the repo
python -m necroid import owner/repo --all              # import every mod matching workspace major
python -m necroid import owner/repo --mod admin-xray   # pick one (repeatable)
python -m necroid mod-update                           # check + apply updates for every imported mod
python -m necroid mod-update <name> --check            # dry-run; populates the GUI cache
python -m necroid mod-update <name> --include-peers    # also refresh siblings sharing (repo, ref)

# GUI:
python -m necroid --gui                     # single window; install-to toggle in the header
python -m necroid --gui -server             # same window, initial install-to=server
```

Install editable (`pip install -e .`) to put `necroid` on PATH as a bare command. The packaged distributable from `packaging/build_dist.py` also uses the bare name `necroid` (no `python -m`).

Per-command flags:

- `init`: `--from {client,server}` picks the PZ install to seed from. Default comes from `config.workspaceSource`, then falls back to whichever install is configured, then `client`. `--yes` auto-confirms the detected workspace major and migrates legacy unversioned mod dirs without prompting. `--major N` overrides the detected major (advanced). Generates `config.workspaceFingerprint` on first run.
- `resync-pristine`: `--from` as for `init`; `--force-major-change` authorises a workspace re-bind when the source install has moved to a new PZ major; `--force-version-drift` proceeds when Steam has rewritten some installed files with a different PZ version's vanilla (skips restore for drifted files, adopts Steam's current bytes as new pristine, flags every mod as needing re-capture); `--force-orphans` proceeds when the install has `.class` files under mod-touched subtrees that are in neither the manifest nor `classes-original/` (adopted into new pristine); `--adopt-install` accepts an install-side manifest written by a different workspace fingerprint; `--yes` skips prompts.
- `install` / `uninstall` / `verify` / `doctor` / `list` / `status`: `--to {client,server}` chooses the install destination / state file / counting lens. Default from `config.defaultInstallTo`.
- `install`: `--adopt-install` adopts a PZ install whose manifest was written by a different Necroid workspace (cloned / moved workspace). Rare.
- `list`: `--all` shows every mod dir regardless of major (default filters to `workspaceMajor`).
- `enter`: `--as {client,server}` picks which per-destination postfix variant to apply when the mod ships one. Default is `config.defaultInstallTo`; forced to `client` if any mod in the stack is `clientOnly`.

Commands that accept a mod name (`install`, `uninstall`, `enter`, `capture`, `clean`) accept either a bare base (`admin-xray`) or the fully-qualified dir name (`admin-xray-41`). Bare bases resolve to `<base>-<workspaceMajor>`. Fully-qualified names must match the workspace major or the command errors with `PzMajorMismatch`.

Install is **atomic**: stages against pristine, compiles via `javac`, restores the previous install to originals, then copies new classes. A conflict during staging or a compile error leaves the PZ install untouched. Inner classes (`Outer$Inner.class`) are globbed automatically — a mod lists source changes, not class enumerations.

There are **no tests and no linter** for the PZ-decompiled code — it's decompiled output, not hand-written. The `javac` compile step is the only correctness gate. The Python tool itself is stdlib-only and also has no test suite yet.

### Creating a new mod

1. `necroid new my-mod --description "..."` — scaffolds `mods/my-mod-<major>/mod.json` + empty `patches/`. Add `--client-only` if the mod touches client-only code.
2. `necroid enter my-mod` — if `src-my-mod/` (at the repo root) doesn't exist yet, seeds it from pristine and applies my-mod's patches (none yet for a fresh mod); if it already exists, preserves its contents. Working tree is now "entered" on my-mod (recorded in `data/.mod-enter.json`, including `installAs`). Each mod gets its own `src-<name>/` tree at the repo root, so switching between mods is non-destructive.
3. Edit files under `src-my-mod/zombie/`. Only touch files you intend to ship — every diff vs pristine becomes a patch.
4. `necroid capture my-mod` — diffs `src-my-mod/` against `data/workspace/src-pristine/` and writes `.java.patch` / `.java.new` / `.java.delete` under `mods/my-mod-<major>/patches/`. Safe to run repeatedly.
5. `necroid test` — javac-only compile of the currently-entered working tree into `data/workspace/build/classes/`. No install, no staging, no PZ-install writes. Fastest way to catch compile errors before touching the game. Run it anytime between edits.
6. `necroid install my-mod --to client` — compile + install; play-test.

### Updating an existing mod

1. `necroid enter my-mod` — marks my-mod as entered and, if no `src-my-mod/` tree exists yet, seeds it from pristine + my-mod's patches. If the tree already exists (from a prior `enter`), its contents are preserved so in-progress edits aren't lost. Pass `--force` to wipe and re-seed, or run `necroid reset` afterwards for the same effect while keeping enter state.
2. Edit under `src-my-mod/zombie/`.
3. `necroid capture my-mod` — rewrites the patch set. Patches for files you reverted to pristine drop out automatically.
4. Only one mod can be entered at a time. `necroid enter other-mod` preserves `src-my-mod/` on disk (switching is non-destructive) and sets other-mod as the entered one. `necroid clean` (see below) removes per-mod trees.
5. Stacking is an **install-time** concern only (`necroid install mod-a mod-b …`). You cannot enter multiple mods; to edit a mod that sits on top of another in a stack, enter it directly — its patches are authored against pristine, not against the upstream mod.
6. Stale mods after a PZ update: `necroid status my-mod` reports whether each patch still applies. If stale, `enter` the mod (expect 3-way merge conflict markers in `src-my-mod/`), resolve by hand, then `capture`.

### Cleaning up entered working trees

- `necroid clean` — delete every `src-*/` directory at the repo root and clear enter state. `--yes` skips the confirmation prompt.
- `necroid clean my-mod` — delete only `src-my-mod/`. If my-mod was the currently entered mod, enter state is cleared too.
- `necroid reset` — re-seeds the currently entered mod's `src-<mod>/` from pristine + patches (discards local edits, keeps enter state). Use this when you want a fresh baseline without losing track of which mod you're on.

### Installing / uninstalling

- `necroid install my-mod --to client` — stage against pristine, compile, roll back the prior `client` install, copy new `.class` files into the client PZ install. Drop `--to` to use `config.defaultInstallTo`.
- `necroid install mod-a mod-b --to server` — stack multiple mods via 3-way merge against pristine. Order matters for conflict resolution; conflicts abort the install.
- `necroid uninstall --to <dest>` — restore every class file the last install on `<dest>` wrote. Hash-aware: a file we recorded as "added" (no pre-install vanilla) is deleted; a file we recorded as "overwritten" is restored from `classes-original/` *only after* verifying both (a) the install file is still what we wrote, and (b) `classes-original/` still hashes to the `originalSha256` we recorded at install time. A Steam-reverted file (live hash matches `originalSha256`) is a no-op. A live hash that matches neither → warn + skip (leave Steam's version in place). Pristine drift → `PristineDrift` error with a doctor pointer. Also deletes the install-side manifest.
- `necroid uninstall my-mod --to <dest>` — remove one from that destination's stack and rebuild the rest.
- `necroid verify --to <dest>` — manifest reconcile + per-file audit (`STILL_MODDED` / `REVERTED_TO_OLD_VANILLA` / `NEW_VERSION_DRIFT` / `MISSING` / `ADDED_UNTOUCHED` / `ADDED_TAMPERED`) + pristine drift check + orphan scan. Legacy installs (no manifest yet) fall back to state-based hashing and skip orphan scan; first install/uninstall after upgrade seeds the manifest.
- `necroid doctor --to <dest>` — read-only variant of verify, formatted as a structured diagnosis + suggested remediation commands. Safe to run any time; never writes. Use this first when something looks wrong.
- `necroid test` — compile the entered working tree via javac into `data/workspace/build/classes/` without installing. Green here means `install` will compile; runtime correctness is still on the play-test.
- Client and server state are independent — you can install one stack to client and a different one to server simultaneously.
- Installing a different stack to the same destination implicitly uninstalls the prior one — no manual uninstall needed before switching.
- Steam "Verify Integrity of Game Files" silently reverts overrides. Re-run `install` to restore, or `doctor --to <dest>` first for a per-file report.

### clientOnly rules

- `install --to server` on a stack containing any `clientOnly: true` mod → **hard error** (`ClientOnlyViolation`). Retry with `--to client`.
- `enter` on a stack containing a `clientOnly: true` mod when `clientPzInstall` is unset → **hard error**. Configure the client install (`necroid init --from client`) or drop `clientOnly`.
- `enter <stack> --as server` when the stack contains a `clientOnly: true` mod → **hard error**.
- `list` / `status` never hide mods. The `Client-only?` column (list) or `clientOnly:` line (status per-mod) is the marker.
- GUI shows all mods. When install-to = server, clientOnly rows gray out and can't be checked; flipping the header toggle back to client re-enables them.

### Self-update

`necroid update` replaces the running binary from the latest GitHub release (`github.com/mrkmg/necroid`). Only active for the frozen PyInstaller binary (`getattr(sys, "frozen", False)`); editable / source installs get a pointer to `git pull` / `pip install -U` and do nothing else. Flags: `--check` (no apply), `--force` (ignore the 24h TTL), `--yes` (no prompt), `--rollback` (swap `necroid.old[.exe]` back). The swap is rename-out + rename-in, so Windows's "can't delete the running .exe" rule is handled; the replaced binary is spawned with a hidden `--post-restart-cleanup` flag that removes the leftover `.old` file. A once-per-24h background check surfaces a one-line stderr notice after non-`update` CLI commands and a dismissable banner in the GUI (it runs in a worker thread so startup isn't blocked). Cache: `data/.update-cache.json` (local-only, gitignored). Escape hatch: `NECROID_NO_UPDATE_CHECK=1`. Test hook: `NECROID_UPDATE_REPO=owner/repo` points the check at a different repo without editing code. Releases must ship a `necroid-v{version}-{platform}-{arch}.zip` asset with the binary at the archive root (matches `packaging/build_dist.py`); any other assets in the release are ignored — the updater replaces only the binary, never the bundled `mods/` or `data/tools/` (those are user-owned on disk).

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
- `data/.mod-config.json` — `clientPzInstall`, `serverPzInstall`, `defaultInstallTo`, `workspaceSource`, `workspaceMajor`, `workspaceVersion`, `workspaceFingerprint` (opaque per-workspace id, minted at `init` — the install-side manifest stamps this to detect collisions between multiple Necroid checkouts pointing at the same PZ install). Schema v1. Local-only.
- `mods/<base>-<major>/` — **top-level**, tracked, the portable artifact. Each mod: `mod.json` (with `clientOnly` + `expectedVersion`) + `patches/` containing `.java.patch` / `.java.new` / `.java.delete`. The `-<major>` suffix (e.g. `admin-xray-41`) is parsed by the tool and enforced against `workspaceMajor`. Everything under `data/` is local-only generated content; `mods/` is the only user-authored tree at the repo root.
- `data/tools/vineflower.jar` — downloaded by `init`. Local-only.
- `data/tools/pz-version-probe/` — compiled `NecroidGetPzVersion.class`, cached on first `init` / `status` / `install` run. Regenerated automatically if `necroid/java/NecroidGetPzVersion.java` changes. Local-only.
- `src-<modname>/{zombie,astar,com,de,fmod,javax,org,se}/` — per-mod editable working tree at the **repo root**. `enter` seeds it from pristine + patches (preserving contents if it already exists), `capture` reads it back into the mod's patch set, `reset` re-seeds it, `clean` deletes it. Gitignored via `/src-*/`. Every class subtree PZ ships is decompiled, so mods can touch any of them (e.g. `se/krka/kahlua/...` for Lua-interpreter changes). Legacy `data/workspace/src/` (single shared tree) is no longer used — safe to delete if present.
- `data/workspace/src-pristine/<same subtrees>/` — **frozen** pristine decompile. Populated by `init`; refreshed by `resync-pristine`.
- `data/workspace/classes-original/` — verbatim class-file copies from the Steam install. Reference and restore source; **do not edit**.
- `data/workspace/libs/` — every jar from the PZ install used to seed the workspace.
- `data/workspace/libs/classpath-originals/` — the `classes-original/` subtrees repackaged as jars for `javac -cp`.
- `data/workspace/build/classes/` — javac output mirroring `zombie/...`.
- `data/workspace/build/stage-src/` — ephemeral install-staging tree.
- `data/.mod-state-client.json` / `data/.mod-state-server.json` — per-destination **local cache** of the install-side manifest. Schema v2 records `stack`, `installed[]` (each entry: `rel`, `modOrigin`, `writtenSha256`, `originalSha256`, `wasAdded`), `pzVersion` (detected version at install time), and `workspaceFingerprint`. The install-side manifest at `<pz>/.necroid-install.json` is the source of truth; this file is the fast-path read. v1 entries (pre-v2 Necroid) are read with `writtenSha256` falling back to the old `sha256` field and `wasAdded`/`originalSha256` defaulting conservatively — `_restore_installed` in `necroid/build/install.py` infers `wasAdded` at uninstall time for legacy entries that lack pristine counterparts.
- `<pz_install>/.necroid-install.json` (or `<pz_install>/java/.necroid-install.json` on the dedicated server) — **install-side manifest**. Schema v1 written by `necroid/core/install_manifest.py`. Hidden on Windows via `SetFileAttributesW`. Authoritative record of what Necroid has done to the install. Written atomically (`.new` + rename) by every `install`; deleted by every full `uninstall`. Never in git; lives in the PZ install dir, not the workspace.
- `data/.mod-enter.json` — the single mod the working tree is currently "entered" on (`{mod, enteredAt, installAs}`), plus the `installAs` destination used when applying postfix variants. Legacy stacked entries (multiple mods) are treated as invalid on read — re-enter with a single mod.
- `data/.update-cache-mods.json` — last `necroid mod-update --check` results per imported mod (`{version: 1, mods: {<dirname>: {checkedAt, localVersion, upstreamVersion, upstreamSha, status, message}}}`). 24h advisory TTL; consumed by the GUI to decorate Version-column badges and the "N updates available" status chip. Local-only; gitignored. Never blocks command success — write failures are swallowed.
- `data/.import-tmp/` and `data/.update-tmp/` — ephemeral working dirs for the `import` / `mod-update` flows (zip + extracted archive). Wiped at the end of each invocation; safe to delete at any time.
- `build/` — PyInstaller scratch + raw output. Local-only.
- `dist/` — produced by `packaging/build_dist.py`: self-contained binary + `mods/`. Local-only; zipped and shipped via GitHub Releases.

## When a PZ update lands

Run `necroid resync-pristine` (one pass — workspace is shared). The flow is:

1. **Major-change gate** — if the source install's detected major differs from `config.workspaceMajor`, abort unless `--force-major-change` is passed.
2. **Integrity audit (new)** — for each destination with installed state, read the install-side manifest and run the reconciliation matrix + per-file audit:
   - `FIRST_TIME` / `CLEAN` — nothing to check, skip.
   - `WIPED` (manifest gone, recorded files also absent) — Steam reinstall; local cache is stale, clear it, no restore needed.
   - `LEGACY_UNMIGRATED` (pre-v2 install, manifest missing but files still on disk) — fall back to state-based audit using the same classifier; this is the happy path on any workspace upgrading from pre-v2 Necroid.
   - `FINGERPRINT_MISMATCH` — abort unless `--adopt-install` is passed.
   - Per-file `NEW_VERSION_DRIFT` / `ADDED_TAMPERED` — Steam rewrote overwritten files with a different PZ version's vanilla, or a mod-added file's bytes changed. Abort unless `--force-version-drift` is passed. Forced-drift strategy: skip the restore for drifted files (let Steam's bytes pass through as the new pristine), warn loudly, every mod ends up flagged STALE.
   - Orphan scan — `.class` files under a mod-touched subtree that are in neither the manifest nor `classes-original/`. Abort unless `--force-orphans` is passed (adopts them into new pristine).
3. **Pre-resync uninstall guard** — for each destination with installed state, run `uninstall_all` (which is hash-aware: restores STILL_MODDED, skips REVERTED, warns+skips drifted, deletes added files, raises `PristineDrift` if `classes-original/` has itself drifted from the recorded `originalSha256`). Without the audit above, this step would blindly copy stale `classes-original/` content over Steam's new vanilla and produce a mixed-version Frankenstein install. The audit ensures the guard only runs on files where it's safe.
4. **Init with `--force`** — refresh `classes-original/`, `libs/`, `libs/classpath-originals/`, and every `src-pristine/<subtree>/`. The `mirror_tree` call used here is **`verify=True`** so a Steam-reverted file with a coincidentally-close mtime is hash-checked rather than silently skipped. Install cost: ~3000 extra SHA-256s on resync (seconds).
5. **Mod applicability check** — re-fingerprint every mod against the new pristine. Mods whose patches no longer apply are flagged STALE — `enter` each in turn (re-run with `--force` to re-seed `src-<mod>/` from the refreshed pristine), resolve conflicts in `src-<mod>/`, then `capture`.

`resync-pristine` does **not** touch existing `src-*/` trees, so old per-mod edits aren't silently rebased — use `necroid reset` (or `necroid clean <mod>` then re-`enter`) to rebase onto the new pristine.

**`necroid doctor --to <dest>`** runs the reconciliation + audit + pristine-drift check + orphan scan read-only, formatted as a diagnosis with suggested remediation commands. Run it first when a resync aborts — it explains *why*. Examples of what doctor finds:

- `NEW_VERSION_DRIFT` listed per-file → hint: Steam "Verify Integrity of Game Files" then re-install the stack, *or* `resync-pristine --force-version-drift` to adopt Steam's bytes.
- `REVERTED_TO_OLD_VANILLA` → Steam Verify silently rolled us back; hint: just re-install the stack.
- `PRISTINE DRIFT` (classes-original/ no longer hashes to recorded `originalSha256`) → someone edited the workspace's pristine directly; hint: full resync.
- Orphans → probably a previous crash between deploy and state-write, or a hand-patch; hint: Steam Verify to restore vanilla, or delete the listed files.

Vineflower writes files declaring `package <subtree>;` into its output root (not a nested folder) because each `classes-original/<subtree>/` *is* the package root. `decompile_subtree` therefore decompiles into a tmp dir and renames it into `src-pristine/<subtree>/` as the final step — that's not a bug. Each subtree is decompiled in its own Vineflower invocation.

## Building the distributable

```bash
pip install pyinstaller
python packaging/build_dist.py
```

Produces `dist/necroid(.exe)` + `dist/mods/` + `dist/README.txt`. PyInstaller does not cross-compile; build on each target OS you need. Vineflower is bundled into the binary and self-extracts on first run. The derived PNG assets (`necroid-mark-256.png`, `necroid-icon-256.png`) are bundled via `--add-data` and resolved at runtime via `necroid/assets.py` (which handles both dev and `sys._MEIPASS` frozen mode). On Windows, `necroid-icon.ico` is also baked into the `.exe` via PyInstaller's `--icon` flag.

## Things that look like bugs but aren't

- Many `.java` files contain `new Float(...)` / `new Double(...)` deprecation warnings and `sun.misc.Unsafe` warnings. These are in PZ's original bytecode — leave them alone unless the file you're modding is one of them.
- Fully-qualified names like `zombie.BaseAmbientStreamManager` inside files already in `package zombie` are a Vineflower quirk, not an error.
- Inner classes in `classes-original/` appear as `Outer$Inner.class` (~2980 class files for the client) but decompile to inner-class declarations inside ~1601 outer `.java` files. The counts match.
