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

Publishing is a separate release action. The repository contract currently
prohibits package publication; no workflow uploads to an index. Before an
authorized release, confirm the distribution name and PyPI ownership, validate
the archives, and configure the chosen publisher. Then update README install
instructions to use `pipx install dmux-ml`. Do not advertise an index install
before the release exists.

Schema v2 removes scoped JSON alias keys. Existing scripts can select
`--schema-version 1`; see [the JSON migration contract](SNAPSHOTS.md).
