"""Simple pub/sub for garden data modification events."""

from __future__ import annotations

from collections.abc import Callable

_listeners: list[Callable[[], None]] = []


def on_garden_modified(fn: Callable[[], None]) -> None:
    """Register a callback invoked when garden data changes."""
    if fn not in _listeners:
        _listeners.append(fn)


def notify_garden_modified() -> None:
    """Notify all registered listeners that garden data changed."""
    for fn in _listeners:
        fn()
