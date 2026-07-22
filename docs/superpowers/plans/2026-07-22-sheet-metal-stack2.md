# Sheet Metal Stacked PR #2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add bend reliefs, manual miter angles and extends to `flange`, register standard operations for `BuildSheet`, and rebuild TTT's sm_hanger with the new API as a CI-verified before/after showcase — stacked PR #2 on #1381.

**Architecture:** All geometry changes live in `_make_bend` (`operations_sheet.py`), following FreeCAD's `smBend` (reference clone at `/home/gabriel/Documentos/open_source/FreeCAD_SheetMetal/SheetMetalCmd.py`). The wall rectangle becomes a trapezoid builder (extends + miters); relief notches are cut from the base sheet before fusing. Everything stays under `SkipClean` — **never call `.clean()`**; fan-face preservation is the load-bearing POC technique.

**Tech Stack:** build123d (OCCT via cadquery-ocp), unittest, pylint.

**Spec:** `docs/superpowers/specs/2026-07-22-sheet-metal-stack2-design.md` (on local `dev` — NEVER commit spec/plan/ledger to the feature branch).

## Global Constraints

- Work in the worktree `/home/gabriel/Documentos/open_source/build123d-sheet-metal-poc`, branch `sheet-metal-stack2` off `sheet-metal-poc`. All commands below run from that directory.
- Python: `.venv/bin/python` (uv venv, editable install). Tests: `.venv/bin/python -m pytest tests/test_build_sheet.py -k <pattern> -v` per task; the **controller** runs the full ~7-minute suite at the end — implementers must NOT background long commands, run everything foreground.
- Never call `.clean()` on a sheet part; all booleans under `SkipClean` with `clean=False`.
- With all new parameters at defaults, geometry must be bit-identical to PR #1381 (existing tests are the regression net).
- `hem()` does NOT get the new parameters.
- `Shape.is_valid` is a property (not a method); `clean()` mutates (use `copy.copy` before comparing face counts).
- pylint on touched `src/build123d/*.py` files must stay 10/10 (`.venv/bin/python -m pylint src/build123d/operations_sheet.py src/build123d/build_sheet.py`).
- Commit messages: plain imperative style matching the branch history; end with the Claude co-author trailer.

---

### Task 1: Branch setup + `ReliefType` enum

**Files:**
- Modify: `src/build123d/build_enums.py` (after `HemType`, ~line 200)
- Modify: `src/build123d/__init__.py` (alphabetical `__all__`, near `"HemType"` at line 61)
- Test: `tests/test_build_sheet.py` (append)

**Interfaces:**
- Produces: `ReliefType` enum with members `RECTANGLE`, `ROUND` — imported by Tasks 4, 5 as `from build123d.build_enums import ReliefType`.

- [ ] **Step 1: Clean worktree and create branch**

```bash
cd /home/gabriel/Documentos/open_source/build123d-sheet-metal-poc
git status --short
git diff docs/sheet_metal_examples.py
```
`docs/sheet_metal_examples.py` is dirty (leftover from the screenshot session). Inspect the diff: if it only touches the `show(...)`/port plumbing, restore it with `git checkout -- docs/sheet_metal_examples.py`. If it contains anything else, STOP and report to the controller. Then:

```bash
git checkout sheet-metal-poc && git checkout -b sheet-metal-stack2
```

- [ ] **Step 2: Write the failing test** — append to `tests/test_build_sheet.py`:

```python
class TestReliefType(unittest.TestCase):
    def test_members(self):
        self.assertEqual(len(ReliefType), 2)
        self.assertEqual(repr(ReliefType.RECTANGLE), "<ReliefType.RECTANGLE>")
        self.assertEqual(repr(ReliefType.ROUND), "<ReliefType.ROUND>")
```
Add `ReliefType` to the test file's build123d imports.

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_build_sheet.py -k TestReliefType -v`
Expected: FAIL — `ImportError: cannot import name 'ReliefType'`

- [ ] **Step 4: Implement** — in `src/build123d/build_enums.py`, insert after the `HemType` class (keep alphabetical-ish grouping with the other sheet enums):

```python
class ReliefType(Enum):
    """Sheet metal bend relief notch shapes"""

    RECTANGLE = auto()  # rectangular notch
    ROUND = auto()  # notch with a semicircular end cap

    def __repr__(self):
        return f"<{self.__class__.__name__}.{self.name}>"
```

In `src/build123d/__init__.py`, add `"ReliefType",` to `__all__` (alphabetical, after `"Plane"`-area entries near `"HemType"`) and ensure it is imported where `HemType` is.

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_build_sheet.py -k TestReliefType -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/build123d/build_enums.py src/build123d/__init__.py tests/test_build_sheet.py
git commit -m "Add ReliefType enum for sheet metal bend reliefs"
```

---

### Task 2: `flange` extends (`extend1`, `extend2`)

**Files:**
- Modify: `src/build123d/operations_sheet.py` (`_make_bend` ~line 101, `flange` ~line 187)
- Test: `tests/test_build_sheet.py` (append)

**Interfaces:**
- Consumes: existing `_make_bend(target, edge, thickness, radius, angle, leg_length, gap1, gap2, offset)`.
- Produces: `_make_bend(..., gap1=0, gap2=0, offset=0, extend1=0, extend2=0)` — the wall widens by `extend_i` beyond the gap-trimmed ends; **the bend sector does not widen** (FreeCAD parity, SheetMetalCmd.py:1583). `flange(..., extend1=0, extend2=0, ...)` keyword-only passthrough. Tasks 3–5 extend the same two signatures further.

- [ ] **Step 1: Write the failing tests** — append:

