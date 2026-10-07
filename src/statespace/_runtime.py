"""Execute Statespace components in a wasmtime sandbox."""

from __future__ import annotations

import math
import os
import threading
from collections import OrderedDict

import wasmtime
from wasmtime import component

_EPOCH_SECONDS = 0.01
_DEFAULT_MEMORY_BYTES = 256 * 1024 * 1024
_CACHED_COMPONENTS = 8


class FunctionError(Exception):
    """A component trapped, timed out, or returned an invalid value."""


class Runtime:
    """Compile each component once and run calls on a shared epoch clock.

    A call gets a fresh store, so no state survives between calls. The guest
    has no filesystem, network, or environment access and is limited to
    ``STATESPACE_MAX_MEMORY_BYTES`` of linear memory (256 MiB by default).
    """

    def __init__(self) -> None:
        config = wasmtime.Config()
        config.epoch_interruption = True
        self._engine = wasmtime.Engine(config)
        self._components: OrderedDict[str, component.Component] = OrderedDict()
        self._lock = threading.Lock()
        self._memory_bytes = _memory_limit()
        self._stopped = threading.Event()
        threading.Thread(target=self._tick, name="statespace-epoch", daemon=True).start()

    def _tick(self) -> None:
        while not self._stopped.wait(_EPOCH_SECONDS):
            self._engine.increment_epoch()

    def _component(self, sha256: str, artifact: bytes) -> component.Component:
        with self._lock:
            compiled = self._components.get(sha256)
            if compiled is None:
                compiled = component.Component(self._engine, artifact)
                self._components[sha256] = compiled
                if len(self._components) > _CACHED_COMPONENTS:
                    self._components.popitem(last=False)
            self._components.move_to_end(sha256)
            return compiled

    def execute(self, sha256: str, artifact: bytes, payload: str, timeout: float) -> str:
        """Call ``execute(payload)`` and return its string result."""
        compiled = self._component(sha256, artifact)
        store = wasmtime.Store(self._engine)
        store.set_limits(memory_size=self._memory_bytes)
        store.set_wasi(wasmtime.WasiConfig())
        store.set_epoch_deadline(max(1, math.ceil(timeout / _EPOCH_SECONDS)))
        try:
            linker = component.Linker(self._engine)
            linker.add_wasip2()
            instance = linker.instantiate(store, compiled)
            execute = instance.get_func(store, "execute")
            if execute is None:
                raise FunctionError("the component does not export execute")
            result = execute(store, payload)
            execute.post_return(store)
        except (wasmtime.Trap, wasmtime.WasmtimeError) as error:
            raise FunctionError(str(error)) from error
        if not isinstance(result, str):
            raise FunctionError("the component returned a non-string value")
        return result

    def close(self) -> None:
        self._stopped.set()


def _memory_limit() -> int:
    value = os.getenv("STATESPACE_MAX_MEMORY_BYTES")
    if value is None:
        return _DEFAULT_MEMORY_BYTES
    try:
        limit = int(value)
    except ValueError as error:
        raise ValueError("STATESPACE_MAX_MEMORY_BYTES must be an integer") from error
    if limit <= 0:
        raise ValueError("STATESPACE_MAX_MEMORY_BYTES must be positive")
    return limit
