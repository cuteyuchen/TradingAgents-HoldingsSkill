"""Process-wide worker stop registry for graceful shutdown."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_WORKERS: dict[tuple[str, int], threading.Event] = {}
_LOCK = threading.Lock()


def register_worker(kind: str, work_id: int, stop_event: threading.Event) -> None:
    with _LOCK:
        _WORKERS[(kind, int(work_id))] = stop_event


def unregister_worker(kind: str, work_id: int) -> None:
    with _LOCK:
        _WORKERS.pop((kind, int(work_id)), None)


def signal_workers(*, kinds: tuple[str, ...] | None = None, timeout: float = 5.0) -> int:
    """Signal registered workers and wait bounded seconds for them to exit."""

    with _LOCK:
        events = [
            event
            for (kind, _work_id), event in _WORKERS.items()
            if kinds is None or kind in kinds
        ]
        count = len(events)
    for event in events:
        event.set()
    if not count or timeout <= 0:
        return count
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with _LOCK:
            remaining = sum(
                1
                for (kind, _work_id) in _WORKERS
                if kinds is None or kind in kinds
            )
        if not remaining:
            break
        time.sleep(0.05)
    if remaining:
        logger.warning("graceful shutdown timed out with %s worker(s) still registered", remaining)
    return count


def active_workers() -> list[dict[str, Any]]:
    with _LOCK:
        return [
            {"kind": kind, "work_id": work_id}
            for kind, work_id in sorted(_WORKERS)
        ]


__all__ = ["active_workers", "register_worker", "signal_workers", "unregister_worker"]
