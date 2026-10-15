"""translate.py — goals to solids, scripts and files; the checker.
Version: 2026.10.15.1

    build(goal)                    -> (solid, seconds per feature)
    script(name, goal)             -> a standalone build123d script that rebuilds the part
    export(solid, path_without_ext, formats)
    iou(a, b)                      -> volumetric IoU of two solids
    load(path)                     -> a solid from STEP / BREP
"""
from __future__ import annotations

import inspect
import math
import re
from pathlib import Path

from . import __version__, ops

build = ops.build_goal


def _call(kind: str, params: dict) -> str:
    args = ", ".join(f"{k}={_lit(v)}" for k, v in params.items())
    return f"b.{kind}({args})"


def _lit(v):
    if isinstance(v, float) and v.is_integer():
        return repr(int(v)) if abs(v) < 1e15 else repr(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_lit(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k!r}: {_lit(x)}" for k, x in v.items()) + "}"
    return repr(v)


def _ops_source() -> str:
    src = inspect.getsource(ops)
    src = re.sub(r'^""".*?"""\n', "", src, count=1, flags=re.S)  # module docstring
    return src.strip() + "\n"


def script(name: str, goal: dict, standalone: bool = True) -> str:
    """A build123d script that rebuilds the goal's part and writes <name>.step. Standalone:
    the builder's code is included (needs only build123d); otherwise it imports this package."""
    feats = goal["features"]
    scale = float(goal.get("scale") or ops.goal_scale(feats))
    head = (f'#!/usr/bin/env python3\n"""{name}: build123d script written by build123d-translator {__version__} '
            f'from its goal ({len(feats)} features).\nRun: python {name}.py  ->  {name}.step\n"""\n')
    lib = ("# ---- builder (build123d_translator.ops) ----\n" + _ops_source() + "\n# ---- the part ----\n"
           if standalone else "from build123d_translator.ops import Builder, single_solid\n\n")
    if not standalone:
        head += "from __future__ import annotations\n"
    body = [f"b = Builder(scale={_lit(scale)})"] + [_call(f["kind"], f.get("params", {})) for f in feats]
    body += ["part = single_solid(b.part)", "", 'if __name__ == "__main__":',
             "    from build123d import export_step", "", f'    export_step(part, "{name}.step")',
             f'    print("{name}.step", round(part.volume, 3), "mm3")']
    return head + lib + "\n".join(body) + "\n"


def export(solid, base: Path, formats=("step",)) -> dict:
    """Write the solid as <base>.<fmt> for each format (step, brep, stl). Returns {fmt: path}."""
    from build123d import export_brep, export_step, export_stl

    out = {}
    base = Path(base)
    for fmt in formats:
        p = base.with_suffix("." + fmt)
        {"step": export_step, "brep": export_brep, "stl": export_stl}[fmt](solid, str(p))
        out[fmt] = str(p)
    return out


def load(path) -> object:
    from build123d import import_brep, import_step

    p = str(path)
    shape = import_brep(p) if p.lower().endswith((".brep", ".brp")) else import_step(p)
    return ops.single_solid(shape)


def iou(a, b) -> float:
    """Volumetric IoU of two solids; 0 for missing or malformed ones (as the FreeCAD checker)."""
    if a is None or b is None:
        return 0.0
    va, vb = a.volume, b.volume
    if va <= 1e-9 or vb <= 1e-9:
        return 0.0
    try:
        inter = (a & b).volume
    except Exception:  # noqa: BLE001 (boolean failure)
        return 0.0
    union = va + vb - inter
    v = inter / union if union > 1e-9 else 0.0
    return 0.0 if (v < 0 or v > 1.001 or math.isnan(v)) else min(v, 1.0)


def describe(solid) -> dict:
    bb = solid.bounding_box()
    return {"valid": bool(solid.is_valid), "volume": round(solid.volume, 3), "area": round(solid.area, 3),
            "bbox": [round(bb.size.X, 3), round(bb.size.Y, 3), round(bb.size.Z, 3)],
            "bbox_min": [round(bb.min.X, 3), round(bb.min.Y, 3), round(bb.min.Z, 3)],
            "faces": len(solid.faces()), "edges": len(solid.edges())}
