# no-zombie-cull

Stops PZ from silently despawning zombies out of **loaded/live chunks** when the client's local zombie count crosses 500.

## What it does

Vanilla PZ runs a client-side "zombie count optimiser" that every tick evaluates each zombie and, once the local count exceeds 500, randomly picks off-screen ones (past 20 tiles, outside the player's vision cone) and queues them in `zombiesForDelete`. The list is then serialised into the next `ZombieSimulation` packet; the server reads the IDs in `NetworkZombiePacker.receivePacket()` and calls `VirtualZombieManager.removeZombieFromWorld(...)` on each — **that's where the zombie actually dies**.

This mod disables the cull on both ends:

- **`zombie/popman/ZombieCountOptimiser.java`** — `incrementZombie()` no-ops. Nothing is ever queued for deletion.
- **`zombie/popman/NetworkZombiePacker.java`** — `receivePacket()` still drains the delete-ID bytes from the buffer (so the rest of the packet parses correctly) but skips the `ServerMap` lookup, the re-broadcast, and the `removeZombieFromWorld` call. A server running the mod ignores cull proposals from any client, vanilla or modded.

## What it does NOT change

- **Population spawning** — `PopulationMultiplier`, `RespawnMultiplier`, `RespawnUnseenHours` in sandbox settings are untouched. Natural respawn/redistribution behaviour is unchanged.
- **Stale-zombie cleanup in `IsoZombie.updateInternal()`** — the 800 ms and 5000 ms timeouts for remote zombies the server has stopped updating are left in place. Those are client-side ghost-cleanup, not live-chunk culling; touching them risks accumulated phantom zombies on lossy links.
- **Player-disconnect sweep** — `NetworkZombieManager.removeZombies(UdpConnection)` still runs when a player leaves. Not considered "live chunk" culling.

## Scope

- clientOnly: **false** — patches classes used on both client and server.
- Files patched: `zombie/popman/ZombieCountOptimiser.java`, `zombie/popman/NetworkZombiePacker.java`.
- No new classes, no API changes, no Lua.

## Deployment

- **Best:** install on **both** client and server (`necroid install no-zombie-cull --to client` + `--to server`). Defense in depth — client never proposes the cull, server would ignore it anyway.
- **Server-only:** fine for dedicated-server operators who can't control client builds. Vanilla clients still *propose* culls, but the server drops them.
- **Client-only (single-player):** sufficient for SP; in MP it only disables that one client's proposals and other vanilla clients can still cull.

## Use case

Maps, mods, or events that pile zombies beyond the 500 cap (hordes, towns, sirens) where the vanilla optimiser silently eats them off-screen. Expect higher CPU load at extreme zombie counts — the cap exists for a reason.

## Verification

1. `necroid test` — compile gate.
2. Spawn >500 zombies near a player and walk around. Vanilla: edge zombies disappear. Modded: count stays above 500.
3. With modded server + vanilla client, watch `MPStatistics.serverZombieCulled` — should stay at 0.