```python
class TestFlangeExtends(unittest.TestCase):
    def _flange_volume(self, **kwargs):
        with BuildSheet(thickness=1, bend_radius=2) as sheet:
            with BuildSketch():
                Rectangle(100, 60)
            edge = (
                sheet.faces().sort_by(Axis.Z)[0]
                .edges().filter_by(Axis.Y).sort_by(Axis.X)[0]
            )
            flange(edge, length=10, gap1=10, gap2=10, **kwargs)
        return sheet.sheet.volume

    def test_extends_widen_wall_only(self):
        # base 6000 + sector over 40 (gapped) + wall 50 x 10 x 1
        sector = radians(90) / 2 * ((2 + 1) ** 2 - 2**2) * 40
        self.assertAlmostEqual(
            self._flange_volume(extend1=5, extend2=5), 6000 + sector + 500, places=3
        )

    def test_sector_unchanged_by_extends(self):
        # extends add exactly the extra wall material: 2 * (5 x 10 x 1)
        plain = self._flange_volume()
        extended = self._flange_volume(extend1=5, extend2=5)
        self.assertAlmostEqual(extended - plain, 100, places=3)

    def test_negative_extend_rejected(self):
        with self.assertRaises(ValueError):
            self._flange_volume(extend1=-1)
```
(`radians` comes from `math` — add to the test file imports if missing.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_build_sheet.py -k TestFlangeExtends -v`
Expected: FAIL — `TypeError: flange() got an unexpected keyword argument 'extend1'`

- [ ] **Step 3: Implement** — in `_make_bend`, change the signature and the wall block:

```python
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
    extend1: float = 0,
    extend2: float = 0,
) -> tuple[list[Solid], list[Solid]]:
```

Replace the wall block (`if leg_length > 0:` ... `additions.append(wall.rotate(axis, angle))`) with:

```python
    if leg_length > 0:
        # extends widen the flat wall beyond the gap-trimmed ends; the bend
        # sector deliberately keeps the gapped width (FreeCAD smBend parity)
        q0 = p0 - axis_dir * extend1
        q1 = p1 + axis_dir * extend2
        wall_face = Face(
            Wire.make_polygon(
                [q0, q1, q1 + f_dir * leg_length, q0 + f_dir * leg_length],
                close=True,
            )
        )
        wall = Solid.extrude(wall_face, thk_dir * thickness)
        additions.append(wall.rotate(axis, angle))
```

In `flange`: add `extend1: float = 0, extend2: float = 0,` to the signature after `gap2`; add validation right after the gap check:

```python
    if extend1 < 0 or extend2 < 0:
        raise ValueError("extends can't be negative")
```

Pass them through in the `_make_bend` call:

```python
        adds, cut_solids = _make_bend(
            target, edge, thickness, radius, angle, length, gap1, gap2, offset,
            extend1, extend2,
        )
```

Docstring: add under `gap1/gap2`:
```
        extend1/extend2 (float, optional): widen the flat wall beyond each
            end of the edge. Only the wall widens — the bend keeps the
            gapped width, so a wide leg can overhang the bend's sides.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_build_sheet.py -k "TestFlangeExtends or TestFlange" -v`
Expected: all PASS (existing flange tests prove default-off behavior unchanged)

- [ ] **Step 5: Commit**

```bash
git add src/build123d/operations_sheet.py tests/test_build_sheet.py
git commit -m "Add extend1/extend2 to flange (widen wall beyond edge ends)"
```

---

### Task 3: `flange` miter angles

**Files:**
- Modify: `src/build123d/operations_sheet.py`
- Test: `tests/test_build_sheet.py` (append)

**Interfaces:**
- Consumes: Task 2's `_make_bend`/`flange` signatures.
- Produces: `_make_bend(..., extend1=0, extend2=0, miter_angle1=0, miter_angle2=0)`; `flange(..., miter_angle1=0, miter_angle2=0, ...)`. Positive angle cuts the wall's far end inward by `leg_length * tan(angle)` from that side; negative widens outward. `ValueError` "miter angles leave no wall at the tip" when the far edge vanishes.

- [ ] **Step 1: Write the failing tests** — append:

```python
class TestFlangeMiter(unittest.TestCase):
    SECTOR_60 = radians(90) / 2 * ((2 + 1) ** 2 - 2**2) * 60

    def _flange_volume(self, **kwargs):
        with BuildSheet(thickness=1, bend_radius=2) as sheet:
            with BuildSketch():
                Rectangle(100, 60)
            edge = (
                sheet.faces().sort_by(Axis.Z)[0]
                .edges().filter_by(Axis.Y).sort_by(Axis.X)[0]
            )
            flange(edge, length=10, **kwargs)
        return sheet.sheet.volume

    def test_positive_miters_cut_trapezoid(self):
        # wall = 60x10 minus two 45-degree triangles (10*10/2 each)
        vol = self._flange_volume(miter_angle1=45, miter_angle2=45)
        self.assertAlmostEqual(vol, 6000 + self.SECTOR_60 + 500, places=3)

    def test_negative_miters_widen(self):
        vol = self._flange_volume(miter_angle1=-45, miter_angle2=-45)
        self.assertAlmostEqual(vol, 6000 + self.SECTOR_60 + 700, places=3)

    def test_degenerate_tip_rejected(self):
        # wall width 20 (gaps), 45+45 over length 10 consumes it entirely
        with self.assertRaises(ValueError):
            self._flange_volume(gap1=20, gap2=20, miter_angle1=45, miter_angle2=45)

    def test_angle_range(self):
        with self.assertRaises(ValueError):
            self._flange_volume(miter_angle1=90)
        with self.assertRaises(ValueError):
            self._flange_volume(miter_angle2=-95)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_build_sheet.py -k TestFlangeMiter -v`
Expected: FAIL — unexpected keyword argument 'miter_angle1'

- [ ] **Step 3: Implement** — `_make_bend` signature gains `miter_angle1: float = 0, miter_angle2: float = 0,` after `extend2`. Replace the wall block from Task 2 with:

```python
    if leg_length > 0:
        # extends widen the flat wall beyond the gap-trimmed ends; the bend
        # sector deliberately keeps the gapped width (FreeCAD smBend parity)
        q0 = p0 - axis_dir * extend1
        q1 = p1 + axis_dir * extend2
        # miter angles shift the far corners along the edge: positive cuts
        # inward, negative widens (FreeCAD smMakeFace angle semantics)
        far0 = q0 + f_dir * leg_length + axis_dir * (
            leg_length * tan(radians(miter_angle1))
        )
        far1 = q1 + f_dir * leg_length - axis_dir * (
            leg_length * tan(radians(miter_angle2))
        )
        if (far1 - far0).dot(axis_dir) <= 0:
            raise ValueError("miter angles leave no wall at the tip")
        wall_face = Face(Wire.make_polygon([q0, q1, far1, far0], close=True))
        wall = Solid.extrude(wall_face, thk_dir * thickness)
        additions.append(wall.rotate(axis, angle))
```

Extend the module's math import: `from math import asin, atan, cos, degrees, radians, sin, sqrt, tan`.

In `flange`: signature gains `miter_angle1: float = 0, miter_angle2: float = 0,` after `extend2`; validation after the extends check:

```python
    if abs(miter_angle1) >= 90 or abs(miter_angle2) >= 90:
        raise ValueError("miter angles must be within (-90, 90) degrees")
```

