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

Patches one file: `zombie.Lua.LuaEventManager`. On the first time `LuaEventManager.triggerEvent("OnMainMenuEnter")` fires (vanilla `MainScreenState.enter()` line 242, runs once at title-screen entry), the mod:

1. Looks for `<userhome>/Zomboid/auto-join.txt`.
2. If absent → no-op, normal main-menu flow.
3. If present:
   - Parses the key=value pairs into a map.
   - **Deletes the file** (always, before any further validation — so a bad file doesn't loop on every launch).
   - Validates required fields (`ip`, `username`, `password`). On failure, traces the error and returns; vanilla flow continues.
   - Clears `args.server.connect` / `args.server.password` system properties so PZ's vanilla auto-popup doesn't *also* fire.
   - Sets `Core.GameMode = "Multiplayer"`, `Core.setDifficulty("Hardcore")`, force-disconnects any prior `GameClient.connection`, resets the disconnect timer, sets `GameClient.bClient = true`, cleans MP saves, and calls `GameClient.instance.doConnect(username, password, ip, "", port, serverPassword, ip, false)` directly.
4. PZ's network handshake starts; the server's `ConnectionDetails` packet creates `ConnectToServerState` via `ConnectionDetails.java:47-49`, driving the join exactly as the Lua-popup path would have.

A static `autoLoginAttempted` flag ensures the helper runs at most once per process — on retry (e.g. wrong password) the user sees PZ's normal rejection and is back at the menu without an infinite reconnect loop.

## Trace log

Every call to `autoLoginTrace(...)` writes to `<userhome>/Zomboid/auto-login-trace.log` *and* to PZ's `DebugLog`. Useful for debugging launcher integration:

```
[Sun Apr 26 12:58:01 EDT 2026] found C:\Users\kevin\Zomboid\auto-join.txt, parsing
[Sun Apr 26 12:58:01 EDT 2026] connecting to 45.143.196.111:16261 as Bob
[Sun Apr 26 12:58:01 EDT 2026] doConnect dispatched
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

`auto-join.txt` contains the character password in plaintext. The mod deletes the file on read (sub-second window), but during that window:

- It exists at a well-known path that other processes on the machine can read.
- Antivirus / file-indexing services may have already cached / scanned it.
- A crash between write and read leaves the file on disk indefinitely until the next successful boot.

For shared machines, consider writing the file with restrictive ACLs, or using a per-launcher temp credential rather than the user's primary password.
