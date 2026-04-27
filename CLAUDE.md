# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

**Necroid** — a Project Zomboid mod manager, not a normal Java project. Workspace state (decompiled `src-pristine/`, `classes-original/`, `libs/`, `build/`, install state caches, install-side manifests, `update-cache-mods.json`, scratch tmp dirs) lives **inside the PZ install** at `<pz_install>/necroid/`. The checkout's only local state is a tiny pointer file (`<repo>/data/.necroid-pointer.json`) naming the anchor PZ install, an entered-mod record (`<repo>/data/.mod-enter.json`), per-mod scratch trees (`<repo>/src-<mod>/`), auto-fetched tools (`<repo>/data/tools/`), and the binary self-update TTL (`<repo>/data/.update-cache.json`). Multiple checkouts of necroid pointing at the same PZ install share one workspace by construction.

Source trees under `<pz>/necroid/workspace/src-pristine/` are **decompiled** output from PZ's shipped class files (via Vineflower 1.11.1). The goal is: edit individual classes, recompile them targeting the workspace's Java release, then **overwrite the `.class` files directly in the PZ install**. PZ does **not** have a Java-mod loader that picks up jars from the Mods folder — a jar-based approach would require writing our own classloader, which we aren't doing.

**Two install layouts are supported.** Recorded in `config.workspaceLayout`:

- **`loose`** (PZ build 41 and earlier): vanilla classes live as a loose tree at the install root (`<pz>/{zombie,astar,se,...}/**/*.class`). Necroid's class-file overwrite IS the mod mechanism. Restoring a file at uninstall = copy from `classes-original/`.
- **`jar`** (PZ build 42+): vanilla classes live inside a single fat `<pz>/projectzomboid.jar`. The launcher's classpath is `./;projectzomboid.jar`, so a loose `.class` dropped under `<pz>/zombie/...` still overrides the jar entry — the install mechanism survives intact. Restoring a file at uninstall = **delete** the loose override, letting the JVM fall back to the jar entry. Every installed file is recorded with `wasAdded=true` because the install path didn't exist as a loose file before Necroid put it there.

`init` detects the layout from the source PZ install (presence of `projectzomboid.jar` at the install root) and seeds `classes-original/` accordingly: a mirror copy of the loose tree on `loose`, or a `zipfile`-extract of `projectzomboid.jar` on `jar`. The decompile and javac stages are layout-agnostic from there on. `libs/classpath-originals/<sub>.jar` rejaring runs only on `loose`; on `jar` the fat jar itself sits on `javac -cp` via `<pz>/necroid/workspace/libs/projectzomboid.jar`.

**Java release target is per-major.** PZ 41 ships JRE 17; PZ 42 ships JDK 25 runtime (class-file v69). `config.javaRelease` is derived at `init` from the workspace major via `{41:17, 42:25}` (extend the table in `necroid/core/profile.py:_JAVA_RELEASE_BY_MAJOR` for future PZ majors). All `javac --release N` invocations and the install-time enforcement use this value. PZ's bundled `<pz>/jre64/` is **runtime-only** (no javac), so users need a system JDK whose major is >= the target — but they don't have to fight PATH: `necroid/util/tools.py:_find_jdk_binary` first tries PATH, then scans well-known JDK install roots (Adoptium / Java / Microsoft / Zulu / Corretto / BellSoft / Semeru on Windows; `/Library/Java/JavaVirtualMachines` on macOS; `/usr/lib/jvm`, `/usr/java`, `/opt` on Linux), and picks the *lowest qualifying major*. The PZ-bundled `<pz>/jre64` is included as an `extra_roots` entry by `pzversion.detect_pz_version` so the version probe always has access to a runtime that matches the install's bytecode (PATH java being too old — e.g. JDK 21 against B42's class-file v69 — is the most common failure mode without this).

**Single shared workspace.** The client (`ProjectZomboid`, Steam app 108600) and dedicated server (`Project Zomboid Dedicated Server`, Steam app 380870, or `./pzserver/`) ship byte-identical Java class trees (loose-tree on B41; fat jar on B42). Necroid therefore keeps **one** workspace at `<pz_workspace_source>/necroid/workspace/{src-pristine,classes-original,libs,build}/`, seeded from whichever PZ install the user points `init` at (`--from client` or `--from server`). The chosen source is recorded in `config.workspaceSource` (which lives in the workspace config at `<pz>/necroid/config.json`).

