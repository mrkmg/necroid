# no-radio-fzzt

Disables **all** radio obfuscation: weather interference, distance falloff, and the scramble pipeline that turns transmissions into `bzzt fzzt` noise. Broadcasts come through as the raw text that was sent.

Install to **client** for solo/local play, or to **server** so every connected client (modded or vanilla) receives clean text. Use on dedicated servers where the radio system is being repurposed for player communication, custom event scripting, or in-game alerts that must be readable regardless of distance or weather.

## What it changes

- `zombie.radio.ZomboidRadio.applyWeatherInterference` — short-circuited; returns the input unchanged.
- `zombie.radio.ZomboidRadio.doDeviceRangeDistortion` — emptied. Range no longer adds garble to the message body.
- `zombie.radio.ZomboidRadio.scrambleString(String, int, boolean, String)` — emptied. The whole word-shuffling/`bzzt fzzt` substitution loop is gone. Any code path that called it (including any future-added one) returns the message untouched.

A small generics fix on the entry-set serializer in the same file is included to keep the source compiling cleanly under javac.

## Usage

Install to client (`necroid install no-radio-fzzt --to client`) or server (`necroid install no-radio-fzzt --to server`). There is no in-game toggle — broadcasts are simply transmitted clean from the moment the mod is active. Restart the game (or the server) for the patch to take effect.

## Compatibility

- **Target:** client or server. Works at either install.
- Server install is authoritative for multiplayer: once the server stops scrambling, every connected client (modded or vanilla) receives clean text — client-side install becomes redundant but doesn't conflict.
- Other Necroid mods (`admin-xray`, `more-zoom`, `gravymod`, `lua-profiler`, `staff-priority`, `weather-flash-fix`) don't touch the radio subsystem and stack cleanly.
- Uninstalling restores the full vanilla scramble pipeline (weather + distance + word shuffler).
