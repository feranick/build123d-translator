"""ops.py — goal features as build123d operations.
Version: 2026.10.15.4

`Builder` turns the features of a goal, in order, into one solid. Each feature kind is a
method with the goal's parameters as keyword arguments, so a goal reads as a script:

    b = Builder(scale=80)
    b.base_box(w=80, d=50, h=12)
    b.boss_cyl(r=10, x=0, y=0, h=15)
    b.hole(r=5, x=0, y=0, at=[0, 0, 27])
    b.mirror(ax=1, ay=0, az=0)
    part = b.part

The meaning of every feature follows the FreeCAD reference builder of the goal format
(which face a feature goes on, which edges a fillet takes, how patterns repeat), checked
feature by feature against it (README, "Conventions checked against FreeCAD").

This module imports only build123d and OCP, so the script writer can copy it into a
standalone script.
"""
from __future__ import annotations

import math

from build123d import (Axis, Cylinder, Edge, Face, GeomType, Kind, Location, Plane,
                       RegularPolygon, Solid, Vector, Wire, extrude, offset)
from OCP.BRep import BRep_Tool

NORMAL_KEYS = ("nx", "ny", "nz")
AXIS_KEYS = ("ax", "ay", "az")


class BuildError(Exception):
    """A feature that cannot be built (no face to put it on, a failed fillet, ...)."""


# ----------------------------------------------------------------------------- geometry helpers

def unit(k: int, sign: float = 1.0) -> Vector:
    v = [0.0, 0.0, 0.0]
    v[k] = sign
    return Vector(*v)


def inplane(k: int) -> tuple[int, int]:
    """Global axes (u, v) of a plane normal to axis k: X then Y then Z, skipping k."""
    a = [i for i in range(3) if i != k]
    return a[0], a[1]


def point(k: int, c: float, u: float, v: float) -> Vector:
    """3D point on the plane {axis k = c} at in-plane coordinates (u, v)."""
    p = [0.0, 0.0, 0.0]
    ua, va = inplane(k)
    p[k], p[ua], p[va] = c, u, v
    return Vector(*p)


def normal_of(p: dict) -> tuple[int, float]:
    """(axis index, sign) of a feature's normal; the top face (+Z) without one."""
    if not any(k in p for k in NORMAL_KEYS):
        return 2, 1.0
    vec = [float(p.get(k, 0.0)) for k in NORMAL_KEYS]
    k = max(range(3), key=lambda i: abs(vec[i]))
    return k, (1.0 if vec[k] >= 0 else -1.0)


def axis_of(p: dict, default: int) -> tuple[int, float, bool]:
    """(axis index, sign, given) of a feature's axis (ax, ay, az)."""
    if not any(k in p for k in AXIS_KEYS):
        return default, 1.0, False
    vec = [float(p.get(k, 0.0)) for k in AXIS_KEYS]
    k = max(range(3), key=lambda i: abs(vec[i]))
    return k, (-1.0 if vec[k] < 0 else 1.0), True


def planar_faces(shape, direction: Vector) -> list:
    return [f for f in shape.faces()
            if f.geom_type == GeomType.PLANE and f.normal_at().dot(direction) > 0.999]


def pick_face(shape, k: int, sign: float, at=None):
    """The planar face with outward normal sign*axis k: the largest (then the farthest along the
    normal), or with `at` the one nearest to that point (then the largest)."""
    d = unit(k, sign)
    cands = planar_faces(shape, d)
    if not cands:
        return None
    if at is None:
        return max(cands, key=lambda f: (round(f.area, 6), f.center().dot(d)))
    q = Vector(*at)
    return min(cands, key=lambda f: (round(f.distance_to(q), 6), -round(f.area, 6)))


def seam_edges(shape) -> list:
    """Seam edges (the closing edge of a cylinder or cone, closed on its own face): fillets skip them.
    Only basic OCP calls, so it works across OCP builds."""
    out = []
    for f in shape.faces():
        for e in f.edges():
            if BRep_Tool.IsClosed_s(e.wrapped, f.wrapped) and not any(e.is_same(x) for x in out):
                out.append(e)
    return out


