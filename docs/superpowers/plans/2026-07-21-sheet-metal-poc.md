# Sheet Metal POC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `BuildSheet` builder plus `flange()` and `hem()` operations to build123d — a sheet metal POC that preserves bend topology (fan faces) by never unifying same-domain faces.

**Architecture:** New `BuildSheet(Builder[Part])` context auto-pads sketch regions into base sheets and forces all booleans through `SkipClean` so bend faces survive. One private geometry engine `_make_bend()` (translated from FreeCAD SheetMetal's `smBend`) serves both `flange` (wall) and `hem` (4 types via pure parameter generators). Spec: `docs/superpowers/specs/2026-07-21-sheet-metal-poc-design.md`.

**Tech Stack:** Python, build123d topology layer (OCP/OCCT wrappers), unittest + pytest, Sphinx docs.

## Global Constraints

- Work on branch `sheet-metal-poc` created from **`origin/dev`** (NOT local `dev` — local `dev` carries spec commits that must not reach the upstream PR).
- **Never unify bend faces**: every fuse/cut involving sheet geometry runs inside `with SkipClean():` and `_add_to_context(..., clean=False)`. `Shape.clean()` / `ShapeUpgrade_UnifySameDomain` on a sheet part destroys the fan-shaped bend end-caps.
- Every operation works in **builder mode AND algebra mode** (no context) — this is a hard build123d convention.
- New files carry the project's Apache-2.0 license header block (copy the format from `src/build123d/build_part.py:1-30`, update name/date/desc).
- New public names must be added to BOTH the import block and `__all__` in `src/build123d/__init__.py`.
- New operation names must be registered in `operations_apply_to` in `src/build123d/build_common.py:155`.
- Tests: unittest classes in flat files under `tests/`, run with `python -m pytest`. Import style: `from build123d import *`.
- Run the full test suite before declaring any task complete if it touches shared files (`build_common.py`, `__init__.py`, `build_enums.py`).
- Commit after every task (small, descriptive commits).
- FreeCAD SheetMetal reference (read-only, for algorithm reference): `/home/gabriel/Documentos/open_source/FreeCAD_SheetMetal` — key files `SheetMetalCmd.py` (`smBend` line 1241), `SheetMetalHem.py` (generators lines 77-155).

## Geometry conventions (used by Tasks 3-7)

Given a selected straight edge `E` on the sheet solid, at the junction of a *sheet face* (top/bottom surface) and a *thickness face* (side wall of the material):

- `thickness_face` = the adjacent face whose shortest boundary edge ≈ `thickness`; `sheet_face` = the other adjacent face.
- `f_dir` = `thickness_face.normal_at()` — direction the flat wall extends before folding.
- `n` = `sheet_face.normal_at()`; `thk_dir = -n` — from the edge into the material.
- `axis_dir = n.cross(f_dir)` — with this orientation, rotating by +`angle` folds the wall **away from the selected sheet face** (select a bottom-face edge to fold up, a top-face edge to fold down).
- `axis_point = p0 + thk_dir * (radius + thickness)` where `p0` is an edge endpoint — the bend axis. The outer bend cylinder (radius `radius + thickness`) is tangent to the plane of `sheet_face`; the inner cylinder (radius `radius`) is tangent to the opposite surface.
- Bend sector = revolve of the thickness rectangle `[p0, p1, p1 + thk_dir·t, p0 + thk_dir·t]` about the axis by `angle` (this rectangle coincides with the thickness face at angle 0).
- Wall = extrude of rectangle `[p0, p1, p1 + f_dir·leg, p0 + f_dir·leg]` by `thk_dir·t`, then rotated about the axis by `angle`.
- If a geometric sign comes out inverted during implementation (fold direction / revolve sweep side), fix the **axis orientation rule**, never the tests' expected volumes — the volume and bounding-box assertions encode the intended behavior.

---

### Task 1: Branch setup + enums (`HemType`, `BendPosition`)

**Files:**
- Modify: `src/build123d/build_enums.py` (append two enums, keep alphabetical-ish placement near `Kind`/`Side`)
- Modify: `src/build123d/__init__.py` (export both enums)
- Test: `tests/test_build_enums.py` (append)

**Interfaces:**
- Produces: `HemType` enum with members `FLAT`, `OPEN`, `TEARDROP`, `ROLLED`; `BendPosition` enum with members `MATERIAL_OUTSIDE`, `MATERIAL_INSIDE`, `THICKNESS_OUTSIDE`. Both importable as `from build123d import HemType, BendPosition`.

- [ ] **Step 1: Create the feature branch from origin/dev**

```bash
cd /home/gabriel/Documentos/open_source/build123d
git remote -v            # confirm which remote tracks gumyr/build123d or the fork
git fetch origin
git checkout -b sheet-metal-poc origin/dev
```
Expected: new branch `sheet-metal-poc` at origin/dev's HEAD. `git log --oneline -1` must NOT show the "sheet metal POC design spec" commits.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_build_enums.py` (inside the existing test class or a new one, matching the file's existing style — open the file first and mirror it):

```python
class TestSheetMetalEnums(unittest.TestCase):
    def test_hem_type(self):
        self.assertEqual(
            {m.name for m in HemType},
            {"FLAT", "OPEN", "TEARDROP", "ROLLED"},
        )
        self.assertEqual(repr(HemType.FLAT), "<HemType.FLAT>")

    def test_bend_position(self):
        self.assertEqual(
            {m.name for m in BendPosition},
            {"MATERIAL_OUTSIDE", "MATERIAL_INSIDE", "THICKNESS_OUTSIDE"},
        )
        self.assertEqual(repr(BendPosition.MATERIAL_OUTSIDE), "<BendPosition.MATERIAL_OUTSIDE>")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_build_enums.py -v -k SheetMetal`
Expected: FAIL with `NameError: name 'HemType' is not defined`

- [ ] **Step 4: Implement the enums**

In `src/build123d/build_enums.py`, following the existing `Align` pattern (with `__repr__`):

```python
class BendPosition(Enum):
    """Sheet metal bend position relative to the selected edge"""

    MATERIAL_OUTSIDE = auto()  # wall & bend added entirely outside the edge
    MATERIAL_INSIDE = auto()  # wall outer surface flush with the edge
    THICKNESS_OUTSIDE = auto()  # bend starts at the edge, wall beyond it

    def __repr__(self):
        return f"<{self.__class__.__name__}.{self.name}>"


class HemType(Enum):
    """Sheet metal hem styles"""

    FLAT = auto()  # 180° fold flat onto the sheet
    OPEN = auto()  # 180° fold with a gap (opening)
    TEARDROP = auto()  # teardrop profile fold
    ROLLED = auto()  # open rolled curl, no flat leg

    def __repr__(self):
        return f"<{self.__class__.__name__}.{self.name}>"
```

In `src/build123d/__init__.py`: `build_enums` is already imported with `*`; add `"BendPosition"` and `"HemType"` to the `__all__` list (alphabetical position).

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_build_enums.py -v -k SheetMetal`
Expected: 2 PASSED

- [ ] **Step 6: Commit**

```bash
git add src/build123d/build_enums.py src/build123d/__init__.py tests/test_build_enums.py
git commit -m "Add HemType and BendPosition enums for sheet metal POC"
```

---

### Task 2: `BuildSheet` builder with auto-padded sketch regions

**Files:**
- Create: `src/build123d/build_sheet.py`
- Modify: `src/build123d/__init__.py`
- Test: `tests/test_build_sheet.py` (create)

**Interfaces:**
- Consumes: `Builder` base (`build_common.py:186`), `SkipClean` (`build123d.topology`), `WorkplaneList`.
- Produces: `class BuildSheet(Builder[Part])` with constructor `BuildSheet(*workplanes, thickness: float, bend_radius: float | None = None, k_factor: float = 0.5, mode: Mode = Mode.ADD)`; attributes `.sheet` (Part), `.thickness`, `.bend_radius` (defaults to `thickness`), `.k_factor`, `.pending_edges`, `.pending_edges_as_wire`. Exiting a `BuildSketch` inside it pads regions by `thickness`. Later tasks call `BuildSheet._get_context()` and `context._add_to_context(*solids, mode=...)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_build_sheet.py` (license header per Global Constraints, mirroring `tests/test_build_part.py:1-35`):

```python
import unittest
from math import pi

