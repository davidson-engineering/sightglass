# Contributing

## Development

```bash
make install    # uv sync --all-extras, and Chromium for the browser tests
make check      # ruff, then every test: what CI runs
make fmt        # format the code
```

The serial tests use pseudo-terminals, so they run on macOS and Linux and are
skipped on Windows. Forgejo CI (`.forgejo/workflows/check.yml`) runs
`make check` on Python 3.11 on Linux for every pull request and push to
`main`. The GitHub mirror's `.github/workflows/ci.yml` covers Python 3.11 to
3.14, and macOS and Windows when run by hand (Actions > CI > Run workflow on
a branch pushed to the `github` remote): use it before merging a change to
platform code (paths, processes, ctypes).

`uv run --all-extras python docs/screenshot.py` retakes the README's
screenshots after a change to the launch demo (`src/sightglass/launch/`).

## Releasing

Releases are published to PyPI by `.github/workflows/release.yml` when a
GitHub release is published.

1. Set `__version__` in `src/sightglass/__init__.py` on a branch named
   `release/v<version>`, and merge its pull request to `main` once CI has
   passed on Linux, macOS and Windows.
2. Create a GitHub release with the tag `v<version>` (e.g. `v0.2.0`). The
   workflow checks the tag matches `__version__`, builds, and uploads.

### One-time PyPI setup

The workflow uses [trusted publishing](https://docs.pypi.org/trusted-publishers/),
so no API token is stored in GitHub. Before the first release:

1. On pypi.org, go to *Your projects > Publishing* and add a **pending
   publisher** (the project doesn't exist on PyPI until its first upload):
   - PyPI project name: `sightglass`
   - Owner: `davidson-engineering`
   - Repository: `sightglass`
   - Workflow: `release.yml`
   - Environment: `pypi`
2. In the GitHub repository settings, create an environment named `pypi`.
   Adding yourself as a required reviewer makes each upload wait for your
   approval.
