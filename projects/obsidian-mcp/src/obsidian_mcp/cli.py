"""Phase-1 access path: hermes shells in over SSH and calls this. Same containment as the server."""
import argparse
import json
import sys
from pathlib import Path

from .paths import PathViolation
from .tools import ObsidianTools
from .writes import WriteLimit


def run(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="obsidian-cli")
    ap.add_argument("--root", required=True)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("search").add_argument("query")
    sub.add_parser("read").add_argument("ref")
    sub.add_parser("backlinks").add_argument("ref")
    sub.add_parser("neighbors").add_argument("ref")
    cap = sub.add_parser("capture")
    cap.add_argument("title")
    cap.add_argument("content")
    cap.add_argument("--tags", default="")
    sub.add_parser("log-daily").add_argument("content")
    pro = sub.add_parser("propose")
    pro.add_argument("target")
    pro.add_argument("rationale")
    pro.add_argument("content")
    sub.add_parser("status")
    a = ap.parse_args(argv)
    t = ObsidianTools(Path(a.root))
    try:
        out = {
            "search": lambda: t.obsidian_search(a.query),
            "read": lambda: t.obsidian_read(a.ref),
            "backlinks": lambda: t.obsidian_backlinks(a.ref),
            "neighbors": lambda: t.obsidian_neighbors(a.ref),
            "capture": lambda: {"ref": t.obsidian_capture(
                a.title, a.content, [x for x in a.tags.split(",") if x])},
            "log-daily": lambda: {"ref": t.obsidian_log_daily(a.content)},
            "propose": lambda: {"ref": t.obsidian_propose(a.target, a.rationale, a.content)},
            "status": lambda: t.obsidian_status(),
        }[a.cmd]()
    except (PathViolation, WriteLimit) as e:
        print(json.dumps({"error": str(e)}))
        return 1
    print(json.dumps(out) if not isinstance(out, str) else json.dumps({"content": out}))
    return 0


def main():
    sys.exit(run())
