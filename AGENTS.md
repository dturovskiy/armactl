# AI contributor instructions for armactl

When preparing releases, do not invent a new release format.

Use these files as the source of truth:

- `docs/release-process.md`
- `docs/release-notes-template.md`
- `CHANGELOG.md`

Release PRs should normally update only:

- `pyproject.toml`
- `src/armactl/__init__.py`
- `CHANGELOG.md`

Keep release notes consistent with previous releases:

- `Added`
- `Changed`
- `Fixed`
- `Validation`
- `Operational notes`

Use concise, operator-focused bullets.
Do not add unrelated code changes to release PRs.
