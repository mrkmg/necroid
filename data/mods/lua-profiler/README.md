# lua-profiler

Per-mod Lua profiler for Project Zomboid. Three sampling strategies, mod- and file-scoped filters, and Chrome-trace flame-graph output. Designed for figuring out which Workshop mod is eating your frame time.

The profiler is dormant until you call one of the global functions from the in-game Lua console — there is no per-tick overhead when it isn't running.

## What it changes

- New class `zombie.Lua.LuaProfiler` — the profiler itself: aggregate counters, sampler thread, Chrome-trace serializer, and a small Lua API installed lazily into the global env on first use.
- `zombie.Lua.Event.trigger` — wraps every callback dispatch with a `nanoTime` measurement and feeds the timing into `LuaProfiler.record(...)`. The wrapper is wrapped in a try/catch so a profiler bug can never break event dispatch. The vanilla "SLOW Lua event" warning still fires when enabled.

## Modes

| Mode | What it measures | When to use |
|------|------------------|-------------|
| `event` (default) | Per-event + per-closure call count and total ns, captured from the `Event.trigger` hook. | Day-to-day mod hunting — which event handlers are slow, broken down by file. |
| `builtin` | Flips Kahlua's hidden `doProfiling` flag and reads its internal `profileEntries` table. | Function-level breakdown of arbitrary Lua code, not just event callbacks. |
| `sample` | Background thread samples the current coroutine's call stack at a configurable interval; emits a Chrome-trace flame graph. | Visual flame graphs in `chrome://tracing` or Speedscope. |

## Lua API

Run from the in-game Lua console (or any mod's Lua):

```lua
LuaProfilerStart(seconds)              -- begin a timed window
LuaProfilerStop()                      -- end early (auto-dumps in sample/event mode)
LuaProfilerDump()                      -- write report to disk
LuaProfilerReset()                     -- clear aggregates
LuaProfilerEnable() / Disable()        -- toggle aggregation (event mode)

LuaProfilerMode("event"|"builtin"|"sample")
LuaProfilerFilter({ mods = {"ModA","ModB"}, files = {"foo.lua"} })
LuaProfilerSampleInterval(microseconds)  -- sample mode only, default 1000us (1ms), min 20us, max 10000us
```

`LuaProfilerStart(seconds)` arms a deadline in both `event` and `sample` mode — when it expires the profiler auto-stops and writes its output to disk without a manual `Dump()`. `builtin` mode ignores the duration and still requires explicit `Stop()` + `Dump()`.

Filters are allowlists: leave `mods` empty to capture every mod, set it to `{"MyMod"}` to capture only that one. `files` works the same way for individual `.lua` files. In `sample` mode, filters keep whole-sample call chains intact — frames outside the filter still appear as ancestor boxes in the flame graph, but only matching frames are counted in the aggregate CSV.

Output goes under the Zomboid user dir (the same place `DebugLog` writes) — the dump function logs the exact path. Files are named with a timestamp, so repeated dumps don't overwrite each other.

## Usage

Quick session against a single mod:

```lua
LuaProfilerMode("event")
LuaProfilerFilter({ mods = {"SuspectMod"} })
LuaProfilerStart(30)        -- profile for 30 seconds
-- ...play normally...
LuaProfilerDump()
```

Flame graph for the entire engine:

```lua
LuaProfilerMode("sample")
LuaProfilerSampleInterval(500)   -- 500us = 2kHz sampling (floor is 20us = 50kHz)
LuaProfilerStart(15)             -- auto-dumps after 15s, no Dump() needed
```

Drop the resulting `*.json` into [Speedscope](https://www.speedscope.app/) or `chrome://tracing`.

## Compatibility

- **Target:** client.
- Stacks cleanly with the other client mods (`admin-xray`, `more-zoom`, `radio-fix`).
- Touches `zombie.Lua.Event` — any other future mod that also patches `Event.trigger` will conflict at install time and fail to merge. None of the bundled mods do.
- Uninstalling removes both the new class and the Event hook; the vanilla SlowLuaEvents warning continues to work either way.
