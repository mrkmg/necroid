# multi-login

Server-side mod that lets the Project Zomboid dedicated-server login queue admit **N** players into the "now loading" slot concurrently instead of the vanilla hard-coded **one**. Big servers that saw conga-lines of players timing out on the Loading screen during peak (because the queue could only process them single-file) now just... don't.

Adds one server option — `MaxConcurrentLogins` (1 – 32, **default 3**) — editable in your `servertest.ini` like any other vanilla setting.

## What it changes

Vanilla's `zombie.network.LoginQueue` is built around a single `UdpConnection currentLoginQueue` field. While that field is non-null, no one else gets past the login handshake; they wait in `PreferredLoginQueue` / `LoginQueue` ArrayLists and receive periodic "position N in queue" updates. When the active loader finishes (or times out), the next player is dequeued. That's the bottleneck this mod removes.

Changes at a glance:

- **`ServerOptions.MaxConcurrentLogins`** — new `IntegerServerOption` (min 1, max 32, default 3). Parsed out of `servertest.ini` by the generic `ServerOptions` loader; no schema changes elsewhere.
- **`LoginQueue` core rewrite** — `currentLoginQueue` (single slot) becomes `currentLogins` (`ArrayList<UdpConnection>` of active slots). A parallel `HashMap<UdpConnection,Long> loginDeadlineMs` tracks per-slot timeout deadlines, replacing vanilla's single shared `UpdateLimit`. `loadNextPlayer()` fills every free slot each tick — preferred queue first, then regular — in a pair of while-loops. `update()` drains completed or timed-out slots individually, leaving healthy concurrent loaders untouched.
- **Immediate promotion on disconnect** — when an admitted connection drops, `disconnect()` frees its slot *and* calls `loadNextPlayer()` inline, so queued players are promoted without waiting for the 3.05 s `UpdateLimit` tick.
- **`receiveLogin` hardening** — the vanilla "impostor" branch kicks the legitimately-admitted player when an unexpected connection calls `receiveLogin`; the rewrite kicks the impostor instead. Strictly a bug fix.
- **Idempotent admission** — a repeated `LoginQueueRequest2` from an already-admitted connection (UDP retry, client double-send) re-acks `ConnectionImmediate` instead of being enqueued or taking a second slot. The original deadline stands — a client can't indefinitely extend its own slot timeout by spamming the queue request.
- **Synchronized getters** — `getDescription()` now reads `currentLogins` under the same monitor as every mutation, removing the vanilla-style race window.

Semantics at `MaxConcurrentLogins=1` collapse to exactly vanilla behaviour — the size-based guard (`< maxSlots()`) matches the old `== null` check at that value. Raising the number is the opt-in.

## Dependencies

- **staff-priority** (required). Builds on staff-priority's admission-guard changes — the `isPreferred` local that routes staff into the preferred queue is shared with multi-login's `slotFree` check, and staff's head-of-queue insertion behaviour stacks unchanged on top of multi-slot admission. `necroid install multi-login` automatically pulls staff-priority into the install stack.

In practice this means staff always get the next free slot — if `MaxConcurrentLogins = 3` and three regular players are loading, a connecting staff member will fill whichever of those slots frees first, ahead of any VIPs or regular players waiting.

## Usage

Install on your server:

```
necroid install multi-login --to server
```

By default the mod ships with `MaxConcurrentLogins = 3`. To tune it, restart the server once so the option is written into `servertest.ini`, then edit:

```ini
MaxConcurrentLogins=5
```

…and restart the server again for the new value to take effect. Range is `1`–`32`. Setting it to `1` makes the mod functionally identical to vanilla + staff-priority.

Confirm with the server console `/queue` hooks or the debug log — `LoginQueue.getDescription()` now reports `queue=[W/P/A/M/"guids"]` (waiting / preferred-waiting / active / max / active GUIDs).

## Tuning guidance

- **Small servers (2 – 4 GB RAM):** stick with the default `3`, or drop to `2` if you see memory pressure during peak joins. Each concurrent admission loads a player save in parallel.
- **Medium servers (8 GB):** `4`–`6` is comfortable.
- **Large community servers (16 GB+):** `8`–`12`. Going above that gives diminishing returns — save-load I/O and network packets start to dominate, not the queue itself.
- **32** is the hard upper bound. Treat it as a "don't care, just admit everyone" setting rather than a target.

Note on `MaxPlayers` interplay: `MaxConcurrentLogins` does not expand your player cap. If ten players are already in and `MaxPlayers = 12`, the queue still only has two slots to hand out — raising `MaxConcurrentLogins` can't create capacity that doesn't exist.

## Compatibility

- **Target:** server. `clientOnly: false` — runs on a locally-hosted coop host too, but the whole login queue is only meaningful on a real dedicated server with a non-trivial join rate.
- **Queue toggle:** relies on the vanilla login queue (`ServerOptions.LoginQueueEnabled = true`, the default). If the queue is disabled server-side, vanilla already admits everyone in parallel and this mod is a no-op.
- **Stacks on staff-priority** (required dep) — staff retain their head-of-preferred-queue behaviour; the queue loop simply fills slots preferred-first so staff continue to get the next open slot.
- Pairs cleanly with other Necroid server mods (`gravymod`, `no-radio-fzzt`, `lua-profiler`) — none of them touch `LoginQueue.java` or `ServerOptions.java`.
- `necroid uninstall multi-login --to server` restores the single-slot queue behaviour (staff-priority stays installed unless also uninstalled). Full rollback: `necroid uninstall --to server`.
