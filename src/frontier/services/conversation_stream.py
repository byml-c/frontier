from __future__ import annotations

import queue
import threading
from collections.abc import Iterator


class ConversationStream:
    """A bounded HTTP subscriber; the dispatcher, not the browser, owns the Agent."""

    def __init__(self) -> None:
        self._queue: queue.Queue[str] = queue.Queue(maxsize=256)
        self._detached = threading.Event()
        self._finished = threading.Event()
        self._overflow = threading.Event()

    def publish(self, event: str) -> None:
        if self._detached.is_set():
            return
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            # Persistence continues even if a browser stops reading. Do not let
            # slow clients block the one foreground Agent or grow server memory.
            self._overflow.set()
            self.detach()

    def finish(self) -> None:
        self._finished.set()

    def detach(self) -> None:
        self._detached.set()
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def events(self) -> Iterator[str]:
        from ..interactive_stream import encode_stream_event

        try:
            while True:
                if self._overflow.is_set():
                    yield encode_stream_event(
                        "error", message="连接读取过慢，Agent 会继续处理；重新打开对话可查看进度。"
                    )
                    return
                try:
                    yield self._queue.get(timeout=1)
                except queue.Empty:
                    if self._finished.is_set():
                        return
                    yield encode_stream_event("heartbeat")
        finally:
            # Closing/refreshing the page only detaches this subscriber.
            self.detach()
