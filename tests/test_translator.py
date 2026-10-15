"""Tests: every reference goal matches the FreeCAD reference builder (volume and bounding box),
scripts rebuild the same part, validation catches bad goals, the API writes files.
Version: 2026.10.15.1

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
