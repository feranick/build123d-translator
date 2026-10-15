# Pillow block example

A small pillow block made of three solid parts, one goal each, in [`pillow_block.json`](pillow_block.json). Units are mm, Z up.

| Goal | Part | Size (mm) | Volume (mm³) |
|---|---|---|---|
| `pillow_base` | Base plate with two 8 mm slots (14 mm long, along X) at x = ±38 | 100 × 40 × 10 | 38,034.69 |
| `bearing_housing` | Tube along X, Ø50 outside, Ø32 bore | 30 × 50 × 50 | 34,777.43 |
| `end_cap` | Disc with 4 × Ø4.5 holes (M4 clearance) on a Ø40 circle | Ø50 × 3 | 5,699.63 |

The volumes are from build 2026.10.15.2 and match the hand calculation from the dimensions.

## Build

From the repository root, with the package installed (see the main [README](../../README.md#install)):

```bash
b3dt validate examples/pillow_block/pillow_block.json
b3dt build examples/pillow_block/pillow_block.json
```

The build writes `<part>.step` and `<part>.py` for each part to `~/b3d_parts/build_<time>/`, or to the folder set by `B3DT_OUT`.

## Rebuild from a script

Each `<part>.py` is standalone: it needs only build123d (`pip install build123d`, Python 3.10+), not this package. Run it in the build folder:

```bash
python end_cap.py        # writes end_cap.step and prints its volume
```

The script writes the `.step` to the current folder, overwriting any file of that name. The printed volume should match the table above.

## Change a part

The end of each script lists one line per feature, with the goal's parameters as arguments. For example, `end_cap.py`:

```python
b = Builder(scale=50)
b.base_cyl(r=25, h=3)          # Ø50 × 3 disc
b.hole(r=2.25, x=20, y=0)      # Ø4.5 hole on the Ø40 circle
b.polar_pattern(n=4)           # 4 holes, 90° apart
```

`r` is a radius, not a diameter. Edits made in a script are not validated. For larger changes, edit `pillow_block.json` and run `b3dt validate` and `b3dt build` again, so the goal stays the source of truth.

## Assembly

Each part is modelled at its own origin.

- **Base:** centred on the origin, from z = 0 to 10.
- **Housing:** its axis is on X through the origin. To sit it on the base, move it up by z = +35 (10 mm base + 25 mm housing radius).
- **End caps:** modelled flat in XY, from z = 0 to 3. Two are needed, one at each end of the housing (x = ±15). To fit one, rotate it so its face is normal to X.

## Notes

- The 8 mm slots are the nominal M8 size, so the bolt will be a tight fit. Use 9 mm (`r` 4.5, with the outline widened to match) for normal M8 clearance.
- The housing has no M4 tapped holes for the cap screws yet.

## View in 3D (optional)

Install the OCP CAD Viewer extension for VS Code (`pip install ocp_vscode`), then add these lines at the end of a script:

```python
from ocp_vscode import show
show(part)
```

Other viewers are listed in the build123d docs under [External Tools and Libraries](https://build123d.readthedocs.io/en/latest/external.html).
