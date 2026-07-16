# Customizing keymap, colors, usage display, and the bridge

All config files live in [`config/`](../config/) and are plain YAML -- edit them in any text editor. `macropad-bridge` reads `config/colors.yaml`, `config/bridge.yaml`, and `config/usage.yaml` at startup (restart it after editing those), and `config/keymap.yaml` on every hook event (no restart needed).

## `config/keymap.yaml` -- which key belongs to which project

The MacroPad's 12 keys are numbered 0-11, left-to-right then top-to-bottom:

```
 0   1   2
 3   4   5
 6   7   8
 9  10  11
```

Each entry under `keys:` maps a key index to a project:

```yaml
keys:
  0:
    label: "macropad-code"
    agent: claude               # claude | codex -- informational, not enforced
    project_path: "~/projects/macropad-code"
  1:
    label: "api-server"
    agent: codex
    project_path: "~/projects/api-server"
```

When a hook fires, it reads the working directory (`cwd`) of the agent session and looks for the entry whose `project_path` is the closest ancestor of that directory -- so a session running in `~/projects/macropad-code/frontend` still matches the `~/projects/macropad-code` entry. If no entry matches, nothing lights up (the hook is a no-op).

You can hand-edit this file directly, or run `macropad-setup` again to add more keys interactively (it won't remove or touch keys you've already configured unless you overwrite them with the same index).

`project_path` accepts `~` and both absolute and relative-to-home paths. It does **not** need to exist yet at assignment time -- `macropad-setup` will warn but still save it.

## `config/colors.yaml` -- what each state looks like

```yaml
brightness: 0.3   # global scale, 0.0-1.0, applied to every color below

states:
  working:
    color: [255, 140, 0]
    description: Agent is actively thinking / running.
  waiting:
    color: [255, 0, 255]
    description: Agent is waiting on your next input.
  # ...
```

- `color` is `[R, G, B]`, each `0-255`.
- `brightness` is a single global multiplier -- turn it down if the pad is uncomfortably bright next to a monitor, rather than editing every color individually.
- To add a brand-new state, add it here *and* map an event to it in `macropad/hooks/*.py` (see [agent-integration.md](agent-integration.md)) -- a state with no color entry falls back to `idle`'s color.

Changes take effect the next time the bridge resolves a state to a color, i.e. on the next hook event after you restart `macropad-bridge`.

## `config/bridge.yaml` -- daemon settings

```yaml
host: "127.0.0.1"
port: 9999

serial:
  baudrate: 115200
  retry_seconds: 2.0
```

- `host`/`port`: the local socket hook scripts use to talk to the bridge. Only change this if `9999` is already taken by something else on your machine -- and if you do, restart the bridge.
- `serial.retry_seconds`: how often the bridge tries to (re)find the MacroPad if it's unplugged, asleep, or not yet connected. Lower it if you want faster reconnects at the cost of slightly more CPU/USB polling.

## `config/usage.yaml` -- OLED usage dashboard

There is no official Anthropic API for your exact Claude subscription session/weekly quota -- see the note in `config/usage.yaml` itself. This repo gives you three ways to populate the OLED dashboard, selected with `source:`, in increasing order of accuracy *and* fragility/overhead. Whatever you pick, the bridge always falls back to `local_estimate` if the chosen source comes up empty -- the OLED never just goes blank because a fancier source failed.

### `source: local_estimate` (default)

```yaml
enabled: true
source: local_estimate
poll_interval_seconds: 60
session_window_hours: 5
weekly_window_days: 7
session_token_budget: null
weekly_token_budget: null
```

The bridge scans Claude Code's own local session transcripts under `~/.claude/projects/**/*.jsonl` and sums the token usage recorded in them over two rolling windows: the last `session_window_hours` and the last `weekly_window_days`. Always available, zero extra setup, Claude Code only (Codex CLI doesn't write anything locally this can read).

Anthropic doesn't publish the exact token budget behind Claude Code's real "session"/"weekly" limits, so by default `session_token_budget`/`weekly_token_budget` are `null` and the OLED shows a raw count (`128.4K`, `1.9M`, ...) rather than a fabricated percentage. If you've empirically learned roughly where your own plan's limits kick in, set either budget to your own estimated token ceiling and that metric switches to a percentage bar instead:

```yaml
session_token_budget: 500000
weekly_token_budget: 2000000
```

Other notes:

