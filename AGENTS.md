# Contributor instructions

## Rules

- Never raise from `assign`, `value`, or a function returned by `function` because of a
  Statespace failure. Return the default, log a warning, and record `statespace.error`.
- Keep assignment identical to the TypeScript and Go SDKs. `tests/test_assignment.py`
  holds the shared vectors.
- Keep the public API in `statespace/__init__.py`.

## Checks

```shell
uv run ruff check . && uv run ruff format --check .
uv run mypy src tests
uv run pytest
uv build
```

## Commits

Use Conventional Commits, such as `fix(group): accept integers for float defaults`.
