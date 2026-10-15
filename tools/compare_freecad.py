#!/usr/bin/env python3
"""compare_freecad.py — the translator against the FreeCAD reference builder, goal by goal:
IoU of the two parts, and the time each took.
Version: 2026.10.15.1

    python tools/compare_freecad.py --goals GOALS.json --freecad DIR [--log teacher.log]
                                    [--workers 8] [--limit N] [--out compare.json]

DIR holds the FreeCAD parts as <name>.step (e.g. a DeepCAD test folder's `teacher/`, written
with goals_test_verified.json; or tools/freecad_reference.sh for any goal file). --log is the
FreeCAD build log (`taiga_build_part.py` output) for its time per part. Goals without a FreeCAD
part are skipped. A goal matches at IoU >= 0.999 (the checker's threshold).
"""
from __future__ import annotations

import argparse
import json
import re
import statistics as st
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build123d_translator import api  # noqa: E402

ROW = re.compile(r"^(\S+)\s+(SUCCESS|FAIL)\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*$")


def bucket(n: int) -> str:
    return "1-5" if n <= 5 else "6-10" if n <= 10 else "11-20" if n <= 20 else "21+"


def freecad_times(log: Path | None) -> dict[str, float]:
    if not log or not log.is_file():
        return {}
    out = {}
    for line in log.read_text(errors="replace").splitlines():
        m = ROW.match(line.strip())
        if m and m.group(1) not in ("all", "part"):
            out[m.group(1)] = float(m.group(8))
    return out


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else float("nan")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--goals", required=True)
    ap.add_argument("--freecad", required=True, help="folder with the FreeCAD parts, <name>.step")
    ap.add_argument("--log", help="FreeCAD build log, for its times")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--chunk", type=int, default=200)
    ap.add_argument("--out", default="compare_freecad.json")
    a = ap.parse_args()

    goals = json.loads(Path(a.goals).read_text())
    fdir = Path(a.freecad)
    names = [n for n in goals if (fdir / f"{n}.step").is_file()]
    if a.limit:
        names = names[:a.limit]
    print(f"{len(names)} goals with a FreeCAD part (of {len(goals)})", flush=True)
    fct = freecad_times(Path(a.log) if a.log else None)

    res = {}
    t0 = time.perf_counter()
    for i in range(0, len(names), a.chunk):
        part = names[i:i + a.chunk]
        jobs = [{"name": n, "goal": goals[n], "compare": str(fdir / f"{n}.step")} for n in part]
        for n, r in zip(part, api._POOL.run(jobs, a.workers)):
            r["features"] = len(goals[n]["features"])
            if n in fct:
                r["freecad_seconds"] = fct[n]
            res[n] = r
        done = len(res)
        ok = sum(1 for r in res.values() if r.get("iou", 0) >= 0.999)
        print(f"  {done}/{len(names)}  match {ok}/{done}  {time.perf_counter() - t0:.0f} s", flush=True)

    Path(a.out).write_text(json.dumps(res, indent=1))
    print(f"\nper goal: {a.out}\n")
    print(f"{'features':>9} {'goals':>6} {'match':>7} {'IoU<0.999':>9} {'failed':>7} {'b3d ms med':>10} {'p90':>7}"
          f" {'FreeCAD s med':>13} {'p90':>7}")
    for b in ("1-5", "6-10", "11-20", "21+", "all"):
        rs = [r for r in res.values() if b == "all" or bucket(r["features"]) == b]
        if not rs:
            continue
        m = sum(1 for r in rs if r.get("iou", 0) >= 0.999)
        failed = sum(1 for r in rs if not r.get("built"))
        bt = [1000 * r["seconds"] for r in rs if r.get("built") and "seconds" in r]
        ft = [r["freecad_seconds"] for r in rs if "freecad_seconds" in r]
        fc = f"{st.median(ft):13.2f} {pct(ft, 0.9):7.2f}" if ft else f"{'-':>13} {'-':>7}"
        print(f"{b:>9} {len(rs):6d} {m / len(rs):7.3f} {len(rs) - m - failed:9d} {failed:7d}"
              f" {st.median(bt) if bt else float('nan'):10.1f} {pct(bt, 0.9):7.1f} {fc}")
    worst = sorted((r.get("iou", 0), n) for n, r in res.items() if r.get("iou", 0) < 0.999)[:15]
    if worst:
        print("\nmismatches (lowest IoU first):")
        for v, n in worst:
            r = res[n]
            kinds = sorted({f["kind"] for f in goals[n]["features"]})
            print(f"  {n}: IoU {v:.4f}  {r.get('why', '')} {r.get('error', '')[:120]}  kinds {','.join(kinds)}")


if __name__ == "__main__":
    main()
