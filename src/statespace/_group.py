"""A subject's group and the parameter values it receives."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

from statespace._assignment import JsonObject

if TYPE_CHECKING:
    from statespace._client import Experiment

logger = logging.getLogger("statespace")

T = TypeVar("T")
Input = TypeVar("Input")
Output = TypeVar("Output")

_DEFAULT_TIMEOUT = 5.0
#: The outcome recorded when a parameter falls back to the application's default.
ERROR_OUTCOME = "statespace.error"


class Group:
    """The group a subject was assigned to. Get one with :meth:`Experiment.assign`.

    ``name`` is the group's name, ``"control"``, or ``None`` when the subject
    is not in the experiment. Every read takes the application's default, which
    is returned for control, for parameters the group does not set, and when a
    value has the wrong type or a function fails.
    """

    def __init__(
        self,
        experiment: Experiment,
        subject_id: str,
        name: str | None,
        parameters: JsonObject,
    ) -> None:
        self.name = name
        self._experiment = experiment
        self._subject_id = subject_id
        self._parameters = parameters

    def __repr__(self) -> str:
        return f"Group({self.name!r})"

    def value(self, name: str, default: T) -> T:
        """Return the group's value for ``name``, or ``default``."""
        parameter = self._parameters.get(name)
        if parameter is None:
            return default
        if parameter["kind"] != "value":
            self._error(name, "type", "is a function; read it with function()")
            return default
        value = parameter["value"]
        if not _matches(value, default):
            self._error(name, "type", f"is {type(value).__name__}, not {type(default).__name__}")
            return default
        return float(value) if isinstance(default, float) else value  # type: ignore[return-value]

    def function(
        self,
        name: str,
        default: Callable[[Input], Output],
        *,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> Callable[[Input], Output]:
        """Return the group's function for ``name``, or ``default``.

        The function takes and returns JSON values. It runs locally in a
        sandbox with ``timeout`` seconds per call, and falls back to
        ``default`` if it fails.
        """
        parameter = self._parameters.get(name)
        if parameter is None:
            return default
        if parameter["kind"] != "function":
            self._error(name, "type", "is a value; read it with value()")
            return default
        sha256 = parameter["sha256"]
        client = self._experiment._client

        def assigned(input: Input) -> Output:
            started = time.perf_counter()
            try:
                payload = json.dumps(input, allow_nan=False)
                output: Output = json.loads(client._execute(sha256, payload, timeout))
                return output
            except Exception as error:  # noqa: BLE001 - never fail the request
                elapsed = time.perf_counter() - started
                if elapsed >= timeout:
                    kind = "timeout"
                elif isinstance(error, (TypeError, ValueError)):
                    kind = "invalid-json"
                else:
                    kind = "failed"
                self._error(name, kind, str(error), duration=elapsed)
            return default(input)

        return assigned

    def _error(self, parameter: str, kind: str, detail: str, duration: float | None = None) -> None:
        logger.warning("%s in %s: %s %s", parameter, self.name, kind, detail)
        data: JsonObject = {"group": self.name, "parameter": parameter, "error": kind}
        if duration is not None:
            data["duration_ms"] = round(duration * 1000, 3)
        self._experiment.log(self._subject_id, ERROR_OUTCOME, data)


def _matches(value: Any, default: Any) -> bool:
    """Whether a JSON value can stand in for the default's type."""
    if default is None:
        return True
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(default, float):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(default, str):
        return isinstance(value, str)
    if isinstance(default, (list, tuple)):
        return isinstance(value, list)
    if isinstance(default, dict):
        return isinstance(value, dict)
    return True
