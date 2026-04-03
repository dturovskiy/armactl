# Release Process

## Goal

Ship a GitHub Release that matches the repo-local operational model of
`armactl`, not just a library-style Python package.

## Before tagging

- Update [CHANGELOG.md](../CHANGELOG.md)
- Confirm README and docs are current
- Run:

  ```bash
  ./scripts/run-host-tests
  ```

- Confirm manual smoke coverage on:
  - fresh install
  - existing server
  - reboot flow

## Tagging

Use semantic version tags:

```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

## Release workflow

The GitHub release workflow should:

- build Python artifacts
- create a source archive suitable for repo-local usage
- attach artifacts to the GitHub Release page

## After the release is published

- Verify installation from the release archive
- Verify `./armactl` on another machine
- Check release notes and attached artifacts