**Install destinations are per-invocation.** `necroid install <stack> --to client|server` chooses where the compiled `.class` files land. Each destination has its own state cache file alongside the workspace: `<pz>/necroid/state-client.json` and `<pz>/necroid/state-server.json`. Both can coexist — a user may have, say, admin-xray installed to client and gravymod installed to server at the same time.

**Mods carry a `clientOnly` flag.** In `mod.json`, `clientOnly: true` means the mod requires a configured client PZ install and cannot be installed to the server (it relies on client-only rendering / input code). `clientOnly: false` (default) means the mod works against either destination. There is no per-mod "target" any more.

**Mods carry a free-form `category` string** in `mod.json` (empty = uncategorized). Suggested vocab: `admin`, `bugfix`, `dev-tools`, `mechanics`, `ui`, `utility` — not enforced, 3rd-party authors can use anything. `necroid list` prints mods grouped under category section headers (`necroid list --category bugfix` filters to one group); the GUI renders each category as a collapsible parent row in the mod tree. Set at scaffold time via `necroid new <name> --category <cat>` or edit `mod.json` directly. The loader lowercases the value on read/write; absent field behaves identically to `""`.

**Workspace is bound to one PZ major version.** Every PZ major (41, 42, ...) decompiles to an incompatible source tree — a 41-authored patch set cannot apply to 42 pristine and vice versa. Necroid handles this by binding a workspace to exactly one major at `init` time (detected via a tiny Java probe that reads `zombie.core.Core.gameVersion` + `Core.buildVersion` via reflection — see `necroid/pzversion.py`). The bound major is stored in `config.workspaceMajor`; the full version string (e.g. `"41.78.19"`) is stored in `config.workspaceVersion`.

