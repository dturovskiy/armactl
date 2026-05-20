# Release process

This document is the source of truth for preparing armactl releases.

## Versioning

Use semantic versioning-style increments:

- Patch release: bug fixes, operational fixes, resilience improvements, small TUI/UX improvements.
- Minor release: new user-facing features or larger workflow changes.
- Major release: breaking changes.

For each release, update all version metadata:

- `pyproject.toml`
- `src/armactl/__init__.py`
- `CHANGELOG.md`

The package version and `__version__` must match the Git tag.

## Changelog style

`CHANGELOG.md` follows a Keep a Changelog-style structure.

Use this section order when applicable:

1. `Added`
2. `Changed`
3. `Fixed`
4. `Validation`
5. `Operational notes`

Keep bullet points short and operator-focused.

Prefer this wording style:

- `Added ...`
- `Changed ...`
- `Fixed ...`
- `Retried ...`
- `Preserved ...`

Avoid marketing-style wording. Release notes should describe what changed and what operators need to do.

## Release PR checklist

A release PR should usually include only:

- `pyproject.toml`
- `src/armactl/__init__.py`
- `CHANGELOG.md`

PR title format:

    Prepare vX.Y.Z release

Commit message format:

    Prepare vX.Y.Z release

## Validation before tagging

Before creating the Git tag, verify CI is green.

Preferred local checks:

    ./scripts/run-host-tests
    python3 -m pytest -q
    python3 -m ruff check src tests
    git diff --check

If local checks cannot be run, the release PR must pass GitHub Actions before merge.

## Tagging

After the release PR is merged:

    git switch main
    git pull --ff-only
    git tag -a vX.Y.Z -m "vX.Y.Z"
    git push origin vX.Y.Z

## GitHub Release notes

Use `docs/release-notes-template.md`.

The GitHub Release body should match the changelog style and section order.
