"""Sealed synthetic task bodies for the finite Invoke client entry only."""

from __future__ import annotations

from typing import Never


def normal_body(context: object, value: int, events: list[str]) -> int:
    events.append("body")
    return value + 1


def raising_body(context: object, value: int, events: list[str]) -> Never:
    events.append("body")
    raise ValueError("callback-failed")
