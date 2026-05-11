# fly-camera

Detach the camera from the player. Hold middle-mouse + drag to pan to a free world position; release to leave the camera anchored there. Click middle-mouse (no drag) to smoothly recenter on the player. The camera keeps the player on screen automatically — when the player walks toward the edge of the view, the anchor lerps along just enough to hold them inside a buffered region. Staff (any access level other than `None`) and debug-mode (`-debug`) players are exempt from the keep-on-screen clamp and can roam the camera freely.

## What it changes

- `zombie.iso.PlayerCamera` — adds a fly hook at the top of `update()`. While engaged, writes the camera's `RightClickX/Y` offset to the screen-space delta between a stored world anchor and the player position. Six accessors (`getOffX/Y`, `getTOffX/Y`, `getLastOffX/Y`) short-circuit to a precomputed `flyCamX/Y` while active so the offset stays exact across the multiple `update()` call-sites per frame and survives the cloned render-side `PlayerCamera` (also patched in `copyFrom`).

The fly anchor is stored in **world coordinates**, so player movement does not slide the view — the screen offset is recomputed every frame from `XToScreen(anchor) − XToScreen(player)`.

## Usage

| Input | Effect |
|---|---|
| Hold middle-mouse + drag | Pan the camera in the drag direction (zoom-scaled). |
| Release after dragging | Camera stays anchored at the panned world position. |
| Click middle-mouse (no drag) | Camera smoothly transitions back to the player. |
| Walk toward the screen edge | Anchor proportionally follows so the player stays inside the buffered region (skipped for staff / `-debug`). |
| Get into a vehicle | Camera smoothly transitions back; lead-cam takes over. |

While fly is active it overrides aim-pan and vehicle lead-cam. Once disengaged (smooth recenter complete), those modes resume normally.

## Tunables

Constants at the top of `PlayerCamera` (edit + `necroid capture` + `necroid install` to taste):

- `FLY_RETURN_LERP = 0.08F` — speed of the smooth recenter (click / vehicle entry).
- `FLY_KEEP_ON_SCREEN_LERP = 0.15F` — how aggressively the anchor follows when the player nears the edge.
- `FLY_SCREEN_BUFFER = 0.7F` — fraction of half-viewport the player can drift before keep-on-screen kicks in.
- `FLY_DEAD_ZONE = 4.0F` — pixel threshold separating click from drag.

## Compatibility

- **Target:** client. `clientOnly: true`.
- Only player index 0; secondary players (split-screen) unaffected.
- No interaction with the `PanCamera` keybind — that branch is skipped while fly is engaged and resumes after.
- Stacks cleanly with other camera mods (`instant-zoom`, `more-zoom`).
- Uninstalling restores vanilla camera behavior.
