---
name: esp32-codex
description: Use the ESP32 Codex MCP tools to inspect and operate a connected MicroPython ESP32 through the real bridge; a mock launcher remains available for development.
---

# ESP32 Codex tools

Check `source` and `simulated` in tool results before interpreting device state. The personal install uses the real bridge; `launch_mock.cmd` remains available as a development fallback.

Use workspace tools to identify the project and configured board profile. The real bridge enumerates ports but never connects automatically. Connect only the user-selected port. Strictly back up existing board files before any write or delete.

`esp32_open_panel` opens the same per-user Windows window and first reads the broker's active shared workspace/profile. Startup never connects a serial port.

Follow the active workspace policy and confirmation response for each action. Do not claim that generic `stop` physically stops motors, and do not treat a bridge response alone as proof that a person confirmed a consequential action.
