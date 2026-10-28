"""goalformat.py — the goal format: kinds, conventions, an example, and static validation.
Version: 2026.10.28.1

A goal file is {name: goal}; a goal is {"features": [{"kind": ..., "params": {...}}, ...]},
one solid part, features in build order, lengths in mm. The format is the one of the
Taiga goal files, so goals carry over between the two tools unchanged.
"""
from __future__ import annotations

import math
import os

from .standards import FITS, HEADS, ISO_METRIC

BASE_KINDS = ("base_box", "base_cyl", "base_hex", "base_ring", "base_sphere", "base_cone", "base_torus", "base_wedge",
              "base_polygon", "profile_base", "profile_revolve", "loft", "sweep", "thicken", "combine")
LATER_BASES = ("profile_revolve", "loft", "sweep", "thicken", "combine")  # also as later features
NON_NUMERIC = ("outline", "sections", "path", "text", "screw")
PATTERN_KINDS = ("polar_pattern", "linear_pattern", "mirror")
SKETCHED_PLACEABLE = ("boss_cyl", "boss_box", "hole", "hole_std", "pocket_rect", "profile_boss", "profile_pocket",
                      "hole_cb", "hole_cs", "text", "slot", "slot_arc")
EXTRUDED = ("boss_cyl", "boss_box", "pocket_rect", "profile_boss", "profile_pocket")  # take taper and until
ANGLED_KINDS = ("boss_cyl", "boss_box", "hole", "pocket_rect", "profile_boss", "profile_pocket", "hole_std", "slot",
                "slot_arc")
HOLED = ("profile_base", "profile_boss", "profile_pocket", "profile_intersect", "sweep", "sweep_cut")  # take holes
SHAPE_ARGS = {"circle": "[cu, cv, r]", "ellipse": "[cu, cv, a, b]", "rect": "[cu, cv, w, d]", "polygon": "[cu, cv, r, n]",
              "slot": "[cu, cv, length, w]", "arc_slot": "[cu, cv, r, w, a1, a2]"}
U_KEYS = ("ux", "uy", "uz")
DRESSUPS = ("fillet_top", "fillet_vertical", "chamfer_top", "shell", "fillet_edges", "chamfer_edges", "draft", "offset",
            "profile_intersect", "split")
_PATTERN_OPT = {"features": "repeat the last k features together (1; a pattern counts as one feature)",
                "all": "true: repeat the whole part instead (the copies must touch it)",
                "skip": "copies to leave out, numbered from 1 (the original); [i, j] on a grid"}
_HOLES = {"holes": "[outline, ...]: closed outlines inside the outline, left open"}