- `poll_interval_seconds` trades freshness for disk I/O -- the scan reads every transcript file modified within the weekly window on every tick. 60s is a reasonable default; raise it if you have a very large `~/.claude/projects/` history.
- The transcript JSONL format is internal to Claude Code and can change between releases. `macropad/usage.py` parses it defensively (any line, file, or field it doesn't recognize is skipped, not raised) so a format change degrades to "shows 0" rather than crashing the bridge -- but the numbers could in principle go stale if Anthropic changes the schema. If that happens, check the field names `macropad/usage.py` looks for (`message.usage.{input,output,cache_creation_input,cache_read_input}_tokens` and a top-level `timestamp`) against a real file in `~/.claude/projects/` to see what changed.

### `source: claude_monitor`

```yaml
source: claude_monitor
claude_monitor:
  state_path: null   # defaults to ~/.claude-monitor/state/latest.json
```

Reads Anthropic's *real* session/weekly percentages from the state file written by the community [Claude-Code-Usage-Monitor](https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor) tool -- if you already run it. We don't maintain that tool and haven't verified its output schema against a live install, so `macropad/usage_monitor.py` tries a few plausible key names (`session_percent_used`, `session_pct`, nested `session.percent_used`, ...) and returns nothing if none match, which falls back to `local_estimate`. If you run the monitor and this never picks up real numbers, open the state file yourself and check what `macropad/usage_monitor.py`'s `_SESSION_KEYS`/`_WEEKLY_KEYS` are actually looking for.

### `source: claude_pty` (experimental)

```yaml
source: claude_pty
poll_interval_seconds: 600   # keep this long -- see below
claude_pty:
  claude_command: "claude"
  working_dir: null          # defaults to your home directory
  timeout_seconds: 20
```

Gets Anthropic's real percentages by periodically launching `claude` itself in a pseudo-terminal, sending it `/usage`, and reading the panel back -- the only source that shows the actual number Claude Code shows you, at real cost:

- **It launches a full `claude` process every poll.** That's real CPU/memory/startup overhead. `macropad-setup` sets `poll_interval_seconds` to 600 when you pick this source specifically because of that -- don't run it every 60s.
- **It needs `claude` already fully onboarded and the target directory already trusted.** We verified this directly while building it: launching `claude` fresh into a directory it hasn't seen, or before you've completed the theme/login wizard once, lands on that wizard or a trust prompt -- not a ready session -- and there is no way to click through a trust prompt from an unattended script without defeating the point of it. So instead of guessing, `macropad/usage_pty.py` watches for those exact screens (`"Select login method"`, `"Choose the text style"`, `"trust the files"`, ...) and bails out cleanly the moment it sees one, rather than hang or misread wizard text as usage data. **Before enabling this**, run `claude` by hand once from `claude_pty.working_dir` and get all the way to a normal chat prompt.
- **It scrapes rendered terminal text, not a documented API.** `/usage`'s output is a human-facing UI that can change wording or layout in any Claude Code release, and we were only able to verify the failure screens above in testing (not the actual `/usage` panel's exact current wording, since that requires a fully logged-in interactive install) -- so the percentage-extraction regexes in `macropad/usage_pty.py` are best-effort. If they ever stop matching a real `/usage` panel, this source silently returns nothing (never garbage, never a hang) and the bridge falls back to `local_estimate`.
- **Needs the optional `pyte` dependency**: `uv sync --extra pty` (or `pip install pyte`). Without it, a cruder regex-based ANSI stripper is used, which is more likely to leave stray control-sequence noise in the extracted text.

If a poll ever seems to hang, it can't -- `timeout_seconds` (split 40/60 between the initial wait and the post-`/usage` wait) bounds the whole cycle, and the child `claude` process is always killed (`SIGTERM` then `SIGKILL`) in a `finally` block even on an exception.

### If you're on pay-as-you-go API billing instead of a subscription

None of the above applies if you're using Claude Code with a raw `ANTHROPIC_API_KEY` rather than a Pro/Max/Team subscription -- API keys don't have a fixed "session"/"weekly" percentage at all, just metered spend. Anthropic's [Usage & Cost Admin API](https://platform.claude.com/docs/en/api/admin/usage_report/retrieve_claude_code) can report that spend over time, but it requires an *organization Admin API key* most individual users won't have, and it reports raw token/cost totals, not a percentage of anything -- it's a genuinely different feature for a different audience, so this repo doesn't wire it up. If that's your situation and you want it on the OLED anyway, `macropad/usage.py`'s `build_display_payload()` is the place to add a fourth `source`.

### Toggling and switching

Run `uv run macropad-setup` again any time to change `enabled`/`source` interactively (it only patches those specific lines in place -- your comments and other settings stay put), or hand-edit `config/usage.yaml` directly.

## Firmware-side changes

Per-key LED behavior and the OLED dashboard layout (bar width, line spacing, brightness clamp, how JSON messages are parsed) live in [`firmware/code.py`](../firmware/code.py) on the MacroPad itself, not in this repo's Python. If you change it, re-copy the file to `CIRCUITPY/code.py` -- no reboot needed, CircuitPython reloads automatically on save.

To change how the usage bars *look* (e.g. wider bars, different labels) without touching the token-counting logic, edit the constants and `_render_bar`/`_apply_usage_message` near the top of `code.py`. `BAR_WIDTH` is deliberately conservative (12 chars) to stay within the OLED's 128px width at the built-in font's ~6px glyph width -- widen it carefully if you switch fonts.
