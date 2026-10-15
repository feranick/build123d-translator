"""api.py — what other programs and the MCP server call. JSON in, JSON out.
Version: 2026.10.15.1

    goal_format()                         the goal format, for the caller to read
    validate(goals)                       static checks, no geometry
    check(goals)                          builds each goal: buildable or not, volume, bbox
    build(goals, formats, scripts)        builds and writes <name>.step (+ .py script, .stl, .brep)
    compare(a, b)                         IoU of two files (STEP / BREP)
    verify(goal, path)                    IoU of a file built elsewhere against the goal's part
    script(goal, name)                    the build123d script of a goal, as text
    read_file(path)                       a written file as base64 (only under the output folder)

Builds run in worker processes with a time limit per part, so a kernel operation that never
returns cannot block the caller. Environment: B3DT_OUT (default ~/b3d_parts), B3DT_WORKERS (4),
B3DT_TIMEOUT (seconds per part, 120).
"""
from __future__ import annotations

import base64
import json
import multiprocessing as mp
import os
import time
import uuid
from pathlib import Path

from . import __version__
from .goalformat import goal_format as _goal_format
from .goalformat import validate as _validate
from .goalformat import validate_goal


def out_root() -> Path:
    return Path(os.environ.get("B3DT_OUT", "~/b3d_parts")).expanduser()


def _workers() -> int:
    return int(os.environ.get("B3DT_WORKERS", "4"))


def _timeout() -> float:
    return float(os.environ.get("B3DT_TIMEOUT", "120"))


def goal_format() -> dict:
    return {**_goal_format(), "translator": __version__}


def validate(goals: dict) -> dict:
    return _validate(goals)


# ----------------------------------------------------------------------------- workers

def _build_one(job: dict) -> dict:
    """Worker: build one goal; with a folder, write the files. Returns a JSON-able record."""
    from . import translate as T

    name, goal = job["name"], job["goal"]
    t0 = time.perf_counter()
    try:
        solid, times = T.build(goal)
    except Exception as exc:  # noqa: BLE001
        return {"built": False, "why": "build failed", "error": str(exc)[:1000],
                "seconds": round(time.perf_counter() - t0, 3)}
    rec = {"built": True, "seconds": round(time.perf_counter() - t0, 3),
           "feature_ms": [round(1000 * t, 1) for t in times], **T.describe(solid)}
    if not rec["valid"]:
        rec.update(built=False, why="the kernel reports an invalid solid")
    if job.get("folder"):
        base = Path(job["folder"]) / name
        t1 = time.perf_counter()
        rec["files"] = T.export(solid, base, tuple(job.get("formats", ("step",))))
        if job.get("scripts", True):
            p = base.with_suffix(".py")
            p.write_text(T.script(name, goal))
            rec["files"]["script"] = str(p)
        rec["export_seconds"] = round(time.perf_counter() - t1, 3)
    if job.get("compare"):
        try:
            other = T.load(job["compare"])
            rec["iou"] = round(T.iou(solid, other), 6)
            rec["other_volume"] = round(other.volume, 3)
        except Exception as exc:  # noqa: BLE001
            rec["iou"], rec["compare_error"] = 0.0, str(exc)[:500]
    return rec


class _Pool:
    """Worker processes that live across calls (build123d is imported once per worker);
    restarted when a part exceeds its time limit."""

    def __init__(self):
        self.pool = None

    def run(self, jobs: list[dict], workers: int | None = None, timeout: float | None = None) -> list[dict]:
        workers = workers or _workers()
        timeout = timeout or _timeout()
        if self.pool is None:
            self.pool = mp.get_context("spawn").Pool(workers)
        pending = [self.pool.apply_async(_build_one, (j,)) for j in jobs]
        out, unfinished, broken = [], [], False
        for i, res in enumerate(pending):
            try:
                if broken and not res.ready():
                    unfinished.append(i)
                    out.append(None)
                    continue
                out.append(res.get(timeout=timeout))
            except mp.TimeoutError:  # this part is stuck in the kernel
                broken = True
                out.append({"built": False, "why": f"time limit ({timeout:g} s) exceeded"})
            except Exception as exc:  # noqa: BLE001
                out.append({"built": False, "why": "worker failed", "error": str(exc)[:500]})
        if broken:  # replace the pool; run again what had not finished
            self.pool.terminate()
            self.pool = None
            for i, r in zip(unfinished, self.run([jobs[i] for i in unfinished], workers, timeout) if unfinished else []):
                out[i] = r
        return out


