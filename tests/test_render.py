"""Pictures of built parts (render.py, render_vtk.py; as cfd-translator and fea-translator 2026.10.28.1): which
feature made each face, section cuts, highlights, validation, the build and render_images, VTK where it can draw
(skipped otherwise), the Matplotlib fallback.
Version: 2026.10.28.1
"""
import json
from pathlib import Path

import numpy as np
import pytest

from build123d_translator import api, render, render_vtk
from build123d_translator.goalformat import EXAMPLE, validate_goal


def _f(kind, **p):
    return {"kind": kind, "params": p}


PLATE = {"features": [_f("base_box", w=60, d=40, h=10),                  # 1: 6 faces
                      _f("boss_cyl", r=8, x=0, y=0, h=10),               # 2: side and top
                      _f("hole", r=3, x=0, y=0, at=[0, 0, 20]),          # 3: the bore through boss and plate
                      _f("hole", r=2, x=15, y=0),                        # 4: one hole
                      _f("polar_pattern", n=4),                          # 5: its three copies
                      _f("fillet_vertical", r=3)]}                       # 6: the four corner fillets


@pytest.fixture()
def out(tmp_path, monkeypatch):
    monkeypatch.setenv("B3DT_OUT", str(tmp_path))
    render_vtk._PROBE.clear()
    yield tmp_path
    render_vtk._PROBE.clear()


def _scene(goal, spec):
    solid, _, parts = render.build(goal)
    return render.scene(solid, parts, [f["kind"] for f in goal["features"]], spec)


def test_each_face_belongs_to_the_feature_that_made_it():
    sc = _scene(PLATE, {"color": "feature"})
    assert sc["faces_by_feature"] == {"1": 6, "2": 2, "3": 1, "4": 1, "5": 3, "6": 4}
    assert [lab for lab, _ in sc["legend"]] == ["1 base_box", "2 boss_cyl", "3 hole", "4 hole", "5 polar_pattern",
                                                "6 fillet_vertical"]
    import matplotlib.colors as mcolors
    for k in range(1, 7):                                               # every face in its feature's colour
        m = sc["face_owner"] == k
        assert np.allclose(sc["face_rgb"][m], mcolors.to_rgb(render.feature_color(k)))
    ex = _scene(EXAMPLE["bracket"], {"color": "feature"})                # mirrors own their copies
    assert ex["faces_by_feature"] == {"1": 6, "2": 2, "3": 1, "4": 1, "5": 1, "6": 1, "7": 1, "8": 4}


def test_highlight_and_section():
    import matplotlib.colors as mcolors
    sc = _scene(PLATE, {"highlight": [4, 5]})
    grey, hl = np.array(mcolors.to_rgb(render.GREY)), sc["face_owner"] >= 4
    assert np.allclose(sc["face_rgb"][~(np.isin(sc["face_owner"], [4, 5]))], grey)
    assert np.allclose(sc["face_rgb"][sc["face_owner"] == 5], mcolors.to_rgb(render.feature_color(5)))
    assert [lab for lab, _ in sc["legend"]] == ["4 hole", "5 polar_pattern"] and hl.any()
    cut = _scene(PLATE, {"color": "feature", "slice": {"axis": "y", "at": 0}})
    sec = np.allclose(cut["face_rgb"], mcolors.to_rgb(render.SECTION), atol=1e-9)
    assert cut["legend"][-1][0] == "section y = 0 mm" and np.all(cut["points"][:, 1] <= 1e-6)
    assert (np.abs(cut["face_rgb"] - np.array(mcolors.to_rgb(render.SECTION))).max(axis=1) < 1e-9).sum() >= 1 or sec
    assert render.view_angles({"slice": {"axis": "y", "at": 0}}) == render.SLICE_VIEWS["y"]   # from the cut's side
    with pytest.raises(ValueError):
        _scene(PLATE, {"slice": {"axis": "z", "at": -5}})


