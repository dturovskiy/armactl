## Summary

- What changed?
- Why was this needed?

## Related issue

Closes #

## Validation

- [ ] `./scripts/run-host-tests`
- [ ] `python3 -m pytest -q`
- [ ] `python3 -m ruff check src tests`
- [ ] Relevant manual flow tested
- [ ] TUI flow tested, if this PR changes Textual screens or user interaction
- [ ] Web flow tested, if this PR changes browser routes, auth, or file access
- [ ] The changed files are relevant to the linked issue
- [ ] This PR does not introduce unrelated changes

## Risk areas

- [ ] Install / bootstrap
- [ ] Existing server detection
- [ ] systemd / timer
- [ ] config.json handling
- [ ] Mods / addon cleanup
- [ ] TUI / UX / Textual screens
- [ ] Telegram bot
- [ ] Web panel / auth / filesystem access
- [ ] Release / versioning
- [ ] docs only

## Automated contribution disclosure

- [ ] This PR was written or substantially reviewed by a human maintainer/contributor
- [ ] This is not a low-effort automated bounty submission
- [ ] If AI or automation was used, the generated changes were manually reviewed

## Notes

- List any migration, rollback, or operator-facing implications.
- For TUI changes, list the manual screens/flows tested.
- For web changes, list auth/session/CSRF checks, file-access checks, and
  deployment or reverse-proxy checks.