from build123d import *


class TestBuildSheetBase(unittest.TestCase):
    def test_base_from_sketch(self):
        """A closed sketch region is auto-padded by thickness"""
        with BuildSheet(thickness=1) as bs:
            with BuildSketch():
                Rectangle(100, 60)
        self.assertTrue(isinstance(bs.sheet, Part))
        self.assertAlmostEqual(bs.sheet.volume, 100 * 60 * 1, 5)

    def test_base_with_hole(self):
        """Mode.SUBTRACT sketch regions cut holes"""
        with BuildSheet(thickness=1) as bs:
            with BuildSketch():
                Rectangle(100, 60)
            with BuildSketch(mode=Mode.SUBTRACT):
                Circle(10)
        self.assertAlmostEqual(bs.sheet.volume, 100 * 60 - pi * 100, 4)

    def test_multiple_regions_fuse(self):
        with BuildSheet(thickness=2) as bs:
            with BuildSketch():
                Rectangle(20, 20)
                with Locations((30, 0)):
                    Rectangle(20, 20)
        self.assertAlmostEqual(bs.sheet.volume, 2 * 20 * 20 * 2, 5)

    def test_defaults(self):
        with BuildSheet(thickness=1.5) as bs:
            with BuildSketch():
                Rectangle(10, 10)
        self.assertAlmostEqual(bs.bend_radius, 1.5, 5)  # defaults to thickness
        self.assertAlmostEqual(bs.k_factor, 0.5, 5)

    def test_workplane_base(self):
        """Sketch on a non-XY workplane pads along that plane's normal"""
        with BuildSheet(Plane.XZ, thickness=1) as bs:
            with BuildSketch(Plane.XZ):
                Rectangle(10, 10)
        self.assertAlmostEqual(bs.sheet.volume, 100, 5)
        self.assertAlmostEqual(abs(bs.sheet.bounding_box().size.Y), 1, 5)

    def test_thickness_required(self):
        with self.assertRaises(TypeError):
            BuildSheet()  # thickness is keyword-required


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_build_sheet.py -v`
Expected: FAIL/ERROR with `NameError: name 'BuildSheet' is not defined`

- [ ] **Step 3: Implement `BuildSheet`**

Create `src/build123d/build_sheet.py` (license header; desc: "This python module is a library used to build sheet metal parts."):

```python
from __future__ import annotations

from build123d.build_common import Builder, WorkplaneList, logger
from build123d.build_enums import Mode
from build123d.geometry import Location, Plane
from build123d.topology import Compound, Edge, Face, Part, Solid, SkipClean


class BuildSheet(Builder[Part]):
    """BuildSheet

    Builder context for sheet metal parts of constant thickness. Closed
    sketch regions exiting into this context are automatically padded by
    ``thickness`` to form the base sheet. Sheet metal operations such as
    :func:`~operations_sheet.flange` and :func:`~operations_sheet.hem`
    fold walls from selected edges while preserving the bend topology.

    Note: the faces of sheet metal parts are deliberately NOT unified
    (cleaned) so that each bend keeps its own cylindrical and fan-shaped
    faces — required by future unfolding tools. Do not call ``clean()``
    on the resulting part.

    Args:
        workplanes (Plane, optional): initial plane to work on. Defaults to Plane.XY.
        thickness (float): sheet material thickness.
        bend_radius (float, optional): default inner bend radius for operations.
            Defaults to ``thickness``.
        k_factor (float, optional): neutral-axis position for future unfold
            calculations, 0 to 1. Defaults to 0.5.
        mode (Mode, optional): combination mode. Defaults to Mode.ADD.
    """

    _tag = "BuildSheet"
    _obj_name = "sheet"
    _shape = Solid
    _sub_class = Part

    def __init__(
        self,
        *workplanes: Face | Plane | Location,
        thickness: float,
        bend_radius: float | None = None,
        k_factor: float = 0.5,
        mode: Mode = Mode.ADD,
    ):
        if thickness <= 0:
            raise ValueError("thickness must be positive")
        if not 0.0 <= k_factor <= 1.0:
            raise ValueError("k_factor must be between 0 and 1")
        self.thickness = thickness
        self.bend_radius = thickness if bend_radius is None else bend_radius
        if self.bend_radius < 0:
            raise ValueError("bend_radius can't be negative")
        self.k_factor = k_factor
        self._sheet: Part | None = None
        self.pending_edges: list[Edge] = []
        super().__init__(*workplanes, mode=mode)

    @property
    def sheet(self) -> Part | None:
        """Get the current sheet"""
        return self._sheet

    @sheet.setter
    def sheet(self, value: Part) -> None:
        """Set the current sheet"""
        self._sheet = value

    @property
    def _obj(self) -> Part | None:
        """Alias _obj to sheet"""
        return self._sheet

    @_obj.setter
    def _obj(self, value: Part) -> None:
        self._sheet = value

    @property
    def pending_edges_as_wire(self):
        """Return a wire representation of the pending edges"""
        from build123d.topology import Wire

        return Wire.combine(self.pending_edges)[0]

    def _add_to_pending(self, *objects: Edge | Face, face_plane: Plane | None = None):
        """Store pending edges (for open profile operations)"""
        self.pending_edges.extend(o for o in objects if isinstance(o, Edge))

    def _add_to_context(
        self,
        *objects: Edge | Face | Solid | Compound,
        faces_to_pending: bool = True,
        clean: bool = True,
        mode: Mode = Mode.ADD,
    ):
        """Add objects to the sheet.

        Faces (typically sketch regions, provided in local workplane
        coordinates) are padded by the sheet thickness into base solids.
        All boolean operations skip face unification to preserve bend
        topology.
        """
        faces: list[Face] = []
        others: list = []
        for obj in objects:
            if obj is None:
                continue
            if isinstance(obj, Face):
                faces.append(obj)
            elif isinstance(obj, Compound) and not obj.solids() and obj.faces():
                faces.extend(obj.faces())
            else:
                others.append(obj)

        pads: list[Solid] = []
        if faces:
            for plane in WorkplaneList._get_context().workplanes:
                for face in faces:
                    global_face = plane.from_local_coords(face)
                    pads.append(
                        Solid.extrude(global_face, plane.z_dir * self.thickness)
                    )

        edges = [o for o in others if isinstance(o, Edge)]
        non_edges = [o for o in others if not isinstance(o, Edge)]
        with SkipClean():
            super()._add_to_context(
                *non_edges,
                *pads,
                faces_to_pending=faces_to_pending,
                clean=False,
                mode=mode,
            )
        if edges:
            self._add_to_pending(*edges)
```

Notes for the implementer:
- `BuildSketch.__exit__` hands over `sketch_local` — faces on `Plane.XY` — which is why `plane.from_local_coords(face)` is applied per workplane (mirrors `Builder._add_to_context` lines 528-532 which only handles this for `BuildPart`/`BuildSketch` tags).
- `Curve` (from `BuildLine.__exit__`) is a `Compound` without solids or faces — it falls into `others`, its edges are typed out by `super()._add_to_context` into nothing for our tag, so we route the edge extraction ourselves. If a `Curve` compound arrives, extract its edges: add `elif isinstance(obj, Compound) and not obj.solids() and not obj.faces(): others.extend(obj.edges())` if the pending-edge test in Task 8 fails without it.
- Update `src/build123d/__init__.py`: add `from build123d.build_sheet import *` to the import block and `"BuildSheet"` to `__all__`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_build_sheet.py -v`
Expected: 6 PASSED

- [ ] **Step 5: Run the full test suite (shared file touched)**

Run: `python -m pytest tests/ -x -q --ignore=tests/test_benchmarks.py`
Expected: all pass (same failures/skips as a pristine checkout, if any pre-exist)

- [ ] **Step 6: Commit**

```bash
git add src/build123d/build_sheet.py src/build123d/__init__.py tests/test_build_sheet.py
git commit -m "Add BuildSheet builder with auto-padded sketch regions"
```

---

### Task 3: Bend engine + `flange()` MVP (MATERIAL_OUTSIDE, no gaps)

**Files:**
- Create: `src/build123d/operations_sheet.py`
- Modify: `src/build123d/build_common.py:155` (`operations_apply_to`)
- Modify: `src/build123d/__init__.py`
- Test: `tests/test_build_sheet.py` (append)

