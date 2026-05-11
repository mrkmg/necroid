# forever-chunk

Server-side primitive for keeping a chunk loaded indefinitely. Exposes a small Lua API that allocates a persistent world slot for a key, holds the cell containing that slot loaded while there are outstanding requests, and persists allocations across server restarts.

Useful as a building block for Lua mods that need a guaranteed-loaded staging area on the server (e.g. background simulation, holding spaces, scripted spawners).

## What it changes

- `zombie.network.ServerMap` — ticks the manager every server frame and triggers a save on shutdown.
- `zombie.Lua.LuaEventManager` — exposes the global Lua functions and registers the `ForeverChunkLoaded` event.
- Adds `zombie.mod.foreverchunk.{ForeverChunkLua, ForeverChunkManager, Slot}` — the manager, the Kahlua-exposed entry points, and a tiny coord record.

## Lua API

All four functions are exposed as globals (server-side).

| Function | Returns | Notes |
|---|---|---|
| `requestForeverChunk(id, key)` | — | Reserve a slot for `key` (allocating one if new) and tag this `id` as a holder. Triggers `ForeverChunkLoaded` once the cell is loaded. |
| `releaseForeverChunk(id)` | — | Drop this `id`'s hold. When the last holder of a `key` releases, the slot enters a grace period before becoming unloadable. |
| `destroyForeverChunk(key)` | `bool` | Permanently free a slot. Fails (returns false) if any holders are still attached. |
| `getForeverChunkCoords(key)` | `int[3]` or `nil` | Read the allocated `(x, y, z)` for a key, or nil if unallocated. |

### Event

```lua
Events.ForeverChunkLoaded.Add(function(id, x, y, z, key)
  -- fired once per request, after the cell is confirmed loaded
end)
```

`id` is the same value passed to `requestForeverChunk`. The event fires for *each* request, even when multiple requests target the same `key`.

### Allocation model

- Slots are assigned from a configurable rectangular pool (default `(0,0)`–`(50,50)`, world tile coords).
- A `key` maps to exactly one slot for its lifetime. Identical keys share a slot and a refcount.
- On first allocation, the manager places a floor sprite at the slot so it's safe to walk on / build on.
- Allocations are persisted to `<save>/forever-chunk-state.json` and reloaded on server start.

## Config

Written on first run to `<save-dir>/forever-chunk.json`:

```json
{
  "minX": 0,
  "minY": 0,
  "maxX": 50,
  "maxY": 50,
  "gracePeriodSeconds": 30,
  "floorSprite": "floors_exterior_natural_01_0"
}
```

- `minX/Y` … `maxX/Y` — inclusive bounds of the slot pool, in world tile coordinates. Pick somewhere out of player play areas.
- `gracePeriodSeconds` — how long a slot's cell stays kept-loaded after its last holder releases.
- `floorSprite` — sprite placed on the slot when it's first allocated.

State persists in `<save-dir>/forever-chunk-state.json`.

## Compatibility

- **Target:** server. Not client-only — install to client for SP/host or to server for dedicated.
- The chosen pool region should be a barren / unused area of the map. The manager places a floor tile in each allocated slot.
- Uninstalling does not remove the saved state files — re-installing later picks up where you left off.
