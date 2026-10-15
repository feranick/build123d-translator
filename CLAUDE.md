# build123d-translator: context for a new session

Read this first, then `README.md`. Written 2026-10-16 when the work moved out of the Taiga session.

## What this is

A deterministic translator from **goals** (one solid part as an ordered list of CAD features with dimensions) to build123d solids, STEP files and standalone build123d scripts, with an IoU checker, a CLI (`b3dt`), a Python API and an MCP server (`b3dt-mcp`). No model, no FreeCAD. The goal format is the one of the Taiga decision model (`~/Desktop/taiga-decision-model`, a separate project that drives FreeCAD with a trained model); goal files carry over unchanged. Decision (Taiga's `docs/README_build123d.md`): once a goal exists every build123d step can be computed from it, so a translator beats a trained model; an LLM writes the goals.

## Conventions

- Versions `YYYY.MM.DD.x` in every module docstring, `__init__.py`, `pyproject.toml`, and the README's version table; update the README with every change.
- Author: Nicola Ferralis <ferralis@generalflash.com>. The user commits and pushes from the Mac (`github.com:feranick/build123d-translator`); carbonio has a clone in `~/Software/build123d-translator`.
- Keep answers concise; give commands ready to paste.

## State (2026-10-16)

- **Matches FreeCAD:** 6,761 of 6,762 DeepCAD test goals (carbonio, build123d 0.13 / OCP 8.0, `tools/compare_freecad.py`), and all 24 references in `tests/freecad_reference.json` (probes of every convention + 9 S 80 pump parts). The one exception, `dc_0088_00881862` (an outline arc wrapping around the base circle), differs by exactly the base circle's volume. 9 ms per part median.
- **Lessons:** OpenCascade booleans fail on coincident faces (equal parts): IoU uses tiny skew shifts and extrapolation (`translate._intersection`); booleans go through OCP directly and face merging runs on a deep copy (build123d's `clean()` and `ShapeUpgrade_UnifySameDomain` change shapes in place and broke fused half-disks); newer OCP lacks `TopTools_IndexedDataMapOfShapeListOfShape`.
- **2026.10.16.1:** `loft`, `loft_cut`, `sweep`, `sweep_cut` (not in Taiga's format; checked against exact volumes). `examples/car_body.json`: a generic car body (loft through 11 sections + wheel arches), 1.4 s. Fillets on lofted end edges can fail in the kernel. Use generic shapes; don't model specific branded designs.
- **MCP:** in the Claude desktop config as `b3dt` → `/Users/feranick/Desktop/build123d-translator/.venv/bin/b3dt-mcp`; restart the app after changes.
- Tests: `.venv/bin/pytest -q` (35 tests).

## Open ideas

- Compare the 52-feature S 80 casing with FreeCAD (`tools/freecad_reference.sh` on mochi, then `compare_freecad.py`).
- FreeCAD times in the comparison (Spark1's DeepCAD `teacher.log`).
- More curved-body tools if needed: variable fillets/blends between lofts, surface patches; placement of several parts (assemblies).
