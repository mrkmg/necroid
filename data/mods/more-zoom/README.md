# more-zoom

Adds two extra camera zoom levels: **300%** (one step further out than vanilla's max) and **25%** (one step further in than vanilla's min). Mouse-wheel zoom and the in-game zoom slider both pick them up automatically.

Useful for spotting distant zombie hordes from a rooftop, or for melee combat where vanilla's closest zoom still feels far away.

## What it changes

- `zombie.core.textures.MultiTextureFBO2` — extends the default `zoomLevelsDefault` array to include 3.0× and 0.25× endpoints, and ensures 300 and 25 are always merged into any user-defined `ZoomLevels` config string.

That's the entire patch. No new classes, no UI changes — the existing zoom UI just gains two more stops.

## Usage

Zoom with the mouse wheel as normal. The two new stops appear at the ends of the existing scale.

If you've set custom zoom levels via `ZoomLevels=...` in your config, the mod still injects 300 and 25 into your list — your custom levels are preserved.

## Compatibility

- **Target:** client.
- Stacks cleanly with `admin-xray` and `radio-fix`.
- Does not affect multiplayer; zoom is local-only.
- Uninstalling restores the vanilla 9-level zoom range.