def straight_edges_along(shape, k: int) -> list:
    """Straight edges parallel to axis k, seams excluded."""
    seams = seam_edges(shape)
    out = []
    for e in shape.edges():
        if e.geom_type != GeomType.LINE:
            continue
        t = e.tangent_at(0)
        if abs(abs((t.X, t.Y, t.Z)[k]) - 1) < 1e-6 and not any(e.is_same(x) for x in seams):
            out.append(e)
    return out


def nearest(elements, pts, tol: float) -> list:
    """The element nearest to each point (no repeats); error if a point has none within tol."""
    out = []
    for p in pts:
        q = Vector(*p)
        d = [e.distance_to(q) for e in elements]
        if not d:
            raise BuildError("nothing to select")
        i = min(range(len(d)), key=lambda j: d[j])
        if d[i] > tol:
            raise BuildError(f"no edge or face within {tol:g} of {list(p)}")
        if elements[i] not in out:
            out.append(elements[i])
    return out


def _chord_params(pts) -> list[float]:
    """Chord-length parameters, as FreeCAD's BSplineCurve.interpolate uses."""
    t = [0.0]
    for a, b in zip(pts, pts[1:]):
        t.append(t[-1] + (b - a).length)
    return [x / t[-1] for x in t] if t[-1] > 0 else None


def outline_face(outline: dict, k: int, c: float) -> Face:
    """Face of a closed outline {'start', 'segs'} on the plane {axis k = c}; (u, v) are the
    plane's global in-plane coordinates (X, then Y, then Z)."""
    cur = point(k, c, *outline["start"])
    edges = []
    for seg in outline["segs"]:
        if seg[0] == "L":
            end = point(k, c, seg[1], seg[2])
            edges.append(Edge.make_line(cur, end))
        elif seg[0] == "A":
            mid, end = point(k, c, seg[1], seg[2]), point(k, c, seg[3], seg[4])
            if (mid - cur).cross(end - cur).length < 1e-9:
                edges.append(Edge.make_line(cur, end))
            else:
                edges.append(Edge.make_three_point_arc(cur, mid, end))
        elif seg[0] == "S":
            pts = [cur] + [point(k, c, *q) for q in seg[1]]
            end = pts[-1]
            edges.append(Edge.make_spline(pts, parameters=_chord_params(pts)))
        else:
            raise BuildError(f"unknown outline segment {seg[0]!r}")
        cur = end
    return Face(Wire(edges))


def circle_face(k: int, c: float, u: float, v: float, r: float) -> Face:
    return Face(Wire([Edge.make_circle(r, Plane(origin=point(k, c, u, v), z_dir=unit(k)))]))


