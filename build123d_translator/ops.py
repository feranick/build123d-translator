"""ops.py — goal features as build123d operations.
Version: 2026.10.28.1

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

from build123d import (AngularDirection, Axis, Compound, Cylinder, Edge, Face, GeomType, Kind, Location, Plane,
                       RegularPolygon, Solid, Vector, Wire, extrude, offset)
from OCP.BRep import BRep_Tool

try:
    from .standards import FITS, HEADS, ISO_METRIC
except ImportError:  # a standalone script: the screw table is inlined above the builder
    pass

NORMAL_KEYS = ("nx", "ny", "nz")
AXIS_KEYS = ("ax", "ay", "az")
B_KEYS = ("bx", "by", "bz")  # second direction of a grid pattern


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


def _parallel_face(f, d: Vector) -> bool:
    """A planar face whose normal is normal to d, or a cylinder whose axis is along d."""
    if f.geom_type == GeomType.PLANE:
        return abs(f.normal_at().dot(d)) < 1e-6
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface

        ax = BRepAdaptor_Surface(f.wrapped).Cylinder().Axis().Direction()
        return abs(abs(ax.X() * d.X + ax.Y() * d.Y + ax.Z() * d.Z) - 1) < 1e-6
    except Exception:  # noqa: BLE001
        return False


def _visible(part, f, n: Vector, size: float) -> bool:
    """True if the face is seen from far along +n: no material in front of its centre."""
    c = f.center()
    return not any(part.is_inside(c + n * (size * t)) for t in (1e-3, 0.01, 0.05, 0.1, 0.2, 0.35, 0.5, 0.75, 1.0))


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


SHAPES = ("circle", "ellipse", "rect", "polygon", "slot", "arc_slot")


def _rounded(P, hw: float, hd: float, c: float) -> dict:
    """Rectangle of half sizes hw, hd with corner radius c (0: sharp), counter-clockwise, through P(x, y)."""
    k = c * (1 - math.sqrt(0.5))  # inset of a corner arc's midpoint
    start = P(hw, -hd + c)
    segs, last = [], (hw, -hd + c)

    def line(x, y):
        nonlocal last
        if math.hypot(x - last[0], y - last[1]) > 1e-12:
            segs.append(["L", *P(x, y)])
        last = (x, y)

    def arc(mx, my, x, y):
        nonlocal last
        if c > 0:
            segs.append(["A", *P(mx, my), *P(x, y)])
        last = (x, y)

    line(hw, hd - c)
    arc(hw - k, hd - k, hw - c, hd)
    line(-hw + c, hd)
    arc(-hw + k, hd - k, -hw, hd - c)
    line(-hw, -hd + c)
    arc(-hw + k, -hd + k, -hw + c, -hd)
    line(hw - c, -hd)
    if c > 0:
        segs.append(["A", *P(hw - k, -hd + k), *start])
    else:
        segs[-1] = ["L", *start]
    return {"start": start, "segs": segs}


def expand_outline(o: dict) -> dict:
    """An outline shorthand as segments: {'circle': [cu, cv, r]}, {'ellipse': [cu, cv, a, b]},
    {'rect': [cu, cv, w, d], 'corner_r': r}, {'polygon': [cu, cv, r, n]} (a corner on +u),
    {'slot': [cu, cv, length, w]} (overall length), {'arc_slot': [cu, cv, r, w, a1, a2]} (centreline
    radius r, from a1 to a2 degrees counter-clockwise); 'rotation' (degrees, counter-clockwise) for all
    but arc_slot. Other outlines are returned as they are."""
    shape = next((s for s in SHAPES if s in o), None) if isinstance(o, dict) else None
    if shape is None:
        return o
    a = [float(x) for x in o[shape]]
    cu, cv = a[0], a[1]
    deg = float(o.get("rotation", 0.0))
    cs, sn = math.cos(math.radians(deg)), math.sin(math.radians(deg))

    def P(x, y):
        return [cu + x * cs - y * sn, cv + x * sn + y * cs]

    if shape == "circle":
        start = P(a[2], 0)
        return {"start": start, "segs": [["A", *P(0, a[2]), *P(-a[2], 0)], ["A", *P(0, -a[2]), *start]]}
    if shape == "ellipse":
        start = P(a[2], 0)
        return {"start": start, "segs": [["E", cu, cv, a[2], a[3], deg, *P(-a[2], 0)], ["E", cu, cv, a[2], a[3], deg, *start]]}
    if shape == "polygon":
        n = int(a[3])
        pts = [P(a[2] * math.cos(2 * math.pi * i / n), a[2] * math.sin(2 * math.pi * i / n)) for i in range(n)]
        return {"start": pts[0], "segs": [["L", *q] for q in pts[1:]] + [["L", *pts[0]]]}
    if shape == "rect":
        hw, hd = a[2] / 2, a[3] / 2
        return _rounded(P, hw, hd, min(float(o.get("corner_r", 0.0)), hw, hd))
    if shape == "slot":
        return _rounded(P, a[2] / 2, a[3] / 2, a[3] / 2)
    r, w, t1, t2 = a[2], a[3] / 2, math.radians(a[4]), math.radians(a[5])
    tm = (t1 + t2) / 2

    def Q(rr, t, dt=0.0):  # on the circle of radius rr at angle t, plus dt along the tangent
        return [cu + rr * math.cos(t) - dt * math.sin(t), cv + rr * math.sin(t) + dt * math.cos(t)]

    start = Q(r + w, t1)
    return {"start": start, "segs": [["A", *Q(r + w, tm), *Q(r + w, t2)], ["A", *Q(r, t2, w), *Q(r - w, t2)],
                                     ["A", *Q(r - w, tm), *Q(r - w, t1)], ["A", *Q(r, t1, -w), *start]]}


def ellipse_arc(pt, uv0, seg) -> Edge:
    """['E', cu, cv, a, b, rotation, u, v]: from the current point (on the ellipse) counter-clockwise to
    (u, v), along the ellipse centred at (cu, cv) with semi-axes a (along the rotation's direction) and b."""
    cu, cv, a, b, deg = (float(x) for x in seg[1:6])
    r = math.radians(deg)
    o = pt(cu, cv)
    eu, ev = pt(cu + 1, cv) - o, pt(cu, cv + 1) - o
    plane = Plane(origin=o, x_dir=eu * math.cos(r) + ev * math.sin(r), z_dir=eu.cross(ev))

    def par(u, v):
        du, dv = float(u) - cu, float(v) - cv
        return math.degrees(math.atan2((-du * math.sin(r) + dv * math.cos(r)) / b, (du * math.cos(r) + dv * math.sin(r)) / a))

    t0, t1 = par(*uv0), par(seg[6], seg[7])
    while t1 <= t0 + 1e-9:
        t1 += 360.0
    return Edge.make_ellipse(a, b, plane, t0, t1, AngularDirection.COUNTER_CLOCKWISE)


def outline_face(outline: dict, k: int, c: float, holes=None, pt=None) -> Face:
    """Face of a closed outline {'start', 'segs'} (or a shorthand) on the plane {axis k = c}, less
    the closed outlines in `holes`; (u, v) are the plane's global in-plane coordinates (X, then Y,
    then Z), or pt(a, b) -> Vector maps them (angled planes)."""
    outer = outline_wire(outline, k, c, pt)
    if not holes:
        return Face(outer)
    inner = [outline_wire(h, k, c, pt) for h in holes]
    try:
        f = Face(outer, inner)
    except Exception as exc:  # noqa: BLE001
        raise BuildError("a hole of the outline is not inside it") from exc
    full = Face(outer).area
    want = full - sum(Face(w).area for w in inner)
    if not f.is_valid or want <= 0 or abs(f.area - want) > 1e-6 * full:
        raise BuildError("the outline's holes must lie inside it, apart from each other")
    return f


def outline_wire(outline: dict, k: int, c: float, pt=None) -> Wire:
    """Wire of an outline or open curve {'start', 'segs'} (or a shorthand) on the plane {axis k = c};
    or, with pt(a, b) -> Vector, on any plane (angled planes: o + a u + b v)."""
    outline = expand_outline(outline)
    if pt is None:
        pt = lambda a, b: point(k, c, a, b)  # noqa: E731
    uv = [float(x) for x in outline["start"]]
    cur = pt(*uv)
    edges = []
    for seg in outline["segs"]:
        if seg[0] == "L":
            end, uv = pt(seg[1], seg[2]), seg[1:3]
            edges.append(Edge.make_line(cur, end))
        elif seg[0] == "A":
            mid, end, uv = pt(seg[1], seg[2]), pt(seg[3], seg[4]), seg[3:5]
            if (mid - cur).cross(end - cur).length < 1e-9:
                edges.append(Edge.make_line(cur, end))
            else:
                edges.append(Edge.make_three_point_arc(cur, mid, end))
        elif seg[0] == "S":
            pts = [cur] + [pt(*q) for q in seg[1]]
            end, uv = pts[-1], seg[1][-1]
            edges.append(Edge.make_spline(pts, parameters=_chord_params(pts)))
        elif seg[0] == "E":
            e = ellipse_arc(pt, uv, seg)
            edges.append(e)
            end, uv = e.position_at(1), seg[6:8]
        else:
            raise BuildError(f"unknown outline segment {seg[0]!r}")
        cur = end
    return Wire(edges)


def helix_edges(cur: Vector, seg) -> tuple[list, Vector]:
    """['H', cx, cy, cz, ax, ay, az, pitch, height]: a helix from the current point about the axis
    through (cx, cy, cz) along (ax, ay, az), rising `height` along it; right-handed for pitch > 0,
    left-handed for pitch < 0. Its radius is the current point's distance from the axis."""
    c, a = Vector(*seg[1:4]), Vector(*seg[4:7])
    if a.length < 1e-12:
        raise BuildError("helix axis has zero length")
    a = a.normalized()
    pitch, height = float(seg[7]), float(seg[8])
    rel = cur - c
    foot = c + a * rel.dot(a)
    radial = cur - foot
    if radial.length < 1e-9:
        raise BuildError("a helix cannot start on its axis")
    w = Edge.make_helix(abs(pitch), height, radial.length, lefthand=pitch < 0)
    loc = Plane(origin=foot, x_dir=radial.normalized(), z_dir=a).location
    edges = [e.moved(loc) for e in (w.edges() if hasattr(w, "edges") else [w])]
    return edges, edges[-1].position_at(1)


def path_wire(path: dict) -> Wire:
    """3D path {'start': [x, y, z], 'segs': [['L', x, y, z], ['A', mx, my, mz, x, y, z],
    ['S', [[x, y, z], ...]], ['H', cx, cy, cz, ax, ay, az, pitch, height]]} (open or closed) as a wire."""
    cur = Vector(*path["start"])
    edges = []
    for seg in path["segs"]:
        if seg[0] == "L":
            end = Vector(*seg[1:4])
            edges.append(Edge.make_line(cur, end))
        elif seg[0] == "A":
            mid, end = Vector(*seg[1:4]), Vector(*seg[4:7])
            if (mid - cur).cross(end - cur).length < 1e-9:
                edges.append(Edge.make_line(cur, end))
            else:
                edges.append(Edge.make_three_point_arc(cur, mid, end))
        elif seg[0] == "S":
            pts = [cur] + [Vector(*q) for q in seg[1]]
            end = pts[-1]
            edges.append(Edge.make_spline(pts, parameters=_chord_params(pts)))
        elif seg[0] == "H":
            hel, end = helix_edges(cur, seg)
            edges += hel
        else:
            raise BuildError(f"unknown path segment {seg[0]!r}")
        cur = end
    return Wire(edges)


# ---- angled planes (Taiga patch 0020): sketches on planes at any angle

U_KEYS = ("ux", "uy", "uz")
ANGLED_KINDS = ("boss_cyl", "boss_box", "hole", "pocket_rect", "profile_boss", "profile_pocket", "hole_std", "slot",
                "slot_arc")


def is_angled(p: dict) -> bool:
    """A sketched feature is on an angled plane when its params give the in-plane x direction."""
    return all(k in p for k in U_KEYS)


def angled_frame(p: dict) -> tuple[Vector, Vector, Vector, Vector]:
    """(origin, u, v, n) of an angled plane, orthonormal: n = (nx, ny, nz) normalised, u = (ux, uy, uz)
    made perpendicular to n, v = n x u; the origin (x, y, z) is the feature's centre."""
    n = Vector(*(float(p.get(k, 0.0)) for k in NORMAL_KEYS))
    if n.length < 1e-12:
        raise BuildError("angled plane: zero normal")
    n = n.normalized()
    u = Vector(*(float(p[k]) for k in U_KEYS))
    u = u - n * u.dot(n)
    if u.length < 1e-12:
        raise BuildError("angled plane: u is parallel to the normal")
    u = u.normalized()
    return Vector(float(p.get("x", 0.0)), float(p.get("y", 0.0)), float(p.get("z", 0.0))), u, n.cross(u), n


def angled_face(shape: tuple, o: Vector, u: Vector, v: Vector, n: Vector) -> Face:
    """Profile in the plane's frame: ("circle", r) and ("rect", w, d) centred on the origin (w along u,
    d along v); ("outline", outline) in (u, v) relative to the origin."""
    if shape[0] == "circle":
        return Face(Wire([Edge.make_circle(shape[1], Plane(origin=o, x_dir=u, z_dir=n))]))
    pt = lambda a, b: o + u * a + v * b  # noqa: E731
    if shape[0] == "rect" and len(shape) > 3 and shape[3]:
        return outline_face({"rect": [0, 0, shape[1], shape[2]], "corner_r": shape[3]}, 0, 0.0, pt=pt)
    if shape[0] == "rect":
        w, d = shape[1] / 2, shape[2] / 2
        return Face(Wire.make_polygon([o + u * (su * w) + v * (sv * d) for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1))],
                                      close=True))
    return outline_face(shape[1], 0, 0.0, shape[2] if len(shape) > 2 else None, pt=pt)


