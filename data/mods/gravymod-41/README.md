# gravymod

Server-side admin toolkit. Adds a single `/gutil` chat command (admin-only) with subcommands for weather reload, player import/export, whitelist priority, max-player count, server password rotation, and Lua slow-event timing — plus two Lua globals for spawning and growing trees from server-side scripts.

Originally an in-house dev/operator mod; bundled here so other operators don't have to re-derive the reflection tricks needed to talk to PZ's internal databases and managers.

## What it changes

- New package `zombie.gravymod` with these classes:
  - `GravyMain` — server bootstrap. Wraps `GameServer.main`, writes a `<servername>.pid` file (handy for systemd / supervisord), patches `CommandBase.childrenClasses[29]` to register the `gutil` command, and starts a watchdog thread that registers the Lua globals once `ChatServer` finishes initialising.
  - `GutilCommand` — the `/gutil` dispatcher. `RequiredRight = 48` (admin).
  - `GravyLua` — exposes `spawnTree` and `growTree` to the global Lua env via `LuaManager.exposer`.
  - `Utilities/PorterCommand` — player binary import/export against `players.db`.
  - `Utilities/PriorityCommand` — flips the `whitelist.priority` column in the world DB.
  - `Utilities/ReloadWeatherCommand` — re-initialises `ClimateManager` and `ErosionMain` without restarting the server, and clears any `OnInitSeasons` Lua handlers that would otherwise fire twice.
  - `Utilities/TreeManipulator` — reflection-driven tree spawning and growth via `ErosionWorld` + `NatureTrees$CategoryData`.

The mod also inserts a **new server entry point**: PZ's launcher must invoke `zombie.gravymod.GravyMain` instead of `zombie.network.GameServer`. Update your server start script accordingly (the original arguments still pass through verbatim).

## Commands

All chat commands are admin-only (`RequiredRight = 48`):

```
/gutil reloadweather
/gutil porter [in|ex] [username | user1,user2 | * | *alive]
/gutil setpriority [username] [true|false]
/gutil setmaxplayers [2..100]
/gutil setserverpass [password]   (min 3 chars)
/gutil clearserverpass
/gutil slowlua [milliseconds]     (0 = disable)
```

`porter` shuttles raw player binaries between `players.db` and a host-side directory:

- Windows: `C:\player-mover\<username>.bin`
- Linux: `/opt/player-mover/<username>.bin`

The directory must exist and be writable by the server process. `*` exports every player; `*alive` skips dead ones.

## Lua API

Available globally inside any server-side Lua script:

```lua
spawnTree(gridSquare, treeName, stage)   -- returns true on success
growTree(gridSquare)                     -- bumps stage by 1, max 5
```

Recognised tree names (case-sensitive, must match exactly):

```
American Holly, Canadian Hemlock, Virginia Pine, Riverbirch,
Cockspur Hawthorn, Dogwood, Carolina Silverbell, Yellowwood,
Eastern Redbud, Redmaple, American Linden
```

The grid square must be at z=0 and free of existing trees, or the call returns false (with a `DebugLog` reason).

## Compatibility

- **Target:** server. Install on the **server** profile (`necroid --target server install gravymod`).
- Requires you to change the server entry point class. Stock launchers that hard-code `zombie.network.GameServer` will skip GravyMod's bootstrap entirely — you'll see no errors, just no `gutil` command.
- Stacks cleanly with `no-radio-fzzt`; they touch entirely separate subsystems.
- Uses Java reflection against private fields (`CommandBase.childrenClasses[29]`, `ServerWorldDatabase.conn`, `ErosionMain.World`, `NatureTrees$CategoryData`). A future PZ patch that renames or reorders these will break GravyMod and require a re-derivation. Run `necroid status gravymod` after every PZ update.
