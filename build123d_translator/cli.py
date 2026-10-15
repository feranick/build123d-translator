"""cli.py — command line: JSON in, JSON out.
Version: 2026.10.15.1

    b3dt format                                   the goal format
    b3dt validate goals.json                      static checks
    b3dt check goals.json                         build each goal, report volume / bbox / errors
    b3dt build goals.json [--stl] [--brep] [--no-script] [--workers N]
    b3dt script goals.json NAME [--import]        print the build123d script of one goal
    b3dt compare a.step b.step                    IoU of two parts
    b3dt verify goals.json NAME part.step         IoU of a part built elsewhere against the goal

`-` reads the goals from stdin. Output folder: B3DT_OUT (default ~/b3d_parts).
"""
from __future__ import annotations

import argparse
import json
import sys

from . import api


def _goals(path: str) -> dict:
    return json.load(sys.stdin if path == "-" else open(path))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="b3dt", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("format")
    for c in ("validate", "check"):
        sub.add_parser(c).add_argument("goals")
    b = sub.add_parser("build")
    b.add_argument("goals")
    b.add_argument("--stl", action="store_true")
    b.add_argument("--brep", action="store_true")
    b.add_argument("--no-script", action="store_true")
    b.add_argument("--workers", type=int)
    s = sub.add_parser("script")
    s.add_argument("goals")
    s.add_argument("name")
    s.add_argument("--import", dest="imp", action="store_true", help="import the builder instead of including it")
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    v = sub.add_parser("verify")
    v.add_argument("goals")
    v.add_argument("name")
    v.add_argument("part")
    a = ap.parse_args(argv)

    if a.cmd == "format":
        r = api.goal_format()
    elif a.cmd == "validate":
        r = api.validate(_goals(a.goals))
    elif a.cmd == "check":
        r = api.check(_goals(a.goals))
    elif a.cmd == "build":
        fmts = ("step",) + (("stl",) if a.stl else ()) + (("brep",) if a.brep else ())
        r = api.build(_goals(a.goals), formats=fmts, scripts=not a.no_script, workers=a.workers)
    elif a.cmd == "script":
        r = api.script(_goals(a.goals)[a.name], a.name, standalone=not a.imp)
        if "script" in r:
            sys.stdout.write(r["script"])
            return
    elif a.cmd == "compare":
        r = api.compare(a.a, a.b)
    else:
        r = api.verify(_goals(a.goals)[a.name], a.part)
    print(json.dumps(r, indent=1))


if __name__ == "__main__":
    main()
