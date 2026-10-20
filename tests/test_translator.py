"""Tests: every reference goal matches the FreeCAD reference builder (volume and bounding box),
scripts rebuild the same part, validation catches bad goals, the API writes files.
Version: 2026.10.20.1

The references (tests/freecad_reference.json) were measured with the FreeCAD 1.1.3 reference
builder of the goal format: probes for each convention (shell inward, hexagon corner on +X,
right-handed revolves and patterns, polar angle only with an axis, linear spacing, fillets
skipping seams) and the S 80 pump parts other than the casing.
"""
import json
import math
from pathlib import Path

import pytest

from build123d_translator import api, translate as T
from build123d_translator.goalformat import validate_goal

REF = json.loads((Path(__file__).parent / "freecad_reference.json").read_text())


@pytest.mark.parametrize("name", sorted(REF))
def test_matches_freecad(name):
    solid, _ = T.build(REF[name])
    fc = REF[name]["freecad"]
    assert abs(solid.volume - fc["volume"]) <= max(1.0, 1e-4 * fc["volume"]), (solid.volume, fc["volume"])
    bb = solid.bounding_box().size
    for got, want in zip((bb.X, bb.Y, bb.Z), fc["bbox"]):
        assert abs(got - want) < 0.06, (name, (bb.X, bb.Y, bb.Z), fc["bbox"])
    assert solid.is_valid


@pytest.mark.parametrize("name", ["motor_flange", "cover_plate", "s80_bearing_bracket", "s80_impeller"])
def test_script_rebuilds_the_part(name, tmp_path):
    src = T.script(name, REF[name])
    ns = {"__name__": "not_main"}
    exec(compile(src, f"{name}.py", "exec"), ns)  # noqa: S102 (our own generated code)
    solid, _ = T.build(REF[name])
    assert T.iou(ns["part"], solid) > 0.999999


def test_validation_catches_errors():
    bad = {"features": [{"kind": "hole", "params": {"r": 3, "x": 0, "y": 0}},
                        {"kind": "boss_cyl", "params": {"r": -1, "x": 0, "h": 2}},
                        {"kind": "mirror", "params": {}}, {"kind": "linear_pattern", "params": {"n": 2, "length": 5, "features": 9}}]}
    errs = " ".join(validate_goal(bad)["errors"])
    for text in ("must be a base", "missing 'y'", "'r' must be positive", "'features' must be a whole number from 1 to 3"):
        assert text in errs


def test_build_errors_are_reported():
    g = {"features": [{"kind": "base_box", "params": {"w": 10, "d": 10, "h": 2}},
                      {"kind": "fillet_vertical", "params": {"r": 8}}]}
    with pytest.raises(Exception) as e:
        T.build(g)
    assert "feature 2" in str(e.value)


def test_api_build_writes_files(tmp_path, monkeypatch):
    monkeypatch.setenv("B3DT_OUT", str(tmp_path))
    goals = {"flange": {"features": REF["motor_flange"]["features"]},
             "broken": {"features": [{"kind": "hole", "params": {"r": 1, "x": 0, "y": 0}}]}}
    r = api.build(goals, formats=("step", "stl"))
    f = r["parts"]["flange"]
    assert f["built"] and Path(f["files"]["step"]).is_file() and Path(f["files"]["script"]).is_file()
    assert abs(f["volume"] - REF["motor_flange"]["freecad"]["volume"]) < 1
    assert r["parts"]["broken"]["built"] is False
    v = api.verify(goals["flange"], f["files"]["step"])
    assert v["iou"] > 0.99999


# ---- loft and sweep (2026.10.16.1): exact volumes

def _sq(c, a):  # square of side a centred at (c, c) in the plane's (u, v)
    h = a / 2
    return {"start": [c - h, c - h], "segs": [["L", c + h, c - h], ["L", c + h, c + h], ["L", c - h, c + h], ["L", c - h, c - h]]}


def _circ(cu, cv, r):
    return {"start": [cu + r, cv], "segs": [["A", cu, cv + r, cu - r, cv], ["A", cu, cv - r, cu + r, cv]]}


def test_ruled_loft_is_a_frustum():
    a, b, h = 40.0, 20.0, 30.0
    g = {"features": [{"kind": "loft", "params": {"nz": 1, "ruled": True, "sections": [
        {"off": 0, "outline": _sq(0, a)}, {"off": h, "outline": _sq(0, b)}]}}]}
    solid, _ = T.build(g)
    assert abs(solid.volume - h / 3 * (a * a + b * b + a * b)) < 1e-6 * solid.volume


def test_sweep_along_quarter_arc_is_a_quarter_torus():
    R, r = 50.0, 5.0  # circle of radius r in the XZ plane at (R, 0, 0), swept a quarter turn about Z
    g = {"features": [{"kind": "sweep", "params": {
        "nx": 0, "ny": 1, "nz": 0, "off": 0, "outline": _circ(R, 0, r),
        "path": {"start": [R, 0, 0], "segs": [["A", R * 0.7071067811865476, R * 0.7071067811865476, 0, 0, R, 0]]}}}]}
    solid, _ = T.build(g)
    assert abs(solid.volume - math.pi * r * r * (math.pi * R / 2)) < 1e-4 * solid.volume


