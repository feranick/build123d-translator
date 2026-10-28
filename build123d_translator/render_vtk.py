"""render_vtk.py — pictures of a built part with VTK (through PyVista), offscreen: lit, smooth shading and
depth-tested edges for an image's "quality": "high". Matplotlib (render.py) stays the default and the fallback.
Version: 2026.10.28.1

    render(scenes, specs, out_dir, name, titles) -> ({name: {path, what, ...}}, warnings)    (in the caller's process)
    probe() -> {ok, window, prefix, why}: can this machine draw offscreen (cached per process)
    status() -> {vtk: version | None, offscreen}: for tool_status

As cfd-translator and fea-translator 2026.10.28.1. The caller (render.py) does the CAD work (tessellation, which
feature made each face, section cuts) and writes each view's scene (triangles, a colour per face, edge polylines,
the legend) to an .npz; the drawing runs in a child process (python -m build123d_translator.render_vtk <specs.json>
<out dir> <name>, printing one JSON line {images, warnings}), so an OpenGL crash cannot take down a server or a
worker. Offscreen OpenGL comes from EGL, OSMesa (libosmesa6) or an X display; without any, the child runs under
xvfb-run when it is installed. B3DT_RENDER=matplotlib turns VTK off; B3DT_RENDER_TIMEOUT (s, default 900) bounds
the child. White background; VTK's text has no '|'.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

_PROBE: dict = {}
SIZE = (1400, 900)


def available() -> bool:
    return importlib.util.find_spec("pyvista") is not None and importlib.util.find_spec("vtk") is not None


_PROBE_CODE = ("import pyvista as pv\n"
               "p = pv.Plotter(off_screen=True, window_size=(64, 64))\n"
               "p.add_mesh(pv.Sphere())\n"
               "img = p.screenshot(return_img=True)\n"
               "print('VTKOK', p.ren_win.GetClassName(), int(img.std() > 0))\n")


def _try(prefix: list) -> tuple[bool, str]:
    try:
        r = subprocess.run(prefix + [sys.executable, "-c", _PROBE_CODE], capture_output=True, text=True, timeout=90,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    line = next((ln for ln in r.stdout.splitlines() if ln.startswith("VTKOK")), None)
    if r.returncode == 0 and line and line.endswith(" 1"):
        return True, line.split()[1]
    lines = [ln for ln in (r.stderr or "").splitlines() if ln.strip()]
    err = [ln for ln in lines if "WARN" in ln or "rror" in ln] or lines[-1:]
    return False, "; ".join(dict.fromkeys(e.split("|")[-1].strip() for e in err[-3:])) or f"exit {r.returncode}"


def probe() -> dict:
    """{ok, window (the VTK render window class), prefix (e.g. xvfb-run), why}. Cached per process."""
    if _PROBE:
        return _PROBE
    if os.environ.get("B3DT_RENDER", "").strip().lower() == "matplotlib":
        _PROBE.update(ok=False, why="B3DT_RENDER=matplotlib")
    elif not available():
        _PROBE.update(ok=False, why="PyVista/VTK not installed (pip install 'build123d-translator[render]')")
    else:
        ok, how = _try([])
        if ok:
            _PROBE.update(ok=True, window=how, prefix=[])
        elif shutil.which("xvfb-run"):
            pre = ["xvfb-run", "-a", "-s", "-screen 0 1920x1080x24"]
            ok2, how2 = _try(pre)
            if not ok2:                                # xvfb-run -a can lose a race for a display number: once more
                ok2, how2 = _try(pre)
            _PROBE.update(ok=ok2, window=(how2 + " (xvfb-run)") if ok2 else None, prefix=pre if ok2 else None,
                          why=None if ok2 else f"no offscreen OpenGL ({how}); xvfb-run failed too ({how2})")
        else:
            _PROBE.update(ok=False, why=f"no offscreen OpenGL ({how}): install libosmesa6 or libegl1, or xvfb")
    return _PROBE


def status() -> dict:
    if not available():
        return {"vtk": None, "offscreen": None}
    from importlib.metadata import version
    try:
        v = version("pyvista")
    except Exception:  # noqa: BLE001
        v = "yes"
    return {"vtk": v, "offscreen": (_PROBE.get("window") or ("no: " + str(_PROBE.get("why")))) if _PROBE else "not checked"}


def _save_scene(sc: dict, path: Path) -> None:
    edges = sc["edges"]
    lens = np.array([len(e) for e in edges], int)
    np.savez_compressed(path, points=sc["points"], tris=sc["tris"], tri_face=sc["tri_face"], face_rgb=sc["face_rgb"],
                        edge_pts=np.concatenate(edges) if edges else np.zeros((0, 3)), edge_len=lens)


def render(scenes: dict, specs: list, out_dir: Path, name: str, titles: dict) -> tuple[dict, list]:
    """Draws the views in a child process; images it could not draw are missing, with a warning each."""
    out_dir = Path(out_dir)
    names = [sp["name"] for sp in specs]
    if not names:
        return {}, []
    pr = probe()
    if not pr.get("ok"):
        return {}, [f"image {n!r}: quality high needs VTK: {pr.get('why')}" for n in names]
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs, files = [], []
    for sp in specs:
        f = out_dir / f".vtk_{os.getpid()}_{sp['name']}.npz"
        _save_scene(scenes[sp["name"]], f)
        files.append(f)
        from .render import view_angles
        jobs.append({"name": sp["name"], "scene": str(f), "view": view_angles(sp), "title": titles[sp["name"]],
                     "legend": scenes[sp["name"]]["legend"]})
    sp_file = out_dir / f".vtk_specs_{os.getpid()}.json"
    sp_file.write_text(json.dumps(jobs))
    env = dict(os.environ)
    pkg_parent = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = pkg_parent + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    cmd = pr["prefix"] + [sys.executable, "-m", "build123d_translator.render_vtk", str(sp_file), str(out_dir), name]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
                           timeout=float(os.environ.get("B3DT_RENDER_TIMEOUT", "900") or 900))
    except subprocess.TimeoutExpired:
        return {}, [f"image {n!r}: VTK took longer than B3DT_RENDER_TIMEOUT" for n in names]
    finally:
        sp_file.unlink(missing_ok=True)
        for f in files:
            f.unlink(missing_ok=True)
    line = next((ln for ln in reversed(r.stdout.splitlines()) if ln.startswith("{")), None)
    if r.returncode != 0 or line is None:
        tail = " ".join((r.stderr or "").strip().splitlines()[-2:])[-300:]
        return {}, [f"image {n!r}: the VTK renderer failed (exit {r.returncode}): {tail}" for n in names]
    res = json.loads(line)
    return res["images"], res["warnings"]


# ---- in the child ---------------------------------------------------------------------------------
def _camera(p, view, bounds):
    elev, azim = np.radians(view[0]), np.radians(view[1])
    b = np.asarray(bounds, float).reshape(3, 2)
    c = b.mean(axis=1)
    d = np.array([np.cos(elev) * np.cos(azim), np.cos(elev) * np.sin(azim), np.sin(elev)])
    p.camera.focal_point = c
    p.camera.position = c + d * float(np.linalg.norm(b[:, 1] - b[:, 0])) * 2
    p.camera.up = (0, 0, 1) if abs(np.sin(elev)) < 0.99 else (0, 1, 0)
    p.reset_camera(bounds=b.ravel())
    p.camera.zoom(1.15)


def draw(job: dict, path: Path) -> dict:
    import pyvista as pv
    z = np.load(job["scene"])
    P, T = z["points"], z["tris"].astype(np.int64)
    m = pv.PolyData(P, np.column_stack([np.full(len(T), 3), T]).ravel())
    m.cell_data["rgb"] = (np.clip(z["face_rgb"][z["tri_face"]], 0, 1) * 255).astype(np.uint8)
    p = pv.Plotter(off_screen=True, window_size=SIZE)
    p.set_background("white")
    p.add_text(job["title"], position="upper_left", font_size=11, color="black")
    try:
        p.enable_anti_aliasing("fxaa")
    except Exception:  # noqa: BLE001
        pass
    p.add_mesh(m, scalars="rgb", rgb=True, smooth_shading=True, split_sharp_edges=True, specular=0.2, ambient=0.35,
               diffuse=0.7)
    ep, el = z["edge_pts"], z["edge_len"]
    if len(el):
        lines, start = [], 0
        for n in el:
            lines += [int(n)] + list(range(start, start + int(n)))
            start += int(n)
        p.add_mesh(pv.PolyData(ep, lines=np.array(lines)), color="black", line_width=1.5)
    for i, (label, color) in enumerate(job["legend"]):   # one line per feature in its colour, under the title
        p.add_text(label, position=(18, SIZE[1] - 64 - 24 * i), font_size=11, color=color)
    _camera(p, job["view"], m.bounds)
    p.screenshot(str(path))
    p.close()
    return {"renderer": "vtk", "triangles": int(len(T)), "edges": int(len(el))}


def _child(spec_file: str, out_dir: str, name: str) -> dict:
    jobs = json.loads(Path(spec_file).read_text())
    images, warns = {}, []
    for j in jobs:
        path = Path(out_dir) / f"{j['name']}.png"
        try:
            images[j["name"]] = {"path": str(path), "what": j["title"], **draw(j, path)}
        except Exception as exc:  # noqa: BLE001
            warns.append(f"image {j['name']!r}: VTK: {type(exc).__name__}: {exc}")
    return {"images": images, "warnings": warns}


if __name__ == "__main__":
    print(json.dumps(_child(*sys.argv[1:4]), default=float))
