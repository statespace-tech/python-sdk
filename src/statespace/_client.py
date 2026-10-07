"""The Statespace client: configuration, experiments, and event delivery."""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import queue
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import certifi

from statespace._assignment import Eligibility, GroupConfig, JsonObject, bucket, choose
from statespace._group import Group
from statespace._runtime import Runtime

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

__all__ = ["Client", "Experiment", "StatespaceError"]

logger = logging.getLogger("statespace")

_DEFAULT_ENDPOINT = "https://api.statespace.com"
_USER_AGENT = "statespace-python/0.1.1"
_MAX_EVENT_BYTES = 64 * 1024
_MAX_BATCH = 100
_MAX_QUEUED = 10_000


class StatespaceError(Exception):
    """A request to Statespace failed or the client is misconfigured."""


@dataclass(frozen=True)
class _Config:
    name: str
    version: int
    status: str
    salt: str
    groups: tuple[GroupConfig, ...]
    eligibility: Eligibility
    stale_after: float
    fetched_at: float


class Client:
    """Connects an application to Statespace.

    Credentials come from the arguments, then ``SSP_API_KEY`` and
    ``STATESPACE_URL``, then the session saved by ``ssp login``. A client
    keeps one long-lived handle per experiment, refreshes configurations in
    the background, and delivers events in batches. Events are flushed when
    the interpreter exits.
    """

    def __init__(self, api_key: str | None = None, endpoint: str | None = None) -> None:
        saved_key, saved_endpoint = _saved_login()
        self._key = api_key or os.getenv("SSP_API_KEY") or saved_key
        if not self._key:
            raise StatespaceError("set SSP_API_KEY or run `ssp login`")
        endpoint = endpoint or os.getenv("STATESPACE_URL") or saved_endpoint
        self._endpoint = (endpoint or _DEFAULT_ENDPOINT).rstrip("/")
        self._tls = _tls_context() if self._endpoint.startswith("https://") else None
        self._experiments: dict[str, Experiment] = {}
        self._artifacts: dict[str, bytes] = {}
        self._lock = threading.Lock()
        self._runtime: Runtime | None = None
        self._events: queue.Queue[JsonObject] = queue.Queue(_MAX_QUEUED)
        self._pending = 0
        self._dropped = 0
        self._idle = threading.Condition()
        self._closed = threading.Event()
        threading.Thread(target=self._deliver, name="statespace-events", daemon=True).start()
        threading.Thread(target=self._refresh, name="statespace-config", daemon=True).start()
        atexit.register(self.close)

    def experiment(self, name: str) -> Experiment:
        """Return the handle for one experiment, loading it on first use."""
        with self._lock:
            experiment = self._experiments.get(name)
            if experiment is None:
                experiment = Experiment(self, name)
                self._experiments[name] = experiment
            return experiment

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait until queued events are delivered.

        Returns False on timeout or if any event was dropped since the last flush.
        """
        deadline = time.monotonic() + timeout
        with self._idle:
            while self._pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._idle.wait(remaining)
            dropped, self._dropped = self._dropped, 0
        return dropped == 0

    def close(self) -> None:
        """Flush events and stop background work."""
        if self._closed.is_set():
            return
        self.flush()
        self._closed.set()
        atexit.unregister(self.close)
        if self._runtime is not None:
            self._runtime.close()

    # Configuration

    def _load(self, name: str) -> _Config:
        path = f"/v1/runtime/experiments/{urllib.parse.quote(name, safe='')}"
        raw = json.loads(self._request("GET", path))
        groups = tuple(
            GroupConfig(
                name=group["name"],
                ranges=tuple((float(start), float(end)) for start, end in group["ranges"]),
                parameters=group["parameters"],
            )
            for group in raw["groups"]
        )
        config = _Config(
            name=raw["name"],
            version=int(raw["version"]),
            status=raw["status"],
            salt=raw["salt"],
            groups=groups,
            eligibility=Eligibility(raw.get("eligibility")),
            stale_after=float(raw.get("stale_after_seconds", 48 * 3600)),
            fetched_at=time.monotonic(),
        )
        for group in groups:
            for parameter in group.parameters.values():
                if parameter["kind"] == "function":
                    self._artifact(parameter["sha256"])
        return config

    def _refresh(self) -> None:
        while not self._closed.wait(60.0):
            with self._lock:
                experiments = list(self._experiments.values())
            for experiment in experiments:
                try:
                    experiment._config = self._load(experiment.name)
                except Exception as error:  # noqa: BLE001 - keep serving the last config
                    logger.warning("could not refresh %s: %s", experiment.name, error)

    def _artifact(self, sha256: str) -> bytes:
        artifact = self._artifacts.get(sha256)
        if artifact is None:
            artifact = self._request("GET", f"/v1/runtime/artifacts/{sha256}", timeout=60.0)
            if hashlib.sha256(artifact).hexdigest() != sha256:
                raise StatespaceError(f"component {sha256} does not match its hash")
            self._artifacts[sha256] = artifact
        return artifact

    def _execute(self, sha256: str, payload: str, timeout: float) -> str:
        with self._lock:
            if self._runtime is None:
                self._runtime = Runtime()
            runtime = self._runtime
        return runtime.execute(sha256, self._artifact(sha256), payload, timeout)

    # Events

    def _enqueue(self, kind: str, event: JsonObject) -> None:
        """Queue an event without ever blocking or failing the application."""
        try:
            size = len(json.dumps(event, separators=(",", ":"), allow_nan=False))
        except (TypeError, ValueError) as error:
            raise TypeError("events must contain only JSON values") from error
        if size > _MAX_EVENT_BYTES:
            raise ValueError("an event must serialize to at most 64 KiB")
        with self._idle:
            self._pending += 1
        try:
            self._events.put_nowait({"kind": kind, "event": event})
        except queue.Full:
            logger.warning("the event queue is full; dropping a %s", kind)
            self._settle(1, dropped=True)

    def _settle(self, count: int, dropped: bool = False) -> None:
        with self._idle:
            self._pending -= count
            if dropped:
                self._dropped += count
            self._idle.notify_all()

    def _deliver(self) -> None:
        while True:
            batch = [self._events.get()]
            while len(batch) < _MAX_BATCH:
                try:
                    batch.append(self._events.get_nowait())
                except queue.Empty:
                    break
            body = {
                "runs": [item["event"] for item in batch if item["kind"] == "run"],
                "outcomes": [item["event"] for item in batch if item["kind"] == "outcome"],
            }
            self._settle(len(batch), dropped=not self._post(body))

    def _post(self, body: JsonObject) -> bool:
        """Send one batch, retrying transient failures. Returns whether it was accepted."""
        delay = 0.5
        for _attempt in range(6):
            try:
                self._request("POST", "/v1/events", body)
                return True
            except _PermanentError as error:
                logger.error("Statespace rejected %s events: %s", _count(body), error)
                return False
            except StatespaceError as error:
                if self._closed.wait(delay):
                    break
                logger.debug("retrying event delivery: %s", error)
                delay = min(delay * 2, 8.0)
        logger.error("could not deliver %s events", _count(body))
        return False

    def _request(
        self,
        method: str,
        path: str,
        body: JsonObject | None = None,
        timeout: float = 10.0,
    ) -> bytes:
        data = None if body is None else json.dumps(body, allow_nan=False).encode()
        request = urllib.request.Request(
            self._endpoint + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
                "User-Agent": _USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=self._tls) as response:
                return bytes(response.read())
        except urllib.error.HTTPError as error:
            message = f"{method} {path} returned HTTP {error.code}"
            if 400 <= error.code < 500 and error.code not in (408, 429):
                raise _PermanentError(message) from error
            raise StatespaceError(message) from error
        except OSError as error:
            raise StatespaceError(f"{method} {path} failed: {error}") from error


class _PermanentError(StatespaceError):
    """A request that retrying cannot fix."""


class Experiment:
    """One experiment. Get one with :func:`statespace.experiment`."""

    def __init__(self, client: Client, name: str) -> None:
        if not name:
            raise ValueError("experiment name must not be empty")
        self.name = name
        self._client = client
        self._config: _Config | None = None
        try:
            self._config = client._load(name)
        except _PermanentError:
            raise
        except Exception as error:  # noqa: BLE001 - serve defaults until a refresh succeeds
            logger.warning("serving defaults for %s: %s", name, error)

    def assign(self, subject_id: str, *, context: JsonObject | None = None) -> Group:
        """Assign a subject to a group and record the assignment.

        ``context`` is the JSON the experiment's eligibility rule reads. The
        same subject always gets the same group. While the experiment is not
        running, or for ineligible subjects, the group has no parameters, so
        every read returns the application's default.
        """
        if not subject_id:
            raise ValueError("subject_id must not be empty")
        context = dict(context or {})
        config = self._config
        if config is None or config.status != "running":
            return Group(self, subject_id, None, {})
        if time.monotonic() - config.fetched_at > config.stale_after:
            logger.warning("the %s configuration is stale; serving defaults", self.name)
            return Group(self, subject_id, None, {})
        assigned: GroupConfig | None = None
        try:
            reason = "assigned" if config.eligibility.evaluate(context) else "ineligible"
        except Exception as error:  # noqa: BLE001 - an error makes the subject ineligible
            logger.warning("eligibility failed for %s: %s", self.name, error)
            reason = "eligibility-error"
        if reason == "assigned":
            assigned = choose(config.groups, bucket(config.salt, subject_id))
        self._client._enqueue(
            "run",
            {
                "id": f"run_{uuid.uuid4().hex}",
                "experiment": config.name,
                "version": config.version,
                "subject_id": subject_id,
                "group": None if assigned is None else assigned.name,
                "reason": reason,
                "context": context,
                "timestamp": _now(),
            },
        )
        if assigned is None:
            return Group(self, subject_id, None, {})
        return Group(self, subject_id, assigned.name, assigned.parameters)

    def log(self, subject_id: str, name: str, data: JsonObject | None = None) -> None:
        """Record an outcome, such as a click or a purchase, for a subject.

        Outcomes can come from any process. Results count each one for the
        group the subject was assigned to before it.
        """
        if not subject_id or not name:
            raise ValueError("subject_id and name must not be empty")
        if data is not None and not isinstance(data, dict):
            raise TypeError("outcome data must be a JSON object")
        self._client._enqueue(
            "outcome",
            {
                "id": f"out_{uuid.uuid4().hex}",
                "experiment": self.name,
                "subject_id": subject_id,
                "name": name,
                "data": data or {},
                "timestamp": _now(),
            },
        )


def _count(body: JsonObject) -> int:
    return len(body["runs"]) + len(body["outcomes"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _saved_login() -> tuple[str | None, str | None]:
    """Read the session saved by ``ssp login``."""
    configured = os.getenv("STATESPACE_CONFIG")
    if configured:
        path = Path(configured)
    elif sys.platform == "darwin":
        path = Path.home() / "Library/Application Support/statespace/config.toml"
    elif os.name == "nt":
        path = Path(os.getenv("APPDATA", "")) / "statespace/config.toml"
    else:
        path = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))
        path = path / "statespace/config.toml"
    try:
        settings = tomllib.loads(path.read_text())
    except (OSError, ValueError):
        return None, None
    token, endpoint = settings.get("token"), settings.get("endpoint")
    return (
        token if isinstance(token, str) else None,
        endpoint if isinstance(endpoint, str) else None,
    )


def _tls_context() -> ssl.SSLContext:
    """Verify TLS even when Python has no system certificate bundle."""
    context = ssl.create_default_context()
    custom = os.getenv("SSL_CERT_FILE") or os.getenv("SSL_CERT_DIR")
    if not custom and not context.get_ca_certs():
        context.load_verify_locations(cafile=certifi.where())
    return context
