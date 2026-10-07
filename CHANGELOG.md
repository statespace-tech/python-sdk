# Changelog

## 0.1.1 - 2026-10-07

The first release of the group model.

- `statespace.experiment(name)` returns a long-lived experiment handle.
- `Experiment.assign()` returns a `Group` with `value()` and `function()`.
- `Experiment.log()` records outcomes for any subject, from any process.
- Failures fall back to the application's default and record a `statespace.error` outcome.
