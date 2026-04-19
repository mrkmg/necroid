# admin-xray

Press **F9** in-game to toggle a staff line-of-sight override. Fog of war clears, dark squares become fully lit, and the camera-side walls of **every** building on screen drop to their cutaway sprite — useful for moderating, finding griefers, or debugging map issues without spawning a noclip character.

Only players with an admin access level (or the game running with `-debug`) can toggle the override. Everyone else's F9 keypress is ignored, so the patched class is safe to ship in mixed-population installs.

## What it changes

- New class `zombie.admin_xray.AdminXray` — holds the boolean toggle, the access-level check, and the white-light constants used while the override is on.
- `zombie.input.GameKeyboard` — adds an F9 down-edge handler that flips `AdminXray.losOverride`.
- `zombie.iso.IsoGridSquare` — when the override is on, every `isCanSee` / `isCouldSee` / `bSeen` / `darkMulti` / `lightInfo` / `lightverts` query returns "fully lit, fully visible" regardless of actual lighting. `getPlayerCutawayFlag` also returns `true` for every square, so the wall-cutaway render path treats every building as "cut" — the camera-side (N + W) walls of every building on screen drop to their cutaway sprite, not just walls of the room the player is currently standing in.
- `zombie.iso.IsoCell` — the `maxZ` clamps that normally shrink the Z render range to `player.z + 1` when the player is inside a building (or peeking through a window) are skipped. `IsDissolvedSquare` short-circuits to `true` for every square above the player's Z, so upper floors of every building dissolve out of the render stack and no longer occlude the rooms below.

## Usage

1. Join a server (or single-player game) as a user with **admin** access — or launch the game with `-debug`.
2. Press **F9**. Console prints `[admin-xray] losOverride=true`.
3. Press **F9** again to turn it off.

State resets to off whenever the game is restarted.

## Compatibility

- **Target:** client. Install on the **client** profile only.
- Stacks cleanly with `radio-fix` and `more-zoom`. Does not touch radio or zoom code.
- Does not modify save files. Uninstalling restores normal LOS immediately.
- Multiplayer-safe: the override is purely a client-side render trick; the server never knows it's on, and other players see no change.