**Interfaces:**
- Consumes: `BuildSheet` from Task 2 (`BuildSheet._get_context("flange")`, `context.thickness`, `context.bend_radius`, `context._add_to_context`, `context.sheet`); `topo_explore_connected_faces` from `build123d.topology`; `SkipClean`.
- Produces:
  - `flange(edges, length: float, angle: float = 90, radius: float | None = None, gap1: float = 0, gap2: float = 0, bend_position: BendPosition = BendPosition.MATERIAL_OUTSIDE, clean: bool = False, mode: Mode = Mode.ADD, thickness: float | None = None) -> Part` (gaps/positions wired in Tasks 4-5, accepted from the start).
  - Private `_bend_frame(edge, target, thickness) -> tuple` returning `(p0, p1, thk_dir, f_dir, axis_dir)`; `_make_bend(target, edge, thickness, radius, angle, leg_length, gap1, gap2, offset) -> tuple[list[Solid], list[Solid]]` (additions, cuts); `_apply_bends(context, target, additions, cuts, clean, mode) -> Part` — all used by Tasks 4-7. `mode` other than `Mode.ADD` raises `ValueError` (POC limitation, documented in the docstrings).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_build_sheet.py`:

```python
class TestFlange(unittest.TestCase):
    def test_flange_90(self):
        """90° flange from a bottom-face edge folds up, exact volume"""
        with BuildSheet(thickness=1, bend_radius=2) as bs:
            with BuildSketch():
                Rectangle(100, 60)
            edge = (
                bs.faces().sort_by(Axis.Z)[0].edges().filter_by(Axis.Y).sort_by(Axis.X)[-1]
            )
            flange(edge, length=10)
        sector = (pi / 4) * ((2 + 1) ** 2 - 2**2) * 60  # θ/2·((R+t)²−R²)·L, θ=π/2
        wall = 10 * 60 * 1
        self.assertAlmostEqual(bs.sheet.volume, 6000 + sector + wall, 3)
        self.assertTrue(bs.sheet.is_valid())
        # folds up (away from the bottom face) and outward
        bbox = bs.sheet.bounding_box()
        self.assertAlmostEqual(bbox.max.Z, 2 + 1 + 10, 3)  # radius+thickness+leg
        self.assertAlmostEqual(bbox.max.X, 50 + 2 + 1, 3)  # edge + radius + thickness

    def test_bend_faces_preserved(self):
        """The fused sheet must keep separate bend faces (no unification)"""
        with BuildSheet(thickness=1, bend_radius=2) as bs:
            with BuildSketch():
                Rectangle(100, 60)
            edge = (
                bs.faces().sort_by(Axis.Z)[0].edges().filter_by(Axis.Y).sort_by(Axis.X)[-1]
            )
            flange(edge, length=10)
        cylinders = bs.sheet.faces().filter_by(GeomType.CYLINDER)
        self.assertEqual(len(cylinders), 2)  # inner and outer bend surface
        cleaned = bs.sheet.clean()
        self.assertGreater(len(bs.sheet.faces()), len(cleaned.faces()))

    def test_flange_returns_part(self):
        with BuildSheet(thickness=1) as bs:
            with BuildSketch():
                Rectangle(20, 20)
            result = flange(
                bs.faces().sort_by(Axis.Z)[0].edges().filter_by(Axis.Y)[0], length=5
            )
        self.assertTrue(isinstance(result, Part))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_build_sheet.py -v -k Flange`
Expected: FAIL with `NameError: name 'flange' is not defined`

- [ ] **Step 3: Implement the engine and `flange`**

Create `src/build123d/operations_sheet.py` (license header):

```python
from __future__ import annotations

from build123d.build_common import flatten_sequence, validate_inputs
from build123d.build_enums import BendPosition, HemType, Mode
from build123d.build_sheet import BuildSheet
from build123d.geometry import Axis, Vector
from build123d.topology import (
    Edge,
    Face,
    GeomType,
    Part,
    Shape,
    SkipClean,
    Solid,
    Wire,
    topo_explore_connected_faces,
)

THICKNESS_TOLERANCE = 1e-4


def _bend_frame(
    edge: Edge, target: Shape, thickness: float
) -> tuple[Vector, Vector, Vector, Vector, Vector]:
    """Derive the bend coordinate frame from a selected edge.

    The edge must lie at the junction of a sheet face (top/bottom surface)
    and a thickness face (material side wall).

    Returns:
        (p0, p1, thk_dir, f_dir, axis_dir) where p0/p1 are the edge end
        points ordered along axis_dir, thk_dir points from the edge into
        the material, f_dir is the outward direction the wall extends and
        axis_dir the bend axis direction (rotation by +angle folds away
        from the sheet face).
    """
    if edge.geom_type != GeomType.LINE:
        raise ValueError("flange/hem edges must be linear")
    adjacent = [Face(f) for f in topo_explore_connected_faces(edge, parent=target)]
    if len(adjacent) != 2:
        raise ValueError(
            f"Selected edge must have exactly 2 adjacent faces, found {len(adjacent)}"
        )

    def is_thickness_face(face: Face) -> bool:
        return (
            abs(min(e.length for e in face.edges()) - thickness)
            < THICKNESS_TOLERANCE * thickness
        )

    thickness_faces = [f for f in adjacent if is_thickness_face(f)]
    if len(thickness_faces) != 1:
        raise ValueError(
            "Selected edge must be at the junction of a sheet face and a "
            "thickness face (e.g. the edge of the top or bottom surface)"
        )
    thickness_face = thickness_faces[0]
    sheet_face = adjacent[0] if adjacent[1] is thickness_face else adjacent[1]

    f_dir = thickness_face.normal_at()
    normal = sheet_face.normal_at()
    thk_dir = -normal
    axis_dir = normal.cross(f_dir)

    p0, p1 = edge.position_at(0), edge.position_at(1)
    if (p1 - p0).dot(axis_dir) < 0:
        p0, p1 = p1, p0
    return p0, p1, thk_dir, f_dir, axis_dir


def _make_bend(
    target: Shape,
    edge: Edge,
    thickness: float,
    radius: float,
    angle: float,
    leg_length: float,
    gap1: float = 0,
    gap2: float = 0,
    offset: float = 0,
) -> tuple[list[Solid], list[Solid]]:
    """Build the solids of one bend: (additions, cuts).

    Translated from FreeCAD SheetMetal smBend: the bend is a thickness
    rectangle revolved about the bend axis; the wall is an extruded
    rectangle rotated to the bend end angle. offset < 0 shifts the whole
    bend into the material (BendPosition.MATERIAL_INSIDE /
    THICKNESS_OUTSIDE) and produces a cut slab.
    """
    p0, p1, thk_dir, f_dir, axis_dir = _bend_frame(edge, target, thickness)
    if gap1 + gap2 >= (p1 - p0).length:
        raise ValueError("gap1 + gap2 leave no bend width on the edge")
    p0 = p0 + axis_dir * gap1
    p1 = p1 - axis_dir * gap2

    cuts: list[Solid] = []
    if offset < 0:
        # shift the working edge into the material and cut the vacated slab
        slab_face = Face(
            Wire.make_polygon(
                [p0, p1, p1 + thk_dir * thickness, p0 + thk_dir * thickness],
                close=True,
            )
        )
        cuts.append(Solid.extrude(slab_face, f_dir * offset))
        p0 = p0 + f_dir * offset
        p1 = p1 + f_dir * offset

    additions: list[Solid] = []
    axis = Axis(p0 + thk_dir * (radius + thickness), axis_dir)

    sector_face = Face(
        Wire.make_polygon(
            [p0, p1, p1 + thk_dir * thickness, p0 + thk_dir * thickness], close=True
        )
    )
    additions.append(Solid.revolve(sector_face, angle, axis))

    if leg_length > 0:
        wall_face = Face(
            Wire.make_polygon(
                [p0, p1, p1 + f_dir * leg_length, p0 + f_dir * leg_length], close=True
            )
        )
        wall = Solid.extrude(wall_face, thk_dir * thickness)
        additions.append(wall.rotate(axis, angle))

    return additions, cuts


