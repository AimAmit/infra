import sys
import threading
import time
from collections import deque
from pathlib import Path

import pytest
from obsidian_mcp.writes import Writer, WriteLimit, MAX_BYTES, MAX_PER_HOUR


def test_creates_file(tmp_path):
    out = Writer().create(tmp_path / "a.md", "hello")
    assert out.read_text() == "hello"

def test_never_overwrites(tmp_path):
    w = Writer()
    first = w.create(tmp_path / "a.md", "one")
    second = w.create(tmp_path / "a.md", "two")
    assert second != first
    assert second.name == "a-2.md"
    assert first.read_text() == "one"

def test_size_cap(tmp_path):
    with pytest.raises(WriteLimit):
        Writer().create(tmp_path / "big.md", "x" * 70000)

def test_rate_cap(tmp_path):
    t = [0.0]
    w = Writer(clock=lambda: t[0])
    for i in range(MAX_PER_HOUR):
        w.create(tmp_path / f"n{i}.md", "ok")
    with pytest.raises(WriteLimit):
        w.create(tmp_path / "over.md", "no")
    t[0] = 3601.0  # window rolls over
    w.create(tmp_path / "later.md", "ok")


def test_size_cap_counts_bytes_not_chars(tmp_path):
    # 30000 chars, but 90000 UTF-8 bytes: under MAX_BYTES as characters,
    # over it as encoded bytes. Pins len(data) against a mutation to len(content).
    payload = "漢" * 30000
    assert len(payload) < MAX_BYTES < len(payload.encode("utf-8"))
    with pytest.raises(WriteLimit):
        Writer().create(tmp_path / "wide.md", payload)
    assert not (tmp_path / "wide.md").exists()


def test_rejects_relative_path(tmp_path, monkeypatch):
    # Last line of defense: a caller that forgot paths.resolve() must not get
    # a write against the process CWD.
    monkeypatch.chdir(tmp_path)
    with pytest.raises(WriteLimit):
        Writer().create(Path("escapee.md"), "no")
    assert not (tmp_path / "escapee.md").exists()


def test_rate_limit_section_is_mutually_exclusive(tmp_path):
    # The evict-check-append sequence must be atomic: if two threads can both
    # pass `len(stamps) >= MAX_PER_HOUR` before either appends, the cap
    # overshoots, and a concurrent popleft() can also empty the deque between
    # the `while stamps` truthiness test and the `stamps[0]` access.
    #
    # The probe is the shared state itself, so occupancy is sampled across the
    # whole check-then-act rather than at one statement inside it:
    #   __bool__ -- the sequence's FIRST read of _stamps, at `while self._stamps`
    #   append   -- its final act
    # A thread is "inside" between those two. Measuring anywhere narrower (e.g.
    # at the clock read) proves only that one statement is serialised, and stays
    # silent when the guard covers the clock but not evict-check-append.
    n_threads = MAX_PER_HOUR  # exactly fills the window: every thread reaches
    # the append, so every __bool__ has a matching decrement and no occupancy
    # leaks via the raise path

    class Probe(deque):
        def __init__(self):
            super().__init__()
            self.inside = 0
            self.peak = 0
            self.tally = threading.Lock()

        def __bool__(self):  # entering: first read of the shared state
            with self.tally:
                self.inside += 1
                self.peak = max(self.peak, self.inside)
            time.sleep(0.003)  # hold the section open
            return super().__len__() > 0

        def append(self, stamp):  # leaving: the act
            super().append(stamp)
            with self.tally:
                self.inside -= 1

    probe = Probe()
    # Frozen clock: `now - stamps[0] > 3600` is never true, so the eviction loop
    # body never runs and __bool__ fires exactly once per create().
    w = Writer(clock=lambda: 0.0)
    w._stamps = probe
    admitted = []
    tally = threading.Lock()
    start = threading.Barrier(n_threads)

    def worker(i):
        start.wait()
        try:
            w.create(tmp_path / f"c{i}.md", "ok")
        except WriteLimit:
            return
        with tally:
            admitted.append(i)

    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        pool = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in pool:
            t.start()
        for t in pool:
            t.join()
    finally:
        sys.setswitchinterval(old_interval)

    assert probe.inside == 0, "unbalanced probe accounting invalidates peak"
    assert probe.peak == 1, (
        f"{probe.peak} threads inside the evict-check-append sequence at once"
    )
    assert len(admitted) == MAX_PER_HOUR, (
        f"{len(admitted)} of {n_threads} writes admitted into a "
        f"{MAX_PER_HOUR}-write window"
    )