def test_loft_cut_and_validation():
    g = {"features": [{"kind": "base_box", "params": {"w": 60, "d": 60, "h": 20}},
                      {"kind": "loft_cut", "params": {"nz": 1, "ruled": True, "sections": [
                          {"off": 5, "outline": _sq(0, 10)}, {"off": 25, "outline": _sq(0, 30)}]}}]}
    solid, _ = T.build(g)
    side = lambda z: 10 + (z - 5)  # side length grows 1 per mm from z=5 (10) to z=25 (30)
    cut = sum(side(5 + (i + 0.5) * 15 / 10000) ** 2 * 15 / 10000 for i in range(10000))  # z 5..20 inside the box
    assert abs(solid.volume - (72000 - cut)) < 1e-3 * 72000
    bad = {"features": [{"kind": "loft", "params": {"nz": 1, "sections": [{"off": 0, "outline": _sq(0, 5)}]}}]}
    assert "at least 2" in " ".join(validate_goal(bad)["errors"])


def test_car_body_example_and_script():
    goal = json.loads((Path(__file__).parents[1] / "examples" / "car_body.json").read_text())["car_body"]
    assert validate_goal(goal)["ok"]
    solid, _ = T.build(goal)
    assert solid.is_valid and 5.9e9 < solid.volume < 6.1e9
    ns = {"__name__": "not_main"}
    exec(compile(T.script("car_body", goal), "car_body.py", "exec"), ns)  # noqa: S102
    assert T.iou(ns["part"], solid) > 0.999999


# ---- 2026.10.16.2: taper, draft, until, blind/counterbored/countersunk holes, intersect, split, offset,
# thicken, helix paths, threads, text, projection: exact volumes

PI = math.pi
TAN = lambda deg: math.tan(math.radians(deg))  # noqa: E731


def _box(w, d, h):
    return {"kind": "base_box", "params": {"w": w, "d": d, "h": h}}


def _f(kind, **p):
    return {"kind": kind, "params": p}


def _window(z):  # a 30 x 4 window through Y at height z in a 40 x 20 block
    return _f("pocket_rect", nx=0, ny=1, nz=0, off=0, w=30, d=4, x=0, y=0, z=z, depth=20)


def _frustum(h, a1, a2):
    return h / 3 * (a1 + a2 + math.sqrt(a1 * a2))


