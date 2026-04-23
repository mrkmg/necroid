# notifications

A shared toast-notification surface other mods call into. A notification appears as a self-contained box in the bottom-right corner of the screen — background + border + text — that slides up into place, holds for a configurable window, then fades out. Stacked notifications push older ones upward as newer ones arrive, and an expiring toast lets the survivors drop back down to fill the gap.

This mod on its own does nothing visible — it's an **API mod**. It ships three public classes so other Necroid mods can display user feedback without re-rolling their own UI. [admin-xray](../admin-xray-41/README.md) is the first consumer: its **F9** / **Shift+F9** toggles now fire a toast telling you which override just flipped.

The mod adds no new keybinds, no new chat commands, and no per-frame work while no notifications are live — the `Toast` elements are only in `UIManager`'s render list for the duration of each notification.

## Usage (for other mods)

Add `"notifications"` to your mod's `mod.json` `dependencies`, then call the API from any client-side code path:

```java
// Minimum: default-styled line of text.
zombie.notifications.NotificationAPI.show("Hello, world");

// Full control via the fluent builder.
long id = zombie.notifications.NotificationAPI.show(
    zombie.notifications.NotificationSpec.of("Admin X-ray LOS: ON")
        .font(zombie.ui.UIFont.Medium)
        .textColor(1.0F, 1.0F, 1.0F, 1.0F)
        .backgroundColor(0.05F, 0.05F, 0.05F, 0.9F)
        .borderColor(0.35F, 0.90F, 0.35F, 1.0F)
        .borderPx(1)
        .padding(12, 8)
        .minWidth(160).maxWidth(480)
        .durationMs(4000).slideInMs(180).fadeOutMs(350)
        .margin(16, 16)           // from bottom-right corner
        .stackGap(6));

// Cancel one early (fades out over ~250ms, no sudden pop).
zombie.notifications.NotificationAPI.dismiss(id);

// Wipe the whole stack.
zombie.notifications.NotificationAPI.dismissAll();
```

Every builder setter returns `this`, clamps its input to a sane range, and falls back to the default if you pass something nonsensical (negative pixels, NaN colors, etc.). Text longer than `maxWidth` is truncated with an ellipsis.

Defaults: `UIFont.Small`, white text on a near-black translucent box, thin grey border, 3.5 s lifetime, 180 ms slide-in, 350 ms fade-out, 16 px margin from the bottom-right corner, 6 px between stacked entries.

## Limits

- **Max 8 live at once.** The 9th call force-expires the oldest entry over a short fade; prevents a misbehaving consumer from flooding the screen.
- **Single-line only.** `maxWidth` truncates with `…`; proper word-wrap is a planned follow-up.
- **No textures.** Background is a solid color fill. Custom icons are a follow-up that pairs with the upcoming "ship assets into the game folder" feature.
- **Client-only.** The mod is `clientOnly: true` — it renders through `UIManager` which only exists on the game client. Server-side mods that want to notify a player need a network packet into client-side code first.

## What it changes

Three new classes, **zero** patches to existing PZ classes:

- `zombie.notifications.NotificationAPI` — public static entry point. Manages the live stack (guarded by a monitor so `show` / `dismiss` are safe from any thread), hands out ids, and enforces the max-concurrent cap.
- `zombie.notifications.NotificationSpec` — fluent builder carrying every visual and timing knob. Immutable-after-show: `NotificationAPI.show` takes an internal copy, so callers can reuse a template.
- `zombie.notifications.Toast` — package-private `UIElement` subclass, one instance per live notification. Measures its own text via `TextManager.MeasureStringX` to size the box, registers itself with `UIManager.AddUI`, eases its `y` toward a stack-computed target each `update()`, and renders border → background → centered text each frame. When its lifetime elapses it flags itself expired, calls `UIManager.RemoveElement`, and removes itself from the stack so the remaining toasts slide down.

Animation is driven off `UIManager.getMillisSinceLastUpdate()` for the position lerp and monotonic wall-clock (`System.nanoTime()`) for the lifecycle timeline — resize the window, tab in and out, the animation keeps its shape.

## Compatibility

- **Target:** client. This mod is `clientOnly: true` and can only be installed to the client profile.
- Stacks cleanly with everything else in the bundle — it touches no vanilla classes and reserves the `zombie.notifications` package to itself.
- Uninstall removes the three new class files from the install; no vanilla restore needed because nothing vanilla was overwritten.
- Mods that depend on this one (e.g. `admin-xray`) pull it into their install stack automatically via Necroid's dependency closure.

## Tuning

All tuning is per-call by the caller — there are no global knobs in this mod. If every consumer in your install is too chatty, deal with it at the consumer (raise its `durationMs` to something longer isn't going to help), or call `NotificationAPI.dismissAll()` from a hotkey mod of your own.
