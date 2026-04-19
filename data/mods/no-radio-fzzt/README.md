# no-radio-fzzt

Server-side mod that disables **all** radio obfuscation: weather interference, distance falloff, and the scramble pipeline that turns transmissions into `bzzt fzzt` noise. Connected clients receive the raw broadcast text exactly as the broadcaster sent it.

Use this on dedicated servers where the radio system is being repurposed for player communication, custom event scripting, or in-game alerts that must be readable regardless of distance or weather.

## What it changes

- `zombie.radio.ZomboidRadio.applyWeatherInterference` — short-circuited; returns the input unchanged. (Same patch as the client-side `radio-fix` mod.)
- `zombie.radio.ZomboidRadio.doDeviceRangeDistortion` — emptied. Range no longer adds garble to the message body.
- `zombie.radio.ZomboidRadio.scrambleString(String, int, boolean, String)` — emptied. The whole word-shuffling/`bzzt fzzt` substitution loop is gone. Any code path that called it (including any future-added one) returns the message untouched.

A small generics fix on the entry-set serializer in the same file is included to keep the source compiling cleanly under javac.

## Usage

Install on your server. There is no in-game toggle — broadcasts are simply transmitted clean from the moment the mod is active. Restart the server for the patch to take effect.

## Compatibility

- **Target:** server. Install on the **server** profile (`necroid --target server install no-radio-fzzt`).
- Pairs naturally with the client-side `radio-fix`, but doesn't require it — once the server stops scrambling, every connected client (modded or vanilla) receives clean text.
- Other Necroid server mods (`gravymod`) don't touch the radio subsystem and stack cleanly.
- Uninstalling restores the full vanilla scramble pipeline (weather + distance + word shuffler).