R1 = 10 - 10 * TAN(10)
CS = 2 / TAN(45)  # countersink height: csr 5 to r 3 at 90 degrees
P, TH = 2.0, 0.6134 * 2.0  # thread pitch and depth
EXACT = {
    "taper_boss": ([_box(40, 40, 10), _f("boss_cyl", r=10, x=0, y=0, h=10, taper=10)],
                   16000 + PI * _frustum(10, 100, R1 * R1)),
    "taper_pocket": ([_box(40, 40, 10), _f("pocket_rect", w=20, d=20, x=0, y=0, depth=5, taper=5)],
                     16000 - _frustum(5, 400, (20 - 10 * TAN(5)) ** 2)),
    "draft": ([_box(20, 20, 20), _f("draft", angle=5)], _frustum(20, 400, (20 - 40 * TAN(5)) ** 2)),
    "pocket_next": ([_box(40, 20, 30), _window(10), _window(20), _f("profile_pocket", outline=_circ(0, 0, 3), until="next")],
                    19200 - PI * 9 * 8),
    "pocket_last": ([_box(40, 20, 30), _window(10), _window(20), _f("profile_pocket", outline=_circ(0, 0, 3), until="last")],
                    19200 - PI * 9 * 22),
    "boss_next": ([_box(40, 20, 30), _window(10), _window(20), _f("boss_cyl", r=3, x=0, y=0, at=[0, 0, 8], until="next")],
                  19200 + PI * 9 * 4),
    "boss_last": ([_box(40, 20, 30), _window(10), _window(20), _f("boss_cyl", r=3, x=0, y=0, at=[0, 0, 8], until="last")],
                  19200 + PI * 9 * 8),
    "blind_hole": ([_box(40, 40, 20), _f("hole", r=3, x=0, y=0, depth=10)], 32000 - PI * 9 * 10),
    "drilled_hole": ([_box(40, 40, 20), _f("hole", r=3, x=0, y=0, depth=10, tip=118)],
                     32000 - PI * 9 * 10 - PI * 9 * (3 / TAN(59)) / 3),
    "counterbore": ([_box(40, 40, 20), _f("hole_cb", r=3, cbr=5, cbd=4, x=0, y=0)], 32000 - PI * 9 * 16 - PI * 25 * 4),
    "countersink_side": ([_box(40, 40, 20), _f("hole_cs", r=3, csr=5, nx=1, ny=0, nz=0, x=20, y=0, z=10, depth=15)],
                         32000 - PI * 9 * (15 - CS) - PI * _frustum(CS, 25, 9)),
    "intersect": ([_box(40, 40, 40), _f("profile_intersect", nx=0, ny=0, nz=1, outline=_circ(0, 0, 10))], PI * 100 * 40),
    "split": ([_box(40, 40, 40), _f("split", nx=0, ny=0, nz=-1, off=10)], 16000),
    "offset": ([_box(20, 20, 20), _f("offset", t=2)], 8000 + 6 * 400 * 2 + 12 * 20 * PI + 4 / 3 * PI * 8),
    "thicken": ([_f("thicken", nx=0, ny=0, nz=1, t=2, ay=1, sections=[
        {"off": 0, "curve": {"start": [-20, 0], "segs": [["L", 20, 0]]}},
        {"off": 30, "curve": {"start": [-20, 0], "segs": [["L", 20, 0]]}}])], 2400),
    # tube r 1 along 4 turns of a helix (R 10, pitch 5); the profile on XZ is tilted to the path by the lead angle
    "helix_sweep": ([_f("sweep", nx=0, ny=1, nz=0, off=0, frenet=True, outline=_circ(10, 0, 1),
                        path={"start": [10, 0, 0], "segs": [["H", 0, 0, 0, 0, 0, 1, 5, 20]]})],
                    PI * 4 * 2 * PI * 10),
    # Pappus on a helical groove: (area inside the shaft) x (2 pi x centroid radius) x turns
    "thread_external": ([_f("base_cyl", r=10, h=30), _f("thread", r=10, pitch=P, length=20, z=5)],
                        PI * 100 * 30 - TH * TH * TAN(30) * 2 * PI * (10 - TH / 3) * 10),
    "thread_internal_lh": ([_box(40, 40, 30), _f("hole", r=8, x=0, y=0), _f("thread", r=8, pitch=-P, length=20, z=5, internal=True)],
                           48000 - PI * 64 * 30 - TH * TH * TAN(30) * 2 * PI * (8 + TH / 3) * 10),
    # a 6 x 10 window projected on a cylinder r 10: patch area 10 x 2r asin(0.3); shell volume A t (1 +- t / 2r)
    "project_emboss": ([_f("base_cyl", r=10, h=40), _f("profile_project", nx=1, ny=0, nz=0, off=30, h=1, outline={
        "start": [-3, 15], "segs": [["L", 3, 15], ["L", 3, 25], ["L", -3, 25], ["L", -3, 15]]})],
                       PI * 100 * 40 + 200 * math.asin(0.3) * (1 + 1 / 20)),
}


@pytest.mark.parametrize("name", sorted(EXACT))
def test_new_features_exact(name):
    feats, want = EXACT[name]
    assert validate_goal({"features": feats})["ok"], validate_goal({"features": feats})
    solid, _ = T.build({"features": feats})
    tol = 3.2e-3 if name == "helix_sweep" else 1e-5  # helix: cos(lead angle) = 0.99685
    assert solid.is_valid and abs(solid.volume - want) <= tol * want, (solid.volume, want)


def test_text_and_engrave_on_curved_face():
    plate = [_box(60, 20, 5), _f("text", text="B3D", size=10, x=0, y=0, h=1)]
    side = [_box(60, 20, 20), _f("text", text="B3D", size=10, nx=0, ny=-1, nz=0, x=0, y=-10, z=10, depth=1)]
    a, _ = T.build({"features": plate})
    b, _ = T.build({"features": side})
    glyphs = a.volume - 6000
    assert a.is_valid and b.is_valid and glyphs > 30 and abs((24000 - b.volume) - glyphs) < 1e-6 * 24000
    cyl = [_f("base_cyl", r=10, h=40), _f("profile_project", nx=1, ny=0, nz=0, off=30, depth=1, outline={
        "start": [-3, 15], "segs": [["L", 3, 15], ["L", 3, 25], ["L", -3, 25], ["L", -3, 15]]})]
    c, _ = T.build({"features": cyl})
    assert abs(PI * 4000 - c.volume - 200 * math.asin(0.3) * (1 - 1 / 20)) < 1e-6 * c.volume


def test_new_features_scripts_and_patterns():
    feats = [_box(60, 40, 10), _f("hole_cb", r=3, cbr=5, cbd=3, x=20, y=0), _f("polar_pattern", n=3),
             _f("boss_cyl", r=8, x=0, y=0, h=12, taper=3), _f("thread", r=8, pitch=1.5, length=8, z=12),
             _f("draft", angle=2, pts=[[30, 0, 5]]), _f("split", nx=0, ny=1, nz=0, off=-15)]
    goal = {"features": feats}
    assert validate_goal(goal)["ok"]
    solid, _ = T.build(goal)
    ns = {"__name__": "not_main"}
    exec(compile(T.script("mix", goal), "mix.py", "exec"), ns)  # noqa: S102
    assert solid.is_valid and T.iou(ns["part"], solid) > 0.999999