_POOL = _Pool()


def _request_dir(prefix: str) -> Path:
    d = out_root() / f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _split(goals: dict) -> tuple[dict, dict]:
    ok, bad = {}, {}
    for n, g in goals.items():
        v = validate_goal(g)
        (ok if v["ok"] else bad)[n] = g if v["ok"] else {"built": False, "why": "invalid goal", "errors": v["errors"]}
    return ok, bad


# ----------------------------------------------------------------------------- calls

def check(goals: dict) -> dict:
    """Builds each goal without writing files: buildable or not, why, volume and bbox."""
    ok, bad = _split(goals)
    names = list(ok)
    res = _POOL.run([{"name": n, "goal": ok[n]} for n in names])
    out = {}
    for n, r in zip(names, res):
        out[n] = {"buildable": r["built"], **{k: v for k, v in r.items() if k != "built"}}
    for n, r in bad.items():
        out[n] = {"buildable": False, **{k: v for k, v in r.items() if k != "built"}}
    return out


def build(goals: dict, formats=("step",), scripts: bool = True, workers: int | None = None) -> dict:
    """Builds the goals into a new request folder under B3DT_OUT: <name>.step (and the other
    formats), <name>.py (the build123d script), goals.json, results.json."""
    folder = _request_dir("build")
    (folder / "goals.json").write_text(json.dumps(goals, indent=1))
    ok, bad = _split(goals)
    names = list(ok)
    t0 = time.perf_counter()
    res = _POOL.run([{"name": n, "goal": ok[n], "folder": str(folder), "formats": list(formats), "scripts": scripts}
                     for n in names], workers)
    parts = dict(zip(names, res)) | bad
    out = {"request": str(folder), "translator": __version__, "seconds": round(time.perf_counter() - t0, 3),
           "parts": parts}
    (folder / "results.json").write_text(json.dumps(out, indent=1))
    return out


def compare(a: str, b: str) -> dict:
    from . import translate as T

    sa, sb = T.load(a), T.load(b)
    return {"iou": round(T.iou(sa, sb), 6), "a": T.describe(sa), "b": T.describe(sb)}


def verify(goal: dict, path: str) -> dict:
    """IoU of a part built elsewhere (FreeCAD, another tool, an LLM's script) against the goal."""
    v = validate_goal(goal)
    if not v["ok"]:
        return {"iou": 0.0, "why": "invalid goal", "errors": v["errors"]}
    r = _POOL.run([{"name": "goal", "goal": goal, "compare": path}])[0]
    return {k: r.get(k) for k in ("iou", "volume", "other_volume", "built", "why", "error", "compare_error") if k in r}


def script(goal: dict, name: str = "part", standalone: bool = True) -> dict:
    from . import translate as T

    v = validate_goal(goal)
    if not v["ok"]:
        return {"error": "invalid goal", "errors": v["errors"]}
    return {"name": name, "script": T.script(name, goal, standalone)}


def read_file(path: str, max_mb: float = 20) -> dict:
    p = Path(path).expanduser().resolve()
    root = out_root().resolve()
    if root not in p.parents:
        return {"error": f"only files under {root} can be read"}
    if not p.is_file():
        return {"error": f"no file {p}"}
    if p.stat().st_size > max_mb * 1e6:
        return {"error": f"{p.stat().st_size / 1e6:.1f} MB is more than max_mb"}
    return {"name": p.name, "bytes": p.stat().st_size, "base64": base64.b64encode(p.read_bytes()).decode()}
