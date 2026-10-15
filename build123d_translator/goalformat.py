"""goalformat.py — the goal format: kinds, conventions, an example, and static validation.
Version: 2026.10.16.2

A goal file is {name: goal}; a goal is {"features": [{"kind": ..., "params": {...}}, ...]},
one solid part, features in build order, lengths in mm. The format is the one of the
Taiga goal files, so goals carry over between the two tools unchanged.
"""
from __future__ import annotations

BASE_KINDS = ("base_box", "base_cyl", "base_hex", "base_ring", "profile_base", "profile_revolve", "loft", "sweep", "thicken")
NON_NUMERIC = ("outline", "sections", "path", "text")
PATTERN_KINDS = ("polar_pattern", "linear_pattern", "mirror")
SKETCHED_PLACEABLE = ("boss_cyl", "boss_box", "hole", "hole_std", "pocket_rect", "profile_boss", "profile_pocket",
                      "hole_cb", "hole_cs", "text")
EXTRUDED = ("boss_cyl", "boss_box", "pocket_rect", "profile_boss", "profile_pocket")  # take taper and until
DRESSUPS = ("fillet_top", "fillet_vertical", "chamfer_top", "shell", "fillet_edges", "chamfer_edges", "draft", "offset",
            "profile_intersect", "split")

