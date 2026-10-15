# build123d-translator

Turns a **goal** (one solid part as an ordered list of CAD features with dimensions: a base, then bosses, holes, pockets, outlines, revolves, patterns, fillets, ...) into a [build123d](https://github.com/gumyr/build123d) solid, a STEP file and a standalone build123d script, deterministically and in milliseconds. It includes a checker (IoU between parts), a command line, a Python API and an MCP server, so LLM-based tools can design parts as goals and get exact geometry back.

The goal format is the one of the Taiga decision model (`taiga-decision-model`), so its goal files carry over unchanged (the translator adds lofts and sweeps, which Taiga does not have); this project does not use Taiga or FreeCAD. Every feature follows the FreeCAD reference builder of that format, checked against it (below).

Why a translator: once a goal exists, there is nothing left to decide. Choosing a face or an edge in build123d is a geometric query computed from the goal, so a fixed translation is exact by construction, free, and faster than any model. The flexible part, turning a request ("a motor mount for a NEMA 17") into a goal, is what an LLM does well; the translator builds it, and the checker confirms it.

## Install

```bash
python -m venv .venv && source .venv/bin/activate      # Python 3.10+
pip install -e ".[mcp,test]"                           # build123d, the MCP SDK, pytest
pytest -q                                              # 35 tests, about 15 s
```

build123d has wheels for Linux, macOS and Windows; no CAD application is needed.

## Use

```bash
b3dt format > goal_format.json                 # kinds, parameters, conventions, an example
b3dt validate examples/parts.json              # static checks
b3dt check examples/parts.json                 # build each goal: volume, bbox, or the error
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
| `check_goals` | Builds each goal without files: buildable or not and why, volume, bbox. |
| `build_parts` | Builds the goals: per part the `.step`, the build123d script `.py` (and `.stl` on request), volume, bbox, time per feature. |
| `get_script` | The standalone build123d script of one goal, as text. |
| `verify_part` | IoU of a `.step` built elsewhere (another CAD tool, an LLM's own script) against a goal. |
| `compare_parts` | IoU of two `.step` files. |
| `get_file` | A written file as base64, for callers on another machine. |

Run it with `b3dt-mcp` (stdio) or `b3dt-mcp --http 8766 [--host 0.0.0.0]` (streamable HTTP at `/mcp`, no authentication: trusted networks or a tunnel only). It runs on the same computer as the desktop client; in the Claude desktop app's `claude_desktop_config.json`:

```json
{"mcpServers": {"b3dt": {"command": "/Users/<you>/Desktop/build123d-translator/.venv/bin/b3dt-mcp"}}}
```

A typical agent loop: read `goal_format`; turn the request into one goal per solid part; `validate_goals` and fix the errors; `build_parts`; return the `.step` files (and the scripts if the user wants editable code).

## The goal format (summary; `b3dt format` has the full list)

- **Units and axes:** mm, global coordinates, Z up; the base sits on z = 0, centred on the origin in X and Y.
- **First feature is the base:** `base_box`, `base_cyl`, `base_hex`, `base_ring`, `profile_base` (any closed outline of lines, arcs and splines) or `profile_revolve` (a turned part).
- **Sketched features** (`boss_cyl`, `boss_box`, `hole`, `hole_std`, `pocket_rect`, `profile_boss`, `profile_pocket`) go on the top face at `x`, `y`; on another face with its normal (`nx`, `ny`, `nz`) and centre `z`; on one of several faces with `at`; on an origin plane with `off`, extruded both ways (cross bores, windows, cavities).
- **Revolve and groove** about an origin axis; **patterns and mirror** repeat the previous feature about or along an origin axis.
- **Curved bodies** (2026.10.16.1): `loft` / `loft_cut` through cross-sections on parallel planes (smooth or ruled), `sweep` / `sweep_cut` of a profile along a 3D path of lines, arcs and splines; as base or later features, and repeatable with patterns and mirror. `examples/car_body.json` is a generic car body: a loft through 11 sections along X (a flat floor line and one spline over sides and roof each), with the four wheel arches cut (mirror across XZ), 1.4 s. These kinds are not in the Taiga goal format (FreeCAD's AdditiveLoft and AdditivePipe would be its counterparts), so they are checked against exact volumes instead (a ruled loft of two squares is a frustum; a circle swept a quarter turn is a quarter torus).
- **Fillets and chamfers:** the top face's edge loop, all vertical edges, a face's edge loop, edges along an axis, or chosen edges by a point on each (`pts`); **shell** inward, open at the top face or at faces named by points.
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
| Package | 2026.10.16.1 |
| `ops.py`, `goalformat.py`, `mcp_server.py` | 2026.10.16.1 (`loft`, `loft_cut`, `sweep`, `sweep_cut`) |
| `ops.py` (before) | 2026.10.15.4 (booleans through OpenCascade directly, faces merged on a copy and kept only if the result stays valid: build123d's `clean()` broke solids fused across shared faces and changed them in place; an invalid result is healed with `fix()`; seam edges found with basic OCP calls, since newer OCP builds no longer expose `TopTools_IndexedDataMapOfShapeListOfShape`) |
| `translate.py` | 2026.10.15.3 (IoU robust to coincident faces: when the plain intersection fails or comes back partial, one part is moved by a tiny step along skew directions and the intersection extrapolated to zero step) |
| `api.py`, `tools/compare_freecad.py` | 2026.10.15.2 (the other part's bbox in comparisons; mismatches saved with their FreeCAD parts) |
| Other modules | 2026.10.15.1 |
| Tested with | build123d 0.12.0, Python 3.10 (Linux) |
