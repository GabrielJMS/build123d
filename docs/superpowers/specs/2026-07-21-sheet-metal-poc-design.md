# Sheet Metal POC for build123d — Design

**Date:** 2026-07-21
**Status:** Approved by Gabriel (user), pending maintainer feedback via draft PR + issue #305 comment
**References:**
- gumyr's API ideation: https://github.com/gumyr/build123d/issues/305#issuecomment-4289307747
- Reference implementation: FreeCAD SheetMetal workbench (local clone: `/home/gabriel/Documentos/open_source/FreeCAD_SheetMetal`)

## Goal

A tiny proof-of-concept that establishes the architecture and modeling technique for
sheet metal design in build123d, opening the way for other contributors. It translates
three FreeCAD SheetMetal capabilities — `smBase` (base sheet from sketch), `AddWall`,
and `AddHem` — into build123d idioms shaped like gumyr's `BuildSheet` ideation.

**Explicitly out of scope** (listed in the PR as a contribution roadmap): `unfold`,
`Bend` between two sketch regions, `BendLine`, `Tab`, bend reliefs, auto/manual miters,
perforation, open-profile bases, `smCreateBaseShape` parametric starters
(flat/L/U/tub/hat/box), and `Material` integration.

Open-profile bases (FreeCAD `smBase`'s wire branch) have a sketched follow-up design:
a `base_sheet(width, side)` operation consuming a `BuildLine` profile from the pending
edges (mirroring `BuildSketch` → `extrude` in `BuildPart`), auto-filleting sharp
corners at the bend radius (FreeCAD pre-rounds the mid-surface wire at
`radius + thickness/2`), thickening the wire into a cross-section face and extruding
once — which naturally keeps bend cylinders as distinct faces. Until then,
`make_brake_formed` is the open-profile answer — and the POC enables it inside
`BuildSheet` (see below).

## User-facing API

```python
from build123d import *

with BuildSheet(thickness=1.0, bend_radius=1.5, k_factor=0.44) as box:
    with BuildSketch() as region:          # base region: exiting the sketch
        Rectangle(100, 60)                 # auto-pads it into the base sheet
    top_edges = box.faces().sort_by(Axis.Z)[-1].edges().filter_by(Axis.X)
    flange(top_edges, length=20, angle=90)
    hem(box.faces().sort_by(Axis.Z)[-1].edges().filter_by(Axis.X), hem_type=HemType.FLAT)

part = box.sheet   # a Part; bend faces preserved (never unified)
```

### `BuildSheet(thickness: float, bend_radius: float | None = None, k_factor: float = 0.5, *workplanes, mode=Mode.ADD)`

`bend_radius` defaults to `thickness` when not given (common shop rule of thumb).

Builder context holding sheet-wide defaults. `k_factor` is stored now, consumed only by
the future `unfold`. The result is exposed as `.sheet` and is a plain `Part` — no new
composite type in the POC (open question for gumyr: dedicated `Sheet` type later?).

**Base creation (covers `smBase`)**: a closed `BuildSketch` region exiting into
`BuildSheet` is automatically padded by `thickness` — no explicit `extrude`, matching
gumyr's "the BuildSheet context would be generating the 3d objects". `Mode.SUBTRACT`
regions cut holes.

### `flange(edges, length, angle=90, radius=None, gap1=0, gap2=0, bend_position=BendPosition.MATERIAL_OUTSIDE, clean=False, mode=Mode.ADD, thickness=None)`

Covers `AddWall`, minimal-core parameter surface. `radius` defaults to the context's
`bend_radius`. Selected edges are straight edges at the junction of a sheet face and a
thickness face (FreeCAD-style edge selection). `length` is the flat leg beyond the bend
(FreeCAD `LengthSpec="Leg"` semantics only). `thickness` is required only in algebra
mode (no context); otherwise inferred from the context (fallback: thickness-face short
dimension).

`BendPosition` ∈ {`MATERIAL_OUTSIDE` (default), `MATERIAL_INSIDE`, `THICKNESS_OUTSIDE`}
— FreeCAD's `BendType` minus the raw Offset variant. The inside variants first cut a
rectangular slab (offset `-(thickness+radius)` / `-radius` respectively) so the fold
consumes existing material.

### `hem(edges, hem_type=HemType.FLAT, width=None, opening=0, radius=None, roll_angle=None, clean=False, mode=Mode.ADD, thickness=None)`

(`roll_angle=None` → the physical maximum `270° + asin(r/(r+t))`, matching
FreeCAD, rather than a fixed 270° — decided during implementation.)

`width` (total hem width, including the bend) is required for FLAT/OPEN/TEARDROP and
ignored for ROLLED; `radius` is used by TEARDROP/ROLLED and defaults to the context's
`bend_radius`; `roll_angle` applies to ROLLED only.

Covers `AddHem`, all four types. Each type is a pure parameter generator returning
`(leg_length, bend_angle, bend_radius)` fed to the same engine as `flange`:

- `FLAT` / `OPEN`: `bend_angle = 180°`, `bend_radius = opening / 2` (FLAT ⇒ opening 0)
- `ROLLED`: `leg_length = 0`, `bend_angle = roll_angle`, `bend_radius = radius`
- `TEARDROP`: bisection solve of FreeCAD's residual
  `L − Lp + Lbend + t·sin(2·atan(R/L)) = 0` → angle `180° + 2·atan(R/L)`

### `make_brake_formed` enabled inside `BuildSheet`

The existing operation is registered for `BuildSheet`
(`"make_brake_formed": ["BuildPart", "BuildSheet"]` in `operations_apply_to`) and
`BuildSheet._add_to_pending` stores edges, so a `BuildLine` profile exiting into
`BuildSheet` feeds it via `context.pending_edges` — an interim open-profile base:

```python
with BuildSheet(thickness=1) as bracket:
    with BuildLine() as profile:
        FilletPolyline((0, 0), (20, 0), (20, 15), radius=2)  # arcs pre-drawn
    make_brake_formed(thickness=1, station_widths=30)
```

Because in-context it delegates `clean` to `_add_to_context`
(`operations_part.py:450`) and `BuildSheet` forces `clean=False`, bend faces survive
automatically. Documented caveats: `thickness` is passed explicitly (signature change
to default from the context is deferred — touches an existing public API, open
question for gumyr), and sharp corners must be pre-drawn as arcs (no auto-fillet,
unlike FreeCAD's `smBase`).

### Conventions honored

- Operations are lowercase module-level **functions** (not classes), per build123d's
  object=class / operation=function convention. gumyr's sketch shows capitalized
  `Flange(...)` but he was explicitly unsure ("object or operation"); this is flagged
  as an open question in the PR.
- Both operations work in **algebra mode** (no context), mirroring every other
  operation. Parent solid found via the edges' `topo_parent`.
- New enums `HemType`, `BendPosition` in `build_enums.py`.

## Architecture

| File | Content |
|---|---|
| `src/build123d/build_sheet.py` | `class BuildSheet(Builder[Part])`: `_tag="BuildSheet"`, `_obj_name="sheet"`, `_shape=Solid`, `_sub_class=Part`; stores `thickness`, `bend_radius`, `k_factor`; `_add_to_context` override auto-pads incoming `Face`s and forces `clean=False` |
| `src/build123d/operations_sheet.py` | `flange()`, `hem()`, private `_make_bend()` engine, `_hem_parameters()` generators |
| `src/build123d/build_enums.py` | `HemType`, `BendPosition` |
| `src/build123d/build_common.py` | register `"flange"`, `"hem"` in `operations_apply_to`; add `"BuildSheet"` to `"make_brake_formed"` |
| `src/build123d/__init__.py` | module import + `__all__` entries |
| `tests/test_build_sheet.py` | full test suite (below) |
| `docs/build_sheet.rst` | sheet metal section page mirroring `build_part.rst`: concepts (thickness, bend radius, k-factor, fan-face/no-clean warning), API usage, autodoc references |
| `docs/tutorial_sheet_metal.rst` | step-by-step tutorial (`tutorial_*.rst` convention) building a small sheet metal box: base region → flanges → hems |
| docs wiring | `tutorials.rst` toctree entry; `builders.rst` entry for `BuildSheet`; `builder_api_reference.rst` autodoc for `BuildSheet`; `operations.rst` table rows for `flange`/`hem`; tutorial code exercised by the docs-example tests |

## Geometry engine: `_make_bend()`

Direct translation of FreeCAD's `smBend` (`SheetMetalCmd.py:1241`), the single engine
serving walls, hems, and (in FreeCAD) base shapes:

1. **Derive bend frame from the selected edge.** Of the edge's two adjacent faces, the
   one whose short dimension ≈ `thickness` is the *thickness face*; its outward normal
   is `face_dir` (direction the wall extends before rotation). The other adjacent face
   is the sheet face; from it derive `thk_dir` (into the material). Bend axis:
   `axis_point = edge_start + thk_dir * (radius + thickness)`, `axis_dir` = edge
   direction oriented so `thk_dir × axis_dir == face_dir` (FreeCAD `smEdge` /
   `getBendetail`).
2. **Bend sector**: a `thickness`-wide rectangle face on the (gap-trimmed) edge,
   revolved about the bend axis by `bend_angle` (`Solid.revolve`).
3. **Wall**: rectangle face `leg_length × trimmed edge`, extruded through thickness
   (`Face.extrude`), rotated about the bend axis by `bend_angle`.
4. **Inside bend positions**: cut the offset slab from the sheet before fusing.
5. **Fuse without cleaning**: `sheet.fuse(bend_sector, wall)` and **never**
   `.clean()`.

### The fan-face preservation technique (load-bearing)

The revolved bend sector's two planar end caps are annular ("fan") sectors, coplanar
with the wall's flat thickness faces. `Shape.clean()` runs OCCT
`ShapeUpgrade_UnifySameDomain`, which would merge each fan face with the adjacent flat
face into one D-shaped face — destroying the bend topology a future unfolder needs to
detect bends. FreeCAD's wall/hem path deliberately never calls `removeSplitter()` (its
other commands do); we mirror this:

- `flange` / `hem` default `clean=False` and pass it through;
- `BuildSheet._add_to_context` forces `clean=False` on all booleans;
- docstrings warn users not to `.clean()` sheet parts.

## Error handling

`ValueError` with specific messages for: non-linear selected edge; edge not at a
sheet-face/thickness-face junction (or thickness underivable in algebra mode);
`length <= 0`; `radius < 0`; flange `angle` outside `(0°, 270°]` (hem generators
validate their own ranges — e.g. ROLLED allows up to `270° + asin(r/(r+t))` per
FreeCAD); non-converging teardrop bisection. Wrong-builder usage is caught by the standard `validate_inputs` machinery.

## Testing

`tests/test_build_sheet.py` (unittest style, like the rest of the suite):

- **BuildSheet basics**: region auto-pad volume = area × thickness; multiple regions
  fuse; `Mode.SUBTRACT` regions cut holes; `.sheet` is a `Part`.
- **flange**: exact expected volume after a 90° flange (base + sector
  `θ/2·((R+t)²−R²)·L` + wall slab); exactly two cylindrical faces; **fan faces
  survive** — face count strictly greater than a `.clean()`ed copy of the same solid;
  `gap1`/`gap2`; each `BendPosition`; multi-edge selection.
- **hem**: all four types produce valid solids; parameter generators unit-tested
  numerically (teardrop residual < tol; open-hem radius = opening/2).
- **make_brake_formed in BuildSheet**: BuildLine profile → valid solid; face count
  preserved vs. a `.clean()`ed copy (fan faces survive via forced `clean=False`).
- **algebra mode**: flange/hem without context, explicit `thickness` (mirrors
  `test_algebra.py`).
- **errors**: every `ValueError` path above.

## Deliverables

1. Feature branch off `origin/dev` (`sheet-metal-poc`) — code, tests, docs section
   page + tutorial with toctree/reference wiring.
   (This spec is committed only on local `dev`, never on the feature branch.)
2. **Draft PR** to `gumyr/build123d:dev`: API summary, the fan-face/no-clean technique
   with credit to FreeCAD SheetMetal, out-of-scope roadmap, open questions for gumyr
   (functions vs. operation classes; dedicated `Sheet` composite type; `Material`
   integration for `k_factor`).
3. **Comment on issue #305** linking the draft PR with a compact design summary.
   Both PR body and comment are reviewed by Gabriel before posting.
