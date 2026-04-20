# weather-tweaks

Preserves client-side `ClimateFloat`, `ClimateColor`, and `ClimateBool` modded overrides across the server's ~10-minute `PacketUpdateClimateVars` sync so Lua weather mods (e.g. Wasteland) don't flash.

## What it does

Vanilla `ClimateManager.readPacketContents` case 0 loops every `ClimateFloat` and runs:

```java
var4.internalValue = var4.finalValue;
var4.setOverride(var1.getFloat(), 0.0F);
```

When a client mod has set `setEnableModded(true)` + `setModdedValue(x)` + `setModdedInterpolate(>0)` on a float, the modded lerp pulls `internalValue` toward `moddedValue` between ticks while the server's weather override drags `finalValue` the other way. At each sync tick `internalValue = finalValue` wipes the modded lerp progress in one frame → visible flash.

This mod gates the sync on modded state — server bytes are always consumed (alignment preserved) but writes are skipped when modded is active:

- **Floats**: skip when `isModded && modInterpolate > 0` (consume 1 float from buffer).
- **Colors**: skip when `isModded && modInterpolate > 0` (drain `override.read(buf)` to consume `ClimateColorInfo` bytes).
- **Booleans**: skip when `isModded` (Bool has no interpolate; calculate() already prioritizes modded, but we also avoid flipping `isOverride`).

Unmodded entries behave exactly as vanilla.

## Scope

- Target: `client`
- Files patched: `zombie/iso/weather/ClimateManager.java` (single hunk covering floats, colors, booleans in case 0)
- No new classes, no API changes.

## Use case

Runs silently in the background. Install it on a client that uses a Lua weather mod and the 10-minute flash disappears. On a client with no Lua weather mod it is a no-op.