def circle_face(k: int, c: float, u: float, v: float, r: float) -> Face:
    return Face(Wire([Edge.make_circle(r, Plane(origin=point(k, c, u, v), z_dir=unit(k)))]))


def rect_face(k: int, c: float, u: float, v: float, w: float, d: float, corner_r: float = 0.0) -> Face:
    if corner_r:
        return outline_face({"rect": [u, v, w, d], "corner_r": corner_r}, k, c)
    pts = [point(k, c, u + su * w / 2, v + sv * d / 2) for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    return Face(Wire.make_polygon(pts, close=True))


def prism(face: Face, k: int, start: float, length: float, sign: float):
    """Extrude `face` (on a plane normal to axis k) from `start` along sign*axis by `length`."""
    f = face.moved(Location(unit(k, start)))
    return Solid.extrude(f, unit(k, sign * length))


def boolean(part, tool, op: str):
    """part + tool (op "add"), part - tool ("sub") or part & tool ("common") with OpenCascade's
    boolean, then merges the faces split by the boolean (as build123d's clean()). Merging can break
    a valid result on shared faces (two half-disks fused across their diameter), so its result is
    kept only if it stays valid with the same volume."""
    from build123d import Compound
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse

    alg = {"add": BRepAlgoAPI_Fuse, "sub": BRepAlgoAPI_Cut, "common": BRepAlgoAPI_Common}[op]
    o = alg(part.wrapped, tool.wrapped)
    if not o.IsDone():
        raise BuildError("the kernel's boolean operation failed")
    raw = Compound(o.Shape())
    solids = raw.solids()
    if not solids:
        return raw
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


def up_to(part, tool, k: int, c: float, s: float, op: str, until: str):
    """The part of the long prism `tool` (from the plane {axis k = c} along s * axis k) that an
    extrusion up to a face keeps. Additive ('add'): 'next' fills the free space up to the first
    material met, 'last' up to the last material's far side. Subtractive: 'next' removes the first
    piece of material met (through the first wall), 'last' all of it (through all)."""
    from build123d import Compound

    def along(sol):  # (near, far) distance along s * axis k from the plane
        bb = sol.bounding_box()
        lo, hi = (bb.min.X, bb.min.Y, bb.min.Z)[k], (bb.max.X, bb.max.Y, bb.max.Z)[k]
        return (lo - c, hi - c) if s > 0 else (c - hi, c - lo)

    tb = tool.bounding_box()
    length = max(along(tool)[1], 1e-9)
    tol = 1e-6 * max(tb.diagonal, 1.0)
    if op == "add":
        free = boolean(tool, part, "sub").solids()
        hit = [x for x in free if along(x)[1] < length - tol]  # stops at material
        if not hit:
            raise BuildError("nothing to extrude up to in that direction")
        if until == "next":
            near = min(along(x)[0] for x in hit)
            keep = [x for x in hit if along(x)[0] <= near + tol]
        else:
            keep = hit
        return keep[0] if len(keep) == 1 else Compound(keep)
    mat = boolean(tool, part, "common").solids()
    if not mat:
        raise BuildError("no material to remove in that direction")
    if until == "next":
        near = min(along(x)[0] for x in mat)
        mat = [x for x in mat if along(x)[0] <= near + tol]
    return mat[0] if len(mat) == 1 else Compound(mat)


def drill_tool(origin: Vector, into: Vector, xdir: Vector, profile) -> Solid:
    """A revolved hole tool: `profile` is [(radius, depth), ...] down the axis from `origin` along
    `into`, from the face (depth 0) to the bottom; closed along the axis."""
    pts = [origin] + [origin + xdir * r + into * z for r, z in profile]
    if profile[-1][0] > 1e-12:
        pts.append(origin + into * profile[-1][1])
    face = Face(Wire.make_polygon(pts, close=True))
    return Solid.revolve(face, 360.0, Axis(origin, into))


def thread_tool(r, pitch, length, start: Vector, a: Vector, radial: Vector, internal=False, depth=None):
    """The helical groove of a 60-degree V thread: into a shaft of radius r (external; r the major
    radius) or into the wall of a bore of radius r (internal), from `start` on the axis along the unit
    vector `a` for `length`; `radial` is a unit vector normal to `a`. Right-handed for pitch > 0."""
    P = abs(float(pitch))
    h = 0.6134 * P if depth is None else float(depth)
    e = 0.1 * P  # the groove reaches past the surface for a clean cut
    if (h + e) * math.tan(math.radians(30)) * 2 >= P:
        raise BuildError("thread depth too large for the pitch")
    if not internal and h >= r:
        raise BuildError("thread depth must be smaller than r")
    inward = -1.0 if not internal else 1.0  # from the surface into the material, radially
    apex = start + radial * (r + inward * h)
    rim = r - inward * e
    half = (h + e) * math.tan(math.radians(30))
    tri = [apex, start + radial * rim + a * half, start + radial * rim - a * half]
    prof = Face(Wire.make_polygon(tri, close=True))
    hel, _ = helix_edges(start + radial * r, ["H", start.X, start.Y, start.Z, a.X, a.Y, a.Z, float(pitch), float(length)])
    try:
        tool = Solid.sweep(prof, Wire(hel), is_frenet=True)  # on a helix: the profile stays in the axial plane
    except Exception as exc:  # noqa: BLE001
        raise BuildError(f"the thread sweep failed in the kernel ({type(exc).__name__})") from exc
    if tool.volume <= 0:
        raise BuildError("the thread has no volume")
    return tool


def _disjoint(shapes, tol: float) -> bool:
    """True if the shapes' bounding boxes are pairwise apart (by more than tol): one boolean can take them all."""
    boxes = [s.bounding_box() for s in shapes]
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            if (a.min.X < b.max.X + tol and b.min.X < a.max.X + tol and a.min.Y < b.max.Y + tol
                    and b.min.Y < a.max.Y + tol and a.min.Z < b.max.Z + tol and b.min.Z < a.max.Z + tol):
                return False
    return True


def apply_tools(part, tools: list, op: str):
    """part combined with each tool in turn; in one boolean when the tools are apart from each other."""
    if len(tools) > 1 and _disjoint(tools, 1e-6 * max(part.bounding_box().diagonal, 1.0)):
        return boolean(part, Compound(tools), op)
    for tl in tools:
        part = boolean(part, tl, op)
    return part


def single_solid(shape):
    solids = shape.solids()
    if len(solids) != 1:
        raise BuildError(f"the part has {len(solids)} solids; a goal is one solid part")
    return solids[0]


# ----------------------------------------------------------------------------- the builder

class Builder:
    """Builds a goal feature by feature. `part` is the solid so far; `last` the feature's tools
    [(shape, "add"/"sub"), ...] (None for fillets, chamfers, shells and other whole-part changes);
    `history` those of every feature, for patterns and mirrors."""

    def __init__(self, scale: float = 0.0, keep_parts: bool = False):
        self.part = None
        self.last = None
        self.history = []
        self.scale = scale
        self.keep_parts = keep_parts   # the part after every feature (pictures: which feature made each face)
        self.parts = []

    # -- helpers --
    def _set(self, shape, tool=None, op=None):
        self._commit(shape, None if tool is None else [(tool, op)])

    def _commit(self, shape, tools):
        """Every feature ends here, once: the new part and its tools, recorded for later patterns."""
        self.part, self.last = shape, tools
        self.history.append(tools)
        if self.keep_parts:
            self.parts.append(shape)
        if not self.scale and shape is not None:  # bases goal_scale cannot size (combine)
            sz = shape.bounding_box().size
            self.scale = max(sz.X, sz.Y, sz.Z)

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
        until, taper = p.get("until"), float(p.get("taper", 0.0))
        if datum and until is None:
            sign = 1.0
        s = sign if (op == "add" or datum) else -sign  # extrusion direction
        if datum and until is not None:
            s = normal_of(p)[1]
        if until is not None:
            if until not in ("next", "last"):
                raise BuildError("until must be 'next' or 'last'")
            if taper:
                raise BuildError("taper and until cannot be combined")
            long = prism(face, k, 0.0, self._through(), s)
            self._combine(up_to(self._need_part(), long, k, c, s, op, until), op)
            return
        full = length is None
        L = self._through() if full else float(length)
        if taper:
            if datum or full:
                raise BuildError("taper needs a face to start from and a height or depth")
            try:
                tool = Solid.extrude_taper(face, unit(k, s * L), taper)
            except Exception as exc:  # noqa: BLE001
                raise BuildError(f"the tapered extrusion failed in the kernel ({type(exc).__name__}): "
                                 "a taper that closes the profile before its height?") from exc
            self._combine(tool, op)
            return
        if datum:
            tool = prism(face, k, -L / 2, L, 1.0)
        elif op == "add":
            tool = prism(face, k, 0.0, L, sign)
        else:
            tool = prism(face, k, 0.0, L, -sign)
        self._combine(tool, op)

    def _angled(self, p: dict, shape: tuple, op: str, length=None):
        """A feature on an angled plane: the profile in the plane's frame, extruded symmetrically
        about the plane (h or depth is the total; holes and pockets without depth: through all, both ways)."""
        for key, why in (("off", "an angled plane takes its position from x, y, z, not 'off'"),
                         ("until", "until is not available on an angled plane"),
                         ("taper", "taper is not available on an angled plane"),
                         ("at", "an angled plane takes its position from x, y, z, not 'at'")):
            if key in p:
                raise BuildError(why)
        o, u, v, n = angled_frame(p)
        face = angled_face(shape, o, u, v, n)
        L = self._through() if length is None else float(length)
        self._combine(Solid.extrude(face.moved(Location(n * (-L / 2))), n * L), op)

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

    def profile_base(self, outline, h, holes=None, **_):
        s = prism(outline_face(outline, 2, 0.0, holes), 2, 0.0, h, 1.0)
        self._set(s, s, "add")

    def base_sphere(self, r):
        s = Solid.make_sphere(r).moved(Location((0, 0, r)))
        self._set(s, s, "add")

    def base_cone(self, r1, h, r2=0.0):
        s = Solid.make_cone(r1, r2, h)
        self._set(s, s, "add")

    def base_torus(self, R, r):
        s = Solid.make_torus(R, r).moved(Location((0, 0, r)))
        self._set(s, s, "add")

    def base_wedge(self, w, d, h, top=0.0):
        """Ramp: the cross-section in XZ runs from x = -w/2 to w/2 at z = 0 and from -w/2 to -w/2 + top
        at z = h (top 0: a triangle), extruded along Y over d, centred."""
        x0, x1, xt = -w / 2, w / 2, -w / 2 + float(top)
        segs = [["L", x1, 0], ["L", xt, h]] + ([["L", x0, h]] if top else []) + [["L", x0, 0]]
        s = prism(outline_face({"start": [x0, 0], "segs": segs}, 1, 0.0), 1, -d / 2, d, 1.0)
        self._set(s, s, "add")

    def base_polygon(self, n, r, h):
        s = prism(outline_face({"polygon": [0, 0, r, n]}, 2, 0.0), 2, 0.0, h, 1.0)
        self._set(s, s, "add")

    # -- sketched features --
    def boss_cyl(self, r, x=0.0, y=0.0, h=1.0, **p):
        p.update(x=x, y=y)
        if is_angled(p):
            return self._angled(p, ("circle", r), "add", h)
        self._sketched(p, lambda k, c: circle_face(k, c, *self._uv(p, k), r), "add", h)

    def boss_box(self, w, d, x=0.0, y=0.0, h=1.0, corner_r=0.0, **p):
        p.update(x=x, y=y)
        if is_angled(p):
            return self._angled(p, ("rect", w, d, corner_r), "add", h)
        self._sketched(p, lambda k, c: rect_face(k, c, *self._uv(p, k), w, d, corner_r), "add", h)

    def hole(self, r, x=0.0, y=0.0, depth=None, tip=None, **p):
        p.update(x=x, y=y)
        if is_angled(p):
            if tip is not None:
                raise BuildError("tip is not available on an angled plane")
            return self._angled(p, ("circle", r), "sub", depth)
        if tip is not None:
            self._drilled(p, r, [], depth, tip)
        else:
            self._sketched(p, lambda k, c: circle_face(k, c, *self._uv(p, k), r), "sub", depth)

    def hole_std(self, screw, x=0.0, y=0.0, fit="normal", head=None, tapped=False, thread=False, depth=None,
                 tip=None, **p):
        """ISO metric screw hole (standards.ISO_METRIC): a clearance hole (fit close | normal | loose),
        with a counterbore (head 'socket', ISO 4762) or a 90-degree countersink (head 'countersunk',
        ISO 10642); or tapped: the tap drill (D - P), with thread: true also the thread (internal, the
        hole's whole depth)."""
        if screw not in ISO_METRIC:
            raise BuildError(f"unknown screw {screw!r}; one of {', '.join(ISO_METRIC)}")
        pitch, tap, clear, (cbd, cbh), csd = ISO_METRIC[screw]
        p.update(x=x, y=y)
        if tapped:
            if head:
                raise BuildError("a tapped hole takes no head (counterbore or countersink)")
            if not thread:
                return self.hole(tap / 2, depth=depth, tip=tip, **p)
            if is_angled(p) or "off" in p:
                raise BuildError("a modelled thread needs a face to start from")
            # the bore as true cylinder and cone surfaces: the kernel's boolean of a helical groove with a
            # coaxial revolved surface (drill_tool) fails silently; bore, tip and groove are removed in turn
            k, sign, c, datum = self._placement(p)
            origin, into = point(k, c, *self._uv(p, k)), unit(k, -sign)
            r = tap / 2
            L = self._through() if depth is None else float(depth)
            tools = [Solid.make_cylinder(r, L, Plane(origin=origin, z_dir=into))]
            if depth is None:  # through: the thread runs through the material along the axis
                bb = boolean(self._need_part(), tools[0], "common").bounding_box()
                L = max((Vector(x_, y_, z_) - origin).dot(into) for x_ in (bb.min.X, bb.max.X)
                        for y_ in (bb.min.Y, bb.max.Y) for z_ in (bb.min.Z, bb.max.Z))
            elif tip is not None:
                tools.append(Solid.make_cone(r, 0, r / math.tan(math.radians(float(tip)) / 2),
                                             Plane(origin=origin + into * L, z_dir=into)))
            tools.append(thread_tool(r, pitch, L, origin, into, unit(inplane(k)[0]), internal=True,
                                     depth=(float(screw[1:]) - tap) / 2))
            part = self._need_part()
            for tl in tools:
                part = boolean(part, tl, "sub")
            return self._commit(part, [(tl, "sub") for tl in tools])
        if thread:
            raise BuildError("thread needs tapped: true")
        if fit not in FITS:
            raise BuildError(f"fit must be one of {', '.join(FITS)}")
        r = clear[FITS.index(fit)] / 2
        if head is None:
            return self.hole(r, depth=depth, tip=tip, **p)
        if head == "socket":
            return self._drilled(p, r, [(cbd / 2, 0.0), (cbd / 2, cbh)], depth, tip)
        if head == "countersunk":
            if csd is None:
                raise BuildError(f"no countersunk screw of size {screw}")
            return self._drilled(p, r, [(csd / 2, 0.0), (r, (csd / 2 - r) / math.tan(math.radians(45)))], depth, tip)
        raise BuildError(f"head must be one of {', '.join(HEADS)}")

    def _drilled(self, p, r, top, depth, tip):
        """A hole from a face: `top` [(radius, depth), ...] (counterbore or countersink) above the
        bore of radius r; blind with depth (a cone of `tip` degrees at the bottom) or through all."""
        self._combine(self._drill_tool(p, r, top, depth, tip)[0], "sub")

    def _drill_tool(self, p, r, top, depth, tip):
        """(tool, origin on the face, unit vector into the material, depth, axis index) of a drilled hole."""
        k, sign, c, datum = self._placement(p)
        if datum:
            raise BuildError("this hole needs a face to start from (not an origin plane)")
        u, v = self._uv(p, k)
        origin, into = point(k, c, u, v), unit(k, -sign)
        xdir = unit(inplane(k)[0])
        if depth is None and tip is not None:
            raise BuildError("tip needs a depth (a blind hole)")
        L = self._through() if depth is None else float(depth)
        prof = list(top) or [(r, 0.0)]
        if prof[-1][0] != r:
            prof.append((r, prof[-1][1]))
        if prof[-1][1] >= L:
            raise BuildError("the hole's depth must exceed its counterbore or countersink")
        prof.append((r, L))
        if tip is not None:
            prof.append((0.0, L + r / math.tan(math.radians(float(tip)) / 2)))
        return drill_tool(origin, into, xdir, prof), origin, into, L, k

    def hole_cb(self, r, cbr, cbd, x=0.0, y=0.0, depth=None, tip=None, **p):
        if cbr <= r:
            raise BuildError("the counterbore radius cbr must exceed r")
        p.update(x=x, y=y)
        self._drilled(p, r, [(cbr, 0.0), (cbr, float(cbd))], depth, tip)

    def hole_cs(self, r, csr, x=0.0, y=0.0, angle=90.0, depth=None, tip=None, **p):
        if csr <= r:
            raise BuildError("the countersink radius csr must exceed r")
        p.update(x=x, y=y)
        self._drilled(p, r, [(csr, 0.0), (r, (csr - r) / math.tan(math.radians(float(angle)) / 2))], depth, tip)

    def pocket_rect(self, w, d, x=0.0, y=0.0, depth=None, corner_r=0.0, **p):
        p.update(x=x, y=y)
        if is_angled(p):  # without depth: through all, both ways
            return self._angled(p, ("rect", w, d, corner_r), "sub", depth)
        self._sketched(p, lambda k, c: rect_face(k, c, *self._uv(p, k), w, d, corner_r), "sub",
                       1.0 if depth is None else depth)

    def profile_boss(self, outline, h=None, holes=None, **p):
        if is_angled(p):
            if h is None:
                raise BuildError("profile_boss needs h")
            return self._angled(p, ("outline", outline, holes), "add", h)
        if h is None and p.get("until") is None:
            raise BuildError("profile_boss needs h or until")
        self._sketched(p, lambda k, c: outline_face(outline, k, c, holes), "add", h)

    def profile_pocket(self, outline, depth=None, holes=None, **p):
        if is_angled(p):
            return self._angled(p, ("outline", outline, holes), "sub", depth)
        self._sketched(p, lambda k, c: outline_face(outline, k, c, holes), "sub", depth)

    # -- slots --
    def _slot(self, p, shape, depth):
        """A slot pocket: shape(cu, cv) is its outline shorthand centred at (cu, cv) in the plane."""
        if is_angled(p):
            return self._angled(p, ("outline", shape(0.0, 0.0)), "sub", depth)
        self._sketched(p, lambda k, c: outline_face(shape(*self._uv(p, k)), k, c), "sub", depth)

    def slot(self, length, w, x=0.0, y=0.0, angle=0.0, depth=None, **p):
        """Straight slot (obround) of overall length and width w centred at (x, y), its length along
        the face's first in-plane axis turned by angle degrees; through all, or depth deep."""
        if length < w:
            raise BuildError("a slot's length must be at least its width")
        p.update(x=x, y=y)
        self._slot(p, lambda cu, cv: {"slot": [cu, cv, length, w], "rotation": angle}, depth)

    def slot_arc(self, r, w, a1, a2, x=0.0, y=0.0, depth=None, **p):
        """Arc slot: centreline radius r about (x, y), width w, from a1 to a2 degrees counter-clockwise
        (from the face's first in-plane axis); through all, or depth deep."""
        if not 0 < a2 - a1 < 360:
            raise BuildError("an arc slot needs a1 < a2 < a1 + 360")
        if w >= 2 * r:
            raise BuildError("an arc slot's width must be smaller than 2 r")
        p.update(x=x, y=y)
        self._slot(p, lambda cu, cv: {"arc_slot": [cu, cv, r, w, a1, a2]}, depth)

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

    # -- loft and sweep (curved bodies) --
    def _add_or_base(self, tool, op: str):
        if self.part is None:
            if op != "add":
                raise BuildError("the first feature must add material")
            self._set(tool, tool, "add")
        else:
            self._combine(tool, op)

    @staticmethod
    def _lofted(sections, ruled, p):
        k, _ = normal_of(p)
        if len(sections) < 2:
            raise BuildError("a loft needs at least two sections")
        wires = [outline_face(sec["outline"], k, float(sec["off"])).outer_wire()
                 for sec in sorted(sections, key=lambda sec: float(sec["off"]))]
        try:
            return Solid.make_loft(wires, bool(ruled))
        except Exception as exc:  # noqa: BLE001
            raise BuildError(f"the loft failed in the kernel ({type(exc).__name__}): sections that twist "
                             "or cross, or very different outlines?") from exc

    def loft(self, sections, ruled=False, **p):
        self._add_or_base(self._lofted(sections, ruled, p), "add")

    def loft_cut(self, sections, ruled=False, **p):
        self._add_or_base(self._lofted(sections, ruled, p), "sub")

    @staticmethod
    def _swept(outline, path, p):
        k, _ = normal_of(p)
        face = outline_face(outline, k, float(p.get("off", 0.0)), p.get("holes"))
        try:
            s = Solid.sweep(face, path_wire(path), is_frenet=bool(p.get("frenet", False)))
        except Exception as exc:  # noqa: BLE001
            raise BuildError(f"the sweep failed in the kernel ({type(exc).__name__}): a path that turns "
                             "tighter than the profile, or a profile not at the path's start?") from exc
        if s.volume <= 0:
            raise BuildError("the sweep has no volume")
        return s

    def sweep(self, outline, path, **p):
        self._add_or_base(self._swept(outline, path, p), "add")

    def sweep_cut(self, outline, path, **p):
        self._add_or_base(self._swept(outline, path, p), "sub")

    # -- patterns and mirror --
    def _copies(self, transforms, features=1, all=False):  # noqa: A002 (the goal's key)
        """Copies of the last `features` features' tools (a pattern counts as one feature: its copies are
        repeated too), or with all: true of the whole part, combined with the part."""
        part = self._need_part()
        if all:
            group = [(single_solid(part), "add")]
        else:
            n = int(features)
            if not 1 <= n <= len(self.history):
                raise BuildError(f"features must be 1 to {len(self.history)} (the features before it)")
            entries = self.history[-n:]
            if any(e is None for e in entries):
                raise BuildError("patterns repeat bosses, holes, pockets, revolves, patterns and combines, not a fillet, "
                                 "chamfer, shell, draft, offset, intersect or split (or use all: true)")
            group = [m for e in entries for m in e]
        if not transforms:
            raise BuildError("every copy is skipped")
        out = []
        for tool, op in group:
            copies = [tf(tool) for tf in transforms]
            part = apply_tools(part, copies, op)
            out += [(tool, op)] + [(cp, op) for cp in copies]
        self._commit(part, out)

    @staticmethod
    def _skips(skip, grid: bool) -> set:
        """Copy numbers to leave out: 1-based, the original is 1 ([i, j] on a grid, the original [1, 1])."""
        out = set()
        for s in skip or []:
            key = tuple(int(v) for v in s) if grid else int(s)
            if key in ((1, 1), 1):
                raise BuildError("skip cannot remove the original (copy 1)")
            out.add(key)
        return out

    def polar_pattern(self, n, skip=None, features=1, all=False, **p):  # noqa: A002
        k, sign, given = axis_of(p, 2)
        ang = float(p.get("angle", 360.0)) if given else 360.0
        n = int(n)
        step = ang / n if abs(ang - 360.0) < 1e-9 else ang / (n - 1)
        ax = Axis((0, 0, 0), unit(k, sign))
        drop = self._skips(skip, False)
        self._copies([lambda t, i=i: t.rotate(ax, i * step) for i in range(1, n) if i + 1 not in drop], features, all)

    def linear_pattern(self, n, length, n2=None, length2=None, skip=None, features=1, all=False, **p):  # noqa: A002
        """n copies over `length` along (ax, ay, az) (default +X); with n2 and length2 also along the
        second direction (bx, by, bz) (default +Y, or +X when the first is not X): a grid."""
        k, sign, _ = axis_of(p, 0)
        n = int(n)
        step = float(length) / (n - 1)
        a = unit(k, sign) * step
        grid = n2 is not None
        if grid:
            if length2 is None:
                raise BuildError("a grid needs length2 with n2")
            vec = [float(p.get(key, 0.0)) for key in B_KEYS] if any(key in p for key in B_KEYS) else None
            if vec is None:
                b_dir = unit(1 if k == 0 else 0)
            else:
                kb = max(range(3), key=lambda i: abs(vec[i]))
                b_dir = unit(kb, -1.0 if vec[kb] < 0 else 1.0)
            if abs(b_dir.dot(unit(k))) > 0.5:
                raise BuildError("the grid's two directions must differ")
            n2 = int(n2)
            b = b_dir * (float(length2) / (n2 - 1))
        else:
            n2, b = 1, Vector(0, 0, 0)
        drop = self._skips(skip, grid)
        moves = [a * i + b * j for j in range(n2) for i in range(n)
                 if (i, j) != (0, 0) and ((i + 1, j + 1) if grid else i + 1) not in drop]
        self._copies([lambda t, m=m: t.moved(Location(m)) for m in moves], features, all)

    def mirror(self, features=1, all=False, **p):  # noqa: A002
        k, _, _ = axis_of(p, 0)
        self._copies([lambda t: t.mirror(Plane(origin=(0, 0, 0), z_dir=unit(k)))], features, all)

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

    @staticmethod
    def _chamfer(part, edges, size, size2, angle, ref):
        """Symmetric chamfer, or with size2 (or angle: size2 = size * tan(angle)) asymmetric: `size` on the
        face of each edge whose normal is closest to `ref`, size2 on the other face."""
        if size2 is None and angle is None:
            return part.chamfer(size, None, edges)
        if ref is None:
            raise BuildError("an asymmetric chamfer needs a reference face: select by normal (nx, ny, nz) or give ref")
        d2 = float(size2) if size2 is not None else float(size) * math.tan(math.radians(float(angle)))
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer

        mk = BRepFilletAPI_MakeChamfer(part.wrapped)
        faces = part.faces()
        for e in edges:
            adj = [f for f in faces if any(e.is_same(x) for x in f.edges())]
            fr = max(adj, key=lambda f: f.normal_at(e.center()).dot(ref))
            mk.Add(float(size), d2, e.wrapped, fr.wrapped)
        mk.Build()
        if not mk.IsDone():
            raise BuildError("the chamfer failed in the kernel: too large for the material around it?")
        return single_solid(Solid(mk.Shape()))

    def chamfer_top(self, size, size2=None, angle=None, **p):
        part, edges = self._edges({"at": p.get("at")} if "at" in p else {})
        self._dressup(lambda: self._chamfer(part, edges, size, size2, angle, unit(2)))

    def fillet_vertical(self, r, **_):
        part = single_solid(self._need_part())
        edges = straight_edges_along(part, 2)
        if not edges:
            raise BuildError("no vertical edges")
        self._dressup(lambda: part.fillet(r, edges))

    def fillet_edges(self, r, **p):
        part, edges = self._edges(p)
        self._dressup(lambda: part.fillet(r, edges))

    def chamfer_edges(self, size, size2=None, angle=None, ref=None, **p):
        part, edges = self._edges(p)
        if ref is not None:
            ref = Vector(*ref)
        elif not p.get("pts") and not any(k in p for k in AXIS_KEYS):
            ref = unit(*normal_of(p))  # selected by a face's normal: that face is the reference
        self._dressup(lambda: self._chamfer(part, edges, size, size2, angle, ref))

    def shell(self, t, faces_t=None, **p):
        """Hollows the part inward by t, open at the faces of pts (default: the top face); faces_t
        [{pt, t}, ...] gives other faces their own wall thickness."""
        part = single_solid(self._need_part())
        tol = max(0.05, 0.02 * self.scale)
        if p.get("pts"):
            faces = nearest(part.faces(), p["pts"], tol)
        else:
            face = pick_face(part, 2, 1.0, p.get("at"))
            if face is None:
                raise BuildError("no top face to open")
            faces = [face]
        if not faces_t:
            return self._dressup(lambda: single_solid(offset(part, amount=-t, openings=faces, kind=Kind.ARC)))

        def thick():
            from OCP.BRepOffset import BRepOffset_MakeOffset, BRepOffset_Skin
            from OCP.GeomAbs import GeomAbs_Arc

            mo = BRepOffset_MakeOffset()
            mo.Initialize(part.wrapped, -float(t), 1e-4, BRepOffset_Skin, False, False, GeomAbs_Arc, True)
            for f in faces:
                mo.AddFace(f.wrapped)
            for spec in faces_t:
                (f,) = nearest(part.faces(), [spec["pt"]], tol)
                if any(f.is_same(o) for o in faces):
                    raise BuildError(f"the face at {spec['pt']} is open (pts), it has no wall")
                mo.SetOffsetOnFace(f.wrapped, -float(spec["t"]))
            mo.MakeThickSolid()
            if not mo.IsDone():
                raise BuildError("the shell failed in the kernel: walls too thick for the part?")
            return single_solid(Solid(mo.Shape()))

        self._dressup(thick)

    def offset(self, t, **_):
        """Grows (t > 0) or shrinks (t < 0) the whole part by t, rounding convex edges."""
        part = single_solid(self._need_part())
        self._dressup(lambda: single_solid(offset(part, amount=float(t), kind=Kind.ARC)))

    def draft(self, angle, **p):
        """Tilts side faces by `angle` degrees about their line on the neutral plane (normal to the
        pull direction ax, ay, az, default +Z, at off along it, default 0): positive angles narrow
        the part along the pull direction, as cast or moulded parts. Faces: pts, or every planar
        and cylindrical face parallel to the pull direction."""
        part = single_solid(self._need_part())
        k, sign, _ = axis_of(p, 2)
        pull = unit(k, sign)
        if p.get("pts"):
            faces = nearest(part.faces(), p["pts"], max(0.05, 0.02 * self.scale))
        else:
            faces = [f for f in part.faces() if f.geom_type in (GeomType.PLANE, GeomType.CYLINDER)
                     and _parallel_face(f, pull)]
        if not faces:
            raise BuildError("no faces to draft")
        plane = Plane(origin=unit(k, float(p.get("off", 0.0))), z_dir=pull)
        self._dressup(lambda: single_solid(part.draft(faces, plane, float(angle))))

    # -- booleans with a tool, split --
    def profile_intersect(self, outline, depth=None, holes=None, **p):
        """Keeps the part inside the outline (on an origin plane, nx, ny, nz at off) extruded both
        ways: through all, or `depth` in total, symmetric."""
        k, _ = normal_of(p)
        L = self._through() if depth is None else float(depth)
        tool = prism(outline_face(outline, k, float(p.get("off", 0.0)), holes), k, -L / 2, L, 1.0)
        res = boolean(self._need_part(), tool, "common")
        if not res.solids():
            raise BuildError("the intersection is empty")
        self._set(res)

    def split(self, **p):
        """Cuts the part with the origin plane normal to (nx, ny, nz) at off and keeps the side the
        normal points to."""
        k, sign = normal_of(p)
        c = float(p.get("off", 0.0))
        L = self._through()
        lo = [-L, -L, -L]
        lo[k] = c if sign > 0 else c - L
        size = [2 * L, 2 * L, 2 * L]
        size[k] = L
        box = Solid.make_box(*size).moved(Location(tuple(lo)))
        res = boolean(self._need_part(), box, "common")
        if not res.solids():
            raise BuildError("nothing is left on that side of the plane")
        self._set(res)

    # -- surfaces made solid --
    def thicken(self, sections, t, ruled=False, **p):
        """A sheet of thickness t through open curves on parallel planes (as loft's sections, with
        'curve' for 'outline'): to the side of (ax, ay, az) if given, else along the surface's normal.
        As base or later feature (adds)."""
        k, _ = normal_of(p)
        if len(sections) < 2:
            raise BuildError("thicken needs at least two sections")
        from build123d import Shell

        wires = [outline_wire(sec["curve"], k, float(sec["off"]))
                 for sec in sorted(sections, key=lambda sec: float(sec["off"]))]
        side = None
        if any(a in p for a in AXIS_KEYS):
            a, sg, _ = axis_of(p, 2)
            side = unit(a, sg)
        try:
            surf = Shell.make_loft(wires, bool(ruled))
            faces = surf.faces()
            tool = Solid.thicken(faces[0] if len(faces) == 1 else surf, float(t), normal_override=side)
        except Exception as exc:  # noqa: BLE001
            raise BuildError(f"thicken failed in the kernel ({type(exc).__name__}): curves that cross, "
                             "or a thickness larger than the surface's curvature radius?") from exc
        if tool.volume <= 0:
            raise BuildError("the thickened sheet has no volume")
        self._add_or_base(tool, "add")

    # -- threads --
    def thread(self, r, pitch, length, x=0.0, y=0.0, z=0.0, internal=False, depth=None, **p):
        """Cuts an ISO-like 60-degree V thread: a helical groove into a shaft of radius r (external;
        r is the major radius) or into the wall of a bore of radius r (internal), from the point
        (x, y, z) on the axis along (ax, ay, az) (default +Z) for `length`. Right-handed for
        pitch > 0, left-handed for pitch < 0. depth: thread depth (default 0.6134 * pitch)."""
        k, sg, _ = axis_of(p, 2)
        tool = thread_tool(float(r), pitch, length, Vector(float(x), float(y), float(z)), unit(k, sg),
                           unit(inplane(k)[0]), bool(internal), depth)
        self._combine(tool, "sub")

    # -- text and projected sketches --
    def text(self, text, size, x=0.0, y=0.0, h=None, depth=None, font="Arial", font_path=None,
             rotation=0.0, **p):
        """Embossed (h) or engraved (depth) text of height `size`, centred at (x, y[, z]) on a face
        (the top face by default), reading from outside the face (up: +Y on top and bottom faces,
        +Z on side faces); rotation in degrees about the face's normal."""
        from build123d import Text

        if (h is None) == (depth is None):
            raise BuildError("text needs one of h (emboss) or depth (engrave)")
        k, sign, c, datum = self._placement(p)
        n = unit(k, sign if not datum else normal_of(p)[1])
        up = unit(1) if k == 2 else unit(2)
        frame = Plane(origin=point(k, c, *self._uv(p, k)), x_dir=up.cross(n), z_dir=n)
        if rotation:
            frame = frame.rotated((0, 0, float(rotation)))
        sk = Text(str(text), float(size), font=font, font_path=font_path)
        faces = [frame.location * f for f in sk.faces()]
        if not faces:
            raise BuildError("the text has no glyphs (empty text or missing font)")
        op = "add" if h is not None else "sub"
        L = float(h if h is not None else depth)
        from build123d import Compound

        if datum:
            solids = [Solid.extrude(f.moved(Location(n * (-L / 2))), n * L) for f in faces]
        else:
            solids = [Solid.extrude(f, n * (L if op == "add" else -L)) for f in faces]
        self._combine(Compound(solids), op)

    def profile_project(self, outline, h=None, depth=None, **p):
        """Projects the outline (on an origin plane, nx, ny, nz at off, beside the part) along
        -(nx, ny, nz) onto the faces of the part it meets first, then embosses it by h or engraves
        it by depth along the surface normal: labels, pads and grooves on curved faces."""
        if (h is None) == (depth is None):
            raise BuildError("profile_project needs one of h (emboss) or depth (engrave)")
        part = single_solid(self._need_part())
        k, sign = normal_of(p)
        n = unit(k, sign)
        face = outline_face(outline, k, float(p.get("off", 0.0)))
        try:
            hits = [f for s in face.project_to_shape(part, -n) for f in s.faces()]
        except Exception as exc:  # noqa: BLE001
            raise BuildError(f"the projection failed in the kernel ({type(exc).__name__})") from exc
        size = part.bounding_box().diagonal
        seen = [f for f in hits if _visible(part, f, n, size)]
        if not seen:
            raise BuildError("the outline does not meet the part (it projects along -normal)")
        op = "add" if h is not None else "sub"
        L = float(h if h is not None else depth)
        from build123d import Compound

        try:
            pieces = [Solid.thicken(f, L, normal_override=n if op == "add" else -n) for f in seen]
        except Exception as exc:  # noqa: BLE001
            raise BuildError(f"thickening the projected outline failed ({type(exc).__name__})") from exc
        self._combine(pieces[0] if len(pieces) == 1 else Compound(pieces), op)

    # -- other parts --
    def combine(self, op="add", goal=None, step=None, move=None, rotate=None):
        """Adds ('add'), removes ('sub') or keeps the common volume ('common') of another part: a goal
        {features} or a STEP file, turned by rotate [rx, ry, rz] (degrees about X, then Y, then Z, about
        the origin), then moved by move [dx, dy, dz]. As the first feature (op 'add') it is the base."""
        if (goal is None) == (step is None):
            raise BuildError("combine needs one of goal or step")
        if isinstance(goal, str):
            raise BuildError(f"goal {goal!r} by name: build it together with that goal, or give the goal itself")
        if op not in ("add", "sub", "common"):
            raise BuildError("op must be add, sub or common")
        if goal is not None:
            tool, _ = build_goal(goal)
        else:
            from build123d import import_step

            try:
                tool = single_solid(import_step(str(step)))
            except BuildError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise BuildError(f"cannot read the STEP file {step!r} ({type(exc).__name__})") from exc
        for ang, ax in zip(rotate or (), (Axis.X, Axis.Y, Axis.Z)):
            if ang:
                tool = tool.rotate(ax, float(ang))
        if move:
            tool = tool.moved(Location(tuple(float(v) for v in move)))
        if self.part is None:
            if op != "add":
                raise BuildError("as the first feature, combine must add (op 'add')")
            return self._set(tool, tool, "add")
        if op == "common":
            res = boolean(self._need_part(), tool, "common")
            if not res.solids():
                raise BuildError("the common volume is empty")
            return self._set(res)
        self._combine(tool, op)

    # -- driver --
    def add(self, kind: str, params: dict):
        fn = getattr(self, kind, None)
        if fn is None or kind not in KINDS:
            raise BuildError(f"unknown kind {kind!r}")
        if is_angled(params) and kind not in ANGLED_KINDS:
            raise BuildError(f"only {', '.join(ANGLED_KINDS)} can be on an angled plane")
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
    if f["kind"] == "base_sphere":
        return 2 * p["r"]
    if f["kind"] == "base_cone":
        return max(2 * p["r1"], 2 * p.get("r2", 0), p["h"])
    if f["kind"] == "base_torus":
        return 2 * (p["R"] + p["r"])
    if f["kind"] == "base_wedge":
        return max(p["w"], p["d"], p["h"])
    if f["kind"] == "base_polygon":
        return max(2 * p["r"], p["h"])
    if f["kind"] == "combine":
        return 0.0  # sized from the part once built (Builder.add)
    if f["kind"] == "loft":
        offs = [float(sec["off"]) for sec in p["sections"]]
        exts = [goal_scale([{"kind": "profile_base", "params": {"outline": sec["outline"], "h": 0}}]) for sec in p["sections"]]
        return max(max(exts), max(offs) - min(offs))
    if f["kind"] == "sweep":
        sz = path_wire(p["path"]).bounding_box().size
        return max(sz.X, sz.Y, sz.Z, goal_scale([{"kind": "profile_base", "params": {"outline": p["outline"], "h": 0}}]))
    if f["kind"] == "thicken":
        k, _ = normal_of(p)
        boxes = [outline_wire(sec["curve"], k, float(sec["off"])).bounding_box() for sec in p["sections"]]
        span = [max(b.max.X for b in boxes) - min(b.min.X for b in boxes), max(b.max.Y for b in boxes) - min(b.min.Y for b in boxes),
                max(b.max.Z for b in boxes) - min(b.min.Z for b in boxes)]
        return max(span)
    o = expand_outline(p["outline"])
    pts = [o["start"]]
    for s in o["segs"]:
        pts += s[1] if s[0] == "S" else [s[-2:]]
    us, vs = [q[0] for q in pts], [q[1] for q in pts]
    ext = max(max(us) - min(us), max(vs) - min(vs))
    return max(ext, p["h"]) if f["kind"] == "profile_base" else ext


def build_goal(goal: dict, builder: Builder | None = None):
    """The solid of a goal {"features": [...]}, plus seconds per feature. builder: one of your own (e.g. with
    keep_parts=True), built in place."""
    import time

    feats = goal["features"]
    b = builder if builder is not None else Builder(scale=float(goal.get("scale") or goal_scale(feats)))
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
         "chamfer_top", "shell", "fillet_edges", "chamfer_edges", "loft", "loft_cut", "sweep", "sweep_cut",
         "hole_cb", "hole_cs", "draft", "offset", "profile_intersect", "split", "thicken", "thread", "text",
         "profile_project", "base_sphere", "base_cone", "base_torus", "base_wedge", "base_polygon", "slot", "slot_arc",
         "combine")
