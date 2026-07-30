"""Entry point: `python3 -m sorter` or `python3 sorter.py`."""

from __future__ import annotations

import argparse
import os
import sys

from .server import serve, state
from .session import Session


def main(argv=None) -> int:
    if sys.version_info < (3, 9):
        print("Snapchat Memories Sorter needs Python 3.9 or newer.", file=sys.stderr)
        return 1

    parser = argparse.ArgumentParser(
        prog="sorter",
        description="Sort your Snapchat memories at the speed of a swipe.",
    )
    parser.add_argument("--source", action="append", metavar="PATH",
                        help="source zip or folder (repeatable) — otherwise pick one in the app")
    parser.add_argument("--dest", metavar="PATH", help="destination folder")
    parser.add_argument("--port", type=int, help="port to listen on (default: 8765)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument("--host", default="127.0.0.1", metavar="ADDRESS",
                        help="address to bind to; stay on 127.0.0.1 unless you are "
                             "inside a container (default: 127.0.0.1)")
    args = parser.parse_args(argv)

    if args.dest:
        dest = os.path.abspath(os.path.expanduser(args.dest))
        session = Session(dest)
        if Session.exists(dest) and not args.source:
            session.load()
            state["session"] = session
            state["autostart"] = True
            print(f"  Session resumed: {dest}")
        elif args.source:
            sources = [os.path.abspath(os.path.expanduser(s)) for s in args.source]
            missing = [s for s in sources if not os.path.exists(s)]
            if missing:
                print(f"  Source not found: {missing[0]}", file=sys.stderr)
                return 1
            session.start(sources, {})
            state["session"] = session
            state["autostart"] = True
            print(f"  Scan started: {len(sources)} source(s) → {dest}")
    elif args.source:
        print("  --source also needs --dest.", file=sys.stderr)
        return 1

    serve(port=args.port, open_browser=not args.no_browser, host=args.host)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