KINDS = {
    "base_box": ({"w": "width along X", "d": "depth along Y", "h": "height along Z"}, {}, "Rectangular block, centred on the origin in X and Y, from z = 0 to h."),
    "base_cyl": ({"r": "radius", "h": "height"}, {}, "Cylinder about Z, centred on the origin, from z = 0 to h."),
    "base_hex": ({"r": "circumradius (a corner on +X)", "h": "height"}, {}, "Hexagonal prism about Z."),
    "base_ring": ({"ri": "inner radius", "ro": "outer radius", "h": "height"}, {}, "Ring about Z, from z = 0 to h."),
    "profile_base": ({"outline": "closed outline in the XY plane (see outline)", "h": "height"}, {}, "Prism of any closed outline, from z = 0 to h."),
    "profile_revolve": ({"outline": "closed outline (see conventions)", "ax": "axis X", "ay": "axis Y", "az": "axis Z",
                         "nx": "sketch plane normal", "ny": "", "nz": "", "off": "0", "x": "0", "y": "0", "z": "0"},
                        {"angle": "sweep in degrees (360), right-handed about the axis"},
                        "Turned part or collar: the outline revolved about an origin axis; as base or later feature."),
    "profile_groove": ({"outline": "", "ax": "", "ay": "", "az": "", "nx": "", "ny": "", "nz": "", "off": "", "x": "", "y": "", "z": ""},
                       {"angle": ""}, "Removes the outline revolved about an origin axis (ring grooves, bores)."),
    "boss_cyl": ({"r": "radius", "x": "centre x", "y": "centre y", "h": "height"}, {"taper": "draft angle in degrees: positive narrows the boss outward or the pocket inward (needs h or depth, not off)", "until": "'next' or 'last' instead of h or depth (see conventions)"}, "Cylindrical boss on a face (top face by default)."),
    "boss_box": ({"w": "", "d": "", "x": "", "y": "", "h": ""}, {"taper": "", "until": ""}, "Rectangular boss on a face."),
    "hole": ({"r": "radius", "x": "", "y": ""}, {"depth": "blind hole depth (omit: through all)", "tip": "drill point angle in degrees at the bottom of a blind hole (118); omit: flat"}, "Hole into the material: through all, or blind with depth (and tip)."),
    "hole_std": ({"r": "", "x": "", "y": ""}, {"depth": "", "tip": ""}, "Same geometry as hole."),
    "hole_cb": ({"r": "bore radius", "cbr": "counterbore radius (> r)", "cbd": "counterbore depth", "x": "", "y": ""}, {"depth": "", "tip": ""},
                "Counterbored hole from a face (socket-head screws): through all or blind."),
    "hole_cs": ({"r": "bore radius", "csr": "countersink radius at the face (> r)", "x": "", "y": ""},
                {"angle": "countersink included angle in degrees (90)", "depth": "", "tip": ""},
                "Countersunk hole from a face (flat-head screws): through all or blind."),
    "pocket_rect": ({"w": "", "d": "", "x": "", "y": "", "depth": ""}, {"taper": "", "until": ""}, "Rectangular pocket."),
    "profile_boss": ({"outline": "closed outline on the face", "h": "height"}, {"taper": "", "until": ""}, "Boss of any closed outline."),
    "profile_pocket": ({"outline": ""}, {"depth": "depth (omit: through all)", "taper": "", "until": ""}, "Pocket of any closed outline."),
    "polar_pattern": ({"n": "number of copies (incl. the original)"},
                      {"ax": "", "ay": "", "az": "axis (default Z; sign: direction of rotation)",
                       "angle": "extent in degrees (360; only with an axis given)"},
                      "Repeats the previous feature about an origin axis: 360/n apart for a full turn, angle/(n-1) otherwise."),
    "linear_pattern": ({"n": "copies", "length": "distance first to last"}, {"ax": "", "ay": "", "az": "direction (default +X)"},
                       "Repeats the previous feature along an axis."),
    "mirror": ({}, {"ax": "", "ay": "", "az": "normal of the mirror plane (default X: the YZ plane)"},
               "Mirrors the previous feature across an origin plane."),
    "fillet_top": ({"r": "radius"}, {}, "Fillets the outer edge loop of the top face (the largest face facing +Z)."),
    "fillet_vertical": ({"r": "radius"}, {}, "Fillets every straight vertical edge."),
    "chamfer_top": ({"size": "size"}, {}, "Chamfers the outer edge loop of the top face."),
    "shell": ({"t": "wall thickness (inward)"}, {"pts": "points on the faces to remove (default: the top face)"}, "Hollows the part, open at the removed faces."),
    "fillet_edges": ({"r": "radius"}, {"pts": "a point on each edge", "nx": "", "ny": "", "nz": "edge loop of the face with this normal",
                                       "at": "[x, y, z]: which such face", "ax": "", "ay": "", "az": "every straight edge parallel to this axis"},
                     "Fillets chosen edges: by points (pts), a face's outer edge loop (normal [+at]) or all edges along an axis."),
    "loft": ({"sections": "list of {'off': position along the axis, 'outline': closed outline}, at least 2",
              "nx": "axis the sections are normal to (one of nx, ny, nz = 1)", "ny": "", "nz": ""},
             {"ruled": "true: straight between sections (default: smooth)"},
             "Solid through cross-sections on parallel planes (car bodies, hulls, transitions, ducts); as base or later feature."),
    "loft_cut": ({"sections": "", "nx": "", "ny": "", "nz": ""}, {"ruled": ""}, "Removes a lofted solid (scoops, tapered cavities)."),
    "sweep": ({"outline": "closed profile on the plane normal to (nx, ny, nz) at off, centred on the path's start",
               "nx": "profile plane normal", "ny": "", "nz": "", "off": "profile plane position",
               "path": "{'start': [x, y, z], 'segs': [['L', x, y, z], ['A', mx, my, mz, x, y, z], ['S', [[x, y, z], ...]], ['H', cx, cy, cz, ax, ay, az, pitch, height]]}"},
              {"frenet": "true: the profile follows the path's curvature frame (helices); default keeps its orientation smoothly"},
              "Profile swept along a 3D path (pipes, rails, handles, bent tubes); as base or later feature."),
    "sweep_cut": ({"outline": "", "nx": "", "ny": "", "nz": "", "off": "", "path": ""}, {"frenet": ""}, "Removes a swept solid (channels, grooves along a path)."),
    "chamfer_edges": ({"size": "size"}, {"pts": "", "nx": "", "ny": "", "nz": "", "at": "", "ax": "", "ay": "", "az": ""},
                      "Chamfers chosen edges, selected like fillet_edges."),
    "draft": ({"angle": "draft angle in degrees; positive narrows the part along the pull direction"},
              {"ax": "", "ay": "", "az": "pull direction (default +Z)", "off": "neutral plane position along it (0)",
               "pts": "a point on each face to draft (default: every planar and cylindrical face parallel to the pull)"},
              "Tilts side faces about their line on the neutral plane (cast and moulded parts)."),
    "offset": ({"t": "distance: positive grows the whole part, negative shrinks it"}, {}, "Offsets every face of the part, rounding convex edges."),
    "profile_intersect": ({"outline": "", "nx": "", "ny": "", "nz": ""}, {"off": "plane position (0)", "depth": "total, symmetric (omit: through all)"},
                          "Keeps only the part inside the outline extruded both ways from an origin plane (two-view parts, trims)."),
    "split": ({"nx": "normal of the cutting plane: the side kept", "ny": "", "nz": "", "off": "plane position along it"}, {},
              "Cuts the part with an origin plane and keeps the side the normal points to."),
    "thicken": ({"sections": "list of {'off', 'curve'}: open curves on parallel planes, at least 2", "nx": "", "ny": "", "nz": "",
                 "t": "sheet thickness"},
                {"ruled": "", "ax": "", "ay": "", "az": "side the thickness goes to (default: the surface's normal)"},
                "Sheet of thickness t through open curves (panels, blades, curved walls); as base or later feature (adds)."),
    "thread": ({"r": "major radius (external) or bore radius (internal)", "pitch": "pitch; negative: left-handed", "length": "thread length"},
               {"x": "", "y": "", "z": "start point on the axis (0, 0, 0)", "ax": "", "ay": "", "az": "axis (default +Z)",
                "internal": "true: thread in a bore of radius r", "depth": "thread depth (0.6134 * pitch)"},
               "Cuts a 60-degree V thread (helical groove) into an existing shaft or bore."),
    "text": ({"text": "the characters", "size": "font height", "x": "", "y": ""},
             {"h": "emboss height", "depth": "engrave depth (one of h, depth)", "font": "font name (Arial)",
              "font_path": "a .ttf/.otf file (for the same result on every machine)", "rotation": "degrees about the face normal"},
             "Embossed or engraved text centred at (x, y) on a face."),
    "profile_project": ({"outline": "", "nx": "", "ny": "", "nz": "", "off": ""}, {"h": "emboss height", "depth": "engrave depth (one of h, depth)"},
                        "Projects an outline along -(nx, ny, nz) onto the curved or flat faces it meets first, then embosses or engraves it."),
}

