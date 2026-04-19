# radio-fix

Removes weather-based radio interference. Vanilla scrambles broadcast text into `bzzt fzzt`-style garble whenever it's raining, foggy, or stormy — even on stations broadcasting from across the room. With this mod installed, the weather modifier is ignored and you hear (or read) the transmission cleanly regardless of conditions.

Distance-based scrambling still works normally — far-away signals continue to degrade as designed. Only the weather component is disabled.

## What it changes

- `zombie.radio.ZomboidRadio.applyWeatherInterference` — short-circuited to return the input unchanged. The original method consulted `ClimateManager.getWeatherInterference()` and randomly garbled words; that path is now unreachable.

No other radio behaviour is touched: range distortion, station scanning, frequency lists, and broadcast scheduling all run vanilla.

## Usage

Install and play. There is no toggle and no config — radios just stop being affected by weather.

## Compatibility

- **Target:** client.
- Stacks cleanly with `admin-xray` and `more-zoom`.
- **Conflicts with `no-radio-fzzt` only at the same target.** `no-radio-fzzt` ships for the **server** target and removes both weather and distance scrambling server-side. If you run a private server with `no-radio-fzzt` installed, this client-side mod becomes redundant (the server already sends the clean text), but they don't fight each other — they touch different installs.
- Uninstalling restores vanilla weather scramble.
