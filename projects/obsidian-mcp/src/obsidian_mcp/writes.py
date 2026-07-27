"""Create-only writes. O_EXCL is the overwrite guard - a syscall, not a convention."""
import os
import time
from collections import deque
from pathlib import Path

MAX_BYTES = 65536
MAX_PER_HOUR = 30


class WriteLimit(Exception):
    pass


class Writer:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._stamps: deque[float] = deque()

    def _check_rate(self) -> None:
        now = self._clock()
        while self._stamps and now - self._stamps[0] > 3600:
            self._stamps.popleft()
        if len(self._stamps) >= MAX_PER_HOUR:
            raise WriteLimit(f"rate cap: {MAX_PER_HOUR} notes/hour")
        self._stamps.append(now)

    def create(self, path: Path, content: str) -> Path:
        data = content.encode("utf-8")
        if len(data) > MAX_BYTES:
            raise WriteLimit(f"note exceeds {MAX_BYTES} bytes")
        self._check_rate()
        path.parent.mkdir(parents=True, exist_ok=True)
        target, n = path, 1
        while True:
            try:
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                break
            except FileExistsError:
                n += 1
                target = path.with_name(f"{path.stem}-{n}.md")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return target