def _apply_bends(
    context: BuildSheet | None,
    target: Shape,
    additions: list[Solid],
    cuts: list[Solid],
    clean: bool,
    mode: Mode,
) -> Part:
    """Fuse bend solids with the target sheet, preserving bend faces."""
    if mode != Mode.ADD:
        raise ValueError("sheet metal operations only support Mode.ADD (POC)")
    with SkipClean():
        new_sheet = target
        if cuts:
            new_sheet = new_sheet.cut(*cuts)
        new_sheet = new_sheet.fuse(*additions)
    if clean:
        new_sheet = new_sheet.clean()
    if context is not None:
        context._add_to_context(new_sheet, mode=Mode.REPLACE)
    return Part(new_sheet.wrapped)


def flange(
    edges: Edge | list[Edge] | None = None,
    length: float = 0,
    angle: float = 90,
    radius: float | None = None,
    gap1: float = 0,
    gap2: float = 0,
    bend_position: BendPosition = BendPosition.MATERIAL_OUTSIDE,
    clean: bool = False,
    mode: Mode = Mode.ADD,
    thickness: float | None = None,
) -> Part:
    """Sheet Metal Operation: flange

    Fold a wall (flange) up from each selected sheet edge with a
    cylindrical bend. The bend fold direction is away from the sheet face
    the edge was selected from. Bend faces are intentionally kept separate
    (not unified) — do not clean() the result.

    Args:
        edges (Edge|list[Edge]): straight edge(s) at the junction of a sheet
            face and a thickness face.
        length (float): flat wall length beyond the bend (leg).
        angle (float, optional): bend angle in degrees, 0 < angle <= 270.
            Defaults to 90.
        radius (float, optional): inner bend radius. Defaults to the
            BuildSheet context bend_radius.
        gap1/gap2 (float, optional): trim from each end of the edge.
        bend_position (BendPosition, optional): where the material sits
            relative to the selected edge. Defaults to MATERIAL_OUTSIDE.
        clean (bool, optional): unify faces — destroys bend topology, only
            for parts that will never be unfolded. Defaults to False.
        mode (Mode, optional): combination mode. Defaults to Mode.ADD.
        thickness (float, optional): sheet thickness — required in algebra
            mode, taken from the context otherwise.

    Raises:
        ValueError: bad edge selection or parameters.
    """
    context: BuildSheet | None = BuildSheet._get_context("flange")
    edge_list = flatten_sequence(edges)
    validate_inputs(context, "flange", edge_list)

    if not edge_list:
        raise ValueError("flange requires at least one edge")
    if length <= 0:
        raise ValueError("length must be positive")
    if not 0 < angle <= 270:
        raise ValueError("angle must be in (0, 270] degrees")

    if thickness is None:
        if context is None:
            raise ValueError("thickness must be provided in algebra mode")
        thickness = context.thickness
    if radius is None:
        radius = context.bend_radius if context is not None else thickness
    if radius < 0:
        raise ValueError("radius can't be negative")
    if gap1 < 0 or gap2 < 0:
        raise ValueError("gaps can't be negative")

    if context is not None and context.sheet is not None:
        target = context.sheet
    else:
        target = edge_list[0].topo_parent
        if target is None:
            raise ValueError("edges must belong to a sheet solid")

    if bend_position == BendPosition.MATERIAL_INSIDE:
        offset = -(thickness + radius)
    elif bend_position == BendPosition.THICKNESS_OUTSIDE:
        offset = -radius
    else:
        offset = 0.0

    additions: list[Solid] = []
    cuts: list[Solid] = []
    for edge in edge_list:
        adds, cut_solids = _make_bend(
            target, edge, thickness, radius, angle, length, gap1, gap2, offset
        )
        additions.extend(adds)
        cuts.extend(cut_solids)

    return _apply_bends(context, target, additions, cuts, clean, mode)