def test_new_features_validation():
    bad = {"features": [_box(10, 10, 10), _f("boss_cyl", r=1, x=0, y=0, until="up"),
                        _f("hole", r=1, x=0, y=0, tip=118), _f("hole_cb", r=2, cbr=1, cbd=1, x=0, y=0, nx=0, ny=0, nz=1, off=0),
                        _f("text", text="", size=3, x=0, y=0), _f("draft", angle=3), _f("mirror")]}
    errs = " ".join(validate_goal(bad)["errors"])
    for text in ("'next' or 'last'", "'tip' needs 'depth'", "not an origin plane", "'cbr' must exceed",
                 "one of 'h'", "non-empty string", "not a draft"):
        assert text in errs, text


# ---- 2026.10.17.1: angled planes (Taiga patch 0020): exact volumes, validation, scripts

COS30, SIN30 = math.cos(math.radians(30)), math.sin(math.radians(30))
BLOCK = {"kind": "base_box", "params": {"w": 60, "d": 40, "h": 30}}
V0 = 60 * 40 * 30
TILT = {"x": 0, "y": 0, "z": 15, "nx": 0, "ny": -SIN30, "nz": COS30, "ux": 1, "uy": 0, "uz": 0}  # 30 deg from Z about X


def _vol(*feats):
    solid, _ = T.build({"features": [BLOCK, *feats]})
    assert solid.is_valid
    return solid.volume


def test_angled_through_hole_and_pin():
    r = 3.0
    # leaves through top and bottom: horizontal sections are ellipses of area pi r^2 / cos
    assert abs(_vol(_f("hole", r=r, **TILT)) - (V0 - PI * r * r * 30 / COS30)) < 1e-6 * V0
    # a pin longer than the part, sticking out on both sides
    assert abs(_vol(_f("boss_cyl", r=r, h=200, **TILT)) - (V0 + PI * r * r * (200 - 30 / COS30))) < 1e-6 * V0
    # u not perpendicular to n is projected: the same bore
    skew = dict(TILT, ux=1.0, uy=0.3, uz=0.2)
    assert abs(_vol(_f("hole", r=r, **skew)) - _vol(_f("hole", r=r, **TILT))) < 1e-6


def test_angled_pockets_and_bosses():
    n = {"nx": 1, "ny": -2, "nz": 4}  # any non-zero normal, not unit
    u = {"ux": 2, "uy": 1, "uz": 0}   # perpendicular to n
    c = {"x": 5, "y": -3, "z": 15}
    assert abs(_vol(_f("pocket_rect", w=10, d=6, depth=8, **n, **u, **c)) - (V0 - 480)) < 1e-6 * V0
    tri = {"start": [0, 0], "segs": [["L", 8, 0], ["L", 0, 6], ["L", 0, 0]]}  # in (u, v) about the origin
    assert abs(_vol(_f("profile_pocket", outline=tri, depth=5, **n, **u, **c)) - (V0 - 120)) < 1e-6 * V0
    # pocket_rect without depth: through all, both ways (6 x 4 window tilted 30 deg)
    assert abs(_vol(_f("pocket_rect", w=6, d=4, **TILT)) - (V0 - 6 * 4 * 30 / COS30)) < 1e-6 * V0
    # boss_box and profile_boss sticking out both ways
    sq = {"start": [-2, -2], "segs": [["L", 2, -2], ["L", 2, 2], ["L", -2, 2], ["L", -2, -2]]}
    assert abs(_vol(_f("boss_box", w=4, d=4, h=200, **TILT)) - (V0 + 16 * (200 - 30 / COS30))) < 1e-6 * V0
    assert abs(_vol(_f("profile_boss", outline=sq, h=200, **TILT)) - (V0 + 16 * (200 - 30 / COS30))) < 1e-6 * V0


def test_angled_validation():
    def errs(kind, **p):
        return " ".join(validate_goal({"features": [BLOCK, _f(kind, **p)]})["errors"])

    assert validate_goal({"features": [BLOCK, _f("hole", r=3, **TILT)]})["ok"]
    assert validate_goal({"features": [BLOCK, _f("pocket_rect", w=6, d=4, **TILT)]})["ok"]  # depth optional
    assert "angled plane: zero normal" in errs("hole", r=3, **dict(TILT, nx=0, ny=0, nz=0))
    assert "angled plane: u is parallel to the normal" in errs("hole", r=3, **dict(TILT, ux=0, uy=-SIN30, uz=COS30))
    assert "not 'off'" in errs("hole", r=3, off=15, **TILT)
    assert "can be on an angled plane" in errs("fillet_edges", r=1, **{k: TILT[k] for k in ("ux", "uy", "uz")})
    for key, val in (("until", "next"), ("taper", 5), ("at", [0, 0, 30])):
        assert "angled plane" in errs("boss_cyl", r=3, h=10, **{key: val}, **TILT), key
    assert "tip" in errs("hole", r=3, depth=10, tip=118, **TILT)
    assert "all of ux, uy, uz" in errs("hole", r=3, ux=1, **{k: TILT[k] for k in ("x", "y", "z", "nx", "ny", "nz")})
    fmt = T.__dict__.get("goal_format") or __import__("build123d_translator.goalformat", fromlist=["x"]).goal_format()
    assert fmt["angled_planes"] and "ux" in fmt["kinds"]["hole"]["optional"]
    with pytest.raises(Exception) as e:
        T.build({"features": [BLOCK, _f("hole", r=3, **dict(TILT, ux=0, uy=-SIN30, uz=COS30))]})
    assert "u is parallel to the normal" in str(e.value)