Update the `_make_bend` call in `flange`:

```python
        adds, cut_solids = _make_bend(
            target, edge, thickness, radius, angle, length, gap1, gap2, offset,
            extend1, extend2, miter_angle1, miter_angle2,
        )
```

Docstring:
```
        miter_angle1/miter_angle2 (float, optional): angled end-cut in
            degrees at each side of the wall's free end — positive cuts
            inward, negative widens the wall outward. The bend itself is
            never mitered.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_build_sheet.py -k "TestFlangeMiter or TestFlangeExtends or TestFlange" -v
`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/build123d/operations_sheet.py tests/test_build_sheet.py
git commit -m "Add miter_angle1/miter_angle2 end-cuts to flange"
```

---

### Task 4: Rectangular bend reliefs

**Files:**
- Modify: `src/build123d/operations_sheet.py`
- Test: `tests/test_build_sheet.py` (append)

**Interfaces:**
- Consumes: Task 3's signatures; `ReliefType` from Task 1.
- Produces: module helper `_relief_cuts(orig0, orig1, axis_dir, f_dir, thk_dir, thickness, gap1, gap2, relief, relief_size, offset) -> list[Solid]` (Task 5 extends it for ROUND); `_make_bend(..., relief=None, relief_size=None)`; `flange(..., relief=None, relief_size=None, ...)` where `relief: ReliefType | None`, `relief_size: tuple[float, float] | None` (width, depth), defaulting to `(0.7 * thickness, 0.7 * thickness)`.

- [ ] **Step 1: Write the failing tests** — append:

```python
class TestFlangeRelief(unittest.TestCase):
    SECTOR_40 = radians(90) / 2 * ((2 + 1) ** 2 - 2**2) * 40

    def _flange_volume(self, **kwargs):
        with BuildSheet(thickness=1, bend_radius=2) as sheet:
            with BuildSketch():
                Rectangle(100, 60)
            edge = (
                sheet.faces().sort_by(Axis.Z)[0]
                .edges().filter_by(Axis.Y).sort_by(Axis.X)[0]
            )
            flange(edge, length=10, **kwargs)
        return sheet.sheet.volume

    def test_rectangle_relief_volume(self):
        # two notches of 2 x 3 x thickness cut from the base sheet
        vol = self._flange_volume(
            gap1=10, gap2=10, relief=ReliefType.RECTANGLE, relief_size=(2, 3)
        )
        self.assertAlmostEqual(vol, 6000 - 12 + self.SECTOR_40 + 400, places=3)

    def test_relief_default_size(self):
        # defaults to (0.7*t, 0.7*t) -> two notches of 0.49
        vol = self._flange_volume(gap1=10, gap2=10, relief=ReliefType.RECTANGLE)
        self.assertAlmostEqual(vol, 6000 - 0.98 + self.SECTOR_40 + 400, places=3)

    def test_relief_only_at_gapped_end(self):
        sector_50 = radians(90) / 2 * ((2 + 1) ** 2 - 2**2) * 50
        vol = self._flange_volume(
            gap1=10, relief=ReliefType.RECTANGLE, relief_size=(2, 3)
        )
        self.assertAlmostEqual(vol, 6000 - 6 + sector_50 + 500, places=3)

    def test_relief_inside_bend_extra_notch(self):
        # MATERIAL_INSIDE: slab 40x3x1 cut, plus 2 notches (2x3x1) plus
        # 2 offset bands (2x3x1) clearing alongside the shifted wall
        vol = self._flange_volume(
            gap1=10, gap2=10,
            relief=ReliefType.RECTANGLE, relief_size=(2, 3),
            bend_position=BendPosition.MATERIAL_INSIDE,
        )
        self.assertAlmostEqual(
            vol, 6000 - 120 - 12 - 12 + self.SECTOR_40 + 400, places=3
        )

    def test_relief_fan_faces_preserved(self):
        with BuildSheet(thickness=1, bend_radius=2) as sheet:
            with BuildSketch():
                Rectangle(100, 60)
            edge = (
                sheet.faces().sort_by(Axis.Z)[0]
                .edges().filter_by(Axis.Y).sort_by(Axis.X)[0]
            )
            flange(edge, length=10, gap1=10, gap2=10,
                   relief=ReliefType.RECTANGLE, relief_size=(2, 3))
        raw_count = len(sheet.sheet.faces())
        cleaned = copy.copy(sheet.sheet).clean()
        self.assertGreater(raw_count, len(cleaned.faces()))

    def test_relief_errors(self):
        with self.assertRaises(ValueError):  # no gapped end
            self._flange_volume(relief=ReliefType.RECTANGLE)
        with self.assertRaises(ValueError):  # width exceeds the gap
            self._flange_volume(gap1=1, relief=ReliefType.RECTANGLE,
                                relief_size=(2, 3))
        with self.assertRaises(ValueError):  # non-positive size
            self._flange_volume(gap1=10, relief=ReliefType.RECTANGLE,
                                relief_size=(0, 3))
        with self.assertRaises(ValueError):  # relief_size without relief
            self._flange_volume(gap1=10, relief_size=(2, 3))
```
(`copy` is already imported by the existing fan-face tests; verify.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_build_sheet.py -k TestFlangeRelief -v`
Expected: FAIL — unexpected keyword argument 'relief'

- [ ] **Step 3: Implement** — add the helper above `_make_bend`:

```python
def _relief_cuts(
    orig0: Vector,
    orig1: Vector,
    axis_dir: Vector,
    f_dir: Vector,
    thk_dir: Vector,
    thickness: float,
    gap1: float,
    gap2: float,
    relief: ReliefType,
    relief_size: tuple[float, float],
    offset: float,
) -> list[Solid]:
    """Bend relief notches cut into the base sheet at each gapped end.

    Each notch is flush against the wall's side, spanning the last
    relief-width of the gap, cut through the sheet thickness (FreeCAD
    smMakeReliefFace placement). For inside bend positions an extra
    rectangular band of depth ``offset`` clears alongside the shifted
    wall (FreeCAD parity).
    """
    width, depth = relief_size
    cuts: list[Solid] = []
    for end, direction, gap in ((orig0, axis_dir, gap1), (orig1, -axis_dir, gap2)):
        if gap <= 0:
            continue
        inner = end + direction * gap  # flush with the wall's side
        outer = inner - direction * width  # toward the sheet corner
        root0, root1 = outer, inner
        if offset < 0:  # wall root shifted into the material
            root0 = outer + f_dir * offset
            root1 = inner + f_dir * offset
            band = Face(
                Wire.make_polygon([outer, inner, root1, root0], close=True)
            )
            cuts.append(Solid.extrude(band, thk_dir * thickness))
        notch = Face(
            Wire.make_polygon(
                [root0, root1, root1 - f_dir * depth, root0 - f_dir * depth],
                close=True,
            )
        )
        cuts.append(Solid.extrude(notch, thk_dir * thickness))
    return cuts
```

