"""Tests: every reference goal matches the FreeCAD reference builder (volume and bounding box),
scripts rebuild the same part, validation catches bad goals, the API writes files.
Version: 2026.10.16.2

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
                        {"kind": "mirror", "params": {}}, {"kind": "linear_pattern", "params": {"n": 2, "length": 5}}]}
    errs = " ".join(validate_goal(bad)["errors"])
    for text in ("must be a base", "missing 'y'", "'r' must be positive", "pattern of a pattern"):
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
