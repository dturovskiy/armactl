# Telegram Bot Design

## Status

This document describes the agreed design for the optional Telegram bot
component in Phase 13.

Current status:

- instance-scoped `bot/.env` is implemented as the source of truth
- the TUI already has a `Telegram Bot` settings screen that reads and writes it
- the bot runtime is implemented via `python -m armactl.telegram_bot --instance <name>`
- `armactl-bot.service` can now be installed and managed from the TUI
- bot actions use a narrow root-owned helper plus a sudoers drop-in instead of
  storing a password or attempting interactive sudo from the bot process
- `/status` now includes server CPU/RAM, best-effort player counts via the
  configured local A2S query port, and player roster details via local RCON
  when RCON is configured

## Goals

- The Telegram bot is an optional management component, not part of the base
  dedicated server process.
- The server install flow should work out of the box without requiring a bot
  token or Telegram setup.
- The user should be able to install and configure bot management from the TUI
  after the server is already installed.

## Runtime model

- The bot runs as a separate systemd service: `armactl-bot.service`
- It should remain available even when the game server is stopped
- It should be able to trigger `start`, `stop`, `restart`, `status`, and
  schedule actions through the existing armactl backend
- Privileged service control is performed through a narrowly-scoped helper
  installed to `/usr/local/libexec/armactl-systemctl-helper` and authorized via
  `/etc/sudoers.d/armactl-systemctl-helper`
- Timer schedule updates use the same helper, but only through a dedicated
  narrow path that rewrites the allowed restart timer file instead of allowing
  arbitrary unit installation

## Source of truth

The single source of truth for Telegram bot configuration is an instance-scoped
environment file:

```text
~/armactl-data/<instance>/bot/.env
```

The TUI bot settings screen reads and writes this file directly. Manual editing
of the same `.env` file must produce the same final state that the TUI sees.

## Repository files

- Repository example: `.env.example`
- Real runtime file: `~/armactl-data/<instance>/bot/.env`
- Real `.env` files must never be committed to git

## Initial configuration keys

- `ARMACTL_BOT_ENABLED`
- `ARMACTL_BOT_TOKEN`
- `ARMACTL_BOT_ADMIN_CHAT_IDS`
- `ARMACTL_BOT_LANGUAGE`
- `ARMACTL_INSTANCE`

## Planned UX

1. Install server via the normal install flow
2. Open `Manage Existing Server -> Telegram Bot`
3. Save token and admin Chat ID(s) into the instance `.env`
4. Use `Apply Bot Service`
   This also installs/refreshes the secure privileged control channel and then
   starts or restarts the bot service automatically.

## Current bot surface

- `/start` opens the inline control menu
- `/status` shows current server, timer, CPU/RAM, player count, and player
  roster state
- `/stop` stops the server
- `/restart` restarts the server
- `/schedule 05:00, 20:00` updates scheduled restart times
- access is restricted by `ARMACTL_BOT_ADMIN_CHAT_IDS`

## Library choice

The preferred library for this project is `python-telegram-bot`, because the
planned bot is a small admin-control surface with buttons and callbacks, not a
large conversational workflow engine.
