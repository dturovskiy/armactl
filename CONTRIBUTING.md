# Contributing

Thanks for helping improve `armactl`.

## Before you start

- Read [README.md](README.md) for product scope and supported environment.
- Read [docs/development.md](docs/development.md) for local setup and test flow.
- For operational context, see [docs/architecture.md](docs/architecture.md).

## Development workflow

1. Clone the repository.
2. Bootstrap the repo-local environment:

   ```bash
   ARMACTL_BOOTSTRAP_MODE=--dev ./armactl
   ```

3. Run the host checks before opening a PR:

   ```bash
   ./scripts/run-host-tests
   ```

## Contribution guidelines

- Keep business logic in backend modules, not in TUI screens.
- Add or update tests when behavior changes.
- Update docs when UX, install flow, or operational behavior changes.
- Never commit real secrets, runtime `.env` files, or server data.
- Keep changes focused. Large refactors should be split into smaller PRs when possible.

## Pull requests

- Explain what changed and why.
- List any manual verification you performed.
- Call out risk areas, especially around install, systemd, SteamCMD, config migration, or Telegram bot behavior.

