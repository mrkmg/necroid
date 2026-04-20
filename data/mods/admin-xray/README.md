# admin-xray

Two independent staff render overrides:

- **F9** — line-of-sight override. Fog of war clears, dark squares become fully lit, and the camera-side walls of **every** building on screen drop to their cutaway sprite.
- **Shift+F9** — transparent-trees override. Every on-screen tree renders at ~25% alpha so you can see players, zombies, and terrain through dense forest.

Useful for moderating, finding griefers, or debugging map issues without spawning a noclip character.

Only players with an admin access level (or the game running with `-debug`) can toggle either override. Everyone else's keypress is ignored, so the patched classes are safe to ship in mixed-population installs.

## What it changes

- New class `zombie.admin_xray.AdminXray` — holds the two boolean toggles (`losOverride`, `treeOverride`), the access-level check, and the white-light constants used while LOS override is on.
- `zombie.input.GameKeyboard` — adds an F9 down-edge handler. Bare F9 flips `losOverride`; Shift+F9 (either shift key) flips `treeOverride`.
- `zombie.iso.IsoGridSquare` — when `losOverride` is on, every `isCanSee` / `isCouldSee` / `bSeen` / `darkMulti` / `lightInfo` / `lightverts` query returns "fully lit, fully visible" regardless of actual lighting. `getPlayerCutawayFlag` also returns `true` for every square, so the wall-cutaway render path treats every building as "cut" — the camera-side (N + W) walls of every building on screen drop to their cutaway sprite, not just walls of the room the player is currently standing in.
- `zombie.iso.IsoCell` — the `maxZ` clamps that normally shrink the Z render range to `player.z + 1` when the player is inside a building (or peeking through a window) are skipped. `IsDissolvedSquare` short-circuits to `true` for every square above the player's Z, so upper floors of every building dissolve out of the render stack and no longer occlude the rooms below.
- `zombie.iso.objects.IsoTree` — when `treeOverride` is on, `render` short-circuits: it force-sets the tree's alpha to `0.25` and does a single un-stenciled `renderInner` pass, then restores the original alpha state. This bypasses the vanilla fade state machine (which is stencil-gated to a small silhouette around the player) and paints every on-screen tree transparent. Un-highlighted trees only — chop-tree indicator still uses the vanilla path.

### Why a dedicated path for trees

Vanilla PZ already fades trees the player stands behind. The fade lives in `IsoTree.render` but is gated by two `glStencilFunc` passes: opaque where `stencil != 128`, faded where `stencil == 128`. The stencil=128 region is only written inside a circular silhouette around the player's character (the wall-cutaway stencil masks in `IsoCell` and `IsoGridSquare`). So just flipping `bRenderFlag = true` globally produces a small fade circle instead of a global fade — the opaque pass wins everywhere outside the silhouette. `treeOverride` sidesteps the whole two-pass stencil dance and writes one solid faded sprite.

## Usage

1. Join a server (or single-player game) as a user with **admin** access — or launch the game with `-debug`.
2. Press **F9**. Console prints `[admin-xray] losOverride=true`. Walls and lighting open up.
3. Press **Shift+F9**. Console prints `[admin-xray] treeOverride=true`. Trees fade to 25% alpha.
4. Press either again to turn it off. Toggles are independent — LOS state doesn't affect trees and vice versa.

State resets to off whenever the game is restarted.

## Compatibility

- **Target:** client. Install on the **client** profile only.
- Stacks cleanly with `radio-fix` and `more-zoom`. Does not touch radio or zoom code.
- Does not modify save files. Uninstalling restores normal LOS and normal tree rendering immediately.
- Multiplayer-safe: both overrides are purely client-side render tricks; the server never knows they're on, and other players see no change.
