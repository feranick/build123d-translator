"""standards.py — ISO metric screw holes for hole_std (data only; inlined into standalone scripts).
Version: 2026.10.20.1

Per size: coarse pitch (ISO 261); tap drill = D - P; clearance holes close / normal / loose (ISO 273
fine / medium / coarse); counterbore diameter (DIN 974-1, for ISO 4762 socket head cap screws) and
depth (head height + clearance); countersink diameter at the face, 90 degrees (ISO 10642 head dk,
M3-M20; ISO 7046 for M1.6-M2.5; None: no countersunk screw of that size).
"""

ISO_METRIC = {
    #        pitch  tap    clearance (close, normal, loose)  counterbore (dia, depth)  countersink dia
    "M1.6": (0.35, 1.25, (1.7, 1.8, 2.0), (3.5, 1.9), 3.0),
    "M2": (0.4, 1.6, (2.2, 2.4, 2.6), (4.4, 2.3), 3.8),
    "M2.5": (0.45, 2.05, (2.7, 2.9, 3.1), (5.5, 2.8), 4.7),
    "M3": (0.5, 2.5, (3.2, 3.4, 3.6), (6.5, 3.4), 6.72),
    "M4": (0.7, 3.3, (4.3, 4.5, 4.8), (8.0, 4.4), 8.96),
    "M5": (0.8, 4.2, (5.3, 5.5, 5.8), (10.0, 5.4), 11.2),
    "M6": (1.0, 5.0, (6.4, 6.6, 7.0), (11.0, 6.4), 13.44),
    "M8": (1.25, 6.8, (8.4, 9.0, 10.0), (15.0, 8.6), 17.92),
    "M10": (1.5, 8.5, (10.5, 11.0, 12.0), (18.0, 10.6), 22.4),
    "M12": (1.75, 10.2, (13.0, 13.5, 14.5), (20.0, 12.6), 26.88),
    "M14": (2.0, 12.0, (15.0, 15.5, 16.5), (24.0, 14.6), 30.8),
    "M16": (2.0, 14.0, (17.0, 17.5, 18.5), (26.0, 16.6), 33.6),
    "M20": (2.5, 17.5, (21.0, 22.0, 24.0), (33.0, 20.6), 40.32),
    "M24": (3.0, 21.0, (25.0, 26.0, 28.0), (40.0, 24.8), None),
}
FITS = ("close", "normal", "loose")
HEADS = ("socket", "countersunk")