CONVENTIONS = [
    "Units are millimetres; coordinates are global, Z up. The first feature is the base (one of: "
    + ", ".join(BASE_KINDS) + "); every later feature changes the part built so far, in order.",
    "Top-face features: x, y are the centre on the top face (the largest planar face facing +Z), measured from the origin.",
    "Any other face: add the face's outward normal (nx, ny, nz; one of them +1 or -1, the others 0) and the "
    "centre (x, y, z); the coordinate along the normal comes from the face. Without 'at' the largest face with "
    "that normal is used; with at = [x, y, z] the one nearest to that point (e.g. the top of a boss).",
    "Bosses extrude out of the face by h; pockets go into it by depth; holes go through all, into the material.",
    "Origin plane with an offset: add the plane normal (nx, ny, nz) and off (the plane's position along that "
    "axis); the centre's coordinate along that axis equals off. The feature is extruded both ways, symmetric "
    "(h or depth is the total; holes and pockets without depth go through all both ways): cross bores, windows, cavities.",
    "Rectangles: w along the face's first in-plane axis (X, then Y, then Z), d along the second.",
    "Outlines: {'start': [u, v], 'segs': [['L', u, v], ['A', um, vm, u, v], ['S', [[u, v], ...]]]}: lines, arcs "
    "through a middle point, splines through points; closed (the last point is the start). On the top face "
    "and XY planes u = X, v = Y; normal to X u = Y, v = Z; normal to Y u = X, v = Z.",
    "Revolve and groove: about the origin axis (ax, ay, az); the outline lies in an origin plane containing the "
    "axis (XZ, normal Y, for the Z and X axes; YZ, normal X, for the Y axis), with off = 0 and x = y = z = 0; it "
    "must stay on one side of the axis.",
    "Patterns and mirror repeat the feature right before them (its added or removed volume); a pattern of a "
    "pattern is not supported, nor a pattern of a fillet, chamfer or shell.",
    "Fillets, chamfers and shells must fit the material around them; holes and pockets need material around them.",
    "Selection by points (pts): a point on each edge (its midpoint is safest) or on each face; the nearest edge or "
    "face is used (within 2 % of the part's size). Avoid points where several edges meet.",
    "Loft: sections on parallel planes normal to one axis (nx, ny, nz), each {'off': position, 'outline': ...} in "
    "that plane's (u, v) convention; smooth through all sections unless ruled. Give every section the same number "
    "of segments, starting at the corresponding point, so the surface does not twist. Curved bodies: 8-20 sections.",
    "Sweep: the profile outline lies on the plane normal to (nx, ny, nz) at off, and should be centred on the path's "
    "start, with the path leaving that plane along its normal. Paths: lines, three-point arcs, splines through points.",
    "Taper (bosses, pockets): a draft angle in degrees on the extrusion; positive narrows it away from the face "
    "(bosses outward, pockets inward). until = 'next' | 'last' replaces h or depth: a boss fills the free space up to "
    "the first material it meets (next) or up to the far side of the last (last); a pocket removes the first piece of "
    "material it meets (next: through the first wall) or all of it (last). A boss that meets nothing is an error.",
    "Holes: blind with depth (tip: a drill point of tip degrees below it); hole_cb (counterbore cbr, cbd) and hole_cs "
    "(countersink radius csr at the face, included angle) start from a face, not an origin plane. Patterns repeat them.",
    "Draft, offset, profile_intersect and split change the whole part (patterns cannot repeat them). split keeps the "
    "side of the plane its normal points to (nz = -1, off = 10 keeps z <= 10).",
    "Thicken: like loft, but each section has an open 'curve' (same segments as an outline, not closed); the sheet "
    "is t thick, to the side (ax, ay, az) if given.",
    "Paths also take helices: ['H', cx, cy, cz, ax, ay, az, pitch, height] from the current point about the axis "
    "through (cx, cy, cz) along (ax, ay, az) (radius: the point's distance from it); pitch < 0 is left-handed. "
    "Sweep a profile along a helix with frenet = true (springs, coils). Screw threads: use thread.",
    "Thread: cuts the groove into material that exists (a shaft of radius r, or the wall of a bore of radius r), from "
    "(x, y, z) on the axis for length; the profile is a 60-degree V of depth 0.6134 * pitch unless depth is given.",
    "Text reads from outside the face; up is +Y on top and bottom faces and +Z on side faces. Glyph shapes depend on the "
    "font installed; give font_path for identical parts on every machine.",
    "profile_project: the outline lies on an origin plane beside the part (nx, ny, nz pointing away from it, at off) and "
    "is projected back along -(nx, ny, nz); h or depth goes along the surface normal.",
    "One goal is one solid part; several separate solids are several goals.",
]