def test_angled_script_round_trip():
    n = {"nx": 1, "ny": -2, "nz": 4}
    u = {"ux": 2, "uy": 1, "uz": 0}
    goal = {"features": [BLOCK, _f("hole", r=3, **TILT), _f("polar_pattern", n=3),
                         _f("pocket_rect", w=10, d=6, depth=8, x=5, y=-3, z=15, **n, **u),
                         _f("boss_cyl", r=2, h=120, x=-20, y=0, z=15, nx=1, ny=0, nz=1, ux=0, uy=1, uz=0)]}
    assert validate_goal(goal)["ok"]
    solid, _ = T.build(goal)
    ns = {"__name__": "not_main"}
    exec(compile(T.script("angled", goal), "angled.py", "exec"), ns)  # noqa: S102
    assert solid.is_valid and T.iou(ns["part"], solid) > 0.999999


# ---- 2026.10.20.1: grids, patterns of several features or of the whole part, skipped copies, outline holes and
# shorthands, ellipse arcs, sphere / cone / torus / wedge / polygon, slots, ISO screw holes, rounded rectangles,
# asymmetric chamfers, per-face shells, combine: exact volumes

def _hole(r, x, y, **p):
    return _f("hole", r=r, x=x, y=y, **p)


HOLE = PI * 4 * 10   # r 2 through a 10 mm plate
M5 = 2.75            # M5 normal clearance radius
V20 = {
    "grid_4x3": ([_box(100, 60, 10), _hole(2, -45, -20), _f("linear_pattern", n=4, length=90, n2=3, length2=40)], 60000 - 12 * HOLE),
    "grid_skip": ([_box(100, 60, 10), _hole(2, -45, -20), _f("linear_pattern", n=4, length=90, n2=3, length2=40, skip=[[2, 2], [4, 3]])],
                  60000 - 10 * HOLE),
    "grid_dirs": ([_box(60, 10, 60), _f("hole", r=2, nx=0, ny=1, nz=0, x=-20, y=5, z=10), _f("linear_pattern", n=3, length=40, n2=2,
                                                                                          length2=40, bx=0, by=0, bz=1)],
                  36000 - 6 * HOLE),
    "polar_skip": ([_box(100, 100, 10), _hole(2, 30, 0), _f("polar_pattern", n=6, skip=[2, 4])], 100000 - 4 * HOLE),
    # four holes about Z, then that pattern repeated 40 mm along X: 8 holes
    "pattern_of_pattern": ([_box(120, 60, 10), _hole(2, -10, 0), _f("polar_pattern", n=4, ax=0, ay=0, az=1, angle=270),
                            _f("linear_pattern", n=2, length=40)], 72000 - 8 * HOLE),
    # a boss with its hole (at the boss top), six times
    "two_features": ([_box(100, 100, 10), _f("boss_cyl", r=6, x=30, y=0, h=5), _hole(2, 30, 0, at=[30, 0, 15]),
                      _f("polar_pattern", n=6, features=2)], 100000 + 6 * PI * 36 * 5 - 6 * PI * 4 * 15),
    "mirror_all": ([_box(20, 20, 10), _f("boss_box", w=5, d=5, x=7.5, y=7.5, h=5), _f("mirror", all=True, ax=1, ay=0, az=0)],
                   4000 + 2 * 125),
    "linear_all": ([_box(20, 20, 10), _f("linear_pattern", n=3, length=40, all=True)], 12000),
    "outline_holes": ([_f("profile_base", h=10, outline={"rect": [0, 0, 60, 40], "corner_r": 5},
                          holes=[{"circle": [-15, 0, 5]}, {"polygon": [15, 0, 6, 6]}])],
                      (2400 - (4 - PI) * 25 - PI * 25 - 1.5 * math.sqrt(3) * 36) * 10),
    "pocket_ring": ([_box(60, 40, 10), _f("profile_pocket", depth=4, outline={"circle": [0, 0, 15]}, holes=[{"circle": [0, 0, 5]}])],
                    24000 - PI * 200 * 4),
    "tube_sweep": ([_f("sweep", nx=0, ny=0, nz=1, off=0, outline={"circle": [0, 0, 5]}, holes=[{"circle": [0, 0, 3]}],
                       path={"start": [0, 0, 0], "segs": [["L", 0, 0, 50]]})], PI * 16 * 50),
    "ellipse": ([_f("profile_base", h=2, outline={"ellipse": [3, 1, 10, 4], "rotation": 20})], PI * 40 * 2),
    "ellipse_tall": ([_f("profile_base", h=2, outline={"ellipse": [0, 0, 4, 10]})], PI * 40 * 2),
    "ellipse_segment": ([_f("profile_base", h=1, outline={"start": [10, 0], "segs": [["E", 0, 0, 10, 5, 0, -10, 0], ["L", 10, 0]]})],
                        PI * 25),
    "sphere": ([_f("base_sphere", r=10)], 4 / 3 * PI * 1000),
    "cone": ([_f("base_cone", r1=10, r2=5, h=12)], PI * _frustum(12, 100, 25)),
    "cone_point": ([_f("base_cone", r1=10, h=12)], PI * 100 * 4),
    "torus": ([_f("base_torus", R=20, r=4)], 2 * PI * PI * 20 * 16),
    "wedge": ([_f("base_wedge", w=30, d=10, h=20)], 3000),
    "wedge_top": ([_f("base_wedge", w=30, d=10, h=20, top=10)], 4000),
    "pentagon": ([_f("base_polygon", n=5, r=10, h=3)], 2.5 * 100 * math.sin(2 * PI / 5) * 3),
    "slot": ([_box(60, 40, 10), _f("slot", length=30, w=8, x=0, y=0, angle=30)], 24000 - ((30 - 8) * 8 + PI * 16) * 10),
    "slot_side": ([_box(60, 40, 20), _f("slot", length=20, w=6, nx=0, ny=-1, nz=0, x=0, y=-20, z=10, depth=5)],
                  48000 - (14 * 6 + PI * 9) * 5),
    "slot_arc": ([_box(60, 60, 10), _f("slot_arc", r=20, w=6, a1=-30, a2=60, x=0, y=0, depth=5)],
                 36000 - (PI / 2 * 20 * 6 + PI * 9) * 5),
    "rounded_pocket": ([_box(60, 40, 10), _f("pocket_rect", w=30, d=20, x=0, y=0, depth=5, corner_r=4)],
                       24000 - (600 - (4 - PI) * 16) * 5),
    "rounded_boss": ([_box(60, 40, 10), _f("boss_box", w=30, d=20, x=0, y=0, h=5, corner_r=10)], 24000 + (600 - (4 - PI) * 100) * 5),
    "hole_std": ([_box(40, 40, 10), _f("hole_std", screw="M5", x=0, y=0)], 16000 - PI * M5 ** 2 * 10),
    "hole_std_loose_blind": ([_box(40, 40, 10), _f("hole_std", screw="M8", fit="loose", x=0, y=0, depth=6)], 16000 - PI * 25 * 6),
    "hole_std_socket": ([_box(40, 40, 10), _f("hole_std", screw="M5", x=0, y=0, head="socket", fit="close")],
                        16000 - PI * 2.65 ** 2 * (10 - 5.4) - PI * 25 * 5.4),
    "hole_std_countersunk": ([_box(40, 40, 10), _f("hole_std", screw="M5", x=0, y=0, head="countersunk")],
                             16000 - PI * M5 ** 2 * (10 - (5.6 - M5)) - PI * _frustum(5.6 - M5, 5.6 ** 2, M5 ** 2)),
    "hole_std_tapped": ([_box(40, 40, 10), _f("hole_std", screw="M5", x=0, y=0, tapped=True, depth=8)], 16000 - PI * 2.1 ** 2 * 8),
    "hole_std_side": ([_box(40, 40, 20), _f("hole_std", screw="M6", nx=1, ny=0, nz=0, x=20, y=0, z=10, depth=12)],
                      32000 - PI * 3.3 ** 2 * 12),
    "chamfer_asym": ([_box(20, 20, 20), _f("chamfer_top", size=2, size2=4)], 8000 - 4 * 0.5 * 2 * 4 * 20 + 4 / 3 * 2 * 2 * 4),
    "chamfer_angle": ([_box(20, 20, 20), _f("chamfer_edges", size=2, angle=60, nx=0, ny=0, nz=1)],
                      8000 - 4 * 0.5 * 2 * 2 * TAN(60) * 20 + 4 / 3 * 4 * 2 * TAN(60)),
    "chamfer_ref": ([_box(20, 20, 20), _f("chamfer_edges", size=1, size2=3, pts=[[10, 0, 20]], ref=[1, 0, 0])], 8000 - 1.5 * 20),
    "shell_walls": ([_box(20, 20, 20), _f("shell", t=1, faces_t=[{"pt": [-10, 0, 10], "t": 3}])], 8000 - 16 * 18 * 19),
    "combine_goal": ([_box(40, 40, 10), _f("combine", op="sub", goal={"features": [_f("base_cyl", r=5, h=10)]}, move=[10, 0, 0])],
                     16000 - PI * 250),
    "combine_base": ([_f("combine", goal={"features": [_box(10, 10, 10)]}, rotate=[0, 0, 45]), _hole(1, 0, 0)], 1000 - PI * 10),
    "combine_common": ([_box(40, 40, 40), _f("combine", op="common", goal={"features": [_f("base_cyl", r=10, h=50)]},
                                             rotate=[90, 0, 0], move=[0, 25, 20])], PI * 100 * 40),
}


