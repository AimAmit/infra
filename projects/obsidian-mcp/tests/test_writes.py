import sys
import threading
import time
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
    # The injected clock is read inside that sequence, so it doubles as a probe:
    # it counts how many threads are inside at once and sleeps to hold the
    # section open. Unguarded, threads pile into the section together and peak
    # climbs well above 1; guarded, entry is serialised and peak stays at 1.
    n_threads = 40
    inside = 0
    peak = 0
    probe = threading.Lock()

    def clock():
        nonlocal inside, peak
        with probe:
            inside += 1
            peak = max(peak, inside)
        time.sleep(0.003)  # hold the critical section open
        with probe:
            inside -= 1
        return 0.0  # frozen: nothing ever ages out of the window

    w = Writer(clock=clock)
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

    assert peak == 1, f"{peak} threads inside the rate-limit section at once"
    assert len(admitted) == MAX_PER_HOUR, (
        f"{len(admitted)} of {n_threads} writes admitted into a "
        f"{MAX_PER_HOUR}-write window"
    )
