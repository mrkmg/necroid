# auto-login

Client-side mod that **auto-joins a server at boot** by reading connection details from a config file. Drop a file at `<userhome>/Zomboid/auto-join.txt`, launch PZ, and it connects directly to the world — no menu, no popup, no typing. The file is **deleted on read** so the next launch is a normal boot.

The original use case: PZ's `+connect`/`+password` Steam URL flow gets you into the connect popup but still asks for character username and password. Custom CLI flags like `+username`/`+upassword` aren't viable on this PZ build (the launcher / JRE strips them — `sun.java.command` and `ProcessHandle.arguments` both come back null/empty). A launcher writes the file, PZ reads it, deletes it. One-shot, no creds in any URL or process command-line.

## Config file

**Path:** `<userhome>/Zomboid/auto-join.txt` — same folder as `console.txt`. On Windows that's `C:\Users\<you>\Zomboid\auto-join.txt`.

**Format:** plain text, `key=value` per line. Keys are case-insensitive. Lines starting with `#` are comments. Blank lines ignored. The `=` is the first `=` only — values may contain `=`.

```
ip=45.143.196.111
port=16261
serverPassword=
username=Bob
password=hunter2
```

| Key              | Required | Default | Notes                                                      |
|------------------|----------|---------|------------------------------------------------------------|
| `ip`             | yes      | —       | Server IP or hostname.                                     |
| `port`           | no       | `16261` | TCP port. Must parse as an integer.                        |
| `serverPassword` | no       | `""`    | Server-level password (the `+password` flag's value). Empty / omitted = no server password. |
| `username`       | yes      | —       | Character account username.                                |
| `password`       | yes      | —       | Character account password. Spaces / `=` characters allowed (everything after the first `=` is the value, untrimmed). |

Whitespace around the value is preserved (so a leading/trailing space in `password=` becomes part of the password). Trim manually in your launcher if that's an issue.

## What it does

Patches one file: `zombie.Lua.LuaEventManager`. The mod hooks two Lua events through the patched `triggerEvent` switch:

- **`OnMainMenuEnter`** — fires once when the title screen comes up (`MainScreenState.enter()`), and again every time the user backs out of a connect attempt or exits a game to menu. Resets the in-flight latch and runs an immediate file check (the boot path).
- **`OnPreUIDraw`** — fires every UI frame (`UIManager.update()`), throttled to **once per second** by the mod. Each tick the mod checks `GameWindow.states.current instanceof MainScreenState` (true on the main menu *and* on the server-join popup, since the popup is an overlay on `MainScreenState`, not a separate state). If the active state is anything else (e.g. `IngameState`), polling is a no-op — so a file that appears mid-game is ignored until the user exits to menu.

Either entry point runs the same flow:

1. Looks for `<userhome>/Zomboid/auto-join.txt`.
2. If absent → no-op.
3. If present:
   - **Stale check.** If `(now - mtime) > 600 seconds` (10 minutes), the file is deleted on the spot and no connect is attempted. This keeps a yesterday-leftover file from ambushing the user when they exit a game to menu later in the day.
   - Parses the key=value pairs into a map.
   - **Deletes the file** (always, before any further validation — so a bad file doesn't loop on every launch).
   - Validates required fields (`ip`, `username`, `password`). On failure, traces the error and returns; vanilla flow continues.
   - Clears `args.server.connect` / `args.server.password` system properties so PZ's vanilla auto-popup doesn't *also* fire.
   - Compiles a Lua snippet that hooks `Events.OnPreUIDraw` once and calls `ConnectToServer.instance:connect(...)` on the next frame, with a fallback to `serverConnect(...)` if `ConnectToServer.instance` isn't ready yet.
4. PZ's network handshake starts; the server's `ConnectionDetails` packet drives the rest of the join.

The `autoLoginInFlight` latch (replaces v0.1.0's permanent `autoLoginAttempted` flag) prevents a second dispatch while one is already in motion. It is **reset on every `OnMainMenuEnter`**, so a failed attempt (wrong password, server kicked, etc.) lets the user drop a corrected file and have it picked up — no process restart needed.

### Polling cadence and stale threshold

| Constant         | Value         | Where                                          |
|------------------|---------------|------------------------------------------------|
| `MAX_AGE_MS`     | `600_000` ms  | reject files older than 10 min                 |
| `POLL_INTERVAL_MS` | `1_000` ms  | one FS-stat per second while on main menu      |

Both are hardcoded constants in `LuaEventManager.java` — no per-file override key. Edit the patch and rebuild if you need different values.

## Trace log

Every call to `autoLoginTrace(...)` writes to `<userhome>/Zomboid/auto-login-trace.log` *and* to PZ's `DebugLog`. Useful for debugging launcher integration. The first line per attempt is tagged with the trigger source — `[boot]` (fired from `OnMainMenuEnter`) or `[poll]` (fired from the `OnPreUIDraw` polling loop):

```
[Sun Apr 26 12:58:01 EDT 2026] [boot] found C:\Users\kevin\Zomboid\auto-join.txt, parsing
[Sun Apr 26 12:58:01 EDT 2026] connecting to 45.143.196.111:16261 as Bob
[Sun Apr 26 12:58:01 EDT 2026] connect scheduled on next OnPreUIDraw
```

A stale-file rejection looks like:

```
[Mon Apr 27 09:15:42 EDT 2026] stale auto-join.txt (age 7234s > 600s); deleting
```

The trace log is **append-only** — it grows across runs. Truncate manually if needed.

## Launcher integration (other apps)

A launcher (Steam URL alternative, custom Electron app, .bat shortcut, etc.) just writes the file then starts PZ:

```python
# Python example
import os, subprocess
zomboid = os.path.expanduser("~/Zomboid")
with open(os.path.join(zomboid, "auto-join.txt"), "w") as f:
    f.write(
        "ip=45.143.196.111\n"
        "port=16261\n"
        "serverPassword=\n"
        "username=Bob\n"
        "password=hunter2\n"
    )
subprocess.Popen(["start", "steam://rungameid/108600"], shell=True)
```

```bat
:: Windows .bat example
> "%USERPROFILE%\Zomboid\auto-join.txt" (
  echo ip=45.143.196.111
  echo port=16261
  echo serverPassword=
  echo username=Bob
  echo password=hunter2
)
start steam://rungameid/108600
```

```csharp
// C# example
var path = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                       "Zomboid", "auto-join.txt");
File.WriteAllLines(path, new[] {
    $"ip={ip}",
    $"port={port}",
    $"serverPassword={srvPwd}",
    $"username={user}",
    $"password={pwd}",
});
Process.Start(new ProcessStartInfo("steam://rungameid/108600") { UseShellExecute = true });
```

The `steam://rungameid/108600` URL form (no extra args) launches PZ exactly the same as clicking Play in Steam. **Don't** include `+connect`/`+password` in the Steam URL when also using `auto-join.txt` — the file takes over and clears those properties anyway.

The mod deletes the file the moment it reads it, so the user's creds are on disk for at most a few seconds during boot. If PZ crashes between writing the file and reaching `OnMainMenuEnter`, the file persists; next launch consumes it.

## Compatibility

- **Target:** client. `clientOnly: true`.
- **Patch surface:** one file, `zombie/Lua/LuaEventManager.java`. No overlap with other mods in this collection.
- `necroid uninstall auto-login --to client` restores vanilla.

## Security warning

`auto-join.txt` contains the character password in plaintext. The mod deletes the file on read, but the read window is now **larger than v0.1.0**:

- v0.1.0: the file existed for at most a sub-second between launcher write and the boot-time `OnMainMenuEnter` read.
- v0.2.0: with the polling path, the file may sit on disk for up to ~1 second after the user reaches the main menu (one polling tick), or up to 10 minutes if the user is in-game when the launcher writes — until either the user exits to menu (file consumed) or the 10-minute stale threshold fires (file deleted, no connect).

During whichever window applies:

- The file exists at a well-known path that other processes on the machine can read.
- Antivirus / file-indexing services may have already cached / scanned it.
- A crash before the file is consumed leaves it on disk indefinitely; on the next launch the boot path picks it up — but only if it's still within the 10-minute mtime window (otherwise the stale check deletes it without connecting).

For shared machines, consider writing the file with restrictive ACLs, or using a per-launcher temp credential rather than the user's primary password.
