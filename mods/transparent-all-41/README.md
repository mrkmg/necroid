# transparent-all

Extends [admin-xray](../admin-xray-41/README.md)'s **Shift+F9** transparent-trees toggle so it fades **every** static world object on screen — doors, windows, walls, fences, mannequins, fridges, washers, fireplaces, barbecues, barricades, curtains, garage doors, generators, jukeboxes, signs, and yes still trees — down to ~25 % alpha. With this installed alongside admin-xray, an admin can spot a hiding griefer or a stuck zombie inside a building, behind a closed door, or wedged against the back wall of a hardware store, instead of only being able to see *through trees*.

Players, zombies, NPCs, dead bodies, and vehicles are unaffected — they're the things you actually want to see.

## Dependencies

- **admin-xray** (required). Provides the Shift+F9 keybind, the `treeOverride` flag this mod reads, and the access-level gate. `necroid install transparent-all` automatically pulls admin-xray into the install stack.

## Usage

Same UX as admin-xray — there is no new keybind:

1. Join a server (or single-player game) as a user with **admin** access — or launch the game with `-debug`.
2. Press **Shift+F9**. Console prints `[admin-xray] treeOverride=true`. Every static IsoObject on screen drops to ~25 % alpha.
3. Press **Shift+F9** again to turn it back off.

Bare **F9** (the LOS / wall-cutaway override) keeps its admin-xray behaviour and is independent of this mod.

State resets to off whenever the game is restarted.

## What it changes

- New class `zombie.transparent_all.TransparentAll` — holds the fade alpha (`XRAY_ALPHA = 0.25F`) and the `shouldFade(IsoObject)` gate that returns true when admin-xray's `treeOverride` is on **and** the object is not an `IsoMovingObject` (so players, zombies, dead bodies, and pushable physics objects are excluded).
- `zombie.iso.IsoObject` — wraps the base `render(...)` method with a save / force / restore around the per-player alpha. Because almost every static IsoObject subclass (`IsoDoor`, `IsoWindow`, `IsoBarricade`, `IsoCurtain`, `IsoFire`, `IsoFireplace`, `IsoBarbecue`, `IsoTrap`, `IsoWorldInventoryObject`, `IsoBall`, `IsoBloodDrop`, `IsoBrokenGlass`, `IsoFallingClothing`, `IsoMolotovCocktail`, `IsoZombieHead`, `IsoZombieGiblets`, washer/dryer/stove/fridge/jukebox/television/generator variants, etc.) ultimately calls `super.render(...)`, the single base hook fades them all without per-class patching.
- `zombie.iso.objects.IsoMannequin` — same wrap, applied directly to its overridden `render(...)`. Mannequins draw via `SpriteRenderer.drawGeneric` + `DeadBodyAtlas` and bypass `super.render()`, so they need their own hook.
- `zombie.iso.objects.IsoTree` — re-routes admin-xray's hard-coded `0.25F` tree fade through the same `TransparentAll.XRAY_ALPHA` constant so trees fade at exactly the same opacity as everything else. Change `XRAY_ALPHA` in one place and **everything** moves together.

### Why a base-class hook instead of patching every subclass

Every sprite / texture draw inside an IsoObject's render path reads alpha via `this.getAlpha(playerIndex)`. Forcing that alpha at the top of the base `render()` propagates through every code path that reaches it — including the attached / overlay sprite passes and the outline-shader pass. Saving and restoring around the call also means vanilla's `updateAlpha()` (which runs inside the base method and would otherwise leave the fade "stuck" for one frame after the toggle clears) writes into our restored value, not the forced one. Toggle-off snaps back instantly with no per-object cleanup.

The only subclasses that bypass `super.render()` and draw their own sprites directly are `IsoTree` (already covered by admin-xray's dedicated tree path) and `IsoMannequin` (covered by the second hook here). All other static-object subclasses inherit the fade for free.

### Why moving objects are excluded

`IsoMovingObject extends IsoObject`, so a naive base hook would also fade players, zombies, NPCs, dead bodies, and pushable furniture. The whole point is to see *through* world geometry to find players / zombies, so the `!(obj instanceof IsoMovingObject)` discriminator in `TransparentAll.shouldFade` keeps them at full opacity. Vehicles render through a separate path and are unaffected.

## Compatibility

- **Target:** client. Install on the **client** profile only (admin-xray is client-targeted).
- Stacks cleanly on top of admin-xray; depends on it explicitly. Stacks cleanly with `no-radio-fzzt` and `more-zoom`.
- Does not modify save files, network state, or anything server-side. Other players see no change. Multiplayer-safe.
- Uninstalling restores normal object rendering immediately. `necroid uninstall transparent-all --to client` leaves admin-xray's tree-only fade intact; `necroid uninstall --to client` (full) restores vanilla.

## Tuning

Open `patches/zombie/transparent_all/TransparentAll.java.new` and change `XRAY_ALPHA = 0.25F` to whatever opacity you want (e.g. `0.10F` for a deeper fade, `0.40F` for a softer one). Re-`enter` and `capture` if you've staged it through the working tree, or just edit the source patch directly and re-`install`. One constant — trees, doors, mannequins, and every other static object move together.
