# Statespace for Python

[![CI](https://github.com/statespace-tech/python-sdk/actions/workflows/ci.yml/badge.svg)](https://github.com/statespace-tech/python-sdk/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-007ec6?style=flat-square)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/statespace-sdk?style=flat-square)](https://pypi.org/project/statespace-sdk/)

Run Statespace A/B tests on functions and values in Python applications. Each subject is
assigned to a group, reads that group's parameters, and falls back to your current code
everywhere else.

## Install

Install the SDK with pip or uv.

```shell
pip install statespace-sdk
```

Set an API key from `ssp key create --preset runtime`. Locally, the SDK uses your `ssp login` session.

```shell
export SSP_API_KEY=ssp_key_...
```

## Quickstart

Get an experiment once and keep it. The SDK refreshes its configuration in the background.

```python
import statespace

experiment = statespace.experiment("ranking")
```

Assign a subject. The same subject always gets the same group.

```python
group = experiment.assign("user-42", context={"country": "US"})
```

Read a value. The second argument is your current value, which control receives.

```python
top_k = group.value("top_k", 20)
```

Read a function. It runs in a local sandbox and falls back to your function if it fails.

```python
rank = group.function("ranker", rerank)
results = rank(items)[:top_k]
```

Log outcomes for the subject, from this process or any other.

```python
experiment.log("user-42", "click", {"position": 3})
```

Compare groups from the command line.

```shell
ssp experiment results ranking --outcome click
```

## Groups

`group.name` is the group's name, `"control"`, or `None` when the subject is not in the experiment.

```python
if group.name is not None:
    print(f"user-42 is in {group.name}")
```

A value must match the type of its default. Otherwise you get the default, and the SDK
records a `statespace.error` outcome.

```python
temperature = group.value("temperature", 0.7)  # accepts 0.2 or 1
prompt = group.value("prompt", DEFAULT_PROMPT)  # accepts strings
```

A function takes one JSON value and returns one. Pass several arguments as an object.

```python
score = group.function("scorer", score_default, timeout=0.05)
score({"query": query, "items": items})
```

## Delivery

Events are sent in the background in batches. Flush before a short-lived process exits.

```python
statespace.flush()
```

Use a client directly to configure credentials in code.

```python
client = statespace.Client(api_key="ssp_key_...", endpoint="https://api.statespace.com")
experiment = client.experiment("ranking")
```

## Guarantees

- Reads never raise because of Statespace. They return the default and log a warning.
  Only `experiment()` raises, for an unknown experiment or an invalid key.
- Assignment is computed locally from a cached configuration, with no network call.
- Functions run without file, network, or environment access, with 256 MiB of memory by
  default (`STATESPACE_MAX_MEMORY_BYTES`).
- Python, TypeScript, and Go assign every subject to the same group.

## License

Apache-2.0
