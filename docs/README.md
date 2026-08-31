# macropad-code docs

- [hardware-setup.md](hardware-setup.md) -- flash the Adafruit MacroPad RP2040 with CircuitPython and this repo's firmware
- [agent-integration.md](agent-integration.md) -- how Claude Code / Codex CLI events become LED colors
- [customization.md](customization.md) -- edit `config/keymap.yaml`, `config/colors.yaml`, and `config/usage.yaml`
- [running-the-bridge.md](running-the-bridge.md) -- run `macropad-bridge` manually or as a background service
- [running-sessions.md](running-sessions.md) -- start/clean up the sessions (tmux or herdr) action keys route into
- [troubleshooting.md](troubleshooting.md) -- common problems

Start with `uv run macropad-setup` (see the top-level [README](../README.md)) -- these docs cover what it does under the hood and how to change things afterward.
