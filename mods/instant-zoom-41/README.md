# instant-zoom

Removes the smooth zoom interpolation. Mouse-wheel zoom (and right-click auto-zoom) snaps directly to the target level instead of animating between zoom stops.

## What it changes

- `zombie.core.textures.MultiTextureFBO2.update()` — replaces the per-frame lerp from `zoom[var1]` toward `targetZoom[var1]` with an immediate assignment. The grid-stack recalc flag (`dirtyRecalcGridStackTime`) still fires on change.

That's the entire patch. No new classes, no UI changes.

## Usage

Zoom with the mouse wheel as normal. Each scroll snaps to the next level instead of gliding.

## Compatibility

- **Target:** client.
- Stacks cleanly with `more-zoom` (and the extra zoom levels also snap).
- Does not affect multiplayer; zoom is local-only.
- Uninstalling restores the vanilla smooth zoom.
