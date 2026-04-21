# staff-priority

Server-side mod that makes staff — any user with `accesslevel` set to `admin`, `moderator`, `overseer`, `gm`, or `observer` — jump to the **front** of the login queue when the server is full. Staff also go ahead of VIP / priority users (players flagged with `priority=1` in the server DB) who already enjoyed queue preference in vanilla.

Use this on dedicated servers where you want staff to always be able to get in promptly during peak hours without manually kicking someone.

## Queue ordering

Three tiers, drained in order by the existing `LoginQueue.loadNextPlayer()` pipeline:

1. **Staff** — any non-`player` access level. FIFO among themselves.
2. **VIP / priority** — DB `priority=1`, no `accesslevel`. FIFO among themselves.
3. **Regular** — everyone else. FIFO.

A staff member arriving while the preferred queue already contains VIPs is inserted *before* the first VIP, so they are served next — VIPs keep their order relative to each other and remain ahead of the regular queue.

**No slot bypass.** Staff still respect `MaxPlayers`; if a slot isn't open the queue fills normally. The mod only changes the *order* within the queue, not the cap.

## What it changes

- `zombie.network.LoginQueue.receiveServerLoginQueueRequest` — derives an `isPreferred` flag from both the vanilla `UdpConnection.preferredInQueue` (DB priority) and the connection's `accessLevel`. Staff are routed into `PreferredLoginQueue` regardless of their DB priority column, and inserted ahead of any non-staff entries already in that queue. Staff-vs-staff order is preserved (plain FIFO insert at the staff / non-staff boundary).
- Added import `zombie.commands.PlayerType` so the patch can test `accessLevel > PlayerType.player` instead of hardcoding the byte constant.
- **Incidental decompile fix:** Vineflower's output shadows the class name `LoginQueue` with the private field of the same name (`private static ArrayList<UdpConnection> LoginQueue`), which prevents the source file from compiling (`LoginQueue.LoginQueueMessageType` resolves to the field). All six occurrences have been rewritten to reference the nested enum directly (`LoginQueueMessageType.ConnectionImmediate`, etc.) — semantically identical; required to get `javac` to produce a class file at all.

## Usage

Install on your server:

```
necroid install staff-priority --to server
```

Restart the server for the patched class to take effect. Connect a staff account while the server is full to verify — the server log will read `"name" attempting to join used preferred queue`, and the connection will be served on the next free slot ahead of any queued VIPs or regular players.

## Compatibility

- **Target:** server. `clientOnly: false` — the patch also works on a locally-hosted coop host, but see the next bullet for the coop caveat.
- **Local Steam coop limitation:** when PZ is run as a Steam coop host (`CoopSlave` branch of `GameServer.receiveLogin`), every joiner is hard-coded to `accessLevel = 1` (regular player) at login time; staff status is only granted post-connect via admin commands. On that path the mod has nothing to key off of at queue-placement time, so behaviour is identical to vanilla. Real dedicated servers (the common case, both `ProjectZomboid64` + `ProjectZomboidServer` and the `380870` dedicated-server profile) take the non-coop branch and are fully covered.
- **Queue toggle:** the feature relies on the vanilla login queue (`ServerOptions.LoginQueueEnabled = true`, the default). If the queue is disabled server-side, there is nothing to jump in front of and the mod is a no-op.
- Pairs cleanly with other Necroid server mods (`gravymod`, `no-radio-fzzt`, `lua-profiler`) — none of them touch `LoginQueue.java`.
- Uninstall restores the stock queue ordering: `necroid uninstall staff-priority --to server`.
