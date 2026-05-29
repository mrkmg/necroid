# vehicle-teleport

Server-side primitive for teleporting a vehicle to an arbitrary world coordinate, including across unloaded cells. Vanilla `BaseVehicle.setX/Y/Z` only moves the simulation — clients keep rendering the vehicle at its old position, and the physics body desyncs. This mod ejects passengers, flips network authority, snaps the world transform, re-parents the chunk pointer, and forces every client to re-sync the vehicle from scratch.

Exposed as a single Lua global. The intended use is from admin commands or scripted events — there is no in-game UI.

## What it changes

- `zombie.vehicles.BaseVehicle` — adds `modVehicleTeleport_markFullDirty()`, which clears the per-client transmit state so the next net update is a full vehicle snapshot rather than an incremental diff.
- `zombie.Lua.LuaEventManager` — exposes the global Lua function.
- Adds `zombie.mod.vehicleteleport.VehicleTeleportLua` — the teleport entry point.

## Lua API

```lua
teleportVehicle(vehicle, x, y, z)  -- returns true on success, false on rejection
```

Server-only — calling from a client returns false. Rejected if:

- vehicle is nil,
- the vehicle is towing or being towed,
- `WorldSimulation` or `GameServer.udpEngine` is unavailable,
- target cell is invalid or not loaded server-side,
- target grid square doesn't resolve,
- target grid square's `IsoChunk` is null (cell is loaded but the specific chunk hasn't been instantiated yet — would orphan the vehicle).

On success the function:

1. Ejects every passenger via the normal `exitVehicle` path.
2. Sets server authority (`Authorization.Server`, owner = -1) and unlocks server updates so the new transform takes effect immediately.
3. Zeroes velocity / throttle and shuts the engine off.
4. Writes the world transform, updates `x/y/z`, re-parents the chunk pointer, and adds the vehicle to the destination chunk's vehicle list.
5. Marks per-client state dirty and emits a `Vehicles` packet (op `8`, "remove from client") so every receiver discards its cached copy and re-fetches a fresh full snapshot.

## Durability

Chunk-list mutation is serialized (single static lock per server) and the vehicle is added to the destination chunk's list **before** being removed from the source chunk's list, so no concurrent save snapshot can see the vehicle missing from every chunk. Each chunk-list mutation also synchronizes on the chunk object itself.

Known limitation: there is **no forced disk flush** after teleport — the vehicle's new chunk is written on the next regular world-save cycle. If the server crashes within that window, the vehicle reloads from whichever chunk file was most recently saved (typically the source chunk's last save, i.e. its previous location). The vehicle is not lost, but the teleport may not have persisted.

## Compatibility

- **Target:** server. Not client-only — install to client for SP/host or to server for dedicated.
- Will not move a tow train — detach trailers first.
- Destination cell must already be loaded server-side. Pair with [forever-chunk](../forever-chunk-41/README.md) if you need a guaranteed-loaded landing zone.
- Uninstalling restores vanilla `BaseVehicle` and removes the Lua global.
