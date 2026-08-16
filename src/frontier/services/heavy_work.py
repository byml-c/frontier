from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from ..config import settings


class _ReentrantGate:
    """Bound concurrency across request threads without deadlocking nested helpers."""

    def __init__(self, limit: int) -> None:
        self._semaphore = threading.BoundedSemaphore(limit)
        self._local = threading.local()

    @contextmanager
    def slot(self) -> Iterator[None]:
        depth = int(getattr(self._local, "depth", 0))
        if depth == 0:
            self._semaphore.acquire()
        self._local.depth = depth + 1
        try:
            yield
        finally:
            self._local.depth -= 1
            if depth == 0:
                self._semaphore.release()


_PAPER_PROCESSING_GATE = _ReentrantGate(settings.paper_processing_max_concurrency)


@contextmanager
def paper_processing_slot() -> Iterator[None]:
    """Serialize memory- and I/O-heavy paper work on low-resource servers."""
    with _PAPER_PROCESSING_GATE.slot():
        yield
