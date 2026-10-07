"""Run Statespace experiments on parameter values and functions.

.. code-block:: python

    import statespace

    experiment = statespace.experiment("ranking")
    group = experiment.assign("u_42", context={"country": "US"})
    rank = group.function("ranker", rerank)
    top_k = group.value("top_k", 20)
    experiment.log("u_42", "click")
"""

from __future__ import annotations

import threading

from statespace._client import Client, Experiment, StatespaceError
from statespace._group import Group

__all__ = ["Client", "Experiment", "Group", "StatespaceError", "experiment", "flush"]

_client: Client | None = None
_lock = threading.Lock()


def _default_client() -> Client:
    global _client
    with _lock:
        if _client is None:
            _client = Client()
        return _client


def experiment(name: str) -> Experiment:
    """Return the experiment ``name`` from the default client.

    The default client reads ``SSP_API_KEY`` and ``STATESPACE_URL``, or the
    session saved by ``ssp login``. Handles are cached, so call this anywhere.
    """
    return _default_client().experiment(name)


def flush(timeout: float = 5.0) -> bool:
    """Wait until queued events are delivered. Returns False on timeout or loss."""
    return _default_client().flush(timeout)
