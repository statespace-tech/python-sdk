# Contributing

Open an issue before a substantial change to the public API. Report security issues
privately, as [SECURITY.md](SECURITY.md) describes.

Install [uv](https://docs.astral.sh/uv/) and run the checks that CI runs.

```shell
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy src tests
uv run pytest
```

Use Conventional Commits and add a `CHANGELOG.md` entry for user-visible changes. By
contributing, you agree that your contribution is licensed under the Apache License 2.0.
