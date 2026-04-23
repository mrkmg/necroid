# slow-chunk-fix

Raises the connecting-client chunk-download stall timeout from **60 seconds to 10 minutes** so slow links finish loading the map instead of giving up at the loading screen.

## What it does

Vanilla `WorldStreamer.requestLargeArea(...)` watches the inbound chunk stream during the multiplayer connect sequence. If `largeAreaDownloads` doesn't tick forward for 60 seconds, it sets `GameLoadingState.mapDownloadFailed = true` and throws `IOException("map download from server timed out")` — you get bounced back to the menu.

This mod bumps the literal from `60000L` to `600000L`, giving the server 10 minutes of zero-progress slack before the client gives up.

## What it does NOT change

- The 8-second per-chunk **resend** trigger in `resendTimedOutRequests()` is left alone — that's a packet-loss recovery mechanism, not a give-up timeout, and lengthening it would slow recovery on lossy links.
- Wall-clock download time isn't capped — the 10-minute window is a *stall* timeout, reset every time a new chunk arrives. A slow-but-steady download finishes whenever the server is done.

## Scope

- Target: `client` (clientOnly)
- Files patched: `zombie/iso/WorldStreamer.java` (one-line literal swap on the stall guard)
- No new classes, no API changes, no Lua.

## Use case

Install on a client that frequently times out connecting to a heavily-modded or remote server. Silent no-op on a healthy connection.
