"""Point d'entrée : `python3 -m snapsort` ou `python3 snapsort.py`."""

from __future__ import annotations

import argparse
import os
import sys

from .server import serve, state
from .session import Session


def main(argv=None) -> int:
    if sys.version_info < (3, 9):
        print("SnapSort nécessite Python 3.9 ou plus récent.", file=sys.stderr)
        return 1

    parser = argparse.ArgumentParser(
        prog="snapsort",
        description="Trie tes souvenirs Snapchat à la vitesse d'un swipe.",
    )
    parser.add_argument("--source", action="append", metavar="CHEMIN",
                        help="zip ou dossier source (répétable) — sinon on choisit dans l'interface")
    parser.add_argument("--dest", metavar="CHEMIN", help="dossier de destination")
    parser.add_argument("--port", type=int, help="port d'écoute (défaut : 8765)")
    parser.add_argument("--no-browser", action="store_true", help="ne pas ouvrir le navigateur")
    args = parser.parse_args(argv)

    if args.dest:
        dest = os.path.abspath(os.path.expanduser(args.dest))
        session = Session(dest)
        if Session.exists(dest) and not args.source:
            session.load()
            state["session"] = session
            state["autostart"] = True
            print(f"  Session reprise : {dest}")
        elif args.source:
            sources = [os.path.abspath(os.path.expanduser(s)) for s in args.source]
            missing = [s for s in sources if not os.path.exists(s)]
            if missing:
                print(f"  Source introuvable : {missing[0]}", file=sys.stderr)
                return 1
            session.start(sources, {})
            state["session"] = session
            state["autostart"] = True
            print(f"  Scan lancé : {len(sources)} source(s) → {dest}")
    elif args.source:
        print("  --source nécessite aussi --dest.", file=sys.stderr)
        return 1

    serve(port=args.port, open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
