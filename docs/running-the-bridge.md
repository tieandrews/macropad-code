# Running macropad-bridge

`macropad-bridge` is the daemon that owns the MacroPad's USB serial connection and relays state changes from hook scripts to actual LED colors. It needs to be running whenever you want the lights to work; nothing bad happens if it isn't (hooks just silently no-op), but nothing lights up either.

## Manually

```bash
uv sync            # once, from the repo root
uv run macropad-bridge
```

Without `uv`:

```bash
pip install -e .          # once, from the repo root -- installs the macropad-bridge command
macropad-bridge
```

or, without installing the package at all:

```bash
pip install -r requirements.txt
./bin/macropad-bridge
```

Leave it running in a terminal (or `tmux`/`screen` session). It logs every state change it relays, e.g.:

```
[macropad-bridge] listening on 127.0.0.1:9999
[macropad-bridge] connected to /dev/ttyACM1
[macropad-bridge] key=0 state=working color=[76, 42, 0] -> sent
```

## As a background service

`macropad-setup` offers to install this for you at the end of setup. If you skipped that or want to redo it, run `uv run macropad-setup` again -- it's safe to re-run.

The generated service files hardcode the Python interpreter that was running `macropad-setup` at the time -- when installed via `uv run macropad-setup`, that's `.venv/bin/python3` inside the repo, already wired up with every dependency `uv sync` installed, so the service doesn't need `uv run` itself at runtime.

What it does per platform:

### macOS (launchd)

Writes `~/Library/LaunchAgents/com.macropad-code.bridge.plist` (from [`macropad/services/launchd.plist.template`](../macropad/services/launchd.plist.template)) and, if you confirm, loads it with `launchctl load -w`. Logs go to `logs/macropad-bridge.{out,err}.log` in the repo.

Manage it manually with:

```bash
launchctl unload ~/Library/LaunchAgents/com.macropad-code.bridge.plist   # stop
launchctl load -w ~/Library/LaunchAgents/com.macropad-code.bridge.plist  # start
launchctl list | grep macropad-code                                     # status
```

### Linux (systemd --user)

Writes `~/.config/systemd/user/macropad-bridge.service` (from [`macropad/services/systemd.service.template`](../macropad/services/systemd.service.template)) and, if you confirm, runs `systemctl --user enable --now macropad-bridge.service`.

Manage it manually with:

```bash
systemctl --user status macropad-bridge     # status
systemctl --user restart macropad-bridge    # restart (e.g. after editing colors.yaml/usage.yaml)
systemctl --user stop macropad-bridge       # stop
journalctl --user -u macropad-bridge -f     # logs
```

If your distro doesn't start user services at boot by default, enable lingering once with `loginctl enable-linger $USER` so the bridge starts without you being logged in interactively.

### Windows

Windows startup isn't fully automated. `macropad-setup` writes a no-console launcher to `%APPDATA%\macropad-code\macropad_bridge_launcher.pyw` (from [`macropad/services/windows_launcher.pyw.template`](../macropad/services/windows_launcher.pyw.template)). To make it run at login:

1. Press `Win+R`, type `shell:startup`, hit Enter.
2. Create a shortcut to `macropad_bridge_launcher.pyw` in the folder that opens.

Double-clicking the `.pyw` file directly also works for a one-off run (it uses `pythonw.exe`, so no console window appears).

## Reconnect behavior

The bridge's serial connection self-heals: if the MacroPad is unplugged, put to sleep, or resets, a background thread keeps retrying every `serial.retry_seconds` (default 2s, see [customization.md](customization.md)) until it's found again. You don't need to restart the bridge after replugging the device.

## Usage display polling

If `config/usage.yaml` has `enabled: true`, the bridge also starts a second background thread that recomputes your Claude Code usage every `poll_interval_seconds` and pushes it to the OLED -- independent of the LED/hook traffic. You'll see it announce itself on startup, including which `source` it's using:

```
[macropad-bridge] usage display enabled (source=local_estimate, every 60s)
[macropad-bridge] usage session=128.4K weekly=1.9M -> sent
```

If that first line never appears, usage display is disabled in `config/usage.yaml` -- see [customization.md](customization.md).

If `source: claude_pty` is set with too short a `poll_interval_seconds`, or without the optional `pyte` dependency installed, the bridge logs a warning at startup rather than silently doing the expensive/degraded thing:

```
[macropad-bridge] warning: usage source is claude_pty but poll_interval_seconds is 60s -- this launches a full claude process every poll; consider 300s+. See config/usage.yaml / docs/customization.md.
[macropad-bridge] warning: usage source is claude_pty but the optional 'pyte' package isn't installed (uv sync --extra pty) -- falling back to a cruder ANSI stripper.
```

Whatever `source` is configured, a per-tick failure (monitor tool not running, `claude` not ready, parsing came up empty) silently falls back to `local_estimate` for that tick -- you can tell which actually supplied the numbers by the value's shape: a clean `NN%` means a real percentage came through, `128.4K`/`1.9M`-style counts mean it fell back.
