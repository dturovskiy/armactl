# Security Policy

## Supported versions

Security fixes are expected only for the latest development state on `main`
and the most recent published release, once releases exist.

## Reporting a vulnerability

Please do not open a public GitHub issue for sensitive vulnerabilities.

Report security issues privately to the maintainer through GitHub contact
channels. Include:

- affected version or commit
- environment details
- reproduction steps
- impact assessment
- whether secrets, sudo rules, or system service behavior are involved

## Security-sensitive areas

`armactl` interacts with:

- `sudo` and systemd units
- SteamCMD
- runtime `.env` files for the Telegram bot
- planned web panel credentials, session secrets, CSRF tokens, and local
  account database
- admin and RCON passwords
- browser file upload/download paths once the web panel is implemented
- host logs and service output

When reporting or discussing bugs, redact:

- `passwordAdmin`
- game password
- `rcon.password`
- Telegram bot tokens
- web panel session secrets, password hashes, reset tokens, or database dumps
- private IPs or hostnames when needed

## Project posture

The project already aims to:

- keep runtime `.env` files out of git
- redact obvious secrets from logs and UI output
- separate repo code, runtime data, and system service files
- keep the planned web panel behind HTTPS/reverse proxy by default and bind the
  app locally unless an operator explicitly chooses direct exposure

If you find a place where secrets leak or privileged behavior is too broad,
please report it.