```

Register and export:
- `src/build123d/build_common.py` `operations_apply_to`: add `"flange": ["BuildSheet"],` (alphabetical).
- `src/build123d/__init__.py`: add `from build123d.operations_sheet import *` and `"flange"` to `__all__`.

Implementation note: if `test_flange_90` fails on the bbox assertions with the wall folded the wrong way, flip only the `axis_dir` rule in `_bend_frame` (see Geometry conventions) and re-run.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_build_sheet.py -v`
Expected: all PASSED (Task 2's tests + 3 new)

- [ ] **Step 5: Run the full suite (shared files touched)**

Run: `python -m pytest tests/ -x -q --ignore=tests/test_benchmarks.py`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/build123d/operations_sheet.py src/build123d/build_common.py src/build123d/__init__.py tests/test_build_sheet.py
git commit -m "Add flange operation with fan-face preserving bend engine"
```

---

### Task 4: Flange gaps and multi-edge selection

**Files:**
- Modify: `src/build123d/operations_sheet.py` (only if tests reveal fixes needed — the gap plumbing exists from Task 3)
- Test: `tests/test_build_sheet.py` (append)

**Interfaces:**
- Consumes: `flange` from Task 3.
- Produces: verified `gap1`/`gap2` and multi-edge behavior relied on by the tutorial (Task 9).

- [ ] **Step 1: Write the failing/verifying tests**

Append to `tests/test_build_sheet.py` inside `TestFlange`:

```python
    def test_flange_gaps(self):
        """gap1/gap2 trim the bend from the edge ends"""
        with BuildSheet(thickness=1, bend_radius=2) as bs:
            with BuildSketch():
                Rectangle(100, 60)
            edge = (
                bs.faces().sort_by(Axis.Z)[0].edges().filter_by(Axis.Y).sort_by(Axis.X)[-1]
            )
            flange(edge, length=10, gap1=5, gap2=10)
        trimmed = 60 - 5 - 10
        sector = (pi / 4) * ((2 + 1) ** 2 - 2**2) * trimmed
        wall = 10 * trimmed * 1
        self.assertAlmostEqual(bs.sheet.volume, 6000 + sector + wall, 3)

    def test_flange_multi_edge(self):
        """All four edges of the bottom face fold up into a tray"""
        with BuildSheet(thickness=1, bend_radius=2) as bs:
            with BuildSketch():
                Rectangle(100, 60)
            edges = bs.faces().sort_by(Axis.Z)[0].edges().filter_by(GeomType.LINE)
            flange(edges, length=10, gap1=3.1, gap2=3.1)
        self.assertEqual(len(edges), 4)
        sector_len = (100 - 6.2) + (100 - 6.2) + (60 - 6.2) + (60 - 6.2)
        sector = (pi / 4) * ((2 + 1) ** 2 - 2**2) * sector_len
        walls = 10 * sector_len * 1
        self.assertAlmostEqual(bs.sheet.volume, 6000 + sector + walls, 3)
        self.assertEqual(len(bs.sheet.faces().filter_by(GeomType.CYLINDER)), 8)

    def test_flange_gap_too_big(self):
        with BuildSheet(thickness=1) as bs:
            with BuildSketch():
                Rectangle(20, 20)
            with self.assertRaises(ValueError):
                flange(
                    bs.faces().sort_by(Axis.Z)[0].edges().filter_by(Axis.Y)[0],
                    length=5,
                    gap1=15,
                    gap2=15,
                )
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/test_build_sheet.py -v -k Flange`
Expected: PASS if Task 3's plumbing is correct; if not, fix `_make_bend` gap trimming (`p0 + axis_dir*gap1`, `p1 - axis_dir*gap2`) until green. The gaps (3.1 > radius+thickness=3) in the multi-edge test keep neighboring bends from touching.

- [ ] **Step 3: Commit**

```bash
git add tests/test_build_sheet.py src/build123d/operations_sheet.py
git commit -m "Test flange gaps and multi-edge selection"
```

---

### Task 5: `BendPosition.MATERIAL_INSIDE` and `THICKNESS_OUTSIDE`

**Files:**
- Modify: `src/build123d/operations_sheet.py` (only fixes; offset plumbing exists from Task 3)
- Test: `tests/test_build_sheet.py` (append)

**Interfaces:**
- Consumes: `flange(..., bend_position=...)` from Task 3.
- Produces: verified inside/outside bend positioning relied on by docs (Task 9).

- [ ] **Step 1: Write the tests**

Append inside `TestFlange`:

```python
    def test_material_inside(self):
        """MATERIAL_INSIDE: flange does not protrude past the original edge"""
        with BuildSheet(thickness=1, bend_radius=2) as bs:
            with BuildSketch():
                Rectangle(100, 60)
            edge = (
                bs.faces().sort_by(Axis.Z)[0].edges().filter_by(Axis.Y).sort_by(Axis.X)[-1]
            )
            flange(edge, length=10, bend_position=BendPosition.MATERIAL_INSIDE)
        bbox = bs.sheet.bounding_box()
        self.assertAlmostEqual(bbox.max.X, 50, 3)  # flush with original edge
        base_after_cut = 6000 - 60 * (2 + 1) * 1  # slab (radius+thickness)·t·L removed
        sector = (pi / 4) * ((2 + 1) ** 2 - 2**2) * 60
        wall = 10 * 60 * 1
        self.assertAlmostEqual(bs.sheet.volume, base_after_cut + sector + wall, 3)

    def test_thickness_outside(self):
        """THICKNESS_OUTSIDE: bend starts radius earlier than MATERIAL_OUTSIDE"""
        with BuildSheet(thickness=1, bend_radius=2) as bs:
            with BuildSketch():
                Rectangle(100, 60)
            edge = (
                bs.faces().sort_by(Axis.Z)[0].edges().filter_by(Axis.Y).sort_by(Axis.X)[-1]
            )
            flange(edge, length=10, bend_position=BendPosition.THICKNESS_OUTSIDE)
        bbox = bs.sheet.bounding_box()
        self.assertAlmostEqual(bbox.max.X, 50 + 1, 3)  # protrudes only by thickness
```

- [ ] **Step 2: Run tests, fix the offset path until green**

Run: `python -m pytest tests/test_build_sheet.py -v -k "inside or outside"`
Expected: PASS. If the cut slab is misplaced, verify: the slab spans from the *original* trimmed edge back along `f_dir * offset` (offset is negative → into the material… no: the slab face sits ON the original edge and is extruded by `f_dir * offset`, which points backward into the material because offset < 0), and only afterwards are `p0`/`p1` translated by `f_dir * offset`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_build_sheet.py src/build123d/operations_sheet.py
git commit -m "Verify flange bend positions (material inside / thickness outside)"
```

---

### Task 6: Flange validation errors + algebra mode

**Files:**
- Modify: `src/build123d/operations_sheet.py` (only fixes)
- Test: `tests/test_build_sheet.py` (append)

**Interfaces:**
- Consumes: `flange` from Task 3.
- Produces: verified algebra-mode contract: `flange(edges_of_standalone_part, length=..., thickness=...)` returns the full fused `Part`.

- [ ] **Step 1: Write the tests**

```python
class TestFlangeErrors(unittest.TestCase):
    def _base(self):
        bs = BuildSheet(thickness=1)
        with bs:
            with BuildSketch():
                Rectangle(20, 20)
        return bs

    def test_bad_length(self):
        bs = self._base()
        edge = bs.faces().sort_by(Axis.Z)[0].edges()[0]
        with self.assertRaises(ValueError):
            flange(edge, length=0, thickness=1)

    def test_bad_angle(self):
        bs = self._base()
        edge = bs.faces().sort_by(Axis.Z)[0].edges()[0]
        with self.assertRaises(ValueError):
            flange(edge, length=5, angle=0, thickness=1)
        with self.assertRaises(ValueError):
            flange(edge, length=5, angle=271, thickness=1)

    def test_bad_radius(self):
        bs = self._base()
        edge = bs.faces().sort_by(Axis.Z)[0].edges()[0]
        with self.assertRaises(ValueError):
            flange(edge, length=5, radius=-1, thickness=1)

    def test_no_edges(self):
        with self.assertRaises(ValueError):
            flange([], length=5, thickness=1)

    def test_non_linear_edge(self):
        with BuildSheet(thickness=1) as bs:
            with BuildSketch():
                Circle(10)
            with self.assertRaises(ValueError):
                flange(bs.faces().sort_by(Axis.Z)[0].edges()[0], length=5)

    def test_thickness_required_in_algebra(self):
        part = extrude(Rectangle(20, 20), 1)
        edge = part.faces().sort_by(Axis.Z)[0].edges().filter_by(Axis.Y)[0]
        with self.assertRaises(ValueError):
            flange(edge, length=5)


class TestFlangeAlgebra(unittest.TestCase):
    def test_algebra_flange(self):
        """flange works without a BuildSheet context"""
        sheet = extrude(Rectangle(100, 60), 1)
        edge = (
            sheet.faces().sort_by(Axis.Z)[0].edges().filter_by(Axis.Y).sort_by(Axis.X)[-1]
        )
        result = flange(edge, length=10, radius=2, thickness=1)
        sector = (pi / 4) * ((2 + 1) ** 2 - 2**2) * 60
        self.assertAlmostEqual(result.volume, 6000 + sector + 600, 3)
        self.assertEqual(len(result.faces().filter_by(GeomType.CYLINDER)), 2)
```

- [ ] **Step 2: Run tests, fix validation gaps until green**

Run: `python -m pytest tests/test_build_sheet.py -v -k "Errors or Algebra"`
Expected: PASS. Note the algebra base is built with `extrude` (a `BuildPart` op usable in algebra mode) — `bs.sheet` from a `with` block works too, but the test proves independence from `BuildSheet`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_build_sheet.py src/build123d/operations_sheet.py
git commit -m "Verify flange validation and algebra mode"
```

---

### Task 7: `hem()` — four types via parameter generators

**Files:**
- Modify: `src/build123d/operations_sheet.py` (append)
- Modify: `src/build123d/build_common.py` (`operations_apply_to`)
- Modify: `src/build123d/__init__.py`
- Test: `tests/test_build_sheet.py` (append)

**Interfaces:**
- Consumes: `_make_bend`, `_apply_bends` from Task 3; `HemType` from Task 1.
- Produces: `hem(edges, hem_type=HemType.FLAT, width=None, opening=0, radius=None, roll_angle=None, clean=False, mode=Mode.ADD, thickness=None) -> Part` and private `_hem_parameters(hem_type, thickness, width, opening, radius, roll_angle) -> tuple[float, float, float]` (`(leg_length, bend_angle, bend_radius)`).

- [ ] **Step 1: Write the failing tests**

```python
class TestHem(unittest.TestCase):
    @staticmethod
    def _sheet_with_edge():
        bs = BuildSheet(thickness=1)
        with bs:
            with BuildSketch():
                Rectangle(100, 60)
        edge = (
            bs.faces().sort_by(Axis.Z)[0].edges().filter_by(Axis.Y).sort_by(Axis.X)[-1]
        )
        return bs, edge

    def test_flat_hem_volume(self):
        with BuildSheet(thickness=1) as bs:
            with BuildSketch():
                Rectangle(100, 60)
            edge = (
                bs.faces().sort_by(Axis.Z)[0].edges().filter_by(Axis.Y).sort_by(Axis.X)[-1]
            )
            hem(edge, hem_type=HemType.FLAT, width=8)
        # flat: radius=0, angle=180, leg = width - (0 + t) = 7
        sector = (pi / 2) * (1**2 - 0**2) * 60  # θ/2·((R+t)²−R²)·L, θ=π
        wall = 7 * 60 * 1
        self.assertAlmostEqual(bs.sheet.volume, 6000 + sector + wall, 3)
        self.assertTrue(bs.sheet.is_valid())

    def test_open_hem(self):
        with BuildSheet(thickness=1) as bs:
            with BuildSketch():
                Rectangle(100, 60)
            edge = (
                bs.faces().sort_by(Axis.Z)[0].edges().filter_by(Axis.Y).sort_by(Axis.X)[-1]
            )
            hem(edge, hem_type=HemType.OPEN, width=8, opening=2)
        # open: radius=1, angle=180, leg = 8 - (1+1) = 6
        sector = (pi / 2) * (2**2 - 1**2) * 60
        wall = 6 * 60 * 1
        self.assertAlmostEqual(bs.sheet.volume, 6000 + sector + wall, 3)

    def test_rolled_hem(self):
        with BuildSheet(thickness=1) as bs:
            with BuildSketch():
                Rectangle(100, 60)
            edge = (
                bs.faces().sort_by(Axis.Z)[0].edges().filter_by(Axis.Y).sort_by(Axis.X)[-1]
            )
            hem(edge, hem_type=HemType.ROLLED, radius=3, roll_angle=270)
        sector = (radians(270) / 2) * ((3 + 1) ** 2 - 3**2) * 60
        self.assertAlmostEqual(bs.sheet.volume, 6000 + sector, 3)
        self.assertTrue(bs.sheet.is_valid())

    def test_teardrop_hem_valid(self):
        with BuildSheet(thickness=1) as bs:
            with BuildSketch():
                Rectangle(100, 60)
            edge = (
                bs.faces().sort_by(Axis.Z)[0].edges().filter_by(Axis.Y).sort_by(Axis.X)[-1]
            )
            hem(edge, hem_type=HemType.TEARDROP, width=12, radius=3)
        self.assertTrue(bs.sheet.is_valid())
        self.assertGreater(bs.sheet.volume, 6000)


class TestHemParameters(unittest.TestCase):
    """Numeric tests of the parameter generators (FreeCAD SheetMetalHem.py)"""

    def test_flat(self):
        leg, bend_angle, bend_radius = _hem_parameters(HemType.FLAT, 1, 8, 0, None, None)
        self.assertAlmostEqual(leg, 7, 6)
        self.assertAlmostEqual(bend_angle, 180, 6)
        self.assertAlmostEqual(bend_radius, 0, 6)

    def test_open(self):
        leg, bend_angle, bend_radius = _hem_parameters(HemType.OPEN, 1, 8, 2, None, None)
        self.assertAlmostEqual(leg, 6, 6)
        self.assertAlmostEqual(bend_radius, 1, 6)

    def test_rolled_default_max_angle(self):
        leg, bend_angle, bend_radius = _hem_parameters(
            HemType.ROLLED, 1, None, 0, 3, None
        )
        self.assertAlmostEqual(leg, 0, 6)
        self.assertAlmostEqual(bend_angle, 270 + degrees(asin(3 / 4)), 6)

    def test_teardrop_residual(self):
        """The teardrop leg satisfies FreeCAD's closure equation"""
        t, r, width = 1.0, 3.0, 12.0
        leg, bend_angle, bend_radius = _hem_parameters(
            HemType.TEARDROP, t, width, 0, r, None
        )
        theta = radians(bend_angle - 180) / 2
        residual = leg - width + (r + t) + t * sin(2 * theta)
        self.assertAlmostEqual(residual, 0, 6)

    def test_errors(self):
        with self.assertRaises(ValueError):
            _hem_parameters(HemType.OPEN, 1, 8, -1, None, None)  # negative opening
        with self.assertRaises(ValueError):
            _hem_parameters(HemType.FLAT, 1, 0.5, 0, None, None)  # width too small
        with self.assertRaises(ValueError):
            _hem_parameters(HemType.ROLLED, 1, None, 0, 3, 350)  # roll angle > max
        with self.assertRaises(ValueError):
            _hem_parameters(HemType.TEARDROP, 1, 3, 0, 3, None)  # width < 2(R+t)
```

Add to the test file imports: `from math import pi, radians, degrees, asin, sin` and `from build123d.operations_sheet import _hem_parameters`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_build_sheet.py -v -k Hem`
Expected: FAIL with `ImportError`/`NameError` (no `hem`, no `_hem_parameters`)

- [ ] **Step 3: Implement generators and `hem`**

Append to `src/build123d/operations_sheet.py` (imports: add `asin, atan, cos, degrees, sin, sqrt` to the `math` import):

```python
def _bisection(func, lower: float, upper: float, eps: float = 1.0e-9) -> float:
    """Root of func in [lower, upper] — from FreeCAD SheetMetalHem.py"""
    f_lower, f_upper = func(lower), func(upper)
    if f_lower * f_upper > 0:
        raise ValueError("Teardrop hem has unexpected incorrect geometry")
    mid = 0.5 * (lower + upper)
    prev_mid = mid + 2 * eps
    while abs(mid - prev_mid) >= eps:
        prev_mid = mid
        f_mid = func(mid)
        if f_lower * f_mid < 0:
            upper = mid
        else:
            lower, f_lower = mid, f_mid
        mid = 0.5 * (lower + upper)
    return mid


def _hem_parameters(
    hem_type: HemType,
    thickness: float,
    width: float | None,
    opening: float,
    radius: float | None,
    roll_angle: float | None,
) -> tuple[float, float, float]:
    """Return (leg_length, bend_angle, bend_radius) for a hem.

    Pure-math translation of FreeCAD SheetMetalHem.py generateOpenHem /
    generateRolledHem / generateTeardropHem (width always includes the bend).
    """
    if hem_type in (HemType.FLAT, HemType.OPEN):
        if opening < 0:
            raise ValueError("opening must be positive")
        if width is None:
            raise ValueError(f"width is required for {hem_type}")
        bend_radius = 0.5 * opening
        if width <= bend_radius + thickness:
            raise ValueError(
                "width must be greater than the bend width "
                "(bend radius + thickness)"
            )
        return width - (bend_radius + thickness), 180.0, bend_radius

    if hem_type == HemType.ROLLED:
        if radius is None or radius <= 0:
            raise ValueError("a positive radius is required for a rolled hem")
        max_roll_angle = 270.0 + degrees(asin(radius / (radius + thickness)))
        if roll_angle is None:
            return 0.0, max_roll_angle, radius
        if roll_angle <= 0:
            raise ValueError("roll_angle must be strictly positive")
        if roll_angle > max_roll_angle:
            raise ValueError(
                f"roll_angle must not exceed physical maximum ({max_roll_angle}°)"
            )
        return 0.0, roll_angle, radius

    if hem_type == HemType.TEARDROP:
        if radius is None or radius <= 0:
            raise ValueError("a positive radius is required for a teardrop hem")
        if opening < 0:
            raise ValueError("opening must be positive")
        if width is None:
            raise ValueError("width is required for a teardrop hem")
        bend_width = radius + thickness
        if width < 2 * bend_width:
            raise ValueError(
                "width must be greater or equal than twice the bend width "
                "(bend radius + thickness)"
            )
        if width == 2 * bend_width:  # degenerate teardrop
            if opening >= radius:
                raise ValueError("opening must be smaller than bend radius")
            return radius - opening, 270.0, radius
        equation = lambda leg: (
            leg - width + bend_width + thickness * sin(2 * atan(radius / leg))
        )
        leg = _bisection(equation, width - bend_width - thickness, width - bend_width)
        if opening == 0.0:
            theta = atan(radius / leg)
            return leg, 180.0 + 2 * degrees(theta), radius
        if opening == 2 * radius:
            return _hem_parameters(HemType.OPEN, thickness, width - bend_width, opening, None, None)
        theta = atan(
            (leg - sqrt(leg**2 - 2.0 * radius * opening + opening**2)) / opening
        )
        leg_length = opening * (cos(2 * theta) - 1) / sin(2 * theta) + leg
        return leg_length, 180.0 + 2 * degrees(theta), radius

    raise ValueError(f"Unknown hem type {hem_type}")


def hem(
    edges: Edge | list[Edge] | None = None,
    hem_type: HemType = HemType.FLAT,
    width: float | None = None,
    opening: float = 0,
    radius: float | None = None,
    roll_angle: float | None = None,
    clean: bool = False,
    mode: Mode = Mode.ADD,
    thickness: float | None = None,
) -> Part:
    """Sheet Metal Operation: hem

    Fold the sheet edge back onto itself. A hem is a bend with an angle of
    180° or more; the hem type determines the fold parameters:

    - HemType.FLAT: fold flat onto the sheet (radius 0).
    - HemType.OPEN: 180° fold leaving a gap of ``opening``.
    - HemType.TEARDROP: teardrop-profile fold of ``radius``.
    - HemType.ROLLED: open curl of ``radius`` and ``roll_angle`` (defaults
      to the physical maximum), no flat leg.

    Args:
        edges (Edge|list[Edge]): straight sheet edge(s) to hem.
        hem_type (HemType, optional): style of hem. Defaults to HemType.FLAT.
        width (float, optional): total hem width including the bend —
            required for FLAT/OPEN/TEARDROP.
        opening (float, optional): gap of an OPEN/TEARDROP hem. Defaults to 0.
        radius (float, optional): bend radius for TEARDROP/ROLLED.
        roll_angle (float, optional): ROLLED sweep angle in degrees.
        clean (bool, optional): unify faces — destroys bend topology.
            Defaults to False.
        mode (Mode, optional): combination mode. Defaults to Mode.ADD.
        thickness (float, optional): sheet thickness — required in algebra
            mode, taken from the context otherwise.

    Raises:
        ValueError: bad edge selection or hem parameters.
    """
    context: BuildSheet | None = BuildSheet._get_context("hem")
    edge_list = flatten_sequence(edges)
    validate_inputs(context, "hem", edge_list)

    if not edge_list:
        raise ValueError("hem requires at least one edge")
    if thickness is None:
        if context is None:
            raise ValueError("thickness must be provided in algebra mode")
        thickness = context.thickness

    leg_length, bend_angle, bend_radius = _hem_parameters(
        hem_type, thickness, width, opening, radius, roll_angle
    )

    if context is not None and context.sheet is not None:
        target = context.sheet
    else:
        target = edge_list[0].topo_parent
        if target is None:
            raise ValueError("edges must belong to a sheet solid")

    additions: list[Solid] = []
    for edge in edge_list:
        adds, _ = _make_bend(
            target, edge, thickness, bend_radius, bend_angle, leg_length
        )
        additions.extend(adds)

    return _apply_bends(context, target, additions, [], clean, mode)
```

Register and export: `"hem": ["BuildSheet"],` in `operations_apply_to`; `"hem"` in `__init__.py` `__all__` (module import line already added in Task 3).

Note: `angle > 270` is fine here — `flange` validates its own user-facing range, hem generators own theirs (rolled max is `270° + asin(r/(r+t))`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_build_sheet.py -v`
Expected: all PASSED. If the FLAT hem (radius 0) revolve fails in OCCT, use a tiny epsilon inner radius (`max(bend_radius, 1e-9)`) — note it in a code comment referencing OCCT degenerate revolve.

- [ ] **Step 5: Full suite + commit**

Run: `python -m pytest tests/ -x -q --ignore=tests/test_benchmarks.py`

```bash
git add src/build123d/operations_sheet.py src/build123d/build_common.py src/build123d/__init__.py tests/test_build_sheet.py
git commit -m "Add hem operation with flat, open, teardrop and rolled types"
```

---

### Task 8: Enable `make_brake_formed` inside `BuildSheet`

**Files:**
- Modify: `src/build123d/build_common.py:164` (one line)
- Test: `tests/test_build_sheet.py` (append)

**Interfaces:**
- Consumes: `BuildSheet.pending_edges` / `pending_edges_as_wire` from Task 2; existing `make_brake_formed` (`operations_part.py:347`).
- Produces: `make_brake_formed` usable inside `BuildSheet` with a `BuildLine` profile.

- [ ] **Step 1: Write the failing test**

```python
class TestMakeBrakeFormedInBuildSheet(unittest.TestCase):
    def test_open_profile_base(self):
        """A BuildLine profile feeds make_brake_formed inside BuildSheet"""
        with BuildSheet(thickness=1) as bs:
            with BuildLine():
                FilletPolyline((0, 0), (20, 0), (20, 15), radius=2)
            make_brake_formed(thickness=1, station_widths=30)
        self.assertTrue(bs.sheet.is_valid())
        self.assertGreater(bs.sheet.volume, 0)
        # bend cylinders stay distinct from flats (forced SkipClean)
        self.assertGreaterEqual(
            len(bs.sheet.faces().filter_by(GeomType.CYLINDER)), 2
        )
        cleaned = bs.sheet.clean()
        self.assertGreaterEqual(len(bs.sheet.faces()), len(cleaned.faces()))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_build_sheet.py -v -k Brake`
Expected: FAIL with `ValueError` from `validate_inputs` ("make_brake_formed doesn't apply to BuildSheet" or similar)

- [ ] **Step 3: Implement**

In `src/build123d/build_common.py` change:

```python
    "make_brake_formed": ["BuildPart"],
```
to:
```python
    "make_brake_formed": ["BuildPart", "BuildSheet"],
```

If the test still fails because the `BuildLine` edges never reached `pending_edges`, apply the fallback noted in Task 2 Step 3 (extract edges from face-less/solid-less `Compound`s in `BuildSheet._add_to_context`). Also note: `make_brake_formed` fetches its context via `BuildPart._get_context(...)` — `Builder._current` is one shared contextvar, so it returns the active `BuildSheet`; duck-typing on `pending_edges_as_wire` (provided in Task 2) makes this work without touching `operations_part.py`.

- [ ] **Step 4: Run tests + full suite**

Run: `python -m pytest tests/test_build_sheet.py -v && python -m pytest tests/ -x -q --ignore=tests/test_benchmarks.py`
Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add src/build123d/build_common.py tests/test_build_sheet.py src/build123d/build_sheet.py
git commit -m "Enable make_brake_formed inside BuildSheet"
```

---

### Task 9: Documentation — sheet metal section, tutorial, and wiring

**Files:**
- Create: `docs/build_sheet.rst`
- Create: `docs/tutorial_sheet_metal.rst`
- Create: `docs/sheet_metal_examples.py` (auto-discovered by `tests/test_docs_examples.py`, which globs all `docs/*.py`)
- Modify: `docs/builders.rst`, `docs/tutorials.rst`, `docs/builder_api_reference.rst`, `docs/operations.rst`
- Test: `python -m pytest tests/test_docs_examples.py -v -k sheet_metal`

**Interfaces:**
- Consumes: the full public API from Tasks 1-8.
- Produces: rendered docs; no code interfaces.

- [ ] **Step 1: Create `docs/build_sheet.rst`**

Before writing, run `grep -n "autoclass\|autofunction" docs/build_part.rst docs/builder_api_reference.rst` and mirror the exact autodoc directive style used there (module-qualified names like `build_part.BuildPart`). Content:

```rst
##########
BuildSheet
##########

The ``BuildSheet`` context is used to create sheet metal parts — parts of
constant material thickness formed by folding a flat sheet.

.. code-block:: python

    with BuildSheet(thickness=1, bend_radius=2) as tray:
        with BuildSketch():
            Rectangle(100, 60)
        bottom_edges = tray.faces().sort_by(Axis.Z)[0].edges().filter_by(GeomType.LINE)
        flange(bottom_edges, length=15, gap1=3.1, gap2=3.1)

*****************
Base sheet
*****************

Closed sketch regions exiting into ``BuildSheet`` are automatically padded
by ``thickness`` — no explicit ``extrude`` is needed. ``Mode.SUBTRACT``
regions cut holes. For open profiles (brake-formed parts) use
:func:`~operations_part.make_brake_formed` with a ``BuildLine`` profile;
note it takes an explicit ``thickness`` and requires bend arcs to be drawn
into the profile (e.g. with ``FilletPolyline``).

*****************
Folding
*****************

:func:`~operations_sheet.flange` folds a wall up from selected sheet
edges; :func:`~operations_sheet.hem` folds an edge back onto itself
(flat, open, teardrop or rolled). Both fold **away from the sheet face**
the selected edge borders: select a bottom-face edge to fold upward.

*****************
Bend topology
*****************

Sheet metal parts keep every bend's cylindrical faces and their flat
"fan" shaped end-caps as separate faces — they are deliberately never
unified. This preserved topology is what future unfolding tools use to
detect bends and compute flat patterns (with the ``k_factor`` stored on
the builder). For this reason **do not call** ``clean()`` on a sheet
metal part.

*****************
Reference
*****************

.. autoclass:: build_sheet.BuildSheet

.. autofunction:: operations_sheet.flange

.. autofunction:: operations_sheet.hem
```

- [ ] **Step 2: Create `docs/tutorial_sheet_metal.rst`**

Open `docs/tutorial_lego.rst` first and mirror its heading style/structure. Content (each code block cumulative, forming one runnable script):

```rst
####################
Sheet Metal Tutorial
####################

This tutorial builds a small open-top sheet metal box with hemmed rims,
introducing the ``BuildSheet`` builder and the sheet metal operations
``flange`` and ``hem``.

**********************
Step 1: The base sheet
**********************

Sheet metal parts start from a flat base. Inside ``BuildSheet``, a closed
sketch region automatically becomes a sheet of the builder's thickness:

.. code-block:: python

    from build123d import *

    with BuildSheet(thickness=1, bend_radius=2) as box:
        with BuildSketch():
            Rectangle(100, 60)

**********************
Step 2: Fold the walls
**********************

``flange`` folds a wall from selected edges. Folds go away from the face
the edge was selected on, so we select the bottom face's edges to fold
upward. The gaps keep neighbouring walls from intersecting at the corners:

.. code-block:: python

        bottom = box.faces().sort_by(Axis.Z)[0]
        flange(
            bottom.edges().filter_by(GeomType.LINE),
            length=20,
            gap1=3.1,
            gap2=3.1,
        )

********************
Step 3: Hem the rims
********************

Raw sheet edges are sharp; a hem folds them back for a safe rim. The top
edges of the two long walls are selected the same way — by the faces they
belong to:

.. code-block:: python

        rims = box.faces().sort_by(Axis.Z)[-1].edges().filter_by(Axis.X)
        hem(rims, hem_type=HemType.OPEN, width=6, opening=2)

*****************
The result
*****************

``box.sheet`` is a regular ``Part`` — export it, measure it, combine it.
Its bend faces are intentionally kept separate (never unified), which is
what future flat-pattern unfolding needs — so don't ``clean()`` it.
```

Implementation note: **run the assembled tutorial code first** as a script and adjust the rim selection (`sort_by(Axis.Z)[-1]` may need `.faces().filter_by(Plane.XY)` refinement to land on the wall top faces) so the tutorial is truthful — then paste the working selections back into the RST. If the hem-on-flange-edge selection proves unstable, hem the two remaining unfolded base edges instead and adjust prose.

Then create `docs/sheet_metal_examples.py` containing exactly the assembled, working tutorial code (license header + the script ending with `assert box.sheet.is_valid()`). `tests/test_docs_examples.py` auto-discovers every `docs/*.py`, so this file makes the tutorial code CI-tested with no wiring.

- [ ] **Step 3: Wire everything up**

- `docs/builders.rst` toctree — after `build_part.rst` add:
  ```rst
      build_sheet.rst
  ```
- `docs/tutorials.rst` toctree — after `tech_drawing_tutorial.rst` add:
  ```rst
      tutorial_sheet_metal.rst
  ```
- `docs/operations.rst` — add two table rows (copy the exact column widths from the `make_brake_formed` row, which reads `| :func:`~operations_part.make_brake_formed`   | Create sheet metal parts           |    |    |    | ✓  |`); a new column is NOT added — mark the Sheet applicability in the description text:
  ```rst
  | :func:`~operations_sheet.flange`             | Fold a sheet metal wall            |    |    |    | ✓  |                                   |
  +----------------------------------------------+------------------------------------+----+----+----+----+-----------------------------------+
  | :func:`~operations_sheet.hem`                | Fold a sheet edge onto itself      |    |    |    | ✓  |                                   |
  +----------------------------------------------+------------------------------------+----+----+----+----+-----------------------------------+
  ```
  and in the autofunction list at the bottom (near `operations_part` entries):
  ```rst
  .. autofunction:: operations_sheet.flange
  .. autofunction:: operations_sheet.hem
  ```
- `docs/builder_api_reference.rst` — mirror how `BuildPart` is referenced (check with `grep -n "build_part" docs/builder_api_reference.rst`) and add the equivalent `build_sheet.BuildSheet` entry.

- [ ] **Step 4: Verify docs build and tutorial runs**

```bash
python - <<'EOF'
from build123d import *
with BuildSheet(thickness=1, bend_radius=2) as box:
    with BuildSketch():
        Rectangle(100, 60)
    bottom = box.faces().sort_by(Axis.Z)[0]
    flange(bottom.edges().filter_by(GeomType.LINE), length=20, gap1=3.1, gap2=3.1)
    rims = box.faces().sort_by(Axis.Z)[-1].edges().filter_by(Axis.X)
    hem(rims, hem_type=HemType.OPEN, width=6, opening=2)
assert box.sheet.is_valid()
print("tutorial OK", box.sheet.volume)
EOF
```
Expected: `tutorial OK <volume>`. Then, if sphinx is available: `sphinx-build -W --keep-going -b html docs /tmp/claude-1000/-home-gabriel-Documentos-open-source-build123d/9fc321a4-18d8-4c5b-a6fc-b8ae975153ca/scratchpad/docs_build 2>&1 | tail -20` and fix any new warnings referencing the two new files (pre-existing warnings elsewhere are out of scope). If sphinx isn't installed, note it and rely on RST review.

- [ ] **Step 5: Run the docs-example test**

Run: `python -m pytest tests/test_docs_examples.py -v -k sheet_metal`
Expected: 1 PASSED (`sheet_metal_examples`)

- [ ] **Step 6: Commit**

```bash
git add docs/build_sheet.rst docs/tutorial_sheet_metal.rst docs/sheet_metal_examples.py docs/builders.rst docs/tutorials.rst docs/operations.rst docs/builder_api_reference.rst
git commit -m "Add sheet metal docs section, tutorial and tested example"
```

---

### Task 10: Final verification + draft PR & issue comment (for user review — DO NOT POST)

**Files:**
- Create: `/tmp/claude-1000/-home-gabriel-Documentos-open-source-build123d/9fc321a4-18d8-4c5b-a6fc-b8ae975153ca/scratchpad/pr_body.md`
- Create: `/tmp/claude-1000/-home-gabriel-Documentos-open-source-build123d/9fc321a4-18d8-4c5b-a6fc-b8ae975153ca/scratchpad/issue_305_comment.md`

**Interfaces:**
- Consumes: everything.
- Produces: a pushed branch and two drafted texts awaiting the user's go-ahead. **Posting the PR and the issue comment requires explicit user approval — stop and ask.**

- [ ] **Step 1: Full test suite, twice-checked**

Run: `python -m pytest tests/ -q --ignore=tests/test_benchmarks.py`
Expected: everything passes; `tests/test_build_sheet.py` alone must show ≥ 25 tests passing.

- [ ] **Step 2: Lint the new files with the project's tooling**

Check for project config first: `ls .pre-commit-config.yaml pyproject.toml`. If pre-commit hooks exist run `pre-commit run --files src/build123d/build_sheet.py src/build123d/operations_sheet.py tests/test_build_sheet.py`; otherwise run `python -m pylint src/build123d/build_sheet.py src/build123d/operations_sheet.py --disable=all --enable=E` and fix errors.

- [ ] **Step 3: Write the PR body draft**

`scratchpad/pr_body.md` — must contain these sections (write real content from the implemented code, not placeholders):
1. **Summary** — BuildSheet + flange + hem POC, one example code block.
2. **The modeling technique** — bend = revolved thickness rectangle fused without face unification (`SkipClean`), fan-face preservation and why unfold needs it; credit: "modeling technique translated from the FreeCAD SheetMetal workbench (https://github.com/shaise/FreeCAD_SheetMetal)".
3. **Relationship to #305** — implements the construction-API direction from gumyr's comment; unfold explicitly out of scope.
4. **Out of scope / contribution roadmap** — copy the list from the spec (Bend between regions, BendLine, Tab, reliefs, miters, perforation, base_sheet() for open profiles, smCreateBaseShape starters, Material integration, unfold).
5. **Open questions for maintainers** — operation functions vs. capitalized operation classes; dedicated `Sheet` composite type vs. plain `Part`; `Material` integration for `k_factor`; defaulting `make_brake_formed` thickness from `BuildSheet`.
6. Footer: `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

- [ ] **Step 4: Write the issue comment draft**

`scratchpad/issue_305_comment.md` — short: we implemented a construction-side POC per the `BuildSheet` brainstorm, link to the draft PR (placeholder `<PR-URL>` filled after the PR exists), 10-line example, one-line technique summary, invitation for feedback.

- [ ] **Step 5: Push branch and STOP for review**

```bash
git push -u origin sheet-metal-poc   # to the user's fork remote if origin is upstream — check `git remote -v` and ask the user if only gumyr's repo is configured
```
Then present both drafts to the user and **wait for approval** before `gh pr create --draft` and before posting the issue comment.

---

## Self-review checklist (run after writing, before execution)

- Spec coverage: enums (T1), BuildSheet + auto-pad + SkipClean (T2), flange core (T3), gaps/multi-edge (T4), bend positions (T5), errors + algebra (T6), hem ×4 + generators (T7), make_brake_formed enablement (T8), docs section + tutorial + wiring (T9), PR + issue comment for review (T10). k_factor stored (T2), never consumed — matches spec.
- Deviation from spec noted: `roll_angle` defaults to `None` (→ physical max, as FreeCAD does) instead of the spec's `270` — better behavior, mention in PR open questions if desired.