EXAMPLE = {"bracket": {"features": [
    {"kind": "base_box", "params": {"w": 80, "d": 50, "h": 12}},
    {"kind": "boss_cyl", "params": {"r": 10, "x": 0, "y": 0, "h": 15}},
    {"kind": "hole", "params": {"r": 5, "x": 0, "y": 0, "at": [0, 0, 27]}},
    {"kind": "hole", "params": {"r": 3, "x": 30, "y": 17}},
    {"kind": "mirror", "params": {"ax": 1, "ay": 0, "az": 0}},
    {"kind": "hole", "params": {"r": 3, "x": 30, "y": -17}},
    {"kind": "mirror", "params": {"ax": 1, "ay": 0, "az": 0}},
    {"kind": "fillet_vertical", "params": {"r": 4}}]}}


def goal_format() -> dict:
    kinds = {}
    for k, (req, opt, what) in KINDS.items():
        o = dict(opt)
        if k in SKETCHED_PLACEABLE:
            o.update({"nx": "face normal", "ny": "", "nz": "", "z": "centre z", "off": "origin-plane offset", "at": "point on the face"})
        kinds[k] = {"what": what, "required": list(req), "optional": list(o),
                    "descriptions": {p: d for p, d in {**req, **o}.items() if d}}
    return {"format": "goal (build123d-translator)", "conventions": CONVENTIONS, "kinds": kinds,
            "example": EXAMPLE, "limits": "Up to a few hundred features per goal; lengths of 0.1 mm to a few metres."}


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _point(p) -> bool:
    return isinstance(p, (list, tuple)) and len(p) == 3 and all(_num(c) for c in p)


