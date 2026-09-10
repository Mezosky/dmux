# Distribution and upgrade notes

The prepared 0.2.0 distribution is `dmux-ml`. Python imports, adapter entry-point
group, and the terminal command remain `dmux`. The unrelated
[PyPI project named dmux](https://pypi.org/project/dmux/) is not this repository.
This release candidate has not been uploaded to PyPI.

## Install from the checkout

```bash
pipx install .
dmux --version
dmux demo --live
```

pipx isolates the CLI's dependencies from training environments. For an existing
virtual environment, `python -m pip install .` works too. Linux and macOS with
Python 3.11+ are supported; native Windows is unsupported.

If this repository's older version was installed as `dmux`, first remove that
distribution from the same environment. For example, in that virtual environment:

```bash
python -m pip uninstall dmux
python -m pip install -e '.[dev]'
```

For an older pipx installation of this repository, use `pipx uninstall dmux`
before `pipx install .`. Do not co-install the two distributions: they expose
the same command/import names and can create duplicate adapter registrations.
The uninstall concerns package installation, not experiment outputs or catalogs.
dmux development does not perform this migration in your training environment.

For an editable installation predating an adapter entry point, reinstall the
checkout to refresh package metadata. SDK compatibility tests may skip with a
reinstall hint when the entry point is absent.

## Build and inspect

```bash
python -m pip install -e '.[dev]'
pytest -q
ruff check .
mypy
python -m build
python -m twine check dist/*
pipx install dist/dmux_ml-0.2.0-py3-none-any.whl
```

Install `twine` separately for the metadata check if needed. The wheel contains
the generic core, separate MLflow/SLURM adapter packages, type marker, and plan
and snapshot schemas. The sdist adds docs and small examples. Neither archive
includes screenshots, the logo, the supplied screenshot ZIP, or tests.
CI retains the built archives from Ubuntu/Python 3.13 for inspection.

## Publish an authorized release

The maintainer has explicitly authorized publishing `dmux-ml` 0.2.0. Publication
uses the manually dispatched `publish.yml` workflow on `main`; ordinary pushes
and pull requests never publish. The build job verifies the requested version,
runs tests/lint/types, builds both archives and checks their metadata. The
separate upload job can publish only those artifacts using PyPI Trusted
Publishing; no stored API token is needed.

For the first release, sign in to [PyPI publishing settings](https://pypi.org/manage/account/publishing/)
and add a pending GitHub publisher with these exact values:

| Field | Value |
| --- | --- |
| PyPI project name | `dmux-ml` |
| Owner | `Mezosky` |
| Repository | `dmux` |
| Workflow filename | `publish.yml` |
| Environment | `pypi` |

See [PyPI's first-release instructions](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/).
Then run **Publish to PyPI** from GitHub Actions on `main`, with version `0.2.0`
and publishing enabled. Disable the publishing input to check the complete build
without uploading. GitHub OIDC permission is confined to the upload job.

After success, verify the PyPI files and installation in a fresh environment,
then update README install instructions to `pipx install dmux-ml`. PyPI versions
cannot be overwritten: make fixes in a new version after publication.

Schema v2 removes scoped JSON alias keys. Existing scripts can select
`--schema-version 1`; see [the JSON migration contract](SNAPSHOTS.md).

Identical adapter entry-point values from stale editable/distribution metadata
are deduplicated. Different factories registered under the same name still fail
closed. Uninstalling the previous local `dmux` distribution before installing
`dmux-ml` remains the recommended migration, because duplicate metadata can also
confuse package managers and uninstall operations.
