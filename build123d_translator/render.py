"""render.py — pictures of a built part: shaded views, faces coloured by the feature that made them (to check
by eye that each feature went where it should), highlighted features, section cuts.
Version: 2026.10.28.1

    build(goal) -> (solid, seconds per feature, parts after each feature)
    scene(solid, parts, kinds, spec) -> plain arrays to draw (triangles, colours, edges, legend)
    render_part(name, goal, out_dir, images, built=None) -> ({view: {path, what, ...}}, warnings)
    default_images() / image_errors(images, goal)

As cfd-translator and fea-translator 2026.10.28.1: Matplotlib (Agg) draws by default and is the fallback;
VTK through PyVista (render_vtk, in a child process) draws "quality": "high" views (lit, smooth, depth-tested
edges). There is no solve here, so no state to save: the goal (goals.json of the request) is rebuilt to draw other
views later (render_images).

Which feature made a face: the builder keeps the part after every feature (Builder(keep_parts=True)). A point on
the final face (the centroid of its largest triangle, projected onto the face's surface) is classified against
each of those parts, last to first; the face belongs to the earliest feature from which the point stays on the
part's boundary up to the end (OpenCascade's point classifier, ON). A hole's wall is the hole's; the top face it
went through is still the base's; a fillet's faces are the fillet's; a pattern's copies are the pattern's.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np

VIEWS = {"iso": [30, -60], "iso_back": [30, 120], "top": [90, -90], "bottom": [-90, -90], "front": [0, -90],
         "back": [0, 90], "right": [0, 0], "left": [0, 180]}
SPEC_KEYS = ("name", "color", "view", "highlight", "edges", "quality", "slice")
AXES = {"x": 0, "y": 1, "z": 2}
PALETTE = ("#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd", "#8c564b", "#e377c2", "#17becf", "#bcbd22",
           "#393b79", "#ad494a", "#637939")
PART = "#9fb4c8"
GREY = "#dcdcdc"
SECTION = "#e07b7b"
SIZE_IN, DPI = (9, 7), 130


def feature_color(k: int) -> str:
    """Colour of feature k (1-based); repeats after 12."""
    return PALETTE[(int(k) - 1) % len(PALETTE)]


def default_images() -> list[dict]:
    """A shaded view, and the faces coloured by feature from the front and from the back (hidden sides)."""
    return [{"name": "iso", "view": "iso"}, {"name": "features", "color": "feature", "view": "iso"},
            {"name": "features_back", "color": "feature", "view": "iso_back"}]


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def image_errors(images, goal: dict) -> list[str]:
    """Errors of a goal's "images": true, or a list of views."""
    if images is True or images is False:
        return []
    if not isinstance(images, list):
        return ["images: true (the default views) or a list of {name, color, view, highlight, edges, quality, slice}"]
    n = len(goal.get("features") or []) if isinstance(goal, dict) else 0
    errs, names = [], []
    for i, sp in enumerate(images):
        w = f"images[{i}]"
        if not isinstance(sp, dict):
            errs.append(f"{w}: an object {{name, color, view, ...}}")
            continue
        for k in sp:
            if k not in SPEC_KEYS:
                errs.append(f"{w}: unknown key {k!r} (allowed: {', '.join(SPEC_KEYS)})")
        if "name" in sp:
            if not isinstance(sp["name"], str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", sp["name"]):
                errs.append(f"{w}.name: a word (letters, digits, _ . -)")
            elif sp["name"] in names:
                errs.append(f"{w}.name: {sp['name']!r} twice")
            names.append(sp.get("name"))
        if sp.get("color", "part") not in ("part", "feature"):
            errs.append(f"{w}.color: part (one colour) or feature (each face by the feature that made it)")
        v = sp.get("view", "iso")
        if not ((isinstance(v, str) and v in VIEWS) or (isinstance(v, (list, tuple)) and len(v) == 2 and all(_num(x) for x in v))):
            errs.append(f"{w}.view: {' | '.join(VIEWS)} or [elevation, azimuth] in degrees")
        if "highlight" in sp:
            h = sp["highlight"]
            if not isinstance(h, list) or not h or not all(_num(x) and int(x) == x and 1 <= x <= n for x in h):
                errs.append(f"{w}.highlight: a list of feature numbers from 1 to {n}")
        if "edges" in sp and not isinstance(sp["edges"], bool):
            errs.append(f"{w}.edges: true (default) or false")
        if "quality" in sp and sp["quality"] not in ("high", "basic"):
            errs.append(f"{w}.quality: high (VTK: lit, smooth) or basic (Matplotlib)")
        if "slice" in sp:
            s = sp["slice"]
            if not isinstance(s, dict) or s.get("axis") not in AXES or not _num(s.get("at")) or set(s) - {"axis", "at"}:
                errs.append(f"{w}.slice: {{axis: x | y | z, at: mm}} (keeps the part on the low side of the plane)")
    return errs


# ---- the part and which feature made each face -------------------------------------------------
def build(goal: dict):
    """(solid, seconds per feature, the part after every feature)."""
    from . import ops
    feats = goal["features"]
    b = ops.Builder(scale=float(goal.get("scale") or ops.goal_scale(feats)), keep_parts=True)
    solid, times = ops.build_goal(goal, b)
    return solid, times, list(b.parts)


def _classifier_on(shape, p, tol: float) -> bool:
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_ON
    for s in (shape.solids() if hasattr(shape, "solids") else [shape]) or [shape]:
        c = BRepClass3d_SolidClassifier(s.wrapped, gp_Pnt(*map(float, p)), tol)
        if c.State() == TopAbs_ON:
            return True
    return False


def _inside(solid, p, tol: float) -> bool:
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_IN
    return BRepClass3d_SolidClassifier(solid.wrapped, gp_Pnt(*map(float, p)), tol).State() == TopAbs_IN


def _on_face_point(face, tri_pts: np.ndarray) -> np.ndarray:
    """The centroid of the face's largest triangle, projected onto the face's surface."""
    a = np.linalg.norm(np.cross(tri_pts[:, 1] - tri_pts[:, 0], tri_pts[:, 2] - tri_pts[:, 0]), axis=1)
    c = tri_pts[int(np.argmax(a))].mean(axis=0)
    try:
        from OCP.BRep import BRep_Tool
        from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
        from OCP.gp import gp_Pnt
        pr = GeomAPI_ProjectPointOnSurf(gp_Pnt(*map(float, c)), BRep_Tool.Surface_s(face.wrapped))
        if pr.NbPoints():
            q = pr.NearestPoint()
            return np.array([q.X(), q.Y(), q.Z()])
    except Exception:  # noqa: BLE001
        pass
    return c


def face_owners(points: list, parts: list, tol: float) -> list[int]:
    """Feature number (1-based) that made each face, from a point on it: the earliest feature from which the point
    is on the part's boundary (ON) through to the last part."""
    n = len(parts)
    out = []
    for p in points:
        k = n
        while k >= 1 and _classifier_on(parts[k - 1], p, tol):
            k -= 1
        out.append(min(k + 1, n))
    return out


def _tessellate(solid, size: float):
    """Per face: (points (m, 3), triangles (t, 3)) at a tolerance of 1/400 of the part's size."""
    out = []
    for f in solid.faces():
        try:
            v, t = f.tessellate(max(size / 400.0, 1e-4), 0.3)
        except Exception:  # noqa: BLE001
            continue
        if not t:
            continue
        out.append((f, np.array([[q.X, q.Y, q.Z] for q in v], float), np.array(t, np.int64)))
    return out


def _edge_lines(solid, size: float) -> list[np.ndarray]:
    lines = []
    for e in solid.edges():
        try:
            L = float(e.length)
            k = 2 if e.geom_type.name == "LINE" else max(4, min(64, int(48 * L / max(size, 1e-9)) + 4))
            lines.append(np.array([[q.X, q.Y, q.Z] for q in (e.position_at(t) for t in np.linspace(0, 1, k))]))
        except Exception:  # noqa: BLE001
            continue
    return lines


def _cut(solid, axis: int, at: float):
    """The part on the low side of {x_axis = at}."""
    from build123d import Location, Solid

    from .ops import boolean, single_solid
    bb = solid.bounding_box()
    L = 4 * bb.diagonal + 10
    lo = [-L, -L, -L]
    lo[axis] = at - 2 * L
    size = [2 * L, 2 * L, 2 * L]
    size[axis] = 2 * L
    box = Solid.make_box(*size).moved(Location(tuple(lo)))
    res = boolean(solid, box, "common")
    if not res.solids():
        raise ValueError("nothing of the part on the low side of the plane")
    return res.solids()[0] if len(res.solids()) == 1 else res


SLICE_VIEWS = {"x": [25, 30], "y": [25, 60], "z": [60, -60]}   # from the cut's side: the section face shows


def view_angles(spec: dict) -> list[float]:
    v = spec.get("view")
    if v is None:
        return list(SLICE_VIEWS[spec["slice"]["axis"]]) if spec.get("slice") else list(VIEWS["iso"])
    return list(VIEWS[v]) if isinstance(v, str) else [float(v[0]), float(v[1])]


def scene(solid, parts: list, kinds: list[str], spec: dict) -> dict:
    """Arrays to draw: points, triangles with their face, a colour per face, edge polylines, the legend."""
    import matplotlib.colors as mcolors
    bb = solid.bounding_box()
    size = float(bb.diagonal)
    tol = max(1e-5 * size, 1e-6)
    sl = spec.get("slice")
    shown = solid
    if sl:
        shown = _cut(solid, AXES[sl["axis"]], float(sl["at"]))
    faces = _tessellate(shown, size)
    pts, tris, tri_face, samples, on_cut = [], [], [], [], []
    off = 0
    for i, (f, v, t) in enumerate(faces):
        pts.append(v)
        tris.append(t + off)
        tri_face.append(np.full(len(t), i))
        off += len(v)
        samples.append(_on_face_point(f, v[t]))
        cut = False
        if sl:
            a = AXES[sl["axis"]]
            cut = bool(np.all(np.abs(v[:, a] - float(sl["at"])) <= max(tol, size / 400.0)))
        on_cut.append(cut)
    color = spec.get("color", "part")
    hl = set(int(x) for x in spec.get("highlight", []) or [])
    need_owner = color == "feature" or hl
    owners = face_owners([s for s, c in zip(samples, on_cut) if not c], parts, tol) if need_owner and parts else []
    it = iter(owners)
    own = [None if c else (next(it) if owners else None) for c in on_cut]
    rgb, legend_k = [], set()
    for o, c in zip(own, on_cut):
        if c:
            rgb.append(mcolors.to_rgb(SECTION))
        elif hl and o not in hl:
            rgb.append(mcolors.to_rgb(GREY))
        elif color == "feature" or (hl and o in hl):
            rgb.append(mcolors.to_rgb(feature_color(o)))
            legend_k.add(o)
        else:
            rgb.append(mcolors.to_rgb(PART))
    legend = [(f"{k} {kinds[k - 1]}", feature_color(k)) for k in sorted(legend_k)]
    if any(on_cut):
        legend.append((f"section {sl['axis']} = {float(sl['at']):g} mm", SECTION))
    counts = {}
    for o in own:
        if o is not None:
            counts[o] = counts.get(o, 0) + 1
    return {"points": np.concatenate(pts) if pts else np.zeros((0, 3)), "tris": np.concatenate(tris) if tris else np.zeros((0, 3), int),
            "tri_face": np.concatenate(tri_face) if tri_face else np.zeros(0, int), "face_rgb": np.array(rgb, float).reshape(-1, 3),
            "face_owner": np.array([o if o is not None else 0 for o in own], int),
            "edges": _edge_lines(shown, size) if spec.get("edges", True) else [], "legend": legend, "size": size,
            "solid": shown, "faces_by_feature": {str(k): v for k, v in sorted(counts.items())}}


# ---- Matplotlib ----------------------------------------------------------------------------------
def _dir(view) -> np.ndarray:
    e, a = np.radians(view[0]), np.radians(view[1])
    return np.array([np.cos(e) * np.cos(a), np.cos(e) * np.sin(a), np.sin(e)])


def _visible_segments(sc: dict, view, max_segments: int = 4000) -> list[np.ndarray]:
    """Edge segments not hidden by the part (Matplotlib cannot hide lines): a segment is hidden when the line of
    sight from its midpoint towards the viewer meets the part's surface again (OpenCascade's ray-shape
    intersector, exact), beyond a small step off the edge itself."""
    from OCP.gp import gp_Dir, gp_Lin, gp_Pnt
    from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
    d = _dir(view)
    solid, size = sc["solid"], sc["size"]
    eps = 1e-4 * size
    hit = IntCurvesFace_ShapeIntersector()
    hit.Load(solid.wrapped, 1e-6 * size)
    segs = [ln[i:i + 2] for ln in sc["edges"] for i in range(len(ln) - 1)]
    if len(segs) > max_segments:
        segs = segs[:: int(math.ceil(len(segs) / max_segments))]
    out = []
    for s in segs:
        m = s.mean(axis=0)
        hit.Perform(gp_Lin(gp_Pnt(*map(float, m)), gp_Dir(*map(float, d))), eps, 1e9)
        if not (hit.IsDone() and hit.NbPnt() > 0):
            out.append(s)
    return out


def _split_long(tri: np.ndarray, face: np.ndarray, longest: float, rounds: int = 4) -> tuple[np.ndarray, np.ndarray]:
    """Triangles with an edge longer than `longest` cut into four (midpoints), a few times: Matplotlib sorts whole
    polygons by depth, and the long thin fan triangles of flat faces sort wrongly."""
    for _ in range(rounds):
        e = np.max(np.linalg.norm(tri - np.roll(tri, 1, axis=1), axis=2), axis=1)
        big = e > longest
        if not big.any() or len(tri) > 25000:
            break
        a, b, c = tri[big, 0], tri[big, 1], tri[big, 2]
        ab, bc, ca = (a + b) / 2, (b + c) / 2, (c + a) / 2
        new = np.concatenate([np.stack(x, axis=1) for x in ((a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca))])
        tri = np.concatenate([tri[~big], new])
        face = np.concatenate([face[~big], np.tile(face[big], 4)])
    return tri, face


def draw_matplotlib(sc: dict, spec: dict, title: str, path: Path) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    from mpl_toolkits.mplot3d.art3d import Line3DCollection, Poly3DCollection

    view = view_angles(spec)
    P, T = sc["points"], sc["tris"]
    tri, tface = _split_long(P[T], sc["tri_face"], sc["size"] / 12.0)
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1), 1e-300)[:, None]
    shade = 0.5 + 0.5 * np.abs(n @ _dir(view))
    fc = sc["face_rgb"][tface] * shade[:, None]
    fig = plt.figure(figsize=SIZE_IN, dpi=DPI)
    fig.patch.set_facecolor("white")
    ax = fig.add_subplot(projection="3d")
    ax.computed_zorder = False
    fc = np.clip(fc, 0, 1)   # outlined in their own colour: no hairline seams between the triangles
    ax.add_collection3d(Poly3DCollection(tri, facecolors=fc, edgecolors=fc, linewidths=0.3, zorder=1))
    segs = _visible_segments(sc, view) if sc["edges"] else []
    if segs:
        ax.add_collection3d(Line3DCollection(segs, colors="black", linewidths=0.6, zorder=2))
    lo, hi = P.min(axis=0), P.max(axis=0)
    pad = 0.02 * float(np.max(hi - lo) or 1.0)
    ax.set_xlim(lo[0] - pad, hi[0] + pad)
    ax.set_ylim(lo[1] - pad, hi[1] + pad)
    ax.set_zlim(lo[2] - pad, hi[2] + pad)
    ax.set_box_aspect(np.maximum(hi - lo, 1e-3 * max(float(np.max(hi - lo)), 1e-9)))
    ax.view_init(elev=view[0], azim=view[1])
    ax.set_xlabel("x (mm)")
    ax.set_ylabel("y (mm)")
    ax.set_zlabel("z (mm)")
    if sc["legend"]:
        ax.legend(handles=[Patch(color=c, label=lab) for lab, c in sc["legend"]], loc="upper left",
                  bbox_to_anchor=(0.0, 1.0), fontsize=8, framealpha=0.9)
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(path, facecolor="white")
    plt.close(fig)
    return {"renderer": "matplotlib", "triangles": int(len(tri)), "edge_segments": len(segs)}