@pytest.mark.parametrize("name", sorted(V20))
def test_20_features_exact(name):
    feats, want = V20[name]
    v = validate_goal({"features": feats})
    assert v["ok"], v
    solid, _ = T.build({"features": feats})
    assert solid.is_valid
    assert abs(solid.volume - want) <= 1e-6 * want, (solid.volume, want)


def test_tapped_thread_and_its_pattern():
    plate = [_box(40, 40, 20), _f("hole_std", screw="M5", x=0, y=0, tapped=True, depth=10)]
    a, _ = T.build({"features": plate})
    b, _ = T.build({"features": [_box(40, 40, 20), _f("hole_std", screw="M5", x=0, y=0, tapped=True, thread=True, depth=10)]})
    h, rc = 0.4, 2.1 + 0.4 / 3  # groove: (D - tap) / 2 deep, a 60-degree V; Pappus over 12.5 turns
    groove = h * h * TAN(30) * 2 * PI * rc * 10 / 0.8
    assert b.is_valid and abs((a.volume - b.volume) - groove) < 0.06 * groove
    four = [_box(60, 60, 10), _f("hole_std", screw="M4", x=15, y=15, tapped=True, thread=True), _f("polar_pattern", n=4)]
    c, _ = T.build({"features": four})
    one, _ = T.build({"features": four[:2]})
    removed = 4 * (36000 - one.volume)   # rotated helical faces: the kernel's approximation, ~2e-4
    assert c.is_valid and abs((36000 - c.volume) - removed) < 1e-3 * removed


