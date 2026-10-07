"""A fake Statespace service for SDK tests."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from statespace import Client

IDENTITY = (Path(__file__).parent / "fixtures/identity.wasm").read_bytes()
IDENTITY_SHA256 = hashlib.sha256(IDENTITY).hexdigest()


class Service:
    def __init__(self) -> None:
        self.experiments: dict[str, dict[str, Any]] = {}
        self.runs: list[dict[str, Any]] = []
        self.outcomes: list[dict[str, Any]] = []

    def publish(self, name: str, status: str = "running", **groups: Any) -> None:
        """Publish groups as `name=(ranges, parameters)`."""
        self.experiments[name] = {
            "name": name,
            "version": 1,
            "status": status,
            "eligibility": 'context.country == "US"',
            "salt": "salt",
            "groups": [{"name": "control", "ranges": [], "parameters": {}}]
            + [
                {"name": group, "ranges": ranges, "parameters": parameters}
                for group, (ranges, parameters) in groups.items()
            ],
            "poll_interval_seconds": 60,
            "stale_after_seconds": 172800,
        }


@pytest.fixture
def service() -> Iterator[Service]:
    state = Service()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: Any) -> None:
            pass

        def reply(self, status: int, body: bytes, kind: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            name = self.path.rsplit("/", 1)[-1]
            if self.path.startswith("/v1/runtime/experiments/") and name in state.experiments:
                self.reply(200, json.dumps(state.experiments[name]).encode())
            elif self.path == f"/v1/runtime/artifacts/{IDENTITY_SHA256}":
                self.reply(200, IDENTITY, "application/wasm")
            else:
                self.reply(404, b'{"error":"not found"}')

        def do_POST(self) -> None:  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.runs.extend(body["runs"])
            state.outcomes.extend(body["outcomes"])
            self.reply(202, json.dumps({"accepted": 1}).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state.endpoint = f"http://127.0.0.1:{server.server_port}"  # type: ignore[attr-defined]
    yield state
    server.shutdown()


@pytest.fixture
def client(service: Service) -> Iterator[Client]:
    client = Client(api_key="ssp_test", endpoint=service.endpoint)  # type: ignore[attr-defined]
    yield client
    client.close()