def rect_face(k: int, c: float, u: float, v: float, w: float, d: float) -> Face:
    pts = [point(k, c, u + su * w / 2, v + sv * d / 2) for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    return Face(Wire.make_polygon(pts, close=True))


def prism(face: Face, k: int, start: float, length: float, sign: float):
    """Extrude `face` (on a plane normal to axis k) from `start` along sign*axis by `length`."""
    f = face.moved(Location(unit(k, start)))
    return Solid.extrude(f, unit(k, sign * length))


def boolean(part, tool, op: str):
    """part + tool (op "add") or part - tool ("sub") with OpenCascade's boolean, then merges the
    faces split by the boolean (as build123d's clean()). Merging can break a valid result on shared
    faces (two half-disks fused across their diameter), so its result is kept only if it stays
    valid with the same volume."""
    from build123d import Compound
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse

    o = (BRepAlgoAPI_Fuse if op == "add" else BRepAlgoAPI_Cut)(part.wrapped, tool.wrapped)
    if not o.IsDone():
        raise BuildError("the kernel's boolean operation failed")
    raw = Compound(o.Shape())
    solids = raw.solids()
    res = solids[0] if len(solids) == 1 else raw
    try:
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
        from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain

        # on a deep copy: the merge changes the shapes it shares with its input in place
        u = ShapeUpgrade_UnifySameDomain(BRepBuilderAPI_Copy(res.wrapped).Shape(), True, True, True)
        u.Build()
        cleaned = type(res)(u.Shape())
        if cleaned.is_valid and len(cleaned.solids()) == len(solids) and abs(cleaned.volume - res.volume) <= 1e-6 * max(res.volume, 1e-9):
            res = cleaned
    except Exception:  # noqa: BLE001
        pass
    return res


def single_solid(shape):
    solids = shape.solids()
    if len(solids) != 1:
        raise BuildError(f"the part has {len(solids)} solids; a goal is one solid part")
    return solids[0]


# ----------------------------------------------------------------------------- the builder

class Builder:
    """Builds a goal feature by feature. `part` is the solid so far; `last` the previous
    feature's tool (shape and "add"/"sub") for patterns and mirrors."""

    def __init__(self, scale: float = 0.0):
        self.part = None
        self.last = None
        self.scale = scale

    # -- helpers --
    def _set(self, shape, tool=None, op=None, pattern=False):
        self.part = shape
        self.last = None if tool is None else (tool, op, pattern)

    def _need_part(self):
        if self.part is None:
            raise BuildError("the first feature must be a base")
        return self.part

    def _through(self) -> float:
        bb = self._need_part().bounding_box()
        return 2 * (bb.diagonal + bb.min.length + bb.max.length) + 10

    def _combine(self, tool, op: str):
        part = self._need_part()
        self._set(boolean(part, tool, op), tool, op)

    def _placement(self, p: dict):
        """(axis k, sign, plane coordinate c, datum) for a sketched feature."""
        if "off" in p:
            k, _ = normal_of(p)
            return k, 1.0, float(p["off"]), True
        k, sign = normal_of(p)
        face = pick_face(self._need_part(), k, sign, p.get("at"))
        if face is None:
            raise BuildError(f"no planar face with normal {'+' if sign > 0 else '-'}{'XYZ'[k]}")
        c = face.center()
        return k, sign, (c.X, c.Y, c.Z)[k], False

    @staticmethod
    def _uv(p: dict, k: int) -> tuple[float, float]:
        ua, va = inplane(k)
        xyz = (float(p.get("x", 0.0)), float(p.get("y", 0.0)), float(p.get("z", 0.0)))
        return xyz[ua], xyz[va]

    def _sketched(self, p: dict, face_fn, op: str, length=None):
        """A sketched feature: face_fn(k, c) gives the profile on its plane; additive features
        extrude outward by `length`, subtractive ones inward by `length` or through all
        (length None); on an origin plane (off) both ways, symmetric."""
        k, sign, c, datum = self._placement(p)
        face = face_fn(k, c)
        full = length is None
        L = self._through() if full else float(length)
        if datum:
            tool = prism(face, k, -L / 2, L, 1.0)
        elif op == "add":
            tool = prism(face, k, 0.0, L, sign)
        else:
            tool = prism(face, k, 0.0, L, -sign)
        self._combine(tool, op)

    # -- bases --
    def base_box(self, w, d, h):
        s = Solid.make_box(w, d, h).moved(Location((-w / 2, -d / 2, 0)))
        self._set(s, s, "add")

    def base_cyl(self, r, h):
        s = Solid.make_cylinder(r, h)
        self._set(s, s, "add")

    def base_hex(self, r, h):
        s = single_solid(extrude(RegularPolygon(r, 6), h))
        self._set(s, s, "add")

    def base_ring(self, ri, ro, h):
        s = boolean(Solid.make_cylinder(ro, h), Solid.make_cylinder(ri, h), "sub")
        self._set(s, s, "add")

    def profile_base(self, outline, h, **_):
        s = prism(outline_face(outline, 2, 0.0), 2, 0.0, h, 1.0)
        self._set(s, s, "add")

    # -- sketched features --
    def boss_cyl(self, r, x=0.0, y=0.0, h=1.0, **p):
        p.update(x=x, y=y)
        self._sketched(p, lambda k, c: circle_face(k, c, *self._uv(p, k), r), "add", h)

    def boss_box(self, w, d, x=0.0, y=0.0, h=1.0, **p):
        p.update(x=x, y=y)
        self._sketched(p, lambda k, c: rect_face(k, c, *self._uv(p, k), w, d), "add", h)

    def hole(self, r, x=0.0, y=0.0, **p):
        p.update(x=x, y=y)
        self._sketched(p, lambda k, c: circle_face(k, c, *self._uv(p, k), r), "sub", None)

    hole_std = hole

    def pocket_rect(self, w, d, x=0.0, y=0.0, depth=1.0, **p):
        p.update(x=x, y=y)
        self._sketched(p, lambda k, c: rect_face(k, c, *self._uv(p, k), w, d), "sub", depth)

    def profile_boss(self, outline, h, **p):
        self._sketched(p, lambda k, c: outline_face(outline, k, c), "add", h)

    def profile_pocket(self, outline, depth=None, **p):
        self._sketched(p, lambda k, c: outline_face(outline, k, c), "sub", depth)

    # -- revolve and groove --
    def _revolved(self, outline, p):
        k, _ = normal_of(p)
        a, _, _ = axis_of(p, 2)
        face = outline_face(outline, k, float(p.get("off", 0.0)))
        return Solid.revolve(face, float(p.get("angle", 360.0)), Axis((0, 0, 0), unit(a)))

    def profile_revolve(self, outline, **p):
        tool = self._revolved(outline, p)
        if self.part is None:
            self._set(tool, tool, "add")
        else:
            self._combine(tool, "add")

    def profile_groove(self, outline, **p):
        self._combine(self._revolved(outline, p), "sub")

    # -- patterns and mirror --
    def _copies(self, transforms):
        if self.last is None:
            raise BuildError("a pattern needs a feature before it (not a fillet, chamfer or shell)")
        tool, op, was_pattern = self.last
        if was_pattern:
            raise BuildError("a pattern of a pattern is not supported")
        part = self._need_part()
        copies = [t(tool) for t in transforms]
        for cpy in copies:
            part = boolean(part, cpy, op)
        self._set(part, tool, op, pattern=True)

    def polar_pattern(self, n, **p):
        k, sign, given = axis_of(p, 2)
        ang = float(p.get("angle", 360.0)) if given else 360.0
        n = int(n)
        step = ang / n if abs(ang - 360.0) < 1e-9 else ang / (n - 1)
        ax = Axis((0, 0, 0), unit(k, sign))
        self._copies([lambda t, i=i: t.rotate(ax, i * step) for i in range(1, n)])

    def linear_pattern(self, n, length, **p):
        k, sign, _ = axis_of(p, 0)
        n = int(n)
        step = float(length) / (n - 1)
        self._copies([lambda t, i=i: t.moved(Location(unit(k, sign * i * step))) for i in range(1, n)])

    def mirror(self, **p):
        k, _, _ = axis_of(p, 0)
        self._copies([lambda t: t.mirror(Plane(origin=(0, 0, 0), z_dir=unit(k)))])

    # -- fillets, chamfers, shell --
    def _edges(self, p, default_top=True):
        part = single_solid(self._need_part())
        if p.get("pts"):
            tol = max(0.05, 0.02 * self.scale)
            return part, nearest(part.edges(), p["pts"], tol)
        if any(k in p for k in AXIS_KEYS):
            k, _, _ = axis_of(p, 2)
            edges = straight_edges_along(part, k)
        else:
            k, sign = normal_of(p)
            face = pick_face(part, k, sign, p.get("at"))
            if face is None:
                raise BuildError(f"no planar face with normal {'+' if sign > 0 else '-'}{'XYZ'[k]}")
            edges = face.outer_wire().edges()
        if not edges:
            raise BuildError("no edges to round or chamfer")
        return part, edges

    def _dressup(self, fn):
        try:
            res = fn()
        except BuildError:
            raise
        except Exception as exc:  # OCC fails on fillets that do not fit
            raise BuildError(f"the fillet/chamfer/shell failed in the kernel ({type(exc).__name__}): "
                             "too large for the material around it?") from exc
        self._set(res)

    def fillet_top(self, r, **p):
        part, edges = self._edges({"at": p.get("at")} if "at" in p else {})
        self._dressup(lambda: part.fillet(r, edges))

    def chamfer_top(self, size, **p):
        part, edges = self._edges({"at": p.get("at")} if "at" in p else {})
        self._dressup(lambda: part.chamfer(size, None, edges))

    def fillet_vertical(self, r, **_):
        part = single_solid(self._need_part())
        edges = straight_edges_along(part, 2)
        if not edges:
            raise BuildError("no vertical edges")
        self._dressup(lambda: part.fillet(r, edges))

    def fillet_edges(self, r, **p):
        part, edges = self._edges(p)
        self._dressup(lambda: part.fillet(r, edges))

    def chamfer_edges(self, size, **p):
        part, edges = self._edges(p)
        self._dressup(lambda: part.chamfer(size, None, edges))

    def shell(self, t, **p):
        part = single_solid(self._need_part())
        if p.get("pts"):
            faces = nearest(part.faces(), p["pts"], max(0.05, 0.02 * self.scale))
        else:
            face = pick_face(part, 2, 1.0, p.get("at"))
            if face is None:
                raise BuildError("no top face to open")
            faces = [face]
        self._dressup(lambda: single_solid(offset(part, amount=-t, openings=faces, kind=Kind.ARC)))

    # -- driver --
    def add(self, kind: str, params: dict):
        fn = getattr(self, kind, None)
        if fn is None or kind not in KINDS:
            raise BuildError(f"unknown kind {kind!r}")
        fn(**params)


def goal_scale(features: list) -> float:
    """Largest extent of the base feature (the tolerance of selection by points scales with it)."""
    f = features[0]
    p = f["params"]
    if f["kind"] == "base_box":
        return max(p["w"], p["d"], p["h"])
    if f["kind"] in ("base_cyl", "base_hex"):
        return max(2 * p["r"], p["h"])
    if f["kind"] == "base_ring":
        return max(2 * p["ro"], p["h"])
    pts = [p["outline"]["start"]]
    for s in p["outline"]["segs"]:
        pts += s[1] if s[0] == "S" else [s[-2:]]
    us, vs = [q[0] for q in pts], [q[1] for q in pts]
    ext = max(max(us) - min(us), max(vs) - min(vs))
    return max(ext, p["h"]) if f["kind"] == "profile_base" else ext


def build_goal(goal: dict):
    """The solid of a goal {"features": [...]}, plus seconds per feature."""
    import time

    feats = goal["features"]
    b = Builder(scale=float(goal.get("scale") or goal_scale(feats)))
    times = []
    for i, f in enumerate(feats):
        t0 = time.perf_counter()
        try:
            b.add(f["kind"], dict(f.get("params", {})))
        except BuildError as exc:
            raise BuildError(f"feature {i + 1} ({f['kind']}): {exc}") from None
        except Exception as exc:  # noqa: BLE001
            raise BuildError(f"feature {i + 1} ({f['kind']}): {type(exc).__name__}: {exc}") from None
        times.append(time.perf_counter() - t0)
    solid = single_solid(b.part)
    if not solid.is_valid:  # booleans can leave small defects FreeCAD's builder repairs; heal them
        try:
            fixed = single_solid(solid.fix())
            if fixed.is_valid:
                solid = fixed
        except Exception:  # noqa: BLE001
            pass
    return solid, times


KINDS = ("base_box", "base_cyl", "base_hex", "base_ring", "profile_base", "profile_revolve",
         "boss_cyl", "boss_box", "hole", "hole_std", "pocket_rect", "profile_boss", "profile_pocket",
         "profile_groove", "polar_pattern", "linear_pattern", "mirror", "fillet_top", "fillet_vertical",
         "chamfer_top", "shell", "fillet_edges", "chamfer_edges")
