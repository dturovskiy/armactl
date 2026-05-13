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
- [ ] Release / versioning
- [ ] docs only

## Automated contribution disclosure

- [ ] This PR was written or substantially reviewed by a human maintainer/contributor
- [ ] This is not a low-effort automated bounty submission
- [ ] If AI or automation was used, the generated changes were manually reviewed

## Notes

- List any migration, rollback, or operator-facing implications.
- For TUI changes, list the manual screens/flows tested.