def test_image_validation():
    def errs(images):
        return " | ".join(validate_goal(dict(PLATE, images=images))["errors"])

    assert errs(True) == "" and errs([{"name": "a", "color": "feature", "view": [10, 20], "highlight": [1, 6]}]) == ""
    cases = [([{"view": "sideways"}], "view: iso | iso_back"), ([{"color": "rainbow"}], "color: part"),
             ([{"highlight": [7]}], "feature numbers from 1 to 6"), ([{"edges": "yes"}], "edges: true"),
             ([{"quality": "ultra"}], "quality: high"), ([{"slice": {"axis": "w", "at": 1}}], "slice: {axis"),
             ([{"shading": 1}], "unknown key 'shading'"), ([{"name": "a b"}], "name: a word"),
             ([{"name": "a"}, {"name": "a"}], "'a' twice"), ("yes", "images: true")]
    for images, text in cases:
        assert text in errs(images), (text, errs(images))


def test_build_with_pictures_and_render_images(out):
    goals = {"plate": dict(PLATE, images=[{"name": "f", "color": "feature"}]), "bracket": EXAMPLE["bracket"]}
    r = api.build(goals, images=True)
    p, b = r["parts"]["plate"], r["parts"]["bracket"]
    assert p["built"] and list(p["images"]) == ["f"] and p["images"]["f"]["faces_by_feature"]["5"] == 3
    assert list(b["images"]) == ["iso", "features", "features_back"]          # the defaults for goals without their own
    import matplotlib.image as mpimg
    for rec in (p["images"]["f"], b["images"]["iso"]):
        path = Path(rec["path"])
        assert path.parent == Path(r["request"]) / "images" / path.parent.name and mpimg.imread(str(path)).std() > 0.02
    assert "images" not in api.check({"plate": PLATE})["plate"]                   # check writes no files
    r2 = api.render_images(r["request"], "plate", [{"name": "top", "view": "top", "highlight": [3]}])
    assert Path(r2["images"]["top"]["path"]).is_file() and r2["warnings"] == []
    assert api.image_path(r2["images"]["top"]["path"]).suffix == ".png"
    with pytest.raises(ValueError):
        api.image_path(str(Path(r["request"]) / "goals.json"))
    assert "no goal 'nope'" in api.render_images(r["request"], "nope")["error"]
    assert api.render_images(r["request"], "plate", [{"highlight": [9]}])["error"] == "invalid images"
    assert "only requests under" in api.render_images(str(out.parent), "plate")["error"]
    # in a worker, as the MCP tool: 'running' until done, then the same call collects
    w = api.in_worker("rendering", "render_images", {"request": r["request"], "name": "plate", "images": [{"name": "fr", "view": "front"}]}, 0)
    for _ in range(30):
        if w.get("state") != "running":
            break
        w = api.in_worker("rendering", "render_images", {"request": r["request"], "name": "plate", "images": [{"name": "fr", "view": "front"}]}, 5)
    assert Path(w["images"]["fr"]["path"]).is_file(), w


def test_matplotlib_fallback(out, monkeypatch):
    monkeypatch.setenv("B3DT_RENDER", "matplotlib")
    res, w = render.render_part("plate", PLATE, out / "img", [{"name": "hq", "quality": "high"}])
    assert res["hq"]["renderer"] == "matplotlib"
    assert any("B3DT_RENDER=matplotlib" in x for x in w) and any("drawn with Matplotlib instead" in x for x in w)
    assert set(api.tool_status()["render"]) == {"vtk", "offscreen"}


def test_vtk_pictures_where_it_can_draw(out):
    if not render_vtk.available() or not render_vtk.probe().get("ok"):
        pytest.skip(f"VTK cannot draw here: {render_vtk.probe().get('why')}")
    res, w = render.render_part("plate", PLATE, out / "img", [{"name": "f", "color": "feature", "quality": "high"},
                                                             {"name": "cut", "slice": {"axis": "x", "at": 0}, "quality": "high"}])
    assert not w, w
    import matplotlib.image as mpimg
    for k in ("f", "cut"):
        img = mpimg.imread(res[k]["path"])
        assert res[k]["renderer"] == "vtk" and img.shape[:2] == (render_vtk.SIZE[1], render_vtk.SIZE[0]) and img.std() > 0.02
    assert res["f"]["faces_by_feature"]["6"] == 4
    assert api.tool_status()["render"]["offscreen"].startswith("vtk")


def test_scripts_ignore_images():
    from build123d_translator import translate as T
    g = dict(PLATE, images=True)
    ns = {"__name__": "not_main"}
    exec(compile(T.script("plate", g), "plate.py", "exec"), ns)  # noqa: S102
    solid, _ = T.build(PLATE)
    assert T.iou(ns["part"], solid) > 0.999999
