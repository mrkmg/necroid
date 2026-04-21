# more-zoom

Adds extra camera zoom levels: 800%, 400%, 300%, 25%, and 12%.

Mostly useful for screenshotting and cinematic purposes, but also just fun to have more zoom range to play with.

800% will TANK your FPS. Use with caution on lower end hardware.

## What it changes

- `zombie.core.textures.MultiTextureFBO2` — extends the default `zoomLevelsDefault` array to include 8.0×, 4.0×, 3.0×, 0.25×, 0.12× endpoints, and ensures 300 and 25 are always merged into any user-defined `ZoomLevels` config string.

That's the entire patch. No new classes, no UI changes — the existing zoom UI just gains two more stops.

## Usage

Zoom with the mouse wheel as normal. The new stops appear at the ends of the existing scale.

If you've set custom zoom levels via `ZoomLevels=...` in your config, the mod still injects 300 and 25 into your list — your custom levels are preserved.

## Compatibility

- **Target:** client.
- Stacks cleanly with `admin-xray` and `no-radio-fzzt`.
- Does not affect multiplayer; zoom is local-only.
- Uninstalling restores the vanilla 9-level zoom range.
