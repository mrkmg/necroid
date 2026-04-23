# ghost-zombie-fix

Server-side fix for "ghost zombies" in multiplayer — zombies visible to exactly one client that, when hit, get stuck in a dying animation forever. Worse on lossy / high-latency connections.

## What a ghost zombie is

Symptom: a zombie appears on one client only. Other players can't see it. When the affected player swings at it, the zombie enters the death animation but never drops to a corpse — it sits on the ground in a permanent dying pose. Reported more often by players on worse connections, but not exclusive to them.

## Root cause

The server tells clients to forget a zombie via `NetworkZombiePacker.deleteZombie()`, which queues the ID into `zombiesDeleted`. Each server tick, `postupdate()` drains the queue into `zombiesDeletedForSending`, then `send(UdpConnection)` iterates per-connection and packs the IDs into the outgoing `ZombieSimulation` packet — **once** — before the queue is cleared. No retry.

The packet is typically sent as `PacketTypes.PacketType.ZombieSimulation` (unreliable UDP). The reliable variant `ZombieSimulationReliable` fires at most once every 5 s globally (gated by `ZombieSimulationReliableLimit`). If the one unreliable packet carrying a delete is dropped, the delete is lost forever — the client still has the zombie in `GameClient.IDToZombieMap`.

Downstream consequence: when the player later hits that stale zombie, the client sends a `DeadZombiePacket` to the server. Server's `ServerMap.ZombieMap.get(id)` returns `null`, `isConsistent()` is false, the death packet is silently dropped, and no `BecomeCorpse` broadcast is sent back. The zombie locks in the dying animation because the client only creates the `IsoDeadBody` when the server broadcasts confirmation.

## What this mod does

Patches `zombie/popman/NetworkZombiePacker.java::send(UdpConnection)` with two small changes:

- **Send gate** — extend the "should we emit a packet this tick" check so a tick whose only payload is delete IDs still ships. Previously a delete-only tick could be skipped when `getZombieData` returned 0 and the send timer hadn't expired.
- **Packet-type selection** — if at least one delete ID was written into the buffer for this connection, force `PacketTypes.PacketType.ZombieSimulationReliable`. The vanilla `ZombieSimulationReliableLimit` (5 s global) still governs empty-delete heartbeats, so this doesn't change packet rate in the common case.

Net effect: any outgoing zombie packet carrying deletions now rides RakNet's reliable channel, which retransmits on loss. The vast majority of ghost-zombie cases caused by unreliable packet drop are eliminated.

## Why this is cheap

Zombie deletions happen on kills, chunk unloads, and player-disconnect sweeps — not every tick. Empty-delete ticks stay unreliable. Bandwidth increase is negligible. The reliable packet type uses the same wire format and parse path, so vanilla and modded clients both consume it unchanged.

## What it does NOT fix

- **Relevance-filter races** — `send()` (line 199 in vanilla) skips writing a delete ID to a connection that is not currently `RelevantTo(x, y)` of the zombie's last-known position. A client out of range at the instant of deletion never gets the ID written into any packet, reliable or otherwise. This mod doesn't touch that gate. Residual ghost rate from this gap will be rare but non-zero.
- **No reverse sync** — the client never asks the server "are these IDs I have still valid?" so a truly desynced ID lingers until the zombie is hit or the player disconnects.
- **Client-side death lockup** — if a ghost *does* make it past both mods, the client-side `DeadZombiePacket` still gets dropped by the server's `isConsistent()` check and the zombie still locks in the dying animation. That's a separate fix.

## Scope

- clientOnly: **false** — patches a server class.
- Files patched: `zombie/popman/NetworkZombiePacker.java`.
- No new classes, no new packet types, no API changes, no Lua.

## Deployment

- **Server**: `necroid install ghost-zombie-fix --to server`. This is the intended target.
- **Client**: no benefit — `NetworkZombiePacker` is only exercised on the authoritative server.
- Vanilla clients receive `ZombieSimulationReliable` via the same code path as `ZombieSimulation`, so you do **not** need to patch clients.

## Compatibility

Stacks cleanly with `no-zombie-cull-41`. That mod patches `NetworkZombiePacker.receivePacket()` and `ZombieCountOptimiser.incrementZombie()`; this mod patches `NetworkZombiePacker.send()`. No overlap.

## Verification

1. `necroid test` — compile gate.
2. `necroid install ghost-zombie-fix --to server` — install gate (atomic compile + copy).
3. Dedicated server + two vanilla clients; one client throttled to ~5 % packet loss and ~150 ms RTT (clumsy on Windows, `tc qdisc add dev <iface> root netem loss 5% delay 150ms` on Linux). Rapidly kill zombies while the lag client walks in and out of range. Baseline (unpatched server): stuck-dying zombies accumulate over 5–15 min. With this mod installed on the server: stuck-dying rate drops sharply.
4. Rollback: `necroid uninstall --to server` restores the original `NetworkZombiePacker.class`.
