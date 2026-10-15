"""Tests: every reference goal matches the FreeCAD reference builder (volume and bounding box),
scripts rebuild the same part, validation catches bad goals, the API writes files.
Version: 2026.10.15.1

The references (tests/freecad_reference.json) were measured with the FreeCAD 1.1.3 reference
builder of the goal format: probes for each convention (shell inward, hexagon corner on +X,
right-handed revolves and patterns, polar angle only with an axis, linear spacing, fillets
skipping seams) and the S 80 pump parts other than the casing.
"""
import json
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
