"""Stable assignment and eligibility, identical in every Statespace SDK."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import celpy
from celpy.adapter import json_to_cel

JsonObject = dict[str, Any]


@dataclass(frozen=True)
class GroupConfig:
    """One group of a published version, as the runtime configuration lists it."""

    name: str
    ranges: tuple[tuple[float, float], ...]
    parameters: JsonObject


def bucket(salt: str, subject_id: str) -> float:
    """Map a subject to a uniform point in ``[0, 1)``.

    The first 64 bits of ``SHA-256(salt:subject_id)`` keep 53 bits of
    precision, which is exactly representable as a double in every language.
    """
    digest = hashlib.sha256(f"{salt}:{subject_id}".encode()).digest()
    return (int.from_bytes(digest[:8], "big") >> 11) / 2**53


def choose(groups: Sequence[GroupConfig], point: float) -> GroupConfig:
    """Return the group whose ranges contain ``point``, or control."""
    for group in groups:
        if any(start <= point < end for start, end in group.ranges):
            return group
    return next(group for group in groups if group.name == "control")


class Eligibility:
    """A CEL expression over ``context``, compiled once per configuration."""

    def __init__(self, expression: str | None) -> None:
        self._program = None
        if expression is not None:
            environment = celpy.Environment()
            self._program = environment.program(environment.compile(expression))

    def evaluate(self, context: JsonObject) -> bool:
        """Return whether the context is eligible. Raises if evaluation fails."""
        if self._program is None:
            return True
        result = self._program.evaluate({"context": json_to_cel(context)})
        if isinstance(result, Exception):
            raise result
        return bool(result)