def _closed(outline, closed: bool = True) -> str | None:
    if not isinstance(outline, dict) or not isinstance(outline.get("start"), (list, tuple)) or len(outline["start"]) != 2:
        return "outline needs 'start': [u, v]"
    segs = outline.get("segs")
    if not isinstance(segs, list) or not segs:
        return "outline needs a non-empty 'segs' list"
    last = outline["start"]
    for s in segs:
        if not isinstance(s, (list, tuple)) or not s or s[0] not in ("L", "A", "S"):
            return f"bad segment {s!r}: use ['L', u, v], ['A', um, vm, u, v] or ['S', [[u, v], ...]]"
        if s[0] == "L" and len(s) == 3 and _num(s[1]) and _num(s[2]):
            last = [s[1], s[2]]
        elif s[0] == "A" and len(s) == 5 and all(_num(c) for c in s[1:]):
            last = [s[3], s[4]]
        elif s[0] == "S" and len(s) == 2 and isinstance(s[1], list) and s[1] and all(
                isinstance(q, (list, tuple)) and len(q) == 2 and all(_num(c) for c in q) for q in s[1]):
            last = list(s[1][-1])
        else:
            return f"bad segment {s!r}"
    if closed and (abs(last[0] - outline["start"][0]) > 1e-6 or abs(last[1] - outline["start"][1]) > 1e-6):
        return "outline is not closed: the last point must equal 'start'"
    return None


def _path(path) -> str | None:
    """None if a 3D path is well formed, else what is wrong."""
    if not isinstance(path, dict) or not _point(path.get("start")):
        return "needs 'start': [x, y, z]"
    segs = path.get("segs")
    if not isinstance(segs, list) or not segs:
        return "needs a non-empty 'segs' list"
    for sg in segs:
        ok = isinstance(sg, (list, tuple)) and sg and (
            (sg[0] == "L" and len(sg) == 4 and all(_num(c) for c in sg[1:]))
            or (sg[0] == "A" and len(sg) == 7 and all(_num(c) for c in sg[1:]))
            or (sg[0] == "S" and len(sg) == 2 and isinstance(sg[1], list) and sg[1] and all(_point(q) for q in sg[1]))
            or (sg[0] == "H" and len(sg) == 9 and all(_num(c) for c in sg[1:]) and sg[7] != 0 and sg[8] > 0
                and any(c != 0 for c in sg[4:7])))
        if not ok:
            return (f"bad segment {sg!r}: use ['L', x, y, z], ['A', mx, my, mz, x, y, z], ['S', [[x, y, z], ...]] or "
                    "['H', cx, cy, cz, ax, ay, az, pitch (not 0), height (> 0)]")
    return None


def _unit(params, keys) -> bool | None:
    if not any(k in params for k in keys):
        return None
    v = [params.get(k, 0) for k in keys]
    return all(_num(x) for x in v) and sorted(abs(x) for x in v) == [0, 0, 1]


