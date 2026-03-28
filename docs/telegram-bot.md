# Telegram Bot Design

## Status

This document describes the agreed design for the optional Telegram bot
component in Phase 13. It is not fully implemented yet.

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

## Source of truth

The single source of truth for Telegram bot configuration is an instance-scoped
environment file:

```text
~/armactl-data/<instance>/bot/.env
```

The future TUI bot settings screen must read and write this file directly.
Manual editing of the same `.env` file must produce the same final state that
the TUI sees.

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
2. Open a future TUI action such as `Telegram Bot`
3. Choose `Install / Configure Bot`
4. Save token and admin Chat ID(s) into the instance `.env`
5. Generate and enable `armactl-bot.service`

## Library choice

The preferred library for this project is `python-telegram-bot`, because the
planned bot is a small admin-control surface with buttons and callbacks, not a
large conversational workflow engine.
