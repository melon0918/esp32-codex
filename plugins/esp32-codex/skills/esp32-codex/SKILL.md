---
name: esp32-codex
description: Use the ESP32 Codex MCP tools to inspect and operate a connected MicroPython ESP32 through the real bridge; a mock launcher remains available for development.
---

# ESP32 Codex tools

Check `source` and `simulated` in tool results before interpreting device state. The personal install uses the real bridge; `launch_mock.cmd` remains available as a development fallback.

Use workspace tools to identify the project and configured board profile. The real bridge enumerates ports but never connects automatically. Connect only the user-selected port. Strictly back up existing board files before any write or delete.

`esp32_open_panel` opens the same per-user Windows window and first reads the broker's active shared workspace/profile. Startup never connects a serial port.

Follow the active workspace policy and confirmation response for each action. Do not claim that generic `stop` physically stops motors, and do not treat a bridge response alone as proof that a person confirmed a consequential action.

When the user explicitly authorizes the operation's target and effect and asks you to operate the panel, use `esp32_confirmation_status` to inspect this MCP session's pending request, then call `esp32_panel_agent_decide` with that request ID and `approve` or `reject`. The original tool call resumes after the decision; do not resend it. This is recorded as `agent_delegated`, not as a human panel click. If the authorization does not cover the pending action, reject or cancel it.