KINDS = {
    "base_box": ({"w": "width along X", "d": "depth along Y", "h": "height along Z"}, {}, "Rectangular block, centred on the origin in X and Y, from z = 0 to h."),
    "base_cyl": ({"r": "radius", "h": "height"}, {}, "Cylinder about Z, centred on the origin, from z = 0 to h."),
    "base_hex": ({"r": "circumradius (a corner on +X)", "h": "height"}, {}, "Hexagonal prism about Z."),
    "base_ring": ({"ri": "inner radius", "ro": "outer radius", "h": "height"}, {}, "Ring about Z, from z = 0 to h."),
    "base_sphere": ({"r": "radius"}, {}, "Sphere resting on z = 0 (centre at (0, 0, r))."),
    "base_cone": ({"r1": "radius at z = 0", "h": "height"}, {"r2": "radius at z = h (0: a point)"},
                  "Cone or frustum about Z, from z = 0 to h."),
    "base_torus": ({"R": "centreline radius", "r": "tube radius (< R)"}, {}, "Torus about Z resting on z = 0 (centre at z = r)."),
    "base_wedge": ({"w": "width along X", "d": "depth along Y", "h": "height"},
                   {"top": "width of the top face (0: a sharp ridge)"},
                   "Ramp: in XZ from x = -w/2 to w/2 at z = 0 and from -w/2 to -w/2 + top at z = h (the slope faces +X), "
                   "over d along Y, centred."),
    "base_polygon": ({"n": "number of sides (>= 3)", "r": "circumradius (a corner on +X)", "h": "height"}, {},
                     "Regular n-sided prism about Z, from z = 0 to h."),
    "profile_base": ({"outline": "closed outline in the XY plane (see outline)", "h": "height"}, dict(_HOLES), "Prism of any closed outline, from z = 0 to h."),
    "profile_revolve": ({"outline": "closed outline (see conventions)", "ax": "axis X", "ay": "axis Y", "az": "axis Z",
                         "nx": "sketch plane normal", "ny": "", "nz": "", "off": "0", "x": "0", "y": "0", "z": "0"},
                        {"angle": "sweep in degrees (360), right-handed about the axis"},
                        "Turned part or collar: the outline revolved about an origin axis; as base or later feature."),
    "profile_groove": ({"outline": "", "ax": "", "ay": "", "az": "", "nx": "", "ny": "", "nz": "", "off": "", "x": "", "y": "", "z": ""},
                       {"angle": ""}, "Removes the outline revolved about an origin axis (ring grooves, bores)."),
    "boss_cyl": ({"r": "radius", "x": "centre x", "y": "centre y", "h": "height"}, {"taper": "draft angle in degrees: positive narrows the boss outward or the pocket inward (needs h or depth, not off)", "until": "'next' or 'last' instead of h or depth (see conventions)"}, "Cylindrical boss on a face (top face by default)."),
    "boss_box": ({"w": "", "d": "", "x": "", "y": "", "h": ""}, {"taper": "", "until": "", "corner_r": "corner radius (0: sharp)"}, "Rectangular boss on a face."),
    "hole": ({"r": "radius", "x": "", "y": ""}, {"depth": "blind hole depth (omit: through all)", "tip": "drill point angle in degrees at the bottom of a blind hole (118); omit: flat"}, "Hole into the material: through all, or blind with depth (and tip)."),
    "hole_std": ({"screw": "ISO metric size: " + ", ".join(ISO_METRIC), "x": "", "y": ""},
                 {"fit": "clearance: close | normal (default) | loose (ISO 273 fine / medium / coarse)",
                  "head": "socket (counterbore for ISO 4762) | countersunk (90 degrees, ISO 10642)",
                  "tapped": "true: the tap drill (D - pitch) instead of a clearance hole",
                  "thread": "with tapped: also cut the internal thread (the hole's whole depth; slower)", "depth": "", "tip": ""},
                 "Hole for an ISO metric screw: clearance (with a counterbore or countersink) or tapped; sizes from the standards."),
    "slot": ({"length": "overall length (>= w)", "w": "width", "x": "centre x", "y": "centre y"},
             {"angle": "direction of the length, degrees from the face's first in-plane axis (0)", "depth": "depth (omit: through all)"},
             "Straight slot (rounded ends) pocket."),
    "slot_arc": ({"r": "centreline radius", "w": "width (< 2 r)", "a1": "start angle, degrees from the face's first in-plane axis",
                  "a2": "end angle (a1 < a2 < a1 + 360), counter-clockwise", "x": "arc centre x", "y": "arc centre y"},
                 {"depth": "depth (omit: through all)"}, "Arc-shaped slot (rounded ends) pocket about (x, y)."),
    "hole_cb": ({"r": "bore radius", "cbr": "counterbore radius (> r)", "cbd": "counterbore depth", "x": "", "y": ""}, {"depth": "", "tip": ""},
                "Counterbored hole from a face (socket-head screws): through all or blind."),
    "hole_cs": ({"r": "bore radius", "csr": "countersink radius at the face (> r)", "x": "", "y": ""},
                {"angle": "countersink included angle in degrees (90)", "depth": "", "tip": ""},
                "Countersunk hole from a face (flat-head screws): through all or blind."),
    "pocket_rect": ({"w": "", "d": "", "x": "", "y": "", "depth": ""}, {"taper": "", "until": "", "corner_r": ""}, "Rectangular pocket."),
    "profile_boss": ({"outline": "closed outline on the face", "h": "height"}, {"taper": "", "until": "", **_HOLES}, "Boss of any closed outline."),
    "profile_pocket": ({"outline": ""}, {"depth": "depth (omit: through all)", "taper": "", "until": "", **_HOLES}, "Pocket of any closed outline."),
    "polar_pattern": ({"n": "number of copies (incl. the original)"},
                      {"ax": "", "ay": "", "az": "axis (default Z; sign: direction of rotation)",
                       "angle": "extent in degrees (360; only with an axis given)", **_PATTERN_OPT},
                      "Repeats the previous feature(s) about an origin axis: 360/n apart for a full turn, angle/(n-1) otherwise."),
    "linear_pattern": ({"n": "copies", "length": "distance first to last"},
                       {"ax": "", "ay": "", "az": "direction (default +X)", "n2": "grid: copies along the second direction",
                        "length2": "grid: distance first to last along it", "bx": "", "by": "",
                        "bz": "grid: second direction (default +Y; +X when the first is not X)", **_PATTERN_OPT},
                       "Repeats the previous feature(s) along an axis, or on a grid (n2, length2)."),
    "mirror": ({}, {"ax": "", "ay": "", "az": "normal of the mirror plane (default X: the YZ plane)",
                    "features": _PATTERN_OPT["features"], "all": "true: mirror the whole part (the halves must touch)"},
               "Mirrors the previous feature(s), or the whole part, across an origin plane."),
    "fillet_top": ({"r": "radius"}, {}, "Fillets the outer edge loop of the top face (the largest face facing +Z)."),
    "fillet_vertical": ({"r": "radius"}, {}, "Fillets every straight vertical edge."),
    "chamfer_top": ({"size": "size (on the top face)"}, {"size2": "asymmetric: the size on the side faces",
                                                         "angle": "asymmetric: size2 = size * tan(angle), degrees"},
                    "Chamfers the outer edge loop of the top face."),
    "shell": ({"t": "wall thickness (inward)"}, {"pts": "points on the faces to remove (default: the top face)",
                                                 "faces_t": "[{pt: [x, y, z], t}, ...]: walls with their own thickness"},
              "Hollows the part, open at the removed faces."),
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
              {"frenet": "true: the profile follows the path's curvature frame (helices); default keeps its orientation smoothly",
               "holes": "[outline, ...]: holes in the profile (tubes, hollow rails)"},
              "Profile swept along a 3D path (pipes, rails, handles, bent tubes); as base or later feature."),
    "sweep_cut": ({"outline": "", "nx": "", "ny": "", "nz": "", "off": "", "path": ""}, {"frenet": "", "holes": ""}, "Removes a swept solid (channels, grooves along a path)."),
    "chamfer_edges": ({"size": "size (on the reference face, if asymmetric)"},
                      {"pts": "", "nx": "", "ny": "", "nz": "", "at": "", "ax": "", "ay": "", "az": "",
                       "size2": "asymmetric: the size on the other face", "angle": "asymmetric: size2 = size * tan(angle)",
                       "ref": "[x, y, z]: reference face of each edge, the one whose normal is closest (selection by pts or axis)"},
                      "Chamfers chosen edges, selected like fillet_edges; symmetric, or asymmetric with size2 or angle."),
    "draft": ({"angle": "draft angle in degrees; positive narrows the part along the pull direction"},
              {"ax": "", "ay": "", "az": "pull direction (default +Z)", "off": "neutral plane position along it (0)",
               "pts": "a point on each face to draft (default: every planar and cylindrical face parallel to the pull)"},
              "Tilts side faces about their line on the neutral plane (cast and moulded parts)."),
    "offset": ({"t": "distance: positive grows the whole part, negative shrinks it"}, {}, "Offsets every face of the part, rounding convex edges."),
    "profile_intersect": ({"outline": "", "nx": "", "ny": "", "nz": ""}, {"off": "plane position (0)", "depth": "total, symmetric (omit: through all)", **_HOLES},
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
    "combine": ({}, {"op": "add (default) | sub | common", "goal": "another goal: its name in the same call, or {features: [...]}",
                     "step": "a STEP file (mm) instead of a goal", "move": "[dx, dy, dz]",
                     "rotate": "[rx, ry, rz]: degrees about X, then Y, then Z (about the origin), before move"},
                "Adds, removes or keeps the common volume of another part (a goal or a STEP file); as the first feature, its base."),
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
    "Patterns and mirror repeat the feature right before them (its added or removed volume); features: k repeats the "
    "last k features together (a pattern counts as one feature, so a pattern of a pattern repeats all its copies); "
    "all: true repeats the whole part (the copies must touch it). They cannot repeat a fillet, chamfer, shell, draft, "
    "offset, intersect, split or a common combine. skip leaves copies out, numbered from 1 (the original, which stays): "
    "[2, 4] in a line or about an axis, [[i, j], ...] on a grid ([1, 1] the original).",
    "Grids: linear_pattern with n2 and length2 repeats along (ax, ay, az) (default +X) and along the second direction "
    "(bx, by, bz) (default +Y, or +X when the first is not X): n x n2 copies, e.g. a 4 x 3 bolt pattern from one hole.",
    "Outline shorthands, anywhere an outline goes (also holes and loft sections): {'circle': [cu, cv, r]}, "
    "{'ellipse': [cu, cv, a, b]} (a along the rotation), {'rect': [cu, cv, w, d], 'corner_r': r}, "
    "{'polygon': [cu, cv, r, n]} (a corner on +u), {'slot': [cu, cv, length, w]}, {'arc_slot': [cu, cv, r, w, a1, a2]}; "
    "'rotation': degrees counter-clockwise (not arc_slot). Elliptical arcs in segments: ['E', cu, cv, a, b, rotation, u, v] "
    "from the current point (on that ellipse) counter-clockwise to (u, v).",
    "Holes in outlines: profile_base, profile_boss, profile_pocket, profile_intersect, sweep and sweep_cut take "
    "holes: [outline, ...], closed outlines inside the outline and apart from each other (a flange with bolt holes in "
    "one feature, a tube swept from a ring).",
    "hole_std: holes for ISO metric screws " + ", ".join(ISO_METRIC) + ": a clearance hole (fit close | normal | loose, "
    "ISO 273), with head socket (a counterbore for ISO 4762 socket head cap screws, DIN 974-1) or countersunk (90 degrees "
    "to the head diameter of ISO 10642; not M24); or tapped: true, the tap drill D - pitch, and with thread: true the "
    "internal thread over the hole's depth. Placed like hole; head and thread need a face to start from.",
    "Asymmetric chamfers: size on the reference face, size2 (or size * tan(angle)) on the other face. chamfer_top: "
    "the top face; chamfer_edges selected by a face's normal: that face; selected by pts or axis: ref [x, y, z], the "
    "adjacent face whose normal is closest to it.",
    "combine: another part, a goal (its name among the goals of the same call, or the goal itself) or a STEP file, "
    "rotated (rx, then ry, then rz degrees about the origin) and moved, then added, removed (cavities, clearance for a "
    "mating part) or intersected with the part; as the first feature it is the base (op add).",
    "Bases sit on z = 0: base_sphere and base_torus rest on it; base_cone runs from r1 at z = 0 to r2 at z = h; base_wedge "
    "is a ramp sloping down towards +X.",
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
    "(countersink radius csr at the face, included angle) start from a face, not an origin plane. Patterns repeat them. "
    "Slots: slot (straight, overall length) and slot_arc (along an arc about (x, y)) are pockets with rounded ends.",
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
    "Angled planes (sketches at any angle: inclined bores, angled pins and bosses, slanted windows; as Taiga's patch "
    "0020): give the plane's in-plane x direction ux, uy, uz with any non-zero normal nx, ny, nz (not limited to +-1) and "
    "the plane's origin x, y, z, which is the feature's centre. Frame: n = (nx, ny, nz) normalised, u = (ux, uy, uz) "
    "made perpendicular to n, v = n x u. Circles and rectangles are centred on the origin (w along u, d along v); "
    "outlines are in (u, v) relative to the origin. The extrusion is symmetric about the plane (h or depth is the "
    "total); a hole, pocket_rect, profile_pocket, slot or hole_std without depth goes through all, both ways. Only "
    + ", ".join(ANGLED_KINDS) + "; no off, at, until, taper or tip (hole_std: no head or thread).",
    "One goal is one solid part; several separate solids are several goals.",
    "Pictures: a goal's \"images\": true (a shaded view, and the faces coloured by the feature that made them, front and "
    "back) or [{name, color: part | feature, view: iso | iso_back | top | bottom | front | back | left | right | [elev, "
    "azim], highlight: [feature numbers], edges, quality: high (VTK) | basic, slice: {axis, at}}] writes PNGs with the "
    "build (parts.<name>.images.<view>.path; get_image shows them): check by eye that every feature went where it "
    "should. render_images draws other views later.",
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
        if k in ANGLED_KINDS:
            o.update({"ux": "angled plane: in-plane x direction (with nx, ny, nz any non-zero normal; x, y, z the origin)",
                      "uy": "", "uz": ""})
        kinds[k] = {"what": what, "required": list(req), "optional": list(o),
                    "descriptions": {p: d for p, d in {**req, **o}.items() if d}}
    screws = {s: {"pitch": v[0], "tap_drill": v[1], "clearance": dict(zip(FITS, v[2])), "counterbore": list(v[3]),
                  "countersink": v[4]} for s, v in ISO_METRIC.items()}
    return {"format": "goal (build123d-translator)", "angled_planes": True, "conventions": CONVENTIONS, "kinds": kinds,
            "example": EXAMPLE, "screws": screws,
            "limits": "Up to a few hundred features per goal; lengths of 0.1 mm to a few metres."}


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _point(p) -> bool:
    return isinstance(p, (list, tuple)) and len(p) == 3 and all(_num(c) for c in p)


def _shape(outline: dict) -> str | None:
    """None if an outline shorthand ({'circle': [...]} etc.) is well formed, else what is wrong."""
    keys = [s for s in SHAPE_ARGS if s in outline]
    if len(keys) > 1:
        return f"one shape per outline, not {keys}"
    s = keys[0]
    a = outline[s]
    n = SHAPE_ARGS[s].count(",") + 1
    if not isinstance(a, (list, tuple)) or len(a) != n or not all(_num(c) for c in a):
        return f"'{s}' needs {SHAPE_ARGS[s]}"
    extra = set(outline) - {s} - ({"rotation"} if s != "arc_slot" else set()) - ({"corner_r"} if s == "rect" else set())
    if extra:
        return f"unknown keys {sorted(extra)} in a '{s}' outline"
    if "rotation" in outline and not _num(outline["rotation"]):
        return "'rotation' must be a number of degrees"
    if s == "circle" and a[2] <= 0 or s in ("ellipse", "rect") and (a[2] <= 0 or a[3] <= 0):
        return f"'{s}' sizes must be positive"
    if s == "rect" and "corner_r" in outline and (not _num(outline["corner_r"]) or not 0 <= outline["corner_r"] <= min(a[2], a[3]) / 2):
        return "'corner_r' must be between 0 and half the smaller side"
    if s == "polygon" and (a[2] <= 0 or a[3] < 3 or int(a[3]) != a[3]):
        return "'polygon' needs r > 0 and a whole n >= 3"
    if s == "slot" and not 0 < a[3] <= a[2]:
        return "'slot' needs 0 < w <= length"
    if s == "arc_slot" and not (a[2] > 0 and 0 < a[3] < 2 * a[2] and 0 < a[5] - a[4] < 360):
        return "'arc_slot' needs r > 0, 0 < w < 2 r and a1 < a2 < a1 + 360"
    return None


def _on_ellipse(seg, u, v) -> bool:
    cu, cv, a, b, deg = seg[1:6]
    r = math.radians(deg)
    du, dv = u - cu, v - cv
    x, y = du * math.cos(r) + dv * math.sin(r), -du * math.sin(r) + dv * math.cos(r)
    return abs((x / a) ** 2 + (y / b) ** 2 - 1) < 1e-4


def _closed(outline, closed: bool = True) -> str | None:
    if isinstance(outline, dict) and any(s in outline for s in SHAPE_ARGS):
        if not closed:
            return "a curve takes segments, not a shape shorthand"
        return _shape(outline)
    if not isinstance(outline, dict) or not isinstance(outline.get("start"), (list, tuple)) or len(outline["start"]) != 2:
        return "outline needs 'start': [u, v] (or a shorthand: " + ", ".join(SHAPE_ARGS) + ")"
    segs = outline.get("segs")
    if not isinstance(segs, list) or not segs:
        return "outline needs a non-empty 'segs' list"
    last = outline["start"]
    for s in segs:
        if not isinstance(s, (list, tuple)) or not s or s[0] not in ("L", "A", "S", "E"):
            return f"bad segment {s!r}: use ['L', u, v], ['A', um, vm, u, v], ['S', [[u, v], ...]] or ['E', cu, cv, a, b, rotation, u, v]"
        if s[0] == "L" and len(s) == 3 and _num(s[1]) and _num(s[2]):
            last = [s[1], s[2]]
        elif s[0] == "A" and len(s) == 5 and all(_num(c) for c in s[1:]):
            last = [s[3], s[4]]
        elif s[0] == "S" and len(s) == 2 and isinstance(s[1], list) and s[1] and all(
                isinstance(q, (list, tuple)) and len(q) == 2 and all(_num(c) for c in q) for q in s[1]):
            last = list(s[1][-1])
        elif s[0] == "E" and len(s) == 8 and all(_num(c) for c in s[1:]):
            if s[3] <= 0 or s[4] <= 0:
                return f"segment {s!r}: the ellipse's a and b must be positive"
            if not (_num(last[0]) and _num(last[1]) and _on_ellipse(s, *last)) or not _on_ellipse(s, s[6], s[7]):
                return f"segment {s!r}: its start (the previous point) and end must lie on its ellipse"
            last = [s[6], s[7]]
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


def _angled_errors(k: str, p: dict) -> list[str]:
    """Errors of a feature on an angled plane (ux, uy, uz given), with Taiga's (patch 0020) messages."""
    errs = []
    if not all(key in p for key in U_KEYS):
        return ["an angled plane needs all of ux, uy, uz"]
    if k not in ANGLED_KINDS:
        errs.append(f"only {', '.join(ANGLED_KINDS)} can be on an angled plane")
    nv = [p.get(key, 0) for key in ("nx", "ny", "nz")]
    uv = [p.get(key, 0) for key in U_KEYS]
    if not all(_num(x) for x in nv + uv):
        errs.append("an angled plane needs numbers nx, ny, nz and ux, uy, uz")
    else:
        nn = sum(x * x for x in nv) ** 0.5
        un = sum(x * x for x in uv) ** 0.5
        if nn < 1e-12:
            errs.append("angled plane: zero normal")
        elif un < 1e-12 or abs(sum(a * b for a, b in zip(nv, uv))) / nn > 0.999 * un:
            errs.append("angled plane: u is parallel to the normal")
    for key in ("x", "y", "z"):
        if key in p and not _num(p[key]):
            errs.append(f"'{key}' must be a number")
    if "off" in p:
        errs.append("an angled plane takes its position from x, y, z, not 'off'")
    for key, why in (("at", "an angled plane takes its position from x, y, z, not 'at'"),
                     ("until", "until is not available on an angled plane (Taiga has no equivalent)"),
                     ("taper", "taper is not available on an angled plane (Taiga has no equivalent)"),
                     ("tip", "tip is not available on an angled plane (Taiga has no equivalent)")):
        if key in p:
            errs.append(why)
    return errs


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
        if i > 0 and k in BASE_KINDS and k not in LATER_BASES:
            errors.append(f"{where}: a base kind can only be the first feature")
        angled = any(key in p for key in U_KEYS)
        for r in KINDS[k][0]:
            if r in ("h", "depth") and k in EXTRUDED and "until" in p:
                continue
            if r == "depth" and angled:  # on an angled plane a pocket without depth goes through all
                continue
            if r not in p:
                errors.append(f"{where}: missing '{r}'")
            elif r not in NON_NUMERIC and not _num(p[r]):
                errors.append(f"{where}: '{r}' must be a number")
        for key in ("w", "d", "h", "r", "ri", "ro", "depth", "size", "length", "cbr", "cbd", "csr", "R", "r1", "length2",
                    "size2") + (("t",) if k != "offset" else ()):
            if key in p and _num(p[key]) and p[key] <= 0:
                errors.append(f"{where}: '{key}' must be positive")
        for key in ("r2", "top", "corner_r", "angle", "a1", "a2", "length2", "size2"):
            if key in p and not _num(p[key]):
                errors.append(f"{where}: '{key}' must be a number")
        for key in ("r2", "top", "corner_r"):
            if _num(p.get(key)) and p[key] < 0:
                errors.append(f"{where}: '{key}' must not be negative")
        if "ri" in p and "ro" in p and _num(p["ri"]) and _num(p["ro"]) and p["ri"] >= p["ro"]:
            errors.append(f"{where}: 'ri' must be smaller than 'ro'")
        for key in ("n", "n2"):
            if key in p and (not _num(p[key]) or p[key] < 2 or int(p[key]) != p[key]):
                errors.append(f"{where}: '{key}' must be a whole number, at least 2")
        if "outline" in p:
            bad = _closed(p["outline"])
            if bad:
                errors.append(f"{where}: {bad}")
        if "holes" in p:
            if k not in HOLED:
                errors.append(f"{where}: 'holes' is for {', '.join(HOLED)}")
            elif not isinstance(p["holes"], list) or not p["holes"]:
                errors.append(f"{where}: 'holes' must be a non-empty list of closed outlines")
            else:
                for j, h in enumerate(p["holes"]):
                    bad = _closed(h)
                    if bad:
                        errors.append(f"{where}: hole {j + 1}: {bad}")
        errors += [f"{where}: {e}" for e in _kind_errors(k, p, i, feats)]
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
        if angled:
            errors += [f"{where}: {e}" for e in _angled_errors(k, p)]
        elif _unit(p, ("nx", "ny", "nz")) is False:
            errors.append(f"{where}: nx, ny, nz must be one of +-1 and the others 0")
        if _unit(p, ("ax", "ay", "az")) is False:
            errors.append(f"{where}: ax, ay, az must be one of +-1 and the others 0")
        if _unit(p, ("bx", "by", "bz")) is False:
            errors.append(f"{where}: bx, by, bz must be one of +-1 and the others 0")
        if "at" in p and not _point(p["at"]):
            errors.append(f"{where}: 'at' must be [x, y, z]")
        if "pts" in p and (not isinstance(p["pts"], list) or not p["pts"] or not all(_point(q) for q in p["pts"])):
            errors.append(f"{where}: 'pts' must be a non-empty list of [x, y, z]")
        if k in PATTERN_KINDS:
            nf = p.get("features", 1)
            if p.get("all") is True:
                if "features" in p:
                    errors.append(f"{where}: 'all' repeats the whole part; leave out 'features'")
            elif "all" in p and not isinstance(p["all"], bool):
                errors.append(f"{where}: 'all' must be true or false")
            elif not _num(nf) or int(nf) != nf or not 1 <= nf <= i:
                errors.append(f"{where}: 'features' must be a whole number from 1 to {i} (the features before it)")
            else:
                for j in range(i - int(nf), i):
                    g = feats[j]
                    prev = g.get("kind") if isinstance(g, dict) else None
                    if prev in DRESSUPS or (prev == "combine" and (g.get("params") or {}).get("op") == "common"):
                        errors.append(f"{where}: patterns repeat bosses, holes, pockets, revolves, patterns and combines, "
                                      f"not a {prev}{' common' if prev == 'combine' else ''} (or use all: true)")
                    elif j == 0:
                        warnings.append(f"{where}: repeats the base")
        if k == "polar_pattern" and "angle" in p and _unit(p, ("ax", "ay", "az")) is None:
            warnings.append(f"{where}: 'angle' is used only with an axis (ax, ay, az); without one the copies fill 360 degrees")
        if k in ("fillet_edges", "chamfer_edges") and not ({"pts", "nx", "ny", "nz", "ax", "ay", "az"} & set(p)):
            warnings.append(f"{where}: no edge selection given; the top face's edge loop is used")
        if angled:
            pass
        elif "off" in p and _unit(p, ("nx", "ny", "nz")) is None:
            errors.append(f"{where}: 'off' needs the plane normal nx, ny, nz")
        elif "off" in p and _num(p["off"]) and k in SKETCHED_PLACEABLE and _unit(p, ("nx", "ny", "nz")):
            axis = next(a for a in "xyz" if p.get("n" + a, 0) != 0)
            if _num(p.get(axis)) and abs(p[axis] - p["off"]) > 1e-6:
                warnings.append(f"{where}: the centre's {axis} ({p[axis]}) differs from off ({p['off']}); the plane at {axis} = off is used")
    if isinstance(goal, dict) and "images" in goal:
        from .render import image_errors
        errors += image_errors(goal["images"], goal)
    return {"ok": not errors, "errors": errors, "warnings": warnings}


def _kind_errors(k: str, p: dict, i: int, feats: list) -> list[str]:
    """Checks of the kinds and options added in 2026.10.20.1."""
    e = []
    if k == "base_polygon" and _num(p.get("n")) and p["n"] < 3:
        e.append("'n' must be at least 3")
    if k == "base_torus" and _num(p.get("r")) and _num(p.get("R")) and p["r"] >= p["R"]:
        e.append("'r' must be smaller than 'R'")
    if k == "base_wedge" and _num(p.get("top")) and _num(p.get("w")) and p["top"] > p["w"]:
        e.append("'top' must not exceed 'w'")
    if k in ("boss_box", "pocket_rect") and _num(p.get("corner_r")) and _num(p.get("w")) and _num(p.get("d")) \
            and p["corner_r"] > min(p["w"], p["d"]) / 2:
        e.append("'corner_r' must not exceed half the smaller side")
    if k == "slot" and _num(p.get("length")) and _num(p.get("w")) and p["length"] < p["w"]:
        e.append("'length' (overall) must be at least 'w'")
    if k == "slot_arc" and all(_num(p.get(q)) for q in ("r", "w", "a1", "a2")):
        if not 0 < p["a2"] - p["a1"] < 360:
            e.append("'a2' must be more than 'a1' and less than a1 + 360")
        if p["w"] >= 2 * p["r"]:
            e.append("'w' must be smaller than 2 r")
    if k == "hole_std":
        angled = any(q in p for q in ("ux", "uy", "uz"))
        if "screw" in p and p["screw"] not in ISO_METRIC:
            e.append(f"'screw' must be one of {', '.join(ISO_METRIC)}")
        if "fit" in p and p["fit"] not in FITS:
            e.append(f"'fit' must be one of {', '.join(FITS)}")
        if "head" in p and p["head"] not in HEADS:
            e.append(f"'head' must be one of {', '.join(HEADS)}")
        for q in ("tapped", "thread"):
            if q in p and not isinstance(p[q], bool):
                e.append(f"'{q}' must be true or false")
        if p.get("tapped") and "head" in p:
            e.append("a tapped hole takes no 'head'")
        if p.get("thread") and not p.get("tapped"):
            e.append("'thread' needs tapped: true")
        if p.get("head") == "countersunk" and ISO_METRIC.get(p.get("screw"), (0, 0, 0, 0, 1))[4] is None:
            e.append(f"no countersunk screw of size {p['screw']}")
        if ("head" in p or p.get("thread")) and ("off" in p or angled):
            e.append("a head or a modelled thread needs a face to start from (not an origin or angled plane)")
    if k in ("chamfer_top", "chamfer_edges"):
        if "size2" in p and "angle" in p:
            e.append("give one of 'size2' or 'angle'")
        if _num(p.get("angle")) and not 0 < p["angle"] < 90:
            e.append("'angle' must be between 0 and 90 degrees")
        asym = "size2" in p or "angle" in p
        if k == "chamfer_edges" and asym and "ref" not in p and ("pts" in p or {"ax", "ay", "az"} & set(p)):
            e.append("an asymmetric chamfer of edges chosen by pts or axis needs 'ref' [x, y, z] (its reference face)")
        if "ref" in p and (not _point(p["ref"]) or not any(p["ref"])):
            e.append("'ref' must be a non-zero [x, y, z]")
        if "ref" in p and k == "chamfer_top":
            e.append("chamfer_top's reference is its top face; 'ref' is for chamfer_edges")
    if k == "shell" and "faces_t" in p:
        ft = p["faces_t"]
        if not isinstance(ft, list) or not ft or not all(
                isinstance(s, dict) and _point(s.get("pt")) and _num(s.get("t")) and s["t"] > 0 for s in ft):
            e.append("'faces_t' must be a list of {pt: [x, y, z], t > 0}")
    if k in PATTERN_KINDS and "skip" in p:
        grid = k == "linear_pattern" and "n2" in p
        sk = p["skip"]
        n, n2 = p.get("n", 2), p.get("n2", 1)
        if k == "mirror":
            e.append("'skip' is for polar_pattern and linear_pattern")
        elif not isinstance(sk, list):
            e.append("'skip' must be a list")
        elif grid and not all(isinstance(s, (list, tuple)) and len(s) == 2 and all(_num(c) and int(c) == c for c in s)
                              and 1 <= s[0] <= n and 1 <= s[1] <= n2 and list(s) != [1, 1] for s in sk):
            e.append(f"'skip' on a grid: [[i, j], ...] with 1 <= i <= {n}, 1 <= j <= {n2}, not [1, 1] (the original)")
        elif not grid and not all(_num(s) and int(s) == s and 2 <= s <= n for s in sk):
            e.append(f"'skip': copy numbers 2 to {n} (1 is the original)")
    if k == "linear_pattern" and ("n2" in p) != ("length2" in p):
        e.append("a grid needs both 'n2' and 'length2'")
    if k == "linear_pattern" and any(q in p for q in ("bx", "by", "bz")) and "n2" not in p:
        e.append("bx, by, bz are the grid's second direction: give n2 and length2")
    if k == "linear_pattern" and _unit(p, ("bx", "by", "bz")) and (_unit(p, ("ax", "ay", "az")) or not any(q in p for q in ("ax", "ay", "az"))):
        a = [abs(p.get(q, 0)) for q in ("ax", "ay", "az")] if any(q in p for q in ("ax", "ay", "az")) else [1, 0, 0]
        b = [abs(p.get(q, 0)) for q in ("bx", "by", "bz")]
        if a == b:
            e.append("the grid's two directions must differ")
    if k == "combine":
        if p.get("op", "add") not in ("add", "sub", "common"):
            e.append("'op' must be add, sub or common")
        if i == 0 and p.get("op", "add") != "add":
            e.append("as the first feature, combine must add (op 'add')")
        if ("goal" in p) == ("step" in p):
            e.append("give one of 'goal' or 'step'")
        if "goal" in p:
            g = p["goal"]
            if isinstance(g, dict):
                v = validate_goal(g)
                e += [f"goal: {x}" for x in v["errors"]]
            elif not isinstance(g, str) or not g:
                e.append("'goal' must be a goal's name or a goal {features: [...]}")
        if "step" in p:
            if not isinstance(p["step"], str) or not p["step"]:
                e.append("'step' must be a file path")
            elif not os.path.isfile(os.path.expanduser(p["step"])):
                e.append(f"no STEP file at {p['step']}")
        for q in ("move", "rotate"):
            if q in p and not _point(p[q]):
                e.append(f"'{q}' must be [x, y, z]")
    return e


def _refs(goal) -> list[str]:
    """Names of the goals a goal combines (by name), also inside inline goals."""
    out = []
    for f in (goal.get("features") or []) if isinstance(goal, dict) else []:
        g = (f.get("params") or {}).get("goal") if isinstance(f, dict) and f.get("kind") == "combine" else None
        if isinstance(g, str):
            out.append(g)
        elif isinstance(g, dict):
            out += _refs(g)
    return out


def resolve(goals: dict) -> dict:
    """The goals with every combine by name replaced by that goal itself (for building). Unknown names
    and cycles are left as names: the builder reports them."""
    def inline(goal, seen):
        if not isinstance(goal, dict) or not isinstance(goal.get("features"), list):
            return goal
        feats = []
        for f in goal["features"]:
            prm = f.get("params") if isinstance(f, dict) else None
            if isinstance(f, dict) and f.get("kind") == "combine" and isinstance(prm, dict):
                g = prm.get("goal")
                if isinstance(g, str) and g in goals and g not in seen:
                    f = {**f, "params": {**prm, "goal": inline(goals[g], seen | {g})}}
                elif isinstance(g, dict):
                    f = {**f, "params": {**prm, "goal": inline(g, seen)}}
            feats.append(f)
        return {**goal, "features": feats}

    return {n: inline(g, {n}) for n, g in goals.items()}


def validate(goals: dict) -> dict:
    out = {name: validate_goal(g) for name, g in goals.items()}
    for name, g in goals.items():  # combine by name: the goal must be in the same call, without cycles
        for ref in _refs(g):
            if ref not in goals:
                out[name]["errors"].append(f"combine: no goal named {ref!r} in this call")
                out[name]["ok"] = False
        seen, stack = set(), [(name, [name])]
        while stack:
            cur, path = stack.pop()
            for ref in _refs(goals.get(cur)):
                if ref == name:
                    out[name]["errors"].append(f"combine: goals refer to each other in a cycle ({' -> '.join(path + [ref])})")
                    out[name]["ok"] = False
                    stack = []
                    break
                if ref in goals and ref not in seen:
                    seen.add(ref)
                    stack.append((ref, path + [ref]))
    return out