def validate_goal(goal: dict) -> dict:
    errors, warnings = [], []
    feats = goal.get("features") if isinstance(goal, dict) else None
    if not isinstance(feats, list) or not feats:
        return {"ok": False, "errors": ["a goal needs a non-empty 'features' list"], "warnings": []}
    for i, f in enumerate(feats):
        where = f"feature {i + 1}"
        if not isinstance(f, dict) or "kind" not in f:
            errors.append(f"{where}: needs 'kind' and 'params'")
            continue
        k, p = f["kind"], f.get("params", {})
        where += f" ({k})"
        if k not in KINDS:
            errors.append(f"{where}: unknown kind; known: {', '.join(sorted(KINDS))}")
            continue
        if not isinstance(p, dict):
            errors.append(f"{where}: params must be an object")
            continue
        if i == 0 and k not in BASE_KINDS:
            errors.append(f"{where}: the first feature must be a base ({', '.join(BASE_KINDS)})")
        if i > 0 and k in BASE_KINDS and k not in ("profile_revolve", "loft", "sweep", "thicken"):
            errors.append(f"{where}: a base kind can only be the first feature")
        for r in KINDS[k][0]:
            if r in ("h", "depth") and k in EXTRUDED and "until" in p:
                continue
            if r not in p:
                errors.append(f"{where}: missing '{r}'")
            elif r not in NON_NUMERIC and not _num(p[r]):
                errors.append(f"{where}: '{r}' must be a number")
        for key in ("w", "d", "h", "r", "ri", "ro", "depth", "size", "length", "cbr", "cbd", "csr") + (("t",) if k != "offset" else ()):
            if key in p and _num(p[key]) and p[key] <= 0:
                errors.append(f"{where}: '{key}' must be positive")
        if "ri" in p and "ro" in p and _num(p["ri"]) and _num(p["ro"]) and p["ri"] >= p["ro"]:
            errors.append(f"{where}: 'ri' must be smaller than 'ro'")
        if "n" in p and (not _num(p["n"]) or p["n"] < 2 or int(p["n"]) != p["n"]):
            errors.append(f"{where}: 'n' must be a whole number, at least 2")
        if "outline" in p:
            bad = _closed(p["outline"])
            if bad:
                errors.append(f"{where}: {bad}")
        if k == "offset" and _num(p.get("t")) and p["t"] == 0:
            errors.append(f"{where}: 't' must not be 0")
        if "until" in p:
            if k not in EXTRUDED:
                errors.append(f"{where}: 'until' is for bosses and pockets ({', '.join(EXTRUDED)})")
            elif p["until"] not in ("next", "last"):
                errors.append(f"{where}: 'until' must be 'next' or 'last'")
            elif "taper" in p:
                errors.append(f"{where}: 'taper' and 'until' cannot be combined")
        if "taper" in p:
            if k not in EXTRUDED:
                errors.append(f"{where}: 'taper' is for bosses and pockets ({', '.join(EXTRUDED)})")
            elif not _num(p["taper"]) or abs(p["taper"]) >= 90:
                errors.append(f"{where}: 'taper' must be a number of degrees between -90 and 90")
            elif "off" in p or (k == "profile_pocket" and "depth" not in p):
                errors.append(f"{where}: 'taper' needs a face to start from and a height or depth")
        for key in ("tip", "angle"):
            if key in p and k in ("hole", "hole_std", "hole_cb", "hole_cs", "draft") and _num(p[key]):
                if k == "draft" and not (-90 < p[key] < 90):
                    errors.append(f"{where}: 'angle' must be between -90 and 90 degrees")
                elif k != "draft" and not (0 < p[key] < 180):
                    errors.append(f"{where}: '{key}' must be between 0 and 180 degrees")
        if "tip" in p and "depth" not in p:
            errors.append(f"{where}: 'tip' needs 'depth' (a blind hole)")
        if k in ("hole_cb", "hole_cs") and "off" in p:
            errors.append(f"{where}: {k} starts from a face, not an origin plane (off)")
        for big in ("cbr", "csr"):
            if big in p and _num(p[big]) and _num(p.get("r")) and p[big] <= p["r"]:
                errors.append(f"{where}: '{big}' must exceed 'r'")
        if k == "hole_cb" and _num(p.get("cbd")) and _num(p.get("depth")) and p["depth"] <= p["cbd"]:
            errors.append(f"{where}: 'depth' must exceed 'cbd'")
        if k in ("text", "profile_project") and ("h" in p) == ("depth" in p):
            errors.append(f"{where}: give one of 'h' (emboss) or 'depth' (engrave)")
        if k == "text" and (not isinstance(p.get("text"), str) or not p["text"].strip()):
            errors.append(f"{where}: 'text' must be a non-empty string")
        if k == "thread" and _num(p.get("pitch")) and p["pitch"] == 0:
            errors.append(f"{where}: 'pitch' must not be 0")
        if k == "thicken" and "sections" in p:
            secs = p["sections"]
            if not isinstance(secs, list) or len(secs) < 2:
                errors.append(f"{where}: 'sections' must be a list of at least 2 sections")
            else:
                for j, sec in enumerate(secs):
                    if not isinstance(sec, dict) or not _num(sec.get("off")):
                        errors.append(f"{where}: section {j + 1} needs a numeric 'off'")
                        continue
                    bad = _closed(sec.get("curve"), closed=False)
                    if bad:
                        errors.append(f"{where}: section {j + 1}: {bad.replace('outline', 'curve')}")
        if k in ("loft", "loft_cut") and "sections" in p:
            secs = p["sections"]
            if not isinstance(secs, list) or len(secs) < 2:
                errors.append(f"{where}: 'sections' must be a list of at least 2 sections")
            else:
                for j, sec in enumerate(secs):
                    if not isinstance(sec, dict) or not _num(sec.get("off")):
                        errors.append(f"{where}: section {j + 1} needs a numeric 'off'")
                        continue
                    bad = _closed(sec.get("outline"))
                    if bad:
                        errors.append(f"{where}: section {j + 1}: {bad}")
                offs = [sec.get("off") for sec in secs if isinstance(sec, dict)]
                if len(set(offs)) != len(offs):
                    errors.append(f"{where}: two sections at the same 'off'")
                counts = {len(sec["outline"]["segs"]) for sec in secs
                          if isinstance(sec, dict) and isinstance(sec.get("outline"), dict) and isinstance(sec["outline"].get("segs"), list)}
                if len(counts) > 1:
                    warnings.append(f"{where}: sections have different numbers of segments ({sorted(counts)}); the surface may twist")
        if k in ("sweep", "sweep_cut") and "path" in p:
            bad = _path(p["path"])
            if bad:
                errors.append(f"{where}: path: {bad}")
        if _unit(p, ("nx", "ny", "nz")) is False:
            errors.append(f"{where}: nx, ny, nz must be one of +-1 and the others 0")
        if _unit(p, ("ax", "ay", "az")) is False:
            errors.append(f"{where}: ax, ay, az must be one of +-1 and the others 0")
        if "at" in p and not _point(p["at"]):
            errors.append(f"{where}: 'at' must be [x, y, z]")
        if "pts" in p and (not isinstance(p["pts"], list) or not p["pts"] or not all(_point(q) for q in p["pts"])):
            errors.append(f"{where}: 'pts' must be a non-empty list of [x, y, z]")
        if k in PATTERN_KINDS:
            prev = feats[i - 1].get("kind") if i > 0 and isinstance(feats[i - 1], dict) else None
            if prev in PATTERN_KINDS:
                errors.append(f"{where}: a pattern of a pattern is not supported; repeat the original feature instead")
            elif prev in DRESSUPS:
                errors.append(f"{where}: patterns repeat a boss, hole, pocket or revolve, not a {prev}")
            elif prev in BASE_KINDS:
                warnings.append(f"{where}: repeats the base")
        if k == "polar_pattern" and "angle" in p and _unit(p, ("ax", "ay", "az")) is None:
            warnings.append(f"{where}: 'angle' is used only with an axis (ax, ay, az); without one the copies fill 360 degrees")
        if k in ("fillet_edges", "chamfer_edges") and not ({"pts", "nx", "ny", "nz", "ax", "ay", "az"} & set(p)):
            warnings.append(f"{where}: no edge selection given; the top face's edge loop is used")
        if "off" in p and _unit(p, ("nx", "ny", "nz")) is None:
            errors.append(f"{where}: 'off' needs the plane normal nx, ny, nz")
        elif "off" in p and _num(p["off"]) and k in SKETCHED_PLACEABLE and _unit(p, ("nx", "ny", "nz")):
            axis = next(a for a in "xyz" if p.get("n" + a, 0) != 0)
            if _num(p.get(axis)) and abs(p[axis] - p["off"]) > 1e-6:
                warnings.append(f"{where}: the centre's {axis} ({p[axis]}) differs from off ({p['off']}); the plane at {axis} = off is used")
    return {"ok": not errors, "errors": errors, "warnings": warnings}


def validate(goals: dict) -> dict:
    return {name: validate_goal(g) for name, g in goals.items()}