def test_combine_by_name_and_step(tmp_path, monkeypatch):
    monkeypatch.setenv("B3DT_OUT", str(tmp_path))
    pin = {"features": [_f("base_cyl", r=4, h=30)]}
    goals = {"pin": pin, "plate": {"features": [_box(40, 40, 10), _f("combine", op="sub", goal="pin", move=[10, 0, -5]),
                                                _f("mirror")]},
             "loop": {"features": [_f("combine", goal="loop")]}, "missing": {"features": [_f("combine", goal="nobody")]}}
    v = T.__dict__.get("x") or __import__("build123d_translator.goalformat", fromlist=["x"]).validate(goals)
    assert v["plate"]["ok"] and not v["loop"]["ok"] and not v["missing"]["ok"]
    assert "cycle" in " ".join(v["loop"]["errors"]) and "no goal named 'nobody'" in " ".join(v["missing"]["errors"])
    r = api.check({k: goals[k] for k in ("pin", "plate")})
    assert r["plate"]["buildable"] and abs(r["plate"]["volume"] - (16000 - 2 * PI * 16 * 10)) < 1e-3
    # a STEP file as base and as a tool
    solid, _ = T.build(pin)
    step = T.export(solid, tmp_path / "pin", ("step",))["step"]
    g = {"features": [_f("combine", step=step), _f("combine", step=step, move=[0, 15, 15], rotate=[90, 0, 0])]}
    assert validate_goal(g)["ok"]
    s, _ = T.build(g)
    assert s.is_valid and abs(s.volume - (2 * PI * 16 * 30 - 16 / 3 * 4 ** 3)) < 1e-6 * s.volume   # a cross less a Steinmetz solid
    assert "no STEP file" in " ".join(validate_goal({"features": [_f("combine", step=str(tmp_path / "none.step"))]})["errors"])


def test_20_scripts_rebuild_the_part():
    goal = {"features": [_box(80, 60, 12), _f("hole_std", screw="M6", x=-30, y=-20, head="socket"),
                         _f("linear_pattern", n=2, length=60, n2=2, length2=40),
                         _f("slot", length=20, w=6, x=0, y=0, angle=90), _f("boss_box", w=20, d=10, x=0, y=20, h=5, corner_r=3),
                         _f("profile_pocket", depth=3, outline={"ellipse": [0, -15, 8, 4]}, holes=[{"circle": [0, -15, 2]}]),
                         _f("chamfer_top", size=1, size2=2),
                         _f("combine", op="sub", goal={"features": [_f("base_sphere", r=5)]}, move=[25, 0, 7])]}
    assert validate_goal(goal)["ok"], validate_goal(goal)
    solid, _ = T.build(goal)
    ns = {"__name__": "not_main"}
    exec(compile(T.script("v20", goal), "v20.py", "exec"), ns)  # noqa: S102
    assert solid.is_valid and T.iou(ns["part"], solid) > 0.999999


