# Project Instructions

## Version Management

When bumping the version, keep all three files in sync:

- `version.txt` — plain version string (e.g. `0.12.2`)
- `pyproject.toml` — `version` field under `[project]`
- `driver.json` — `version` field

The CI build workflow reads the version from `driver.json` to name the output artifact (`uc-intg-bluos-<version>-aarch64.tar.gz`). If `driver.json` is out of sync, the artifact filename will show the wrong version.
