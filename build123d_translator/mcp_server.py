"""mcp_server.py — MCP server: lets LLM-based tools build CAD parts from goals with build123d.
Version: 2026.10.15.1

Tools (all JSON):
  goal_format      the goal format: kinds, parameters, conventions, an example. Read it first.
  validate_goals   static checks of {name: goal} (fast; fix errors before building)
  check_goals      builds each goal: buildable or not and why, volume, bounding box
  build_parts      builds {name: goal}: per part the .step file, the build123d script (.py), volume, bbox
  get_script       the build123d script of one goal, as text (to read, edit or run elsewhere)
  verify_part      IoU of a part built elsewhere (a .step path) against a goal
  compare_parts    IoU of two .step files
  get_file         a written file as base64 (for callers on another machine)

Run:  python -m build123d_translator.mcp_server            (stdio)
      python -m build123d_translator.mcp_server --http 8766 [--host 0.0.0.0]
Needs the MCP Python SDK: pip install "mcp>=1.2".
"""
from __future__ import annotations

import argparse

from . import api

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer as _Server
    _V2 = True
except ImportError:  # 1.x
    from mcp.server.fastmcp import FastMCP as _Server
    _V2 = False

INSTRUCTIONS = """build123d-translator builds solid CAD parts from a goal: an ordered list of features with
dimensions (a base, then bosses, holes, pockets, outlines, revolves, patterns, fillets...). The
translation is exact and fast (milliseconds to about a second per part).
Workflow: call goal_format once and follow its conventions; write one goal per solid part;
validate_goals and fix every error; build_parts. Results give the .step file and a standalone
build123d script (.py) per part; get_file returns them if you are on another machine.
Units are mm, Z up."""

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
    """Builds each goal without writing files: buildable (bool), the error if not, volume, bbox."""
    return api.check(goals)


@server.tool()
def build_parts(goals: dict, stl: bool = False) -> dict:
    """Builds the goals. Per part: built (bool), the files (.step, the build123d script .py, .stl
    if asked), volume, bounding box, time per feature; or why it could not be built."""
    return api.build(goals, formats=("step", "stl") if stl else ("step",))


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
