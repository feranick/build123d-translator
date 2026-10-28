"""mcp_server.py — MCP server: lets LLM-based tools build CAD parts from goals with build123d.
Version: 2026.10.28.1

Tools (all JSON):
  goal_format      the goal format: kinds, parameters, conventions, an example. Read it first.
  validate_goals   static checks of {name: goal} (fast; fix errors before building)
  check_goals      builds each goal: buildable or not and why, volume, bounding box, mass properties
  build_parts      builds {name: goal}: per part the .step file, the build123d script (.py), volume, bbox
  get_script       the build123d script of one goal, as text (to read, edit or run elsewhere)
  verify_part      IoU of a part built elsewhere (a .step path) against a goal
  compare_parts    IoU of two .step files
  get_file         a written file as base64 (for callers on another machine)
  render_images    new pictures of a built part (shaded, faces by feature, highlights, section cuts), in a worker
  get_image        a PNG written by build_parts or render_images, as an image

Run:  python -m build123d_translator.mcp_server            (stdio)
      python -m build123d_translator.mcp_server --http 8766 [--host 0.0.0.0]
Needs the MCP Python SDK: pip install "mcp>=1.2".
"""
from __future__ import annotations

import argparse

from . import api

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import Image
    from mcp.server.mcpserver import MCPServer as _Server
    _V2 = True
except ImportError:  # 1.x
    from mcp.server.fastmcp import FastMCP as _Server
    from mcp.server.fastmcp import Image
    _V2 = False

INSTRUCTIONS = """build123d-translator builds solid CAD parts from a goal: an ordered list of features with
dimensions (a base, then bosses, holes, pockets, slots, outlines with holes, revolves, patterns and
grids, fillets, chamfers, drafts, counterbored, countersunk, blind and ISO screw holes (M1.6-M24,
clearance or tapped), threads, text, lofts, sweeps and thickened sheets for curved bodies, and
combinations with other goals or STEP files). The
translation is exact and fast (milliseconds to about a second per part).
Workflow: call goal_format once and follow its conventions; write one goal per solid part;
validate_goals and fix every error; build_parts. Results give the .step file and a standalone
build123d script (.py) per part; get_file returns them if you are on another machine. Results also
give volume, center_of_mass and inertia_mm5 (about the centre of mass, unit density: x density [kg/m3]
x 1e-15 = kg m2), for masses and inertias in other translators.
Pictures: build_parts(images=true) (or a goal's "images") writes PNGs: a shaded view and the faces coloured by the
feature that made them (front and back), so you can check by eye that each feature went where it should; get_image
shows each (parts.<name>.images.<view>.path); render_images draws other views (highlight features, section cuts,
top/front/side views) of a built part. Units are mm, Z up."""

server = _Server("build123d-translator", instructions=INSTRUCTIONS)


@server.tool()
def goal_format() -> dict:
    """The goal format: feature kinds with required and optional parameters, the coordinate
    conventions and an example goal. Read this before writing goals."""
    return api.goal_format()


@server.tool()
def validate_goals(goals: dict) -> dict:
    """Static checks of goals given as {name: {"features": [{"kind": ..., "params": {...}}, ...]}}.
    Returns per goal ok, errors and warnings."""
    return api.validate(goals)


@server.tool()
def check_goals(goals: dict) -> dict:
    """Builds each goal without writing files: buildable (bool), the error if not, volume, bbox, center_of_mass
    (mm) and inertia_mm5 (3x3 about the centre of mass for unit density: x density [kg/m3] x 1e-15 = kg m2)."""
    return api.check(goals)


@server.tool()
def build_parts(goals: dict, stl: bool = False, images: bool = False) -> dict:
    """Builds the goals. Per part: built (bool), the files (.step, the build123d script .py, .stl
    if asked), volume, bounding box, time per feature; or why it could not be built. images: true draws the
    default pictures of each part without its own "images" (a few seconds per part): parts.<name>.images."""
    return api.build(goals, formats=("step", "stl") if stl else ("step",), images=True if images else None)


@server.tool()
def get_script(goal: dict, name: str = "part") -> dict:
    """The standalone build123d script that rebuilds one goal's part (needs only build123d)."""
    return api.script(goal, name)


@server.tool()
def verify_part(goal: dict, path: str) -> dict:
    """IoU between the goal's part and a .step file built elsewhere (1.0 = identical)."""
    return api.verify(goal, path)


@server.tool()
def compare_parts(a: str, b: str) -> dict:
    """IoU of two .step files, with their volumes and bounding boxes."""
    return api.compare(a, b)


@server.tool()
def render_images(request: str, name: str, images: list | bool = True, wait_s: float = 45) -> dict:
    """New pictures of a part built by build_parts (its 'request' folder and the goal's name), without changing its
    files: images true (a shaded view, faces by feature front and back) or [{name, color: part | feature, view: iso
    | iso_back | top | bottom | front | back | left | right | [elev, azim], highlight: [feature numbers], edges,
    quality: high (VTK) | basic, slice: {axis, at}}]. Returns within wait_s (at most 50): if still drawing, state
    'running' (call again with the same arguments). Then get_image each path."""
    return api.in_worker("rendering", "render_images", {"request": request, "name": name, "images": images},
                         min(max(float(wait_s), 0.0), 50.0))


@server.tool()
def get_image(path: str) -> Image:
    """A PNG written by build_parts (parts.<name>.images.<view>.path) or render_images, as an image you can see
    and show."""
    return Image(path=str(api.image_path(path)))


@server.tool()
def get_file(path: str, max_mb: float = 20) -> dict:
    """A file written by build_parts (a path from its result) as base64."""
    return api.read_file(path, max_mb)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--http", type=int, help="serve MCP over streamable HTTP on this port (default: stdio)")
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    if a.http is None:
        server.run()
    elif _V2:
        server.run(transport="streamable-http", host=a.host, port=a.http)
    else:
        server.settings.host, server.settings.port = a.host, a.http
        server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