def title_of(name: str, spec: dict) -> str:
    v = spec.get("view") or ("iso" if not spec.get("slice") else view_angles(spec))
    s = f"{name}: {v if isinstance(v, str) else 'elev %g, azim %g' % tuple(v)} view"
    if spec.get("color") == "feature":
        s += ", faces by the feature that made them"
    if spec.get("highlight"):
        s += ", features " + ", ".join(str(k) for k in spec["highlight"]) + " highlighted"
    if spec.get("slice"):
        s += f", cut at {spec['slice']['axis']} = {float(spec['slice']['at']):g} mm"
    return s


def render_part(name: str, goal: dict, out_dir: Path, images=True, built=None) -> tuple[dict, list]:
    """PNGs <out_dir>/<view>.png of a goal's part. built: (solid, times, parts) from build() (else built here).
    quality 'high' views go to VTK (render_vtk, a child process); one VTK cannot draw falls back to Matplotlib."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    solid, _, parts = built if built is not None else build(goal)
    kinds = [f["kind"] for f in goal["features"]]
    specs = default_images() if images is True else list(images)
    specs = [dict(sp, name=sp.get("name") or f"image{k + 1}") for k, sp in enumerate(specs)]
    out, warns, scenes = {}, [], {}

    def scene_of(sp):
        key = sp["name"]
        if key not in scenes:
            scenes[key] = scene(solid, parts, kinds, sp)
        return scenes[key]

    hq = [sp for sp in specs if sp.get("quality") == "high"]
    if hq:
        from . import render_vtk
        ok = []
        for sp in hq:
            try:
                scene_of(sp)
                ok.append(sp)
            except Exception as exc:  # noqa: BLE001
                warns.append(f"image {sp['name']!r}: {type(exc).__name__}: {exc}")
        got, w = render_vtk.render({sp["name"]: scenes[sp["name"]] for sp in ok}, ok, out_dir, name,
                                   {sp["name"]: title_of(name, sp) for sp in ok})
        out.update(got)
        warns += w
        for sp in ok:
            if sp["name"] not in got:
                warns.append(f"image {sp['name']!r}: drawn with Matplotlib instead")
    for sp in specs:
        nm = sp["name"]
        if nm in out:
            continue
        path = out_dir / f"{nm}.png"
        try:
            sc = scene_of(sp)
            what = title_of(name, sp)
            info = draw_matplotlib(sc, sp, what, path)
            out[nm] = {"path": str(path), "what": what, "faces_by_feature": sc["faces_by_feature"], **info}
        except Exception as exc:  # noqa: BLE001 (a picture must not fail the build)
            warns.append(f"image {nm!r}: {type(exc).__name__}: {exc}")
    for nm, sc in scenes.items():
        if nm in out and "faces_by_feature" not in out[nm]:
            out[nm]["faces_by_feature"] = sc["faces_by_feature"]
    return out, warns