In `_make_bend`: signature gains `relief: ReliefType | None = None, relief_size: tuple[float, float] | None = None,` after `miter_angle2`. Right after the frame derivation and gap-width check (before the gap trim mutates `p0`/`p1`), capture the originals; after the `offset < 0` slab block, add the relief cuts:

```python
    p0, p1, thk_dir, f_dir, axis_dir = _bend_frame(edge, target, thickness)
    if gap1 + gap2 >= (p1 - p0).length:
        raise ValueError("gap1 + gap2 leave no bend width on the edge")
    orig0, orig1 = p0, p1
    p0 = p0 + axis_dir * gap1
    p1 = p1 - axis_dir * gap2

    cuts: list[Solid] = []
    if relief is not None:
        cuts.extend(
            _relief_cuts(
                orig0, orig1, axis_dir, f_dir, thk_dir, thickness,
                gap1, gap2, relief, relief_size, offset,
            )
        )
    if offset < 0:
        ...existing slab block unchanged...
```

Import `ReliefType` in the module's `build_enums` import line.

In `flange`: signature gains `relief: ReliefType | None = None, relief_size: tuple[float, float] | None = None,` after `miter_angle2`. Validation after the miter check (note: `thickness` is already resolved a few lines below — move this block AFTER the thickness/radius resolution so the default can use it):

```python
    if relief_size is not None and relief is None:
        raise ValueError("relief_size requires relief")
    if relief is not None:
        if gap1 <= 0 and gap2 <= 0:
            raise ValueError("relief requires gap1 or gap2 > 0")
        if relief_size is None:
            relief_size = (0.7 * thickness, 0.7 * thickness)
        if relief_size[0] <= 0 or relief_size[1] <= 0:
            raise ValueError("relief_size values must be positive")
        for gap in (gap1, gap2):
            if 0 < gap < relief_size[0]:
                raise ValueError("relief width must not exceed the gap")
```

Pass `relief, relief_size` through to `_make_bend`. Docstring:
```
        relief (ReliefType, optional): cut a bend relief notch into the
            base sheet at each gapped end of the wall. Defaults to None.
        relief_size (tuple[float, float], optional): (width, depth) of the
            notch. Defaults to 0.7 x thickness for both.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_build_sheet.py -k "TestFlangeRelief or TestFlange" -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/build123d/operations_sheet.py tests/test_build_sheet.py
git commit -m "Add rectangular bend reliefs to flange"
```

---

### Task 5: Round reliefs

**Files:**
- Modify: `src/build123d/operations_sheet.py` (`_relief_cuts` only)
- Test: `tests/test_build_sheet.py` (append)

**Interfaces:**
- Consumes: Task 4's `_relief_cuts` and `ReliefType`.
- Produces: `ReliefType.ROUND` support — straight sides plus a semicircular cap of radius `width/2`; a single arc when `depth <= width/2` (FreeCAD `smMakeReliefFace`, SheetMetalCmd.py:447-478).

- [ ] **Step 1: Write the failing tests** — append:

