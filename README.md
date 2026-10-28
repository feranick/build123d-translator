# build123d-translator

Turns a **goal** (one solid part as an ordered list of CAD features with dimensions: a base, then bosses, holes, pockets, outlines, revolves, patterns, fillets, ...) into a [build123d](https://github.com/gumyr/build123d) solid, a STEP file and a standalone build123d script, deterministically and in milliseconds. It includes a checker (IoU between parts), a command line, a Python API and an MCP server, so LLM-based tools can design parts as goals and get exact geometry back.

The goal format is the one of the Taiga decision model (`taiga-decision-model`), so its goal files carry over unchanged (the translator adds lofts and sweeps, which Taiga does not have); this project does not use Taiga or FreeCAD. Every feature follows the FreeCAD reference builder of that format, checked against it (below).

Why a translator: once a goal exists, there is nothing left to decide. Choosing a face or an edge in build123d is a geometric query computed from the goal, so a fixed translation is exact by construction, free, and faster than any model. The flexible part, turning a request ("a motor mount for a NEMA 17") into a goal, is what an LLM does well; the translator builds it, and the checker confirms it.

## Install

```bash
python -m venv .venv && source .venv/bin/activate      # Python 3.10+
pip install -e ".[mcp,test]"                           # build123d, matplotlib, the MCP SDK, pytest
pip install -e ".[render]"                             # optional: VTK pictures (PyVista), "quality": "high"
pytest -q                                              # 114 tests (one needs VTK), about 45 s
```

build123d has wheels for Linux, macOS and Windows; no CAD application is needed. VTK draws offscreen, so a server without a display needs one of: a GPU driver's EGL (`sudo apt install libegl1`), OSMesa (`sudo apt install libosmesa6`), or Xvfb (`sudo apt install xvfb`: the renderer then runs under `xvfb-run -a`). `b3dt status` shows what it found under `render` once a picture has been drawn. Matplotlib needs nothing more.

## Use

```bash
b3dt format > goal_format.json                 # kinds, parameters, conventions, an example
b3dt validate examples/parts.json              # static checks
b3dt check examples/parts.json                 # build each goal: volume, bbox, mass properties, or the error
b3dt build examples/parts.json [--stl]         # <name>.step + <name>.py per part, in ~/b3d_parts/build_<time>/
b3dt script examples/parts.json bracket        # print the build123d script of one goal
b3dt compare a.step b.step                     # IoU of two parts
b3dt verify examples/parts.json bracket x.step # IoU of a part built elsewhere against the goal
```

Every command prints JSON. `-` reads the goals from stdin. Output folder: `B3DT_OUT` (default `~/b3d_parts`); builds run in `B3DT_WORKERS` worker processes (4) with `B3DT_TIMEOUT` seconds per part (120), so a kernel operation that never returns cannot block the caller.

From Python:

```python
from build123d_translator import translate as T
solid, ms_per_feature = T.build(goal)                 # a build123d Solid
open("bracket.py", "w").write(T.script("bracket", goal))
T.export(solid, "bracket", ("step", "stl"))
```

### The scripts

`<name>.py` rebuilds the part with build123d alone (the builder is included) and writes `<name>.step`. One line per feature, the goal's parameters as arguments, so it can be read and edited like the goal:

```python
b = Builder(scale=80)
b.base_box(w=80, d=50, h=12)
b.boss_cyl(r=10, x=0, y=0, h=15)
b.hole(r=5, x=0, y=0, at=[0, 0, 27])
b.hole(r=3, x=30, y=17)
b.mirror(ax=1, ay=0, az=0)
...
part = single_solid(b.part)
```

`b3dt script ... --import` writes the short form that imports the builder from this package instead.

## For LLM tools: the MCP server

| Tool | What it does |
|---|---|
| `goal_format` | The goal format: kinds, parameters, conventions, an example. The agent reads it first. |
| `validate_goals` | Static checks of `{name: goal}`. |
| `check_goals` | Builds each goal without files: buildable or not and why, volume, bbox, `center_of_mass` (mm) and `inertia_mm5` (the inertia tensor about the centre of mass for unit density, mm⁵; OpenCascade's convention: off-diagonal = −∫xy; × density [kg/m³] × 1e-15 = kg·m²). |
| `build_parts` | Builds the goals: per part the `.step`, the build123d script `.py` (and `.stl` on request), volume, bbox, time per feature. |
| `get_script` | The standalone build123d script of one goal, as text. |
| `verify_part` | IoU of a `.step` built elsewhere (another CAD tool, an LLM's own script) against a goal. |
| `compare_parts` | IoU of two `.step` files. |
| `get_file` | A written file as base64, for callers on another machine. |
| `render_images` | New pictures of a part built by `build_parts` (its `request` and the goal's name), without changing its files; in a worker process, returns within `wait_s` (state `running` until done: call again). |
| `get_image` | A PNG of a build or of `render_images` (under the output folder), as an image. |

**Pictures** (2026.10.28.1, as cfd-translator and fea-translator 2026.10.28.1). A part can be valid and still wrong (a hole on the wrong face, a pattern the wrong way): volume and bbox do not show it, a picture does. `build_parts(images=true)` (or a goal's own `"images"`) writes `<request>/images/<name>/<view>.png`; the part's result lists them under `images`, with `faces_by_feature` (how many faces each feature left on the part).
- *Views:* `{name, color: part | feature, view: iso | iso_back | top | bottom | front | back | left | right | [elev, azim], highlight: [feature numbers], edges (true), quality: high | basic, slice: {axis, at}}`. Defaults: a shaded `iso` view, and `features` / `features_back` (each face in the colour of the feature that made it, with a legend `3 hole`, `5 polar_pattern`, ... ; from the front and from the back). `highlight` greys out every other feature. `slice` keeps the part on the low side of the plane, the section face in its own colour, and by default looks from the cut's side.
- *Which feature made a face:* the builder keeps the part after every feature (`Builder(keep_parts=True)`); a point on each final face is classified against those parts (OpenCascade's point classifier), and the face belongs to the earliest feature from which the point stays on the part's boundary to the end. A hole's wall is the hole's, the face it went through stays the base's, fillet faces are the fillet's, a pattern's and a mirror's copies are theirs.
- *Renderers:* Matplotlib (Agg) by default and as the fallback: shaded triangles (long ones split so its depth sort holds), edges hidden exactly by a ray test (OpenCascade's ray-shape intersector), white background. VTK through PyVista for `"quality": "high"`: lit, smooth, depth-tested edges, drawn in a child process (`python -m build123d_translator.render_vtk <specs.json> <out dir> <name>`, one JSON line back) from scenes the parent writes, so an OpenGL crash cannot take down a worker or the server; `probe()` (cached per process) finds EGL, OSMesa or X, else runs it under `xvfb-run -a`. Without VTK a high-quality view is drawn with Matplotlib, with a warning.
- *No saved state:* there is no solve, so `render_images` rebuilds the goal from the request's `goals.json` (deterministic, milliseconds to seconds) and draws it.

| Setting | Default | What |
|---|---|---|
| `B3DT_RENDER` | (VTK when it can draw) | `matplotlib`: never VTK |
| `B3DT_RENDER_TIMEOUT` | 900 | seconds for the VTK child process |
| `B3DT_RENDER_EXTRA` | 300 | seconds added to `B3DT_TIMEOUT` for a part with pictures |
| `B3DT_AUX_WORKERS` | 2 | worker processes of `render_images` |

Run it with `b3dt-mcp` (stdio) or `b3dt-mcp --http 8766 [--host 0.0.0.0]` (streamable HTTP at `/mcp`, no authentication: trusted networks or a tunnel only). It runs on the same computer as the desktop client; in the Claude desktop app's `claude_desktop_config.json`:

```json
{"mcpServers": {"b3dt": {"command": "/Users/<you>/Desktop/build123d-translator/.venv/bin/b3dt-mcp"}}}
```

A typical agent loop: read `goal_format`; turn the request into one goal per solid part; `validate_goals` and fix the errors; `build_parts`; return the `.step` files (and the scripts if the user wants editable code).

## The goal format (summary; `b3dt format` has the full list)

- **Units and axes:** mm, global coordinates, Z up; the base sits on z = 0, centred on the origin in X and Y.
- **First feature is the base:** `base_box`, `base_cyl`, `base_hex`, `base_ring`, `profile_base` (any closed outline of lines, arcs and splines) or `profile_revolve` (a turned part).
- **Sketched features** (`boss_cyl`, `boss_box`, `hole`, `hole_std`, `pocket_rect`, `profile_boss`, `profile_pocket`) go on the top face at `x`, `y`; on another face with its normal (`nx`, `ny`, `nz`) and centre `z`; on one of several faces with `at`; on an origin plane with `off`, extruded both ways (cross bores, windows, cavities).
- **Angled planes** (2026.10.17.1, Taiga's patch 0020): `boss_cyl`, `boss_box`, `hole`, `pocket_rect`, `profile_boss` and `profile_pocket` can lie on a plane at any angle: give its in-plane x direction `ux`, `uy`, `uz`, any non-zero normal `nx`, `ny`, `nz`, and its origin `x`, `y`, `z` (the feature's centre). Frame: n normalised, u made perpendicular to n, v = n × u; circles and rectangles centred on the origin (w along u, d along v), outlines in (u, v) about it; extruded symmetrically (h or depth is the total; holes and pockets without depth go through all, both ways). Inclined bores, angled pins, slanted windows. No `off`, `at`, `until`, `taper` or `tip` there (Taiga has no equivalent). Checked against exact volumes: a bore tilted θ through a block removes π r² h / cos θ.
- **Revolve and groove** about an origin axis; **patterns and mirror** repeat the previous feature about or along an origin axis.
- **Curved bodies** (2026.10.16.1): `loft` / `loft_cut` through cross-sections on parallel planes (smooth or ruled), `sweep` / `sweep_cut` of a profile along a 3D path of lines, arcs and splines; as base or later features, and repeatable with patterns and mirror. `examples/car_body.json` is a generic car body: a loft through 11 sections along X (a flat floor line and one spline over sides and roof each), with the four wheel arches cut (mirror across XZ), 1.4 s. These kinds are not in the Taiga goal format (FreeCAD's AdditiveLoft and AdditivePipe would be its counterparts), so they are checked against exact volumes instead (a ruled loft of two squares is a frustum; a circle swept a quarter turn is a quarter torus).
- **Machining and moulding details** (2026.10.16.2): `taper` (draft angle) on bosses and pockets; `until: "next" | "last"` instead of a height or depth (a boss up to the next or last material, a pocket through the first wall or all of it); blind holes (`depth`, optional drill point `tip`), `hole_cb` (counterbore) and `hole_cs` (countersink); `draft` of side faces about a neutral plane; `thread` (a 60° V groove cut along a helix into a shaft or bore, right- or left-handed); helix segments `['H', ...]` in sweep paths (springs, coils).
- **Whole-part operations** (2026.10.16.2): `profile_intersect` (keep the part inside an outline extruded through it), `split` (keep one side of a plane), `offset` (grow or shrink every face).
- **Surfaces and markings** (2026.10.16.2): `thicken` (a sheet of thickness t through open curves: panels, blades), `text` (embossed or engraved on a face; give `font_path` for the same glyphs on every machine), `profile_project` (an outline projected onto curved faces, then embossed or engraved). Like lofts and sweeps, these are not in the Taiga format and are checked against exact volumes (frustums for taper and draft, Pappus for threads, cylinder shells for projections); all match to 1e-6 or better except the helix tube, whose 0.3 % is the profile's tilt to the path.
- **Fillets and chamfers:** the top face's edge loop, all vertical edges, a face's edge loop, edges along an axis, or chosen edges by a point on each (`pts`); **shell** inward, open at the top face or at faces named by points.
- **More vocabulary** (2026.10.20.1; not in the Taiga format, checked against exact volumes):
  - *Patterns:* `linear_pattern` grids (`n2`, `length2`, second direction `bx`, `by`, `bz`); `features: k` repeats the last k features together (a boss with its hole), and a pattern counts as one feature, so a pattern of a pattern now repeats all its copies; `all: true` repeats or mirrors the whole part; `skip` leaves copies out (`[2, 4]`, on a grid `[[i, j], ...]`). Copies apart from each other are combined in one boolean.
  - *Outlines:* shorthands anywhere an outline goes: `{"circle": [cu, cv, r]}`, `{"ellipse": [cu, cv, a, b]}`, `{"rect": [cu, cv, w, d], "corner_r": r}`, `{"polygon": [cu, cv, r, n]}`, `{"slot": [cu, cv, length, w]}`, `{"arc_slot": [cu, cv, r, w, a1, a2]}`, with `rotation`; elliptical arc segments `['E', cu, cv, a, b, rotation, u, v]`; `holes: [outline, ...]` in `profile_base`, `profile_boss`, `profile_pocket`, `profile_intersect`, `sweep`, `sweep_cut` (a flange with its bolt holes in one feature, a tube swept from a ring).
  - *Bases:* `base_sphere`, `base_cone` (cone or frustum), `base_torus`, `base_wedge` (ramp, optional flat top), `base_polygon` (n sides).
  - *Slots:* `slot` (straight, overall length, any angle) and `slot_arc` (along an arc), pockets with rounded ends; on faces, origin planes and angled planes.
  - *Screw holes:* `hole_std` takes an ISO metric size (`"M1.6"` to `"M24"`, `build123d_translator/standards.py`): a clearance hole (`fit` close / normal / loose, ISO 273), with `head: "socket"` (counterbore for ISO 4762, DIN 974-1) or `"countersunk"` (90°, ISO 10642 head diameter), or `tapped: true` (tap drill D − P) and `thread: true` (the internal thread, depth (D − tap)/2, over the hole's depth). `goal_format` lists the table under `screws`.
  - *Details:* `corner_r` on `boss_box` and `pocket_rect`; asymmetric chamfers (`size2`, or `angle`: size2 = size·tan(angle); size on the reference face: the top face, the face selected by normal, or the face nearest `ref`); `shell` with `faces_t: [{pt, t}]` (walls of their own thickness).
  - *Other parts:* `combine` adds, removes or intersects another goal (by name in the same call, or inline) or a STEP file, rotated and moved; as the first feature it is the base (start from an imported part). Validation checks names and cycles; the scripts carry the other goal inline.
  - A threaded tapped hole's bore is built from true cylinder and cone surfaces: OpenCascade's boolean of the helical groove with a coaxial revolved surface fails silently (it returned the bore without the groove).
- **One goal per solid part.**

## Conventions checked against FreeCAD

The meaning of each feature was measured on the FreeCAD 1.1.3 reference builder (volume and bounding box of probe goals) and reproduced here; `tests/freecad_reference.json` holds the references, and the tests require a match to 0.01 % in volume and 0.06 mm in bounding box.

| Convention | FreeCAD reference | Here |
|---|---|---|
| Shell | inward; outside unchanged (box 40×30×20, t 2: 7,152 mm³) | the same |
| Hexagon | a corner on +X (r 10: bbox 20 × 17.3) | the same |
| Revolve, polar pattern | right-handed about the signed axis | the same |
| Polar pattern angle | used only with an axis given; 360/n apart for a full turn, angle/(n−1) otherwise | the same |
| Linear pattern | spacing length/(n−1), towards the sign of the axis | the same |
| Fillets on vertical edges | seam edges of holes and cylinders skipped | the same |
| Faces | the largest planar face with the normal (then the farthest out); with `at`, the nearest to it (then the largest) | the same |
| Holes, pockets through all | from the face into the material only; both ways on an origin plane | the same |
| Splines | interpolated through the points, chord-length parameters | the same |

**Results (2026-10-15):** 6,761 of 6,762 DeepCAD test goals and all 24 reference goals match FreeCAD (below). The reference goals are 15 probes and test parts and the 9 S 80 pump parts other than the casing (including the impeller's spline blades), built at 1–250 ms per part. The 52-feature S 80 casing builds in about 1 s (slowest feature 65 ms); its FreeCAD reference takes longer than an MCP call allows, so it is compared with the tool below.

### Comparing at scale

`tools/compare_freecad.py` compares the translator with FreeCAD parts goal by goal (IoU, and time per part by length bucket). The DeepCAD test folders of a Taiga work folder already hold the teacher's STEP for every verified goal, so no FreeCAD run is needed:

```bash
python tools/compare_freecad.py --goals ~/taiga-expanded-0013/deepcad_test/goals_test_verified.json \
    --freecad ~/taiga-expanded-0013/deepcad_test/teacher --log ~/taiga-expanded-0013/deepcad_test/teacher.log --workers 8
```

First run (2026-10-15, carbonio, build123d 0.13 / OCP 8.0): 6,701 of 6,762 DeepCAD test goals (99.1 %) matched FreeCAD at IoU ≥ 0.999, at 10 ms per part median (180 ms for 11–20 features). The mismatches were nearly all the checker and the booleans, not the translation: equal parts share their faces exactly, and OpenCascade's intersection then often fails (IoU 0 with equal volumes and bounding boxes); build123d's face merging after a boolean broke solids fused across shared faces. With 2026.10.15.4 (carbonio, OCP 8.0): **6,761 of 6,762 match** (1–5, 6–10 and 11–20 features all 1.000), at 9 ms per part median, 60 ms at the 90th percentile and 186 ms median for 11–20 features. The one left (`dc_0088_00881862`, IoU 0.67, an outline whose arc wraps around the base circle) differs from FreeCAD by exactly the base circle's volume (98 mm³). The tool saves mismatches with their FreeCAD parts in `<out>_mismatches/`, so they can be rerun or reproduced elsewhere.

For any other goal file (the S 80, synthetic suites), `tools/freecad_reference.sh GOALS.json OUT_DIR` builds the FreeCAD parts first, on a machine with a Taiga work folder.

## Files

| File | What it is |
|---|---|
| `build123d_translator/ops.py` | The builder: each feature kind as a build123d operation (also copied into the scripts) |
| `build123d_translator/goalformat.py` | The goal format and static validation |
| `build123d_translator/standards.py` | ISO metric screw holes for `hole_std` (pitch, tap drill, clearances, counterbores, countersinks) |
| `build123d_translator/render.py` | Pictures: which feature made each face, scenes, validation, defaults, Matplotlib |
| `build123d_translator/render_vtk.py` | Pictures with VTK (PyVista) in a child process; the offscreen probe |
| `build123d_translator/translate.py` | Build, script, export, load, IoU |
| `build123d_translator/api.py` | JSON functions with worker processes and time limits |
| `build123d_translator/cli.py` | `b3dt` command line |
| `build123d_translator/mcp_server.py` | `b3dt-mcp` MCP server |
| `tests/` | Tests and the FreeCAD references |
| `tools/compare_freecad.py`, `tools/freecad_reference.sh` | Comparison with FreeCAD at scale |
| `examples/parts.json`, `examples/s80_goals.json`, `examples/car_body.json` | A bracket, a motor flange, a spacer, a cover plate; the S 80 pump parts; a generic car body (loft) |
| `examples/pillow_block/` | A three-part pillow block (base, bearing housing, end cap), with a README on building, running and editing the scripts |

## Versions

| Component | Version |
|---|---|
| Package | 2026.10.28.1 |
| `render.py`, `render_vtk.py` (new), `api.py`, `cli.py`, `mcp_server.py`, `goalformat.py`, `ops.py` | 2026.10.28.1 (pictures, as cfd-translator and fea-translator 2026.10.28.1: a goal's `images` or `build_parts(images=true)`; faces coloured by the feature that made them, highlights, section cuts, named views; `render_images` and `get_image` tools, `b3dt images`, `b3dt status` / `tool_status` with `render`; Matplotlib by default, VTK (`render` extra) for `quality: high` in a child process; `Builder(keep_parts=True)`; matplotlib a dependency) |
| `translate.py`, `mcp_server.py` (before) | 2026.10.21.1 (results add `center_of_mass` and `inertia_mm5`, the mass properties for unit density, so openmodelica-translator and others get masses and moments of inertia from a goal) |
| `ops.py`, `goalformat.py`, `standards.py`, `translate.py`, `api.py`, `mcp_server.py` | 2026.10.20.1 (grids, patterns of several features, of patterns and of the whole part, skipped copies; outline shorthands, ellipse arcs, holes in outlines; `base_sphere`, `base_cone`, `base_torus`, `base_wedge`, `base_polygon`; `slot`, `slot_arc`; `hole_std` with ISO metric sizes, fits, heads, tapped and threaded holes; `corner_r`; asymmetric chamfers; per-face shell thickness; `combine` with other goals and STEP files; scripts inline the screw table) |
| `ops.py`, `goalformat.py` (before) | 2026.10.17.1 (angled planes, as Taiga's patch 0020: `ux`, `uy`, `uz` on the six sketched kinds, the plane's frame and symmetric extrusion; validation with Taiga's messages, plus errors for `at`, `until`, `taper`, `tip`; `pocket_rect` without depth goes through all on an angled plane) |
| `ops.py`, `goalformat.py`, `mcp_server.py` (before) | 2026.10.16.2 (`taper` and `until` on bosses and pockets; blind holes with `tip`, `hole_cb`, `hole_cs`; `draft`, `offset`, `profile_intersect`, `split`, `thicken`, `thread`, `text`, `profile_project`; helix path segments; loft, sweep and thicken accepted as later features in validation) |
| `ops.py`, `goalformat.py`, `mcp_server.py` (before) | 2026.10.16.1 (`loft`, `loft_cut`, `sweep`, `sweep_cut`) |
| `ops.py` (before) | 2026.10.15.4 (booleans through OpenCascade directly, faces merged on a copy and kept only if the result stays valid: build123d's `clean()` broke solids fused across shared faces and changed them in place; an invalid result is healed with `fix()`; seam edges found with basic OCP calls, since newer OCP builds no longer expose `TopTools_IndexedDataMapOfShapeListOfShape`) |
| `translate.py` (before) | 2026.10.15.3 (IoU robust to coincident faces: when the plain intersection fails or comes back partial, one part is moved by a tiny step along skew directions and the intersection extrapolated to zero step) |
| `api.py` (before), `tools/compare_freecad.py` | 2026.10.15.2 (the other part's bbox in comparisons; mismatches saved with their FreeCAD parts) |
| Other modules | 2026.10.15.1 |
| Tested with | build123d 0.12.0, Python 3.10 (Linux) |