**Mod dirs encode their PZ major in the name.** `mods/<base>-<major>/` — e.g. `admin-xray-41`. The `-<major>` suffix is authoritative: `list`, `status`, `install`, `enter`, and the GUI filter mods against `workspaceMajor` and refuse to touch incompatible variants. `necroid install admin-xray` resolves bare bases against the workspace major (so `admin-xray` → `admin-xray-41` if that's what the workspace is bound to). `mod.json` additionally stamps `expectedVersion` (the full PZ version at the time of last `capture`) for soft minor/patch-drift warnings — the dir suffix is the hard gate, `expectedVersion` is the recapture hint.

**Major changes require an explicit opt-in.** `necroid resync-pristine` refuses to silently re-bind the workspace to a different major when the source install has moved (e.g. 41 → 42). Pass `--force-major-change` to acknowledge that every existing mod's patches will need to be re-captured against the new pristine. The old-major mod dirs are left on disk (filtered out of default views); re-enter and re-capture each to port.

Uninstall restores originals from `<pz>/necroid/workspace/classes-original/` (verbatim copy of the install's class tree on `loose`, or `zipfile`-extracted contents of `projectzomboid.jar` on `jar`) — that directory is the single source of truth for "what the vanilla class looks like", and **must not be edited**. On `jar` layout the restore step short-circuits to `delete the loose .class` (the JVM falls back to the in-jar entry); `classes-original/` is still consulted for hash provenance and audit reporting.

Writing to `C:\Program Files (x86)\...` requires an elevated shell. If Steam "Verify Integrity of Game Files" is run, it will revert any installed overrides — just re-run `necroid install <stack> --to <dest>` afterwards (or `necroid doctor --to <dest>` first to see exactly what Steam changed).

**Steam's file management is asymmetric — the integrity system has strong implications for this tool.** Steam's Verify / patch-update flows only touch files that appear in Steam's manifest for the current PZ build. That means:

- On `loose` layout: files Necroid **overwrote** (e.g. `zombie/core/Core.class`) may be rewritten by Steam back to vanilla — either the *same* vanilla (Verify on an unchanged build) or a *different-version* vanilla (patch update). Necroid can't tell those apart without hash evidence.
- On `jar` layout: there are no overwrites to revert (the fat jar is what Steam tracks; Necroid's loose `.class` files don't appear in any Steam manifest). Steam leaves the loose overrides alone, but a patch update will replace the fat jar itself — workspaces are then pinned to the *old* jar's bytecode while the live install runs the new one. The `pzJarSha256` field in the install-side manifest detects this; `verify` / `doctor` / `resync-pristine --force-version-drift` handle it.
- Files Necroid **added** (e.g. `zombie/gravymod/GravyMain.class` compiled from a mod's `.java.new`) are not in Steam's manifest, so Steam leaves them alone forever. They can outlive uninstalls, PZ reinstalls, and major-version changes if Necroid state ever gets lost.

This is the root reason for the install-side manifest (below): without an authoritative record stamped into the install, a `resync-pristine` can't distinguish "Steam reverted my overwrite to the new-version vanilla" (dangerous — would poison `classes-original/`) from "Steam left everything alone" (safe). See the **Install-side manifest** and **When a PZ update lands** sections.

Mods are diff-based: each mod is a directory of unified diffs under `mods/<name>/patches/`, authored against the frozen pristine decompile at `<pz>/necroid/workspace/src-pristine/`. Multiple mods touching the same file combine via 3-way merge at install time. See `necroid --help`.

**Mods can declare relationships** via `mod.json`:

- `dependencies: ["admin-xray"]` — list of bare names (no `-<major>` suffix; resolved against the workspace major at command time). At `enter` time the dep's patches are applied first and the dependent's edits sit on top; at `capture` time the diff is taken against **pristine + applied deps** (built in an ephemeral `<pz>/necroid/workspace/build/capture-baseline/<mod>/` tree), so the dependent's own `patches/` contain only what it adds *beyond* its deps. At `install` time the full dep closure is expanded in topo order (deps before dependents). A dependent's effective `clientOnly` is true if any transitive dep is `clientOnly`.
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

`mod-update --check` writes results into `<pz>/necroid/update-cache-mods.json` (24h TTL, schema v1). The GUI reads this cache to decorate the Version column with `⬆ <new>` badges and to drive the "N updates available" status-strip chip; the cache file is the single source of truth for "is upstream newer". The GUI also runs `mod-update --check` from the **Check Updates** header button and from the per-mod right-click menu.

**Bundled mods participate in the same `mod-update` flow.** `packaging/build_dist.py:stamp_bundled_origins` walks the `mods/` tree being copied into the dist and stamps each mod's `mod.json` with `origin = {repo: "mrkmg/necroid", ref: "main", subdir: "mods/<dirname>", commitSha: "", ...}`. The empty `commitSha` is by design — the first `mod-update --check` resolves the real SHA and writes it back to the local `mod.json` (in the up-to-date branch of `_process_one`, mirroring the apply branch). Subsequent runs short-circuit the archive download via the SHA-equality check in `_process_group`. The source-tree `mods/*/mod.json` files are **not** stamped — they have no origin, so source installs (`pip install -e .`) treat them as local; only the dist-shipped variants carry the origin block. Users can "unbind" a bundled mod by deleting its `origin` block — it then behaves like a hand-authored mod and is ignored by `mod-update`. Stamp logic is idempotent and skips any mod that already has an origin (so re-runs and forks don't clobber).

**Install-side manifest is the authoritative record.** `<pz_install>/necroid/install-manifest.json` is written by every `necroid install` and deleted by every full `necroid uninstall`. It's the source of truth for "what has Necroid done to this install". The state cache file `<pz>/necroid/state-<dest>.json` is a fast-path local cache — the install-side manifest wins on any disagreement.

Both client and server keep their manifest at `<install>/necroid/install-manifest.json` (install root, not content dir — the dedicated server's content dir is `<server>/java/` but its manifest still lives at `<server>/necroid/install-manifest.json` for symmetry).

The manifest lets Necroid detect three categories of tampering that the cache-only model could not:

1. **Steam Verify / patch-update** — the install file's bytes differ from both `writtenSha256` (what we wrote) and `originalSha256` (what was there when we installed). Classified as `NEW_VERSION_DRIFT`. Resync refuses to silently adopt unless `--force-version-drift` is passed.
2. **PZ reinstall / Steam uninstall+reinstall** — the manifest is gone but local cache says a stack is installed. Classified as `WIPED` (if no recorded files exist on disk) or `LEGACY_UNMIGRATED` (if they do — install predates the install-side manifest). The first install/uninstall after a legacy detection seeds a manifest.
3. **Manual tamper / orphaned files** — `.class` files under a mod-touched subtree that are in neither the manifest nor `classes-original/` (user hand-patched, or a prior crash between deploy and state-write). The orphan scan catches these.

The previous "two-workspace collision" detection (workspaceFingerprint + `--adopt-install`) was deleted: the workspace now lives inside the PZ install, so two checkouts of necroid pointing at the same install share one workspace by construction — collisions are impossible.

Manifest schema v1 (written by `necroid/core/install_manifest.py`):

```json
{
  "schemaVersion": 1,
  "workspace": {
    "workspaceDir": "C:/.../ProjectZomboid/necroid",
    "workspaceMajor": 42,
    "workspaceLayout": "jar"
  },
  "destination": "client",
  "pzVersionAtInstall": "42.17.0",
  "pzJarSha256": "<hex>",
  "installedAt": "<ISO UTC>",
  "stack": [{"dirname": "admin-xray-42", "version": "0.3.1"}],
  "files": [
    {"rel": "zombie/Foo.class",
     "writtenSha256": "<hex>",
     "originalSha256": "<hex>|null",
     "wasAdded": true,
     "modOrigin": "admin-xray-42"}
  ]
}
```

On `jar` installs, every `wasAdded` is `true` because the install path didn't exist as a loose file before — uninstall = delete. `originalSha256` is still recorded (the jar-entry hash) so audits can describe provenance even though the restore path doesn't use it.

**Reconciliation matrix** (`install_manifest.reconcile()`): read at the start of `install` / `uninstall` / `verify` / `doctor` / `resync-pristine`. Returns one of `CLEAN`, `FIRST_TIME`, `WIPED`, `LEGACY_UNMIGRATED`, `CACHE_STALE`, `TAMPERED`. Callers decide whether each is a hard error, a warning, or auto-healed (e.g. `CACHE_STALE` refreshes the local cache from the install-side manifest transparently).

**Per-file audit** (`install_manifest.audit_manifest_files()`): hashes every file the manifest claims we installed and classifies each as `STILL_MODDED` / `REVERTED_TO_OLD_VANILLA` / `NEW_VERSION_DRIFT` / `MISSING` / `ADDED_UNTOUCHED` / `ADDED_TAMPERED`. The audit is the mechanism behind `verify`, `doctor`, and the `resync-pristine` pre-flight. On `jar` layout every installed file lands in `ADDED_UNTOUCHED` / `ADDED_TAMPERED` since `wasAdded=true` for all of them.

**Fat-jar audit** (`install_manifest.audit_pz_jar()`, jar-layout only): compares the live `<pz>/projectzomboid.jar` hash to the manifest's `pzJarSha256`. Returns one of `NOT_TRACKED` (loose layout or pre-B42 manifest), `CLEAN`, `JAR_MISSING`, or `JAR_DRIFT`. `JAR_DRIFT` is the jar-layout equivalent of per-file `NEW_VERSION_DRIFT` — Steam shipped a patch update that swapped the jar's bytes. Surfaced by `verify` / `doctor`; gated by `resync-pristine --force-version-drift`.

**State cache schema v2** (`<pz>/necroid/state-<dest>.json`): records `writtenSha256`, `originalSha256`, `wasAdded`. The cache is regenerated from the install-side manifest on any disagreement (CACHE_STALE) so corruption / staleness is self-healing.

**Branding:** Name = Necroid. Tagline = "Beyond Workshop". Palette = Charcoals + Bone (see `necroid/gui.py` `PALETTE` dict). Brand assets live in `assets/`; `assets/necroid.png` is the 1024² source, and derived icons (`necroid-mark-256.png`, `necroid-icon-256.png`, `necroid-icon.ico`) are regenerated via `bash assets/build-assets.sh` (requires ImageMagick; end users don't need it).

**Distribution model:** the repo is git-tracked for sharing with other modders, but nothing PZ-owned ships through git — and the workspace itself doesn't even live in the checkout (it's inside the PZ install). `.gitignore` excludes `data/.necroid-pointer.json`, `data/.mod-enter.json`, `data/.update-cache.json`, `data/tools/*` (auto-fetched JDK/git/vineflower), `src-*/`, `dist/`, `build/`, Python caches, plus the legacy paths from pre-PZ-anchored Necroid (kept ignored in case anyone migrates a checkout that still has them). On a fresh clone, `necroid init` writes the pointer + bootstraps `<pz>/necroid/` from the user's own Steam install — they must own a copy of PZ. Only `necroid/` (Python source), `packaging/`, `assets/`, `mods/` (the patch-set library at the repo root), and docs are tracked. Releases ship via GitHub Releases at `github.com/mrkmg/necroid` — tag, run `packaging/build_dist.py`, zip `dist/`, attach to the tagged release. **3rd-party mod authors use the same layout**: drop `necroid` at the root of their own repo, `necroid init` (which scaffolds `mods/` and writes a default `.gitignore`), develop under `mods/`, commit `README.md` + `mods/`. Users then `necroid import owner/repo` and the canonical `mods/<name>/mod.json` layout is picked up automatically.

## Tool: `necroid`

Python 3.10+ (stdlib only — tkinter, subprocess, hashlib, urllib, json). Cross-platform (Windows / Linux / macOS). Two entry points:

- **CLI** — full feature set. Developers and automation use this.
- **GUI** (tkinter) — simplified end-user surface: Set Up / Update from Game, and a state-based checkbox list with a single **Apply Changes** button (plus **Revert** to drop pending edits). Checkboxes auto-seed from `<pz>/necroid/state-<dest>.json` on load and on every destination flip; Apply Changes diffs the user's selection against the installed stack and shells out to `necroid install <desired> --to <dest>` (or `necroid uninstall --to <dest>` when the selection is empty). Header also has **Import…** (two-stage discover-then-select modal that shells `necroid import --list --json` then `necroid import …`) and **Check Updates** (shells `necroid mod-update --check`). Mod table includes Origin (`⤓` glyph for imported) and Version columns; outdated imports show a `⬆ <new>` badge. A per-row right-click menu exposes Check / Update / Update with peers / Reimport / Show origin / Open on GitHub. The status strip surfaces an "N updates available" chip when the update cache shows outdated mods. Launch with `--gui`. Themed charcoal/bone; logo + window icon load from `assets/`.

External requirements on PATH: `git`, `java` (17+), `javac` (17+), `jar` (ships with JDK). `init` downloads Vineflower itself.

**Auto-fetch fallback for missing tools.** When `git` or any JDK binary (`java`/`javac`/`jar`) is absent from PATH and no JDK is found in well-known install roots, Necroid downloads a portable copy into `data/tools/` on demand: an Eclipse Temurin JDK via the Adoptium API on all 3 OSes (`tools_dir/jdk-<major>/`), and MinGit on Windows (`tools_dir/git/cmd/git.exe`). macOS/Linux have no first-party portable git; if `git` is missing on those, Necroid still raises `ToolMissing` with the existing `brew install git` / `apt install git` hint. The fetch is lazy (first command that needs the tool triggers it), idempotent (subsequent commands hit the on-disk cache), and SHA-verified for the JDK against Adoptium's checksum endpoint. Bound to the auto-fetch cache via `tools.set_tools_dir(data/tools)` from `cli.main()`. Opt-out: `NECROID_NO_AUTO_FETCH=1`. See `necroid/util/tools_fetch.py` and the fallback wiring in `necroid/util/tools.py`.

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

- `init`: `--from {client,server}` picks the PZ install to seed from. Default comes from `config.workspaceSource`, then falls back to whichever install is configured, then `client`. Writes the pointer file at `<repo>/data/.necroid-pointer.json` and bootstraps `<pz>/necroid/`. `--yes` auto-confirms the detected workspace major. `--major N` overrides the detected major (advanced). Refuses to run if a legacy on-disk layout (old `data/workspace/` etc.) is present — capture any in-progress edits first, then delete the legacy paths and re-init.
- `resync-pristine`: `--from` as for `init`; `--force-major-change` authorises a workspace re-bind when the source install has moved to a new PZ major; `--force-version-drift` proceeds when Steam has rewritten some installed files with a different PZ version's vanilla (skips restore for drifted files, adopts Steam's current bytes as new pristine, flags every mod as needing re-capture); `--force-orphans` proceeds when the install has `.class` files under mod-touched subtrees that are in neither the manifest nor `classes-original/` (adopted into new pristine); `--yes` skips prompts.
- `install` / `uninstall` / `verify` / `doctor` / `list` / `status`: `--to {client,server}` chooses the install destination / state file / counting lens. Default from `config.defaultInstallTo`.
- `list`: `--all` shows every mod dir regardless of major (default filters to `workspaceMajor`).
- `enter`: `--as {client,server}` picks which per-destination postfix variant to apply when the mod ships one. Default is `config.defaultInstallTo`; forced to `client` if any mod in the stack is `clientOnly`.

Commands that accept a mod name (`install`, `uninstall`, `enter`, `capture`, `clean`) accept either a bare base (`admin-xray`) or the fully-qualified dir name (`admin-xray-41`). Bare bases resolve to `<base>-<workspaceMajor>`. Fully-qualified names must match the workspace major or the command errors with `PzMajorMismatch`.

Install is **atomic**: stages against pristine, compiles via `javac`, restores the previous install to originals, then copies new classes. A conflict during staging or a compile error leaves the PZ install untouched. Inner classes (`Outer$Inner.class`) are globbed automatically — a mod lists source changes, not class enumerations.

There are **no tests and no linter** for the PZ-decompiled code — it's decompiled output, not hand-written. The `javac` compile step is the only correctness gate. The Python tool itself is stdlib-only and also has no test suite yet.

### Creating a new mod

1. `necroid new my-mod --description "..."` — scaffolds `mods/my-mod-<major>/mod.json` + empty `patches/`. Add `--client-only` if the mod touches client-only code. Add `--category <cat>` (e.g. `--category utility`) to group it in `list` and the GUI.
2. `necroid enter my-mod` — if `src-my-mod/` (at the repo root) doesn't exist yet, seeds it from pristine and applies my-mod's patches (none yet for a fresh mod); if it already exists, preserves its contents. Working tree is now "entered" on my-mod (recorded in `<repo>/data/.mod-enter.json`, including `installAs` — the entered record stays checkout-local since per-mod scratch trees do too). Each mod gets its own `src-<name>/` tree at the repo root, so switching between mods is non-destructive.
3. Edit files under `src-my-mod/zombie/`. Only touch files you intend to ship — every diff vs pristine becomes a patch.
4. `necroid capture my-mod` — diffs `src-my-mod/` against `<pz>/necroid/workspace/src-pristine/` and writes `.java.patch` / `.java.new` / `.java.delete` under `mods/my-mod-<major>/patches/`. Safe to run repeatedly.
5. `necroid test` — javac-only compile of the currently-entered working tree into `<pz>/necroid/workspace/build/classes/`. No install, no staging, no PZ-install writes. Fastest way to catch compile errors before touching the game. Run it anytime between edits.
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
- `necroid test` — compile the entered working tree via javac into `<pz>/necroid/workspace/build/classes/` without installing. Green here means `install` will compile; runtime correctness is still on the play-test.
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

- **Only pass modified files to `javac`** (the `install` flow does this automatically). Compiling all decompiled files produces thousands of errors — decompiled Java doesn't round-trip cleanly (lambdas, generics erasure, obfuscation artifacts). The install overwrites individual `.class` files, so compiling the changed files only is correct.
- `buildjava.javac_compile` deliberately **does not pass `-sourcepath`**. With a sourcepath, javac would try to recompile sibling decompiled files on demand. Every non-modified symbol resolves from the original classpath jars: `<pz>/necroid/workspace/libs/classpath-originals/<sub>.jar` on `loose` layout, or `<pz>/necroid/workspace/libs/projectzomboid.jar` directly on `jar` layout.
- **Java release target is per-major** (`config.javaRelease`, derived at `init`): PZ 41 → `javac --release 17`, PZ 42 → `javac --release 25`. PZ ships a matching JRE under `<pz>/jre64/` but it has no javac, so a system JDK whose major >= the target is required. `buildjava.javac_compile` resolves javac through `tools.require_javac_release()` — PATH first, then a scan of well-known JDK roots — so a stale shell PATH (the common winget case where Temurin 25 was installed but the existing terminal still has 21) doesn't block the compile.
- `<pz>/necroid/workspace/build/classes/` is the javac output; `<pz>/necroid/workspace/build/stage-src/` is the ephemeral staging tree for each install. Both safe to delete.

## Directory roles

- `necroid/` — Python package (CLI, GUI, commands, install orchestrator). Flat layout at repo root.
- `packaging/build_dist.py` — PyInstaller builder; writes `<repo-root>/dist/`.
- `assets/` — brand assets. `necroid.png` (source 1024²), `necroid-mark-256.png` (GUI header skull), `necroid-icon-256.png` (window icon), `necroid-icon.ico` (Windows exe icon), `build-assets.sh` (ImageMagick regen).
- `pyproject.toml` — project metadata; script entry point `necroid = "necroid.cli:main"`.
- `mods/<base>-<major>/` — **top-level**, tracked, the portable artifact. Each mod: `mod.json` (with `clientOnly` + `expectedVersion`) + `patches/` containing `.java.patch` / `.java.new` / `.java.delete`. The `-<major>` suffix (e.g. `admin-xray-41`) is parsed by the tool and enforced against `workspaceMajor`. The only user-authored tree at the repo root.

### Checkout-local (`<repo>/`)

- `data/.necroid-pointer.json` — schema v1; one field `pzInstall` naming the PZ install that holds this checkout's workspace. Written by `init`; read by every other command via `read_pointer()`.
- `data/.mod-enter.json` — the single mod currently "entered" (`{mod, enteredAt, installAs}`). Stays checkout-local because per-mod scratch trees do too — two checkouts can each have a different mod entered against the same shared workspace.
- `data/.update-cache.json` — binary self-update TTL (per-binary, gitignored).
- `data/tools/vineflower.jar` — downloaded by `init`. Local-only.
- `data/tools/pz-version-probe/` — compiled `NecroidGetPzVersion.class`, cached on first `init` / `status` / `install` run. Regenerated automatically if `necroid/java/NecroidGetPzVersion.java` changes. Local-only.
- `data/tools/jdk-<major>/`, `data/tools/git/` — auto-fetched portable JDK / MinGit (Windows) when no system equivalent is on PATH. Local-only.
- `src-<modname>/{zombie,astar,com,de,fmod,javax,org,se}/` — per-mod editable working tree at the **repo root**. `enter` seeds it from pristine + patches (preserving contents if it already exists), `capture` reads it back into the mod's patch set, `reset` re-seeds it, `clean` deletes it. Gitignored via `/src-*/`.

### Workspace state (`<pz_workspace_source>/necroid/`)

Lives inside the PZ install, not the checkout. Multiple checkouts pointing at the same PZ install share this workspace.

- `<pz>/necroid/config.json` — workspace config: `clientPzInstall`, `serverPzInstall`, `defaultInstallTo`, `workspaceSource`, `workspaceMajor`, `workspaceVersion`, `workspaceLayout` (`"loose"` or `"jar"`), `javaRelease` (int, derived from major). Schema v1.
- `<pz>/necroid/workspace/src-pristine/<subtrees>/` — **frozen** pristine decompile. Populated by `init`; refreshed by `resync-pristine`.
- `<pz>/necroid/workspace/classes-original/` — verbatim class-file copies from the Steam install. Populated by `mirror_tree(<pz>, ...)` on `loose` layout; by `build/jar_extract.extract_fat_jar(<pz>/projectzomboid.jar, ...)` on `jar` layout. Reference and restore source; **do not edit**. Layout-agnostic from the consumer's POV.
- `<pz>/necroid/workspace/libs/` — every jar from the PZ install used to seed the workspace. On `jar` layout this is where `projectzomboid.jar` lives (and is the sole entry on `javac -cp`).
- `<pz>/necroid/workspace/libs/classpath-originals/` — the `classes-original/` subtrees repackaged as jars for `javac -cp`. **Only populated on `loose` layout**; empty on `jar` (the fat jar in `libs/` already serves this role).
- `<pz>/necroid/workspace/build/classes/` — javac output mirroring `zombie/...`.
- `<pz>/necroid/workspace/build/stage-src/` — ephemeral install-staging tree.
- `<pz>/necroid/state-client.json` / `<pz>/necroid/state-server.json` — per-destination state cache. Schema v2 records `stack`, `installed[]` (each entry: `rel`, `modOrigin`, `writtenSha256`, `originalSha256`, `wasAdded`), `pzVersion`. The install-side manifest is the source of truth; this file is the fast-path read.
- `<pz>/necroid/update-cache-mods.json` — last `necroid mod-update --check` results per imported mod (`{version: 1, mods: {<dirname>: {checkedAt, localVersion, upstreamVersion, upstreamSha, status, message}}}`). 24h advisory TTL; consumed by the GUI to decorate Version-column badges and the "N updates available" status chip. Never blocks command success — write failures are swallowed.
- `<pz>/necroid/tmp/import-tmp/` and `<pz>/necroid/tmp/update-tmp/` — ephemeral working dirs for the `import` / `mod-update` flows (zip + extracted archive). Wiped at the end of each invocation; safe to delete at any time.

### Install-side (per-destination)

- `<pz_install>/necroid/install-manifest.json` — **install-side manifest**. Written by `necroid/core/install_manifest.py`. Authoritative record of what Necroid has done to the install. Written atomically (`.new` + rename) by every `install`; deleted by every full `uninstall`. One per destination — server keeps its own at `<pz_server>/necroid/install-manifest.json` (install root, NOT under `java/`). When a destination is also the workspace home, the manifest sits next to the workspace dir; when a destination is a destination only, this file is the only thing in `<pz>/necroid/`.

### Build artifacts

- `build/` — PyInstaller scratch + raw output. Local-only.
- `dist/` — produced by `packaging/build_dist.py`: self-contained binary + `mods/`. Local-only; zipped and shipped via GitHub Releases.

## When a PZ update lands

Run `necroid resync-pristine` (one pass — workspace is shared). The flow is:

1. **Major-change gate** — if the source install's detected major differs from `config.workspaceMajor`, abort unless `--force-major-change` is passed.
2. **Integrity audit** — for each destination with installed state, read the install-side manifest and run the reconciliation matrix + per-file audit:
   - `FIRST_TIME` / `CLEAN` — nothing to check, skip.
   - `WIPED` (manifest gone, recorded files also absent) — Steam reinstall; local cache is stale, clear it, no restore needed.
   - `LEGACY_UNMIGRATED` (manifest missing but files still on disk) — fall back to state-based audit using the same classifier.
   - **Fat-jar drift** (`jar` layout only) — `audit_pz_jar` compares live `projectzomboid.jar` to recorded `pzJarSha256`. `JAR_DRIFT` aborts unless `--force-version-drift` is passed; `JAR_MISSING` is reported by doctor/verify. Forced-drift strategy on jar: re-extract the new jar at the next `init --force` step and let it become the new `classes-original/`, every mod flagged STALE.
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
- Inner classes in `classes-original/` appear as `Outer$Inner.class` but decompile to inner-class declarations inside the corresponding outer `.java` files. The counts match (e.g. on B42: 18,107 `.class` entries extracted from `projectzomboid.jar`, decompiling to 10,917 `.java` files across 8 subtrees).
- On B42, `<pz>/zombie/` may contain leftover loose subdirs (e.g. `admin_xray/`, `gravymod/`) from prior B41 mod installs against the same Steam install. They're orphans relative to the B42 install (B42's vanilla classes all live in the jar) but harmless — the classpath-prepend trick means Necroid's loose overrides are how the mod system *works* on B42 too. `doctor`'s orphan scan surfaces these on the next install.