def test_20_validation():
    def errs(*feats):
        return " | ".join(validate_goal({"features": list(feats)})["errors"])

    B = _box(40, 40, 10)
    cases = [
        ((B, _hole(2, 0, 0), _f("linear_pattern", n=2, length=10, n2=2)), "needs both 'n2' and 'length2'"),
        ((B, _hole(2, 0, 0), _f("linear_pattern", n=2, length=10, n2=2, length2=5, skip=[[1, 1]])), "not [1, 1]"),
        ((B, _hole(2, 0, 0), _f("polar_pattern", n=4, skip=[5])), "copy numbers 2 to 4"),
        ((B, _hole(2, 0, 0), _f("linear_pattern", n=2, length=10, n2=2, length2=5, bx=1, by=0, bz=0)), "must differ"),
        ((B, _f("fillet_vertical", r=1), _f("mirror")), "not a fillet_vertical"),
        ((B, _hole(2, 0, 0), _f("mirror", features=1, all=True)), "leave out 'features'"),
        ((B, _f("profile_pocket", outline={"circle": [0, 0, 5]}, holes=[{"circle": [0, 0, -1]}])), "sizes must be positive"),
        ((B, _f("boss_cyl", r=2, x=0, y=0, h=2, holes=[{"circle": [0, 0, 1]}])), "'holes' is for"),
        ((_f("profile_base", h=1, outline={"rect": [0, 0, 10, 4], "corner_r": 3}),), "'corner_r' must be between"),
        ((_f("profile_base", h=1, outline={"start": [9, 0], "segs": [["E", 0, 0, 10, 5, 0, -10, 0], ["L", 9, 0]]}),), "lie on its ellipse"),
        ((_f("base_torus", R=5, r=5),), "'r' must be smaller than 'R'"),
        ((_f("base_polygon", n=2, r=5, h=1),), "at least 3"),
        ((_f("base_wedge", w=5, d=5, h=5, top=6),), "'top' must not exceed"),
        ((B, _f("slot", length=4, w=6, x=0, y=0)), "at least 'w'"),
        ((B, _f("slot_arc", r=10, w=4, a1=90, a2=10, x=0, y=0)), "'a2' must be more"),
        ((B, _f("hole_std", screw="M7", x=0, y=0)), "'screw' must be one of"),
        ((B, _f("hole_std", screw="M5", x=0, y=0, tapped=True, head="socket")), "takes no 'head'"),
        ((B, _f("hole_std", screw="M5", x=0, y=0, thread=True)), "needs tapped: true"),
        ((B, _f("hole_std", screw="M24", x=0, y=0, head="countersunk")), "no countersunk screw"),
        ((B, _f("hole_std", screw="M5", x=0, y=0, z=5, head="socket", nx=0, ny=1, nz=0, off=0)), "needs a face"),
        ((B, _f("chamfer_edges", size=1, size2=2, pts=[[20, 0, 10]])), "needs 'ref'"),
        ((B, _f("chamfer_top", size=1, size2=2, angle=30)), "one of 'size2' or 'angle'"),
        ((B, _f("shell", t=1, faces_t=[{"pt": [0, 0], "t": 1}])), "'faces_t' must be"),
        ((_f("combine", op="sub", goal={"features": [B]}),), "must add"),
        ((B, _f("combine", goal={"features": [_hole(1, 0, 0)]})), "goal: feature 1 (hole): the first feature must be a base"),
        ((B, _f("combine", op="common", goal={"features": [B]}), _f("mirror")), "not a combine common"),
    ]
    for feats, text in cases:
        assert text in errs(*feats), (text, errs(*feats))


def test_20_goal_format():
    from build123d_translator.goalformat import goal_format
    from build123d_translator import ops, standards

    fmt = goal_format()
    for k in ("base_sphere", "base_cone", "base_torus", "base_wedge", "base_polygon", "slot", "slot_arc", "combine"):
        assert k in fmt["kinds"] and k in ops.KINDS
    assert set(fmt["kinds"]) == set(ops.KINDS)
    assert "n2" in fmt["kinds"]["linear_pattern"]["optional"] and "holes" in fmt["kinds"]["profile_pocket"]["optional"]
    assert fmt["screws"]["M5"]["clearance"]["normal"] == 5.5 and fmt["screws"]["M8"]["tap_drill"] == 6.8
    for s, (pitch, tap, clear, cb, cs) in standards.ISO_METRIC.items():
        d = float(s[1:])
        assert abs(tap - (d - pitch)) < 0.051 and d < clear[0] < clear[1] < clear[2] < cb[0] and cb[1] > d * 0.95
        assert cs is None or cs > clear[2]


def test_20_angled_slots_and_screw_holes():
    sl = _f("slot", length=12, w=4, **TILT)                        # through all, both ways
    assert validate_goal({"features": [BLOCK, sl]})["ok"]
    assert abs(_vol(sl) - (V0 - (8 * 4 + PI * 4) * 30 / COS30)) < 1e-6 * V0
    hs = _f("hole_std", screw="M6", **TILT)
    assert abs(_vol(hs) - (V0 - PI * 3.3 ** 2 * 30 / COS30)) < 1e-6 * V0
    bad = validate_goal({"features": [BLOCK, _f("hole_std", screw="M6", head="socket", **TILT)]})
    assert "needs a face" in " ".join(bad["errors"])