```python
class TestFlangeRoundRelief(unittest.TestCase):
    SECTOR_40 = radians(90) / 2 * ((2 + 1) ** 2 - 2**2) * 40

    def _flange_volume(self, relief_size):
        with BuildSheet(thickness=1, bend_radius=2) as sheet:
            with BuildSketch():
                Rectangle(100, 60)
            edge = (
                sheet.faces().sort_by(Axis.Z)[0]
                .edges().filter_by(Axis.Y).sort_by(Axis.X)[0]
            )
            flange(edge, length=10, gap1=10, gap2=10,
                   relief=ReliefType.ROUND, relief_size=relief_size)
        return sheet.sheet.volume

    def test_round_relief_volume(self):
        # notch area = w*(d - w/2) + pi*(w/2)^2/2 with w=2, d=3 -> 4 + pi/2
        notches = 2 * (4 + pi / 2)
        self.assertAlmostEqual(
            self._flange_volume((2, 3)),
            6000 - notches + self.SECTOR_40 + 400, places=3,
        )

    def test_round_relief_semicircle_degenerate(self):
        # depth == width/2 -> pure semicircle, area pi/2 each
        self.assertAlmostEqual(
            self._flange_volume((2, 1)),
            6000 - pi + self.SECTOR_40 + 400, places=3,
        )
```
(`pi` from `math` in the test imports.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_build_sheet.py -k TestFlangeRoundRelief -v`
Expected: FAIL (round branch missing — either wrong volume from a rectangle fallback or an error; if Task 4's code silently made a rectangle, this test catches it)

- [ ] **Step 3: Implement** — in `_relief_cuts`, replace the `notch = Face(...)` rectangle with a shape dispatch:

```python
        if relief == ReliefType.RECTANGLE:
            notch = Face(
                Wire.make_polygon(
                    [root0, root1, root1 - f_dir * depth, root0 - f_dir * depth],
                    close=True,
                )
            )
        else:  # ReliefType.ROUND
            cap_radius = width / 2
            mid = (root0 + root1) * 0.5
            mouth = Edge.make_line(root0, root1)
            if depth <= cap_radius + 1e-9:
                cap = Edge.make_three_point_arc(
                    root1, mid - f_dir * depth, root0
                )
                notch = Face(Wire([mouth, cap]))
            else:
                shoulder0 = root0 - f_dir * (depth - cap_radius)
                shoulder1 = root1 - f_dir * (depth - cap_radius)
                notch = Face(
                    Wire(
                        [
                            mouth,
                            Edge.make_line(root1, shoulder1),
                            Edge.make_three_point_arc(
                                shoulder1, mid - f_dir * depth, shoulder0
                            ),
                            Edge.make_line(shoulder0, root0),
                        ]
                    )
                )
        cuts.append(Solid.extrude(notch, thk_dir * thickness))
```
If `Edge.make_three_point_arc` does not exist under that name in this build123d version, check `src/build123d/objects_curve.py` `ThreePointArc` for the actual constructor it uses and call that — do not substitute an approximation.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_build_sheet.py -k "Relief" -v`
Expected: all PASS (including Task 4's)

- [ ] **Step 5: Add the algebra-mode test for all new params** — append, run, and confirm PASS:

```python
class TestFlangeNewParamsAlgebra(unittest.TestCase):
    def test_algebra_extends_miter_relief(self):
        sheet = Part(
            Compound([Solid.make_box(100, 60, 1)])
        )
        edge = (
            sheet.faces().sort_by(Axis.Z)[0]
            .edges().filter_by(Axis.Y).sort_by(Axis.X)[0]
        )
        result = flange(
            edge, length=10, thickness=1, radius=2,
            gap1=5, gap2=5, extend1=2, miter_angle2=-20,
            relief=ReliefType.ROUND, relief_size=(2, 2),
        )
        self.assertTrue(result.is_valid)
        self.assertGreater(result.volume, 6000)
```
Match the existing `TestFlangeAlgebra` class's sheet-construction idiom if it differs (read it first) — the point under test is that the new params work without a context.

Run: `.venv/bin/python -m pytest tests/test_build_sheet.py -k "Algebra" -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/build123d/operations_sheet.py tests/test_build_sheet.py
git commit -m "Add round bend reliefs and algebra-mode coverage for new flange params"
```

---

### Task 6: Register standard operations for BuildSheet

**Files:**
- Modify: `src/build123d/build_common.py` (`operations_apply_to` dict, ~lines 155-170)
- Modify: `src/build123d/build_sheet.py` (`BuildSheet.__init__`)
- Test: `tests/test_build_sheet.py` (append)

**Interfaces:**
- Consumes: `BuildSheet._add_to_context` (forces `clean=False` — this is why registration is safe).
- Produces: `extrude`, `fillet`, `chamfer`, `add`, `mirror` legal inside `BuildSheet`; `BuildSheet.pending_faces` / `pending_face_planes` (empty lists) so no-arg `extrude()` fails with `ValueError`, not `AttributeError`. Task 7 relies on `extrude(<sketch>, amount=..., mode=Mode.SUBTRACT)` and `fillet` inside `BuildSheet`.

**Key subtlety:** `BuildSheet` auto-pads sketch faces on sketch exit, so there are never pending faces — `extrude` inside `BuildSheet` must always be given an explicit sketch/face built with `mode=Mode.PRIVATE`. Document this in Task 8's docs.

- [ ] **Step 1: Write the failing tests** — append:

```python
class TestBuildSheetRegisteredOps(unittest.TestCase):
    """Standard operations registered for BuildSheet preserve bend faces."""

    SECTOR_60 = radians(90) / 2 * ((2 + 1) ** 2 - 2**2) * 60
    BASE_WITH_FLANGE = 6000 + SECTOR_60 + 600  # full-width flange, length 10

    @staticmethod
    def _fan_faces_preserved(part):
        cleaned = copy.copy(part).clean()
        return len(part.faces()) > len(cleaned.faces())

    def _sheet_with_flange(self):
        builder = BuildSheet(thickness=1, bend_radius=2)
        builder.__enter__()
        with BuildSketch():
            Rectangle(100, 60)
        edge = (
            builder.faces().sort_by(Axis.Z)[0]
            .edges().filter_by(Axis.Y).sort_by(Axis.X)[0]
        )
        flange(edge, length=10)
        return builder

    def test_extrude_subtract(self):
        with BuildSheet(thickness=1, bend_radius=2) as sheet:
            with BuildSketch():
                Rectangle(100, 60)
            edge = (
                sheet.faces().sort_by(Axis.Z)[0]
                .edges().filter_by(Axis.Y).sort_by(Axis.X)[0]
            )
            flange(edge, length=10)
            with BuildSketch(Plane.XY.offset(1), mode=Mode.PRIVATE) as hole:
                Circle(5)
            extrude(hole.sketch, amount=-1, mode=Mode.SUBTRACT)
        self.assertAlmostEqual(
            sheet.sheet.volume, self.BASE_WITH_FLANGE - 25 * pi, places=3
        )
        self.assertTrue(self._fan_faces_preserved(sheet.sheet))

    def test_extrude_without_sketch_raises_value_error(self):
        with BuildSheet(thickness=1) as sheet:
            with BuildSketch():
                Rectangle(10, 10)
            with self.assertRaises(ValueError):
                extrude(amount=5)

    def test_fillet(self):
        with BuildSheet(thickness=1, bend_radius=2) as sheet:
            with BuildSketch():
                Rectangle(100, 60)
            edge = (
                sheet.faces().sort_by(Axis.Z)[0]
                .edges().filter_by(Axis.Y).sort_by(Axis.X)[0]
            )
            flange(edge, length=10)
            corners = sheet.edges().filter_by(Axis.Z).group_by(Axis.X)[-1]
            fillet(corners, radius=5)
        removed = 2 * (25 - 25 * pi / 4)
        self.assertAlmostEqual(
            sheet.sheet.volume, self.BASE_WITH_FLANGE - removed, places=3
        )
        self.assertTrue(self._fan_faces_preserved(sheet.sheet))

    def test_chamfer(self):
        with BuildSheet(thickness=1, bend_radius=2) as sheet:
            with BuildSketch():
                Rectangle(100, 60)
            edge = (
                sheet.faces().sort_by(Axis.Z)[0]
                .edges().filter_by(Axis.Y).sort_by(Axis.X)[0]
            )
            flange(edge, length=10)
            corners = sheet.edges().filter_by(Axis.Z).group_by(Axis.X)[-1]
            chamfer(corners, length=2)
        self.assertAlmostEqual(
            sheet.sheet.volume, self.BASE_WITH_FLANGE - 4, places=3
        )

    def test_add(self):
        with BuildSheet(thickness=1, bend_radius=2) as sheet:
            with BuildSketch():
                Rectangle(100, 60)
            edge = (
                sheet.faces().sort_by(Axis.Z)[0]
                .edges().filter_by(Axis.Y).sort_by(Axis.X)[0]
            )
            flange(edge, length=10)
            box = Solid.make_box(10, 10, 10).locate(Location((10, -5, 1)))
            add(box)
        self.assertAlmostEqual(
            sheet.sheet.volume, self.BASE_WITH_FLANGE + 1000, places=3
        )

    def test_mirror(self):
        with BuildSheet(thickness=1, bend_radius=2) as sheet:
            with BuildSketch():
                with Locations((25, 0)):
                    Rectangle(50, 60)
            edge = (
                sheet.faces().sort_by(Axis.Z)[0]
                .edges().filter_by(Axis.Y).sort_by(Axis.X)[-1]
            )
            flange(edge, length=10)
            mirror(about=Plane.YZ)
        half = 3000 + self.SECTOR_60 + 600
        self.assertAlmostEqual(sheet.sheet.volume, 2 * half, places=3)
        self.assertTrue(self._fan_faces_preserved(sheet.sheet))
```
Remove the unused `_sheet_with_flange` helper if the final tests don't use it. Note `test_add`'s box sits on top of the base (z from 1) overlapping nothing, face-to-face — if OCCT fuse of a face-touching box gives volume trouble, overlap it by 0.5 into the sheet and adjust the expected volume to `+ 950`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_build_sheet.py -k TestBuildSheetRegisteredOps -v`
Expected: FAIL — `RuntimeError`/`ValueError` from `validate_inputs` ("extrude doesn't apply to BuildSheet" or similar)

- [ ] **Step 3: Implement** — in `src/build123d/build_common.py` `operations_apply_to`, append `"BuildSheet"`:

```python
    "add": ["BuildPart", "BuildSketch", "BuildLine", "BuildSheet"],
    ...
    "chamfer": ["BuildPart", "BuildSketch", "BuildLine", "BuildSheet"],
    ...
    "extrude": ["BuildPart", "BuildSheet"],
    "fillet": ["BuildPart", "BuildSketch", "BuildLine", "BuildSheet"],
    ...
    "mirror": ["BuildPart", "BuildSketch", "BuildLine", "BuildSheet"],
```
(Only these five lines change; `flange`/`hem`/`make_brake_formed` entries stay as they are.)

In `src/build123d/build_sheet.py` `BuildSheet.__init__`, next to the existing pending-edges initialization, add:

```python
        self.pending_faces: list[Face] = []
        self.pending_face_planes: list[Plane] = []
```
(Import `Face`/`Plane` only if not already imported.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_build_sheet.py -k TestBuildSheetRegisteredOps -v`
Expected: all PASS. If `test_extrude_without_sketch_raises_value_error` fails with `AttributeError`, the pending lists are missing or misnamed — check `operations_part.extrude` for the exact attribute names it reads.

- [ ] **Step 5: Run the whole sheet test file**

Run: `.venv/bin/python -m pytest tests/test_build_sheet.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/build123d/build_common.py src/build123d/build_sheet.py tests/test_build_sheet.py
git commit -m "Register extrude/fillet/chamfer/add/mirror for BuildSheet"
```

---

### Task 7: TTT sm_hanger rewrite

**Files:**
- Create: `docs/assets/ttt/ttt-23-02-02-sm_hanger_buildsheet.py`
- Reference (read, do not modify): `docs/assets/ttt/ttt-23-02-02-sm_hanger.py`

**Interfaces:**
- Consumes: everything from Tasks 2–6.
- Produces: a runnable example with an ACTIVE mass assertion (`abs(mass - 1028) < 10`), auto-discovered by `tests/test_examples.py` (it executes every `docs/assets/ttt/*.py` with `ocp_vscode` mocked).

**Ground truth first:** the original encodes bend allowances in magic numbers; the new version derives everything from the TTT drawing dimensions. Two details are empirically ambiguous (which side of the brake-formed line carries the material for the tab; exact fillet targets) — resolve them against the probe, not by guessing.

- [ ] **Step 1: Probe the original** — write to the scratchpad (NOT the repo) and run:

```python
# probe_original.py
from unittest.mock import MagicMock
import sys
sys.modules["ocp_vscode"] = MagicMock()
import runpy
ns = runpy.run_path(
    "docs/assets/ttt/ttt-23-02-02-sm_hanger.py", run_name="not_main"
)
part = ns["sm_hanger"].part
print("volume", part.volume)
print("bbox", part.bounding_box())
for name in ("side", "wing"):
    print(name, ns[name].part.volume, ns[name].part.bounding_box())
print("tab", ns["tab"].volume, ns["tab"].bounding_box())
```
Run: `.venv/bin/python <scratchpad>/probe_original.py`
Record the printed volumes and bounding boxes — they are the calibration targets (total volume should be ≈ 131,800 mm³ for 1028 g at 7800 kg/m³).

- [ ] **Step 2: Write the new example** — create `docs/assets/ttt/ttt-23-02-02-sm_hanger_buildsheet.py`:

```python
"""
Too Tall Toby's sm_hanger — BuildSheet edition

name: ttt_sm_hanger_buildsheet.py
by:   Gabriel Jesus
date: July 22nd 2026

desc:
    The same sheet metal part as ttt-23-02-02-sm_hanger.py, built with the
    BuildSheet API: one flat base sketch and flange folds. Every dimension
    below comes straight off the TTT drawing — no manual bend-allowance
    constants (the original needs 1.526 * sheet_thickness and
    PolarLine(..., 20.371288916) to pre-compensate the bends).

license:

    Copyright 2026 Gabriel Jesus

    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at

        http://www.apache.org/licenses/LICENSE-2.0

    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
"""

from math import atan, degrees, radians, sin, tan

from build123d import *
from ocp_vscode import *

thickness = 4 * MM
outer_radius = 7 * MM  # every bend fillet on the TTT drawing
inner_radius = outer_radius - thickness

# drawing dimensions
top_z = 65 * MM  # top surface height
overall_half_x = 170 / 2 * MM  # to the outside of the sloped legs
plate_width = 80 * MM  # top plate span in Y
leg_angle = 60  # slope from horizontal
leg_width = 112.52 * MM  # legs widen to this across the slope
wing_angle = 75
wing_span_x = 110 * MM
tab_hole_z = 80 * MM

# tangent trim at a bend: the flat region ends where the outer-radius arc
# starts, r * tan(bend_angle / 2) before the sharp-corner intersection
def trim(bend_angle: float) -> float:
    return outer_radius * tan(radians(bend_angle / 2))

plate_half_x = overall_half_x - trim(leg_angle)
slope_flat = 65 / sin(radians(leg_angle)) - trim(leg_angle) - trim(120)
foot_flat = 65 / tan(radians(leg_angle)) - trim(120)
taper_miter = -degrees(atan(((leg_width - plate_width) / 2) / slope_flat))
wing_half_y = (plate_width / 2 + 46.104 - 40) - trim(wing_angle)
wing_flat = 20.371288916 - trim(wing_angle)  # == 15.0 exactly
tab_flat_end_x = 28 - trim(90)  # tab bends up to a face at x = +/-28
tab_leg = 88 - (top_z - thickness) - outer_radius

with BuildSheet(thickness=thickness, bend_radius=inner_radius) as sm_hanger:
    with BuildSketch(Plane.XY.offset(top_z - thickness)) as base:
        Rectangle(2 * plate_half_x, plate_width)
        Rectangle(wing_span_x, 2 * wing_half_y)
        # central cutouts with rounded outer corners, one strip left for
        # each tab
        with Locations((20, 0)):
            Rectangle(30, 30, align=(Align.MIN, Align.CENTER), mode=Mode.SUBTRACT)
        with Locations((-20, 0)):
            Rectangle(30, 30, align=(Align.MAX, Align.CENTER), mode=Mode.SUBTRACT)
        fillet(
            base.vertices().filter_by(
                lambda v: abs(abs(v.X) - 50) < 1e-6 and abs(abs(v.Y) - 15) < 1e-6
            ),
            7,
        )
        with Locations((20, 0)):
            Rectangle(tab_flat_end_x - 20, 16, align=(Align.MIN, Align.CENTER))
        with Locations((-20, 0)):
            Rectangle(tab_flat_end_x - 20, 16, align=(Align.MAX, Align.CENTER))

    # NOTE: every flange REPLACES the sheet, so edges must be re-selected
    # from sm_hanger immediately before each call — never reuse a face or
    # edge captured before a previous flange (stale topology).

    # sloped legs, widening 80 -> 112.52 across the slope (negative miters)
    top_face = sm_hanger.faces().sort_by(Axis.Z)[-1]
    leg_edges = top_face.edges().filter_by(Axis.Y).sort_by(Axis.X)
    flange(
        [leg_edges[0], leg_edges[-1]],
        length=slope_flat,
        angle=leg_angle,
        miter_angle1=taper_miter,
        miter_angle2=taper_miter,
    )
    # feet: fold a further 120 degrees back to horizontal — the free edge
    # of each slope wall is the lowest leg_width-long edge on its side
    foot_edges = (
        sm_hanger.edges()
        .filter_by(GeomType.LINE)
        .filter_by(lambda e: abs(e.length - leg_width) < 0.1)
        .group_by(Axis.Z)[0]
    )
    flange(foot_edges, length=foot_flat, angle=120)

    # wings at 75 degrees
    top_face = sm_hanger.faces().sort_by(Axis.Z)[-1]
    wing_edges = top_face.edges().filter_by(Axis.X).sort_by(Axis.Y)
    flange([wing_edges[0], wing_edges[-1]], length=wing_flat, angle=wing_angle)

    # tabs fold up from the strip ends
    bottom_face = sm_hanger.faces().sort_by(Axis.Z)[0]
    tab_edges = bottom_face.edges().filter_by(Axis.Y).filter_by(
        lambda e: abs(abs(e.center().X) - tab_flat_end_x) < 1e-6
    )
    flange(tab_edges, length=tab_leg, angle=90)

    # corner rounds on foot / wing / tab tips (drawing R7, tab R5)
    fillet(sm_hanger.edges().filter_by(Axis.Z).group_by(Axis.Z)[0], 7)
    fillet(sm_hanger.edges().filter_by(Axis.X).group_by(Axis.Z)[-1], 5)

    # slot cutouts pierce the folded legs — prism cuts across bends
    with BuildSketch(Plane.XY.offset(top_z), mode=Mode.PRIVATE) as top_slots:
        SlotCenterPoint((154, 0), (154 / 2, 0), 20)
        SlotCenterPoint((-154, 0), (-154 / 2, 0), 20)
    extrude(top_slots.sketch, amount=-40, mode=Mode.SUBTRACT)
    with BuildSketch(Plane.XY, mode=Mode.PRIVATE) as bottom_slots:
        SlotCenterPoint((206, 0), (206 / 2, 0), 20)
        SlotCenterPoint((-206, 0), (-206 / 2, 0), 20)
    extrude(bottom_slots.sketch, amount=40, mode=Mode.SUBTRACT)
    # tab holes: one prism cut along X through both tabs
    with BuildSketch(Plane.YZ.offset(-50), mode=Mode.PRIVATE) as tab_hole:
        with Locations((0, tab_hole_z)):
            Circle(5)
    extrude(tab_hole.sketch, amount=100, mode=Mode.SUBTRACT)

got_mass = sm_hanger.sheet.volume * 7800 * 1e-6
want_mass = 1028
print(f"Mass: {got_mass:0.1f} g")
assert abs(got_mass - want_mass) < 10, f"{got_mass=}, {want_mass=}"

show(sm_hanger)
```

The `foot_edges` line and both `fillet` selectors are deliberately calibration points — they cannot be finalized without running. Resolve them in Step 3.

- [ ] **Step 3: Calibrate against the probe** — run:

```bash
.venv/bin/python - <<'EOF'
from unittest.mock import MagicMock
import sys
sys.modules["ocp_vscode"] = MagicMock()  # headless: mock show()
import runpy
runpy.run_path("docs/assets/ttt/ttt-23-02-02-sm_hanger_buildsheet.py")
EOF
```
The mass print and assert run before `show`, so a failure is visible immediately. Iterate until the mass assert passes:

1. Verify `foot_edges` resolves to exactly 2 edges, each ~112.52 long, and the feet fold inward (compare the result's bounding box with the probe's: same overall footprint). If the fold goes outward, the selector picked the inner-surface junction — take `group_by(Axis.Z)[1]` or select from the wall's outer face instead.
2. Fix the fillet selectors by comparing with the probe's per-body bounding boxes; the drawing rounds: foot outer corners R7, wing tip corners R7, tab tip corners R5.
3. If the mass is off by more than ~10 g, diff against the probe: check (a) `wing_half_y` — if wrong, the original's `46.104` implies the wing flat ends at `40 + 1.526 * 4 = 46.104` before trim; (b) the tab strip: the original's tab line sits at `65 - thickness`, i.e. the tab flat may lie BELOW the plate (z 57..61) as an extra pad — if so, add the strip as a second `BuildSketch(Plane.XY.offset(top_z - 2 * thickness))` pad instead of carving it from the plate, and re-derive `tab_leg`.
4. Cross-check overlap: `(new_part & original_part).volume / original_part.volume` should be > 0.98 (build the original via `runpy` as in the probe). Report the final ratio in the task summary.

Escape hatch (only if some sub-shape cannot hit the tolerance with flanges): build that one sub-shape with `make_brake_formed` inside the same `BuildSheet` and note it in the file's docstring — do NOT silently widen the tolerance.

- [ ] **Step 4: Verify via the example test harness**

Run: `.venv/bin/python -m pytest tests/test_examples.py -k sm_hanger -v`
Expected: PASS for BOTH the original and the new file.

- [ ] **Step 5: Commit**

```bash
git add docs/assets/ttt/ttt-23-02-02-sm_hanger_buildsheet.py
git commit -m "Add BuildSheet version of TTT sm_hanger with CI-enforced mass check"
```

---

### Task 8: Docs — reliefs/miters/extends section

**Files:**
- Modify: `docs/build_sheet.rst` (insert a section between "Folding" and "Bend topology")
- Create: `docs/assets/sheet_metal_relief.png` (controller generates — see Step 3)

**Interfaces:**
- Consumes: final `flange` signature from Tasks 2–5; registrations from Task 6.

- [ ] **Step 1: Add the docs section** — insert into `docs/build_sheet.rst` after the "Folding" section:

```rst
*******************************
Reliefs, miters and extends
*******************************

Real flanges need corner treatment. ``flange`` provides three tools, all
off by default:

.. image:: assets/sheet_metal_relief.png
    :align: center

.. code-block:: python

    with BuildSheet(thickness=1, bend_radius=2) as bracket:
        with BuildSketch():
            Rectangle(60, 40)
        edge = bracket.faces().sort_by(Axis.Z)[0].edges().sort_by(Axis.X)[0]
        flange(
            edge,
            length=15,
            gap1=6,
            gap2=6,
            relief=ReliefType.ROUND,     # notch so the fold doesn't tear
            miter_angle2=45,             # angled end-cut on the free end
        )

* ``relief`` / ``relief_size`` cut a bend relief notch into the base sheet
  at each gapped end — ``ReliefType.RECTANGLE`` or ``ReliefType.ROUND``,
  sized ``(width, depth)`` and defaulting to ``0.7 x thickness``.
* ``miter_angle1`` / ``miter_angle2`` cut the wall's free end at an angle
  (positive trims inward, negative widens), for corners where two flanges
  meet.
* ``extend1`` / ``extend2`` widen the flat wall beyond the edge ends; the
  bend itself keeps the gapped width.

``extrude``, ``fillet``, ``chamfer``, ``add`` and ``mirror`` also work
inside ``BuildSheet`` — cuts that cross bends (slots through a folded
leg) are plain ``extrude(..., mode=Mode.SUBTRACT)`` calls. Note that
sketches exiting into ``BuildSheet`` are consumed as base-sheet regions,
so build the profile for an ``extrude`` with ``mode=Mode.PRIVATE`` and
pass it explicitly. See
``docs/assets/ttt/ttt-23-02-02-sm_hanger_buildsheet.py`` for a complete
part built this way next to its pre-BuildSheet equivalent.
```

- [ ] **Step 2: Check enum/docs wiring** — run `grep -rn "HemType" docs/*.rst`; wherever `HemType` is listed (e.g. an enums table), add `ReliefType` the same way. If nothing lists it, autodoc covers it — do nothing.

- [ ] **Step 3 (CONTROLLER, not subagent): generate the screenshot** — same flow as the POC box image:

```bash
cd /home/gabriel/Documentos/open_source/build123d-sheet-metal-poc
.venv/bin/python -m ocp_vscode --port 3940 &   # standalone viewer
# connect a browser to http://127.0.0.1:3940/viewer via playwright, then:
OCP_PORT=3940 .venv/bin/python - <<'EOF'
from build123d import *
from ocp_vscode import show, save_screenshot
with BuildSheet(thickness=1, bend_radius=2) as bracket:
    with BuildSketch():
        Rectangle(60, 40)
    edge = bracket.faces().sort_by(Axis.Z)[0].edges().sort_by(Axis.X)[0]
    flange(edge, length=15, gap1=6, gap2=6,
           relief=ReliefType.ROUND, miter_angle2=45)
show(bracket)
save_screenshot("docs/assets/sheet_metal_relief.png")
EOF
git add -f docs/assets/sheet_metal_relief.png   # *.png is gitignored; docs assets are force-added
```

- [ ] **Step 4: Build docs check (cheap)** — the docs example in the rst is illustrative (not extracted); just verify the rst renders: `.venv/bin/python -m pytest tests/test_docs_examples.py -v` still passes (it ignores `ttt/`).

- [ ] **Step 5: Commit**

```bash
git add docs/build_sheet.rst
git add -f docs/assets/sheet_metal_relief.png
git commit -m "Document flange reliefs, miters, extends and BuildSheet operations"
```

---

### Task 9: Full verification + draft PR (controller task — user gate before posting)

**Files:**
- Create (scratchpad): `pr2_body.md`

- [ ] **Step 1: Full test suite (controller runs, foreground, ~7 min)**

Run: `.venv/bin/python -m pytest tests/ -x -q`
Expected: everything passes (2063+ tests, 2 skipped as before, plus the new ones).

- [ ] **Step 2: Lint**

Run: `.venv/bin/python -m pylint src/build123d/operations_sheet.py src/build123d/build_sheet.py src/build123d/build_enums.py`
Expected: 10.00/10. Fix any findings, amend into the relevant commit or add a lint-cleanup commit.

- [ ] **Step 3: Draft the PR body** in the scratchpad (`pr2_body.md`): title `Sheet metal: bend reliefs, miters, extends + TTT comparison (stacked on #1381)`; body opens with "Contains #1381 — review commits after `cef2f8db`"; feature summary table; the two hanger scripts side by side (line counts, the vanished `1.526 * sheet_thickness`); the CI mass assertion as the equivalence proof; note auto-miter and hem reliefs as deliberate follow-ups. End with the Claude Code attribution line.

- [ ] **Step 4: STOP — present `pr2_body.md` to Gabriel for review.** Do not push or open the PR until approved.

- [ ] **Step 5 (after approval): push and open the draft PR**

```bash
git push -u origin sheet-metal-stack2
gh pr create --repo gumyr/build123d --base dev --head GabrielJMS:sheet-metal-stack2 \
  --draft --title "Sheet metal: bend reliefs, miters, extends + TTT comparison (stacked on #1381)" \
  --body-file <scratchpad>/pr2_body.md
```

- [ ] **Step 6: Update ledger and memory** — `.superpowers/sdd/progress.md` (on local `dev`) with the PR URL and per-task status; update `sheet-metal-poc-state.md` memory (roadmap items 1–3 shipped in PR #2, auto-miter now top of the remaining list).
