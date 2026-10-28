"""api.py — what other programs and the MCP server call. JSON in, JSON out.
Version: 2026.10.28.1

    goal_format()                         the goal format, for the caller to read
    validate(goals)                       static checks, no geometry
    check(goals)                          builds each goal: buildable or not, volume, bbox
    build(goals, formats, scripts)        builds and writes <name>.step (+ .py script, .stl, .brep)
    compare(a, b)                         IoU of two files (STEP / BREP)
    verify(goal, path)                    IoU of a file built elsewhere against the goal's part
    script(goal, name)                    the build123d script of a goal, as text
    read_file(path)                       a written file as base64 (only under the output folder)
    render_images(request, name, images)  new pictures of a built part (the goal is rebuilt; no files change)
    in_worker(what, fn_name, kwargs, wait) slow work of a tool call in a worker process, within wait
    image_path(path)                      a PNG written by a build or render_images (only under the output folder)
    tool_status()                         versions and the renderers

Builds run in worker processes with a time limit per part, so a kernel operation that never
returns cannot block the caller. Environment: B3DT_OUT (default ~/b3d_parts), B3DT_WORKERS (4),
B3DT_TIMEOUT (seconds per part, 120; plus B3DT_RENDER_EXTRA, 300, for parts with pictures), B3DT_RENDER
(matplotlib: no VTK), B3DT_RENDER_TIMEOUT (900 s, the VTK child), B3DT_AUX_WORKERS (2: render_images workers).
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
from .goalformat import resolve
from .goalformat import validate as _validate
from .goalformat import validate_goal


def out_root() -> Path:
    return Path(os.environ.get("B3DT_OUT", "~/b3d_parts")).expanduser()


def _workers() -> int:
    return int(os.environ.get("B3DT_WORKERS", "4"))


def _timeout() -> float:
    return float(os.environ.get("B3DT_TIMEOUT", "120"))


def goal_format() -> dict:
    return {**_goal_format(), "translator": __version__, "status": tool_status()}


def tool_status() -> dict:
    try:
        from importlib.metadata import version
        b3d = version("build123d")
    except Exception:  # noqa: BLE001
        b3d = None
    from .render_vtk import status as render_status
    return {"translator": __version__, "build123d": b3d, "render": render_status()}


def validate(goals: dict) -> dict:
    return _validate(goals)


# ----------------------------------------------------------------------------- workers

def _build_one(job: dict) -> dict:
    """Worker: build one goal; with a folder, write the files. Returns a JSON-able record."""
    from . import translate as T

    name, goal = job["name"], job["goal"]
    images = goal.get("images", job.get("images")) if job.get("folder") else None
    t0 = time.perf_counter()
    try:
        if images:   # the part after every feature too: which feature made each face
            from . import render as R
            solid, times, parts = R.build(goal)
        else:
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
        if images:
            t2 = time.perf_counter()
            try:
                rec["images"], w = R.render_part(name, goal, Path(job["folder"]) / "images" / name, images, (solid, times, parts))
                if w:
                    rec["warnings"] = rec.get("warnings", []) + w
            except Exception as exc:  # noqa: BLE001 (pictures must not fail the build)
                rec["warnings"] = rec.get("warnings", []) + [f"images: {type(exc).__name__}: {exc}"[:300]]
            rec["render_seconds"] = round(time.perf_counter() - t2, 3)
    if job.get("compare"):
        try:
            other = T.load(job["compare"])
            rec["iou"] = round(T.iou(solid, other), 6)
            rec["other_volume"] = round(other.volume, 3)
            od = T.describe(other)
            rec["other_bbox"], rec["other_bbox_min"], rec["other_valid"] = od["bbox"], od["bbox_min"], od["valid"]
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
        if any(j.get("folder") and j["goal"].get("images", j.get("images")) for j in jobs):
            timeout += float(os.environ.get("B3DT_RENDER_EXTRA", "300"))   # pictures take longer than the build
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
    """(valid goals, with the goals they combine by name inlined; {name: why} of the invalid ones)."""
    ok, bad = {}, {}
    checked = _validate(goals)
    full = resolve(goals)
    for n, g in goals.items():
        v = checked[n]
        (ok if v["ok"] else bad)[n] = full[n] if v["ok"] else {"built": False, "why": "invalid goal", "errors": v["errors"]}
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


def build(goals: dict, formats=("step",), scripts: bool = True, workers: int | None = None, images=None) -> dict:
    """Builds the goals into a new request folder under B3DT_OUT: <name>.step (and the other
    formats), <name>.py (the build123d script), goals.json, results.json; with pictures (a goal's "images", or
    images for the goals without their own), images/<name>/<view>.png."""
    folder = _request_dir("build")
    (folder / "goals.json").write_text(json.dumps(goals, indent=1))
    ok, bad = _split(goals)
    names = list(ok)
    t0 = time.perf_counter()
    res = _POOL.run([{"name": n, "goal": ok[n], "folder": str(folder), "formats": list(formats), "scripts": scripts,
                      "images": images} for n in names], workers)
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


# ----------------------------------------------------------------------------- pictures
# Slow work of a tool call (rendering) in worker processes, as cfd-translator's in_worker: a call returns within
# its wait (at most 50 s) and a repeated call with the same arguments picks the result up.
_AUX: dict = {"pool": None, "pending": {}}


def _aux_pool():
    if _AUX["pool"] is None:
        _AUX["pool"] = mp.get_context("spawn").Pool(int(os.environ.get("B3DT_AUX_WORKERS", "2") or 2), maxtasksperchild=20)
    return _AUX["pool"]


def _aux_run(fn_name: str, kwargs: dict, out: str | None = None):
    if out:   # the worker may outlive a change of B3DT_OUT
        os.environ["B3DT_OUT"] = out
    return globals()[fn_name](**kwargs)


def in_worker(what: str, fn_name: str, kwargs: dict, wait: float) -> dict:
    """fn_name(**kwargs) in a worker; waits at most `wait` s (<= 50). Unfinished: {state: running, ...}; the same
    call again waits for the same work."""
    key = f"{fn_name}:{json.dumps(kwargs, sort_keys=True, default=str)}"
    if key not in _AUX["pending"]:
        _AUX["pending"][key] = (_aux_pool().apply_async(_aux_run, (fn_name, kwargs, str(out_root()))), time.time())
    h, t0 = _AUX["pending"][key]
    h.wait(max(0.0, min(float(wait), 50.0)))
    if not h.ready():
        return {"state": "running", "what": what, "elapsed_s": round(time.time() - t0, 1),
                "next": f"still {what}: call {fn_name} again with the same arguments (the work goes on meanwhile)"}
    _AUX["pending"].pop(key, None)
    try:
        return h.get()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def render_images(request: str, name: str, images=True) -> dict:
    """New pictures of a part built by build() (its request folder and goal name): the goal is rebuilt from the
    request's goals.json (the build is deterministic) and drawn into <request>/images/<name>/."""
    from .render import build as rbuild
    from .render import image_errors, render_part

    d = Path(request).expanduser().resolve()
    root = out_root().resolve()
    if root not in d.parents:
        return {"error": f"only requests under {root}"}
    try:
        goals = json.loads((d / "goals.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {"error": f"no goals.json in {d} (a build_parts request folder)"}
    if name not in goals:
        return {"error": f"no goal {name!r} in {d}; goals: {', '.join(goals)}"}
    goal = resolve(goals)[name]
    errs = image_errors(images, goal)
    if errs:
        return {"error": "invalid images", "errors": errs}
    try:
        built = rbuild(goal)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"the goal does not build: {exc}"[:1000]}
    out, warns = render_part(name, goal, d / "images" / name, images, built)
    return {"images": out, "warnings": warns}


def image_path(path: str) -> Path:
    """A PNG written by a build or by render_images (only under the output folder)."""
    p = Path(path).expanduser().resolve()
    root = out_root().resolve()
    if root not in p.parents or p.suffix.lower() != ".png" or not p.is_file():
        raise ValueError(f"no image {p} under {root}")
    return p


def _close_pools() -> None:
    for pool in (_POOL.pool, _AUX["pool"]):
        if pool is not None:
            pool.terminate()
    _POOL.pool = _AUX["pool"] = None


import atexit  # noqa: E402

atexit.register(_close_pools)
