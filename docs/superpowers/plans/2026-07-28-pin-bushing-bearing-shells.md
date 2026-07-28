# Pin Bushing and Bearing Shells Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the pin bushing and the two big-end bearing shells to the piston group, and modify the connecting rod and rod cap to receive them.

**Architecture:** Two new standalone part scripts (`bushing.py`, `bearing_shell.py`) built in the *connecting rod's* coordinate frame, exactly like the existing `rod_cap.py`, so `assembly.py` places them with the `rod_loc` transform it already computes. `rod.py` opens its small end to make room for the bush (bore ø24 → ø28, boss ø34 → ø38); `rod.py` and `rod_cap.py` each gain a tang notch so the shells cannot rotate in the housing. The big-end bore, OD, lugs and every fillet set stay untouched — the shell wall is taken out of the crankpin (ø64 → ø60), not out of the rod. A new `check_bearings.py` carries the verification and grows one function per task.

**Tech Stack:** Python, build123d (OCC/OpenCascade kernel), run via `uv run python` from the repo root. Rendering via the build123d-designer skill's `render_model.py`. No pytest — this project verifies CAD by executing the part scripts and asserting on measured geometry, so `check_bearings.py` is the test suite and is run directly.

## Global Constraints

Copied verbatim from `docs/superpowers/specs/2026-07-28-pin-bushing-bearing-shells-design.md`:

- Pin is a hard **ø24** — it runs in the piston bosses too and must not change. `pin.py` is not modified.
- **`piston.py` is not modified.** The ø38 boss was measured clear of the piston (the R18 arch belongs to the U-towers, which only exist at |y| ≥ 20.4; the tunnel is open past ø44).
- Big end: **ø64 is the housing bore** = shell OD. Shells 2 mm wall, **ø60 ID**, so the crankpin becomes ø60. Rod and cap keep ø64 bore, ø72 OD, 36 wide, lug span 86, lugs 25 × 28 R4 at x = ±39.5.
- Small end: bore **ø28**, boss **ø38**, bush 2 mm wall (ø28 OD / ø24 ID).
- Both bearing elements are **36 wide** (= `rod.py` `end_w`), flush with the rod faces.
- Shell tang: 4 wide along Y, **centred axially**, 1.5 proud of the ø64 OD, 1.5 thick in Z against the parting plane. Axial centring is load-bearing for the design: it makes the shell one non-handed part, reused via `Rot(180, 0, 0)`.
- Notch is on the **+X side of both halves** (rod and cap), so a shell cannot be fitted backwards.
- The notch is cut **last, after every fillet call** — the project's established late-cut rule.
- Everything is modelled **nominal-on-nominal**. Press fits read as zero clearance, not interference. Interference assertions therefore use a 1.0 mm³ threshold, not exact zero.
- Kept simplifications: shells exactly 180° with no crush/nip relief; no shell oil hole.
- New part files follow `rod_cap.py` and do **not** import `ocp_vscode`.
- Every part script keeps its module docstring in the project's DESIGN BRIEF style (role in the system / datum / interfaces / feature-by-feature intent / modelling notes and deviations) — see `rod_cap.py` for the shape of it.

**Working directory for every command below:** `/home/gabriel/Documentos/open_source/build123d/piston`

**Renders** use:
```bash
uv run --with matplotlib python ~/.claude/skills/build123d-designer/scripts/render_model.py <file>.py --var part --out <file>_render.png
```

---

### Task 1: Pin bushing part

**Files:**
- Create: `piston/bushing.py`
- Create: `piston/check_bearings.py`

**Interfaces:**
- Consumes: nothing (reads `rod.py`'s `rod_len` value 160 as a literal constant with a `# = rod.py rod_len` comment, matching how `rod_cap.py` mirrors `crank_bore_d`).
- Produces:
  - `bushing.part` — the bush solid, in the rod frame, centred (0, 0, 160), axis along Y.
  - `bushing.pin_d = 24`, `bushing.bush_t = 2`, `bushing.bush_od = 28`, `bushing.bush_w = 36`, `bushing.oil_hole_d = 3.2` — module constants later tasks and `assembly.py` read.
  - `check_bearings.py` exposes `checks` (a list of zero-arg functions) and `main()`, plus the helpers `mock_ocp_vscode()` and `approx_zero(name, volume)`. Later tasks append functions to `checks`.

- [ ] **Step 1: Write the failing test**

Create `piston/check_bearings.py`:

```python
"""Verification for the piston group's bearing elements — pin bushing and
big-end shells. Run directly: `uv run python check_bearings.py`.

This project has no pytest suite for the CAD parts; correctness is checked
by building the solids and asserting on measured geometry (volume, solid
count, validity, boolean interference). Each check prints what it measured
so a failure says which number moved.
"""

import math
import pathlib
import sys
import types

TOL = 1.0  # mm3 — nominal-on-nominal fits touch exactly; see the spec's
           # "known modelling caveat". Anything above this is real overlap.


def mock_ocp_vscode():
    """piston.py / pin.py / rod.py import ocp_vscode for interactive use.
    Stub it so this script runs headless."""
    if "ocp_vscode" in sys.modules:
        return
    m = types.ModuleType("ocp_vscode")
    m.show = lambda *a, **k: None
    m.show_all = lambda *a, **k: None
    sys.modules["ocp_vscode"] = m


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
mock_ocp_vscode()

from build123d import *  # noqa: E402


def approx_zero(name, volume):
    """Assert two placed solids do not overlap."""
    print(f"  {name:34s} overlap {volume:9.3f} mm3")
    assert volume < TOL, f"{name}: {volume:.3f} mm3 of interference"


def solid_ok(name, part, expect_mass_g, density, band=0.05):
    """Assert one valid solid of about the expected mass."""
    mass = part.volume * density * 1e-3
    print(f"  {name:34s} {part.volume / 1000:7.3f} cm3, {mass:6.1f} g, "
          f"{len(part.solids())} solid(s)")
    assert part.is_valid, f"{name}: invalid solid"
    assert len(part.solids()) == 1, f"{name}: {len(part.solids())} solids"
    assert abs(mass - expect_mass_g) <= band * expect_mass_g, (
        f"{name}: mass {mass:.1f} g is not within "
        f"{band:.0%} of the expected {expect_mass_g:.1f} g")


def check_bushing():
    """Bush is one valid solid, ø28/ø24 x 36, with the oil hole through."""
    import bushing as B
    print("bushing:")
    solid_ok("bush solid", B.part, 51.5, 8.8e-3)

    bb = B.part.bounding_box()
    assert abs(bb.size.Y - B.bush_w) < 1e-6, f"width {bb.size.Y} != 36"
    assert abs(bb.size.X - B.bush_od) < 1e-6, f"OD {bb.size.X} != 28"
    assert abs(bb.center().Z - 160) < 1e-6, "bush is not at the small end"
    print(f"  {'bbox':34s} {bb.size}")

    # the oil hole must break through the wall, not stop in it: a probe
    # cylinder on the hole axis, inside the wall band, must find no metal
    probe = Cylinder(B.oil_hole_d / 2 - 0.1, B.bush_t + 0.4).moved(
        Pos(0, 0, 160 + (B.pin_d + B.bush_od) / 4))
    hole = (B.part & probe).volume
    print(f"  {'metal left in the oil hole':34s} {hole:9.3f} mm3")
    assert hole < TOL, "the oil hole does not go through the wall"


checks = [check_bushing]


def main():
    failed = []
    for fn in checks:
        try:
            fn()
        except AssertionError as exc:
            failed.append(f"{fn.__name__}: {exc}")
            print(f"  FAIL {exc}")
    print()
    if failed:
        print(f"FAILED {len(failed)}/{len(checks)}")
        for f in failed:
            print(f"  - {f}")
        sys.exit(1)
    print(f"OK {len(checks)}/{len(checks)} checks passed")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /home/gabriel/Documentos/open_source/build123d/piston && uv run python check_bearings.py`

Expected: `ModuleNotFoundError: No module named 'bushing'` (the import is inside `check_bushing`, so the traceback names the missing module).

- [ ] **Step 3: Write the minimal implementation**

Create `piston/bushing.py`:

```python
"""Pin bushing — ø28 / ø24 x 36 wide, phosphor bronze.
Part of an engine piston / con-rod / crank assembly.

Job: the sacrificial surface of the pin joint. The rod small end is steel
     and the pin is hardened steel; run together they gall, and any wear
     is wear in the rod, which is not replaceable. A bronze bush takes
     the wear instead, embeds the debris that would otherwise score the
     pin, and is pressed out and renewed at overhaul. It also gives the
     joint a dissimilar-metal pair, which is what lets the film survive
     the reversal at TDC where the sliding speed passes through zero.
Frame: the connecting rod's — big-end bore centre at the origin, bore
       along Y, small end at (0,0,160). Modelled in the rod's frame so
       the assembly places it with the rod's own transform.
Mates: ø28 OD = press fit in the rod small-end bore | ø24 ID = the piston
       pin, a running fit (the pin is full-floating, so it turns in HERE
       and in the piston bosses) | 36 wide = the rod's end_w, flush with
       both boss faces | ø3.2 oil hole at +Z, coaxial with the rod's
       small-end oil hole.

 1 ø28 / ø24 sleeve, 36 wide: 2 mm wall. Thick enough to be pressed
   without collapsing and to carry a bearing lining's worth of wear,
   thin enough that the steel boss behind it still does the hoop work.
 2 0.5 x 45° chamfer on the OD at both ends: press-fit lead-in, and it
   stops the leading edge shaving the bore on the way in.
 3 0.5 x 45° chamfer on the ID at both ends: assembly lead-in for the
   pin, and an oil lead-in — a sharp bore edge scrapes the film off
   instead of dragging it under.
 4 ø3.2 radial oil hole at +Z, on the rod's oil hole axis. Without it
   the rod's drilling from the top of the boss dead-ends on this bush's
   OD and the pin is never fed. At +Z because that is where the joint is
   unloaded on the firing stroke, so a film can form there.

Simplifications: no internal oil groove or spreader slot, no flange, and
the press fit is modelled nominal-on-nominal (ø28 in a ø28 bore), so the
interference that actually retains it is not represented.
"""

from build123d import *

pin_d = 24 * MM                      # = pin.py pin_d, the running bore
bush_t = 2 * MM                      # wall
bush_od = pin_d + 2 * bush_t         # 28, press fit in the rod
bush_w = 36 * MM                     # = rod.py end_w, flush with the boss
rod_len = 160 * MM                   # = rod.py rod_len (small-end centre)
oil_hole_d = 3.2 * MM                # = rod.py oil_hole_d
lead_chamfer = 0.5 * MM              # 0.5 x 45 deg, OD and ID both ends

# workplane at the small-end centre, normal along +Y -> cylinders grow
# along the pin axis, matching pin.py's in-place convention
bush_plane = Plane((0, 0, rod_len), x_dir=(1, 0, 0), z_dir=(0, 1, 0))

with BuildPart(bush_plane) as bushing:
    Cylinder(bush_od / 2, bush_w)
    # chamfer the OD rims before boring, so only the two outer circles
    # are in the selection (pin.py uses the same ordering trick)
    chamfer(bushing.edges().filter_by(GeomType.CIRCLE), lead_chamfer)
    Cylinder(pin_d / 2, bush_w + 2, mode=Mode.SUBTRACT)
    # now the only ø24 circles are the two fresh bore rims
    chamfer(
        bushing.edges()
        .filter_by(GeomType.CIRCLE)
        .filter_by(lambda e: abs(e.radius - pin_d / 2) < 0.01),
        lead_chamfer,
    )
    # oil hole, drilled radially in from the top on the rod's hole axis
    with BuildSketch(Plane.XY.offset(rod_len + bush_od / 2 + 2)):
        Circle(oil_hole_d / 2)
    extrude(amount=-(bush_t + 4), mode=Mode.SUBTRACT)

part = bushing.part
print(f"bushing: volume {part.volume / 1000:.2f} cm3, "
      f"mass (bronze 8.8) {part.volume * 8.8e-3:.0f} g")
assert len(part.solids()) == 1
```

- [ ] **Step 4: Run it to verify it passes**

Run: `cd /home/gabriel/Documentos/open_source/build123d/piston && uv run python check_bearings.py`

Expected: PASS, `OK 1/1 checks passed`, bush ≈ 5.85 cm³ / ≈ 51 g.

If the ID chamfer selection catches more than two edges, tighten the filter to also require `abs(abs(e.arc_center.Y) - bush_w / 2) < 0.01` (the pattern `rod.py` uses for its rim fillets).

- [ ] **Step 5: Render and eyeball it**

```bash
cd /home/gabriel/Documentos/open_source/build123d/piston
uv run --with matplotlib python ~/.claude/skills/build123d-designer/scripts/render_model.py bushing.py --var part --out bushing_render.png
```

Read the PNG. Expect a plain sleeve with chamfered ends and one small hole at the top. Per the project's known-issue note: OCC's hidden-line pass sometimes garbles a single view when tangent surfaces are edge-on — if one view looks wrong but `is_valid` and the volume are right, that is benign.

- [ ] **Step 6: Commit**

```bash
cd /home/gabriel/Documentos/open_source/build123d
git add piston/bushing.py piston/check_bearings.py piston/bushing_render.png
git commit -m "Add pin bushing part and bearing verification script"
```

---

### Task 2: Open the rod small end for the bush

**Files:**
- Modify: `piston/rod.py:55-56` (constants), `piston/rod.py:141-145` (bore sketch), `piston/rod.py:1-50` (docstring)
- Modify: `piston/check_bearings.py` (add `check_bush_in_rod`)

**Interfaces:**
- Consumes: `bushing.part`, `bushing.bush_od`, `bushing.pin_d` from Task 1.
- Produces: `rod.small_bore_d = 28`, `rod.pin_d = 24`, `rod.bush_t = 2`, `rod.small_od = 38`. **`rod.pin_bore_d` is deleted** — grep for it before finishing. `rod.rod_len`, `rod.end_w`, `rod.crank_bore_d`, `rod.lug_span`, `rod.lug_t`, `rod.cbore_depth` are unchanged and still read by `assembly.py`.

- [ ] **Step 1: Write the failing test**

Add to `piston/check_bearings.py`, above the `checks` list:

```python
def check_bush_in_rod():
    """The bush is a press fit in the rod small end: same bore diameter,
    same width, and no material overlap. Both are already in the rod's
    frame, so they are compared as modelled."""
    import bushing as B
    import rod as R
    print("bush in rod:")
    assert abs(R.small_bore_d - B.bush_od) < 1e-9, (
        f"rod small bore {R.small_bore_d} != bush OD {B.bush_od}")
    assert abs(R.end_w - B.bush_w) < 1e-9, (
        f"rod end width {R.end_w} != bush width {B.bush_w}")
    print(f"  {'rod small bore = bush OD':34s} ø{R.small_bore_d}")

    approx_zero("bush vs rod", (B.part & R.part).volume)

    # the wall over the bush must still be the 5 the brief calls for
    wall = (R.small_od - B.bush_od) / 2
    print(f"  {'boss wall over the bush':34s} {wall:9.3f} mm")
    assert abs(wall - 5.0) < 1e-9, f"boss wall is {wall}, not 5"

    # the rod's oil hole must reach the bush's, not stop in the boss:
    # probe the annulus band between bore and boss OD on the hole axis
    probe = Cylinder(R.oil_hole_d / 2 - 0.1, wall + 0.4).moved(
        Pos(0, 0, R.rod_len + (B.bush_od + R.small_od) / 4))
    blocked = (R.part & probe).volume
    print(f"  {'metal left in the rod oil hole':34s} {blocked:9.3f} mm3")
    assert blocked < TOL, "the rod's oil hole does not reach the bush"


checks = [check_bushing, check_bush_in_rod]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /home/gabriel/Documentos/open_source/build123d/piston && uv run python check_bearings.py`

Expected: FAIL on `check_bush_in_rod` with `AttributeError: module 'rod' has no attribute 'small_bore_d'`. (An `AttributeError` is not caught by `main()`'s `except AssertionError`, so it aborts the run — that is the red state. It becomes a clean pass/fail table once Step 3 lands.)

- [ ] **Step 3: Write the minimal implementation**

In `piston/rod.py`, replace lines 55-56:

```python
pin_bore_d = 24 * MM                 # = piston pin
small_od = 34 * MM                   # ref proportion (slimmer than before)
```

with:

```python
pin_d = 24 * MM                      # = piston pin, reached through the bush
bush_t = 2 * MM                      # = bushing.py bush_t
small_bore_d = pin_d + 2 * bush_t    # 28, press fit for the bush
small_od = 38 * MM                   # holds the 5 wall over the bush
```

Then in the bore sketch (line ~143), change `Circle(pin_bore_d / 2)` to `Circle(small_bore_d / 2)`.

Nothing else in the file needs touching, and that is worth verifying rather than assuming:
- the oil hole starts at `small_od / 2 + 1` and runs `small_od / 2 + 3`, so it follows the boss outward on its own;
- the rim fillet filter keys off `small_od / 2`, now 19;
- `pocket_z1 = 138` still clears the boss bottom, which moves 143 → 141;
- `shank_z1 = 150` is still buried 9 deep in the boss.

- [ ] **Step 4: Run it to verify it passes**

Run: `cd /home/gabriel/Documentos/open_source/build123d/piston && uv run python check_bearings.py`

Expected: PASS, `OK 2/2 checks passed`.

Then confirm no stale references and that the rod still builds alone:

```bash
cd /home/gabriel/Documentos/open_source/build123d/piston
grep -rn "pin_bore_d" . --include=*.py    # expect no output
uv run python -c "
import sys, types
m = types.ModuleType('ocp_vscode'); m.show = m.show_all = lambda *a, **k: None
sys.modules['ocp_vscode'] = m
import rod
print('one solid:', len(rod.part.solids()) == 1, '| valid:', rod.part.is_valid)
"
```

Expected: no grep output; `one solid: True | valid: True`; and the rod's own print line showing the new mass (expect a modest increase — the ø38 boss adds more than the ø28 bore removes).

**If a fillet call now fails** (the R1.5 shank blanket or the R2 rim set — the boss/shank blend geometry has moved): do not weaken the radius blindly. Bisect which selection broke by printing `len(...)` of each edge set before its `fillet(...)`, and prefer fixing the *filter* over the radius. This project's recorded lesson: `Shape.is_valid` is a **property**, so `part.is_valid()` raises `TypeError` and a bare `except` will misreport it as a fillet failure.

- [ ] **Step 5: Update the rod's design brief**

In `piston/rod.py`'s module docstring, make these edits — the brief is a maintained document here, not decoration:

- Title line: `ø24 small end` → `ø28 small end (ø24 bushed)`.
- `Mates:` — replace `ø24 small end = the piston pin (a production rod would be bushed)` with `ø28 small end = pressed bronze bush (bushing.py), ø24 bore in it = the piston pin`.
- `Mates:` — replace `ø64 big end = crankpin` with `ø64 big end = bearing shell housing bore (shells give ø60 on the crankpin)`.
- Feature 2: `Small-end boss ø34, 36 wide. Deliberately slim, 5 mm wall` → `Small-end boss ø38, 36 wide. Deliberately slim, 5 mm wall over the bush`.
- Feature 6: `Bores ø24 small end, ø64 big end` → `Bores ø28 small end (bush seat), ø64 big end (shell housing)`.
- Feature 8: add to the end of the sentence: `— it continues through the ø3.2 hole in the bush, which is what actually reaches the pin`.

- [ ] **Step 6: Commit**

```bash
cd /home/gabriel/Documentos/open_source/build123d
git add piston/rod.py piston/check_bearings.py
git commit -m "Open the rod small end to o28/o38 for the pin bush"
```

---

### Task 3: Bearing shell part

**Files:**
- Create: `piston/bearing_shell.py`
- Modify: `piston/check_bearings.py` (add `check_shell`)

**Interfaces:**
- Consumes: nothing (mirrors `rod.py`'s `crank_bore_d` and `end_w` as commented literals, the way `rod_cap.py` already does).
- Produces:
  - `bearing_shell.part` — the upper half-shell, above Z = 0, tang at +X.
  - `bearing_shell.shell_od = 64`, `shell_t = 2`, `shell_id = 60`, `shell_w = 36`, `tang_w = 4`, `tang_h = 1.5`, `tang_t = 1.5`, `tang_root = 0.5` — read by Task 4's notch and by `assembly.py`.

- [ ] **Step 1: Write the failing test**

Add to `piston/check_bearings.py`, above the `checks` list:

```python
def check_shell():
    """One valid half-shell, ø64/ø60 x 36, tang proud at +X and centred
    on the width so the same part serves as the lower shell."""
    import bearing_shell as S
    print("bearing shell:")
    solid_ok("shell solid", S.part, 56.2, 8.0e-3)

    bb = S.part.bounding_box()
    assert abs(bb.size.Y - S.shell_w) < 1e-6, f"width {bb.size.Y} != 36"
    assert bb.min.Z > -1e-6, "shell dips below the split plane"
    print(f"  {'bbox':34s} {bb.size}")

    # tang: proud of the OD on +X only, and centred on the width — both
    # are what let Rot(180,0,0) produce the lower shell from this part
    proud = bb.max.X - S.shell_od / 2
    print(f"  {'tang proud of the OD':34s} {proud:9.3f} mm")
    assert abs(proud - S.tang_h) < 1e-6, f"tang stands {proud}, not {S.tang_h}"
    assert abs(bb.min.X + S.shell_od / 2) < 1e-6, "something is proud at -X"

    flipped = S.part.moved(Rot(180, 0, 0))
    fb = flipped.bounding_box()
    assert abs(fb.max.X - bb.max.X) < 1e-6, "tang moved off +X when flipped"
    assert abs(fb.min.Y - bb.min.Y) < 1e-6, "tang is not centred on the width"
    approx_zero("upper shell vs lower shell", (S.part & flipped).volume)


checks = [check_bushing, check_bush_in_rod, check_shell]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /home/gabriel/Documentos/open_source/build123d/piston && uv run python check_bearings.py`

Expected: FAIL — `ModuleNotFoundError: No module named 'bearing_shell'`.

- [ ] **Step 3: Write the minimal implementation**

Create `piston/bearing_shell.py`:

```python
"""Big-end bearing shell — ø64 / ø60 x 36 wide, 180°, steel-backed
trimetal. Part of an engine piston / con-rod / crank assembly. Two of
these, identical, make the split plain bearing.

Job: the crankpin bearing surface, and the only part of the big end
     designed to be consumed. It is a plain hydrodynamic bearing: the
     pin never touches it in service, it rides on an oil film the pin's
     own rotation drags into the converging gap. The shell's real jobs
     are to give that film a soft, conformable, debris-embedding
     surface for the moments the film is not there (every start, and
     TDC on overrun where the load reverses), and to be the part that
     wears instead of the rod or the crank. Steel-backed because the
     lining alone cannot take the hoop load of the press fit.
Frame: the connecting rod's — big-end bore centre at the origin, bore
       along Y. This is the UPPER shell, above the split plane Z=0; the
       lower one is this same solid rotated 180° about X.
Mates: ø64 OD = the rod / cap housing bore, a crush fit | ø60 ID = the
       crankpin, running clearance | 36 wide = the rod's end_w, flush
       with both faces | tang at the +X parting end sits in the notch
       milled into the rod and cap split faces.

 1 180° half-annulus, ø64 / ø60, 36 wide: 2 mm wall. The pair closes on
   the two parting planes at Z=0, so the joint is a plain butt.
 2 Locating tang at the +X parting end: 4 wide, standing 1.5 proud of
   the OD, 1.5 thick against the parting plane, rooted 0.5 into the
   wall so it is a solid lug and not a tangent sliver. It carries no
   load — the crush fit is what stops the shell turning. The tang's job
   is assembly: it holds the shell put while the cap is offered up, and
   with the rod and cap notches BOTH on +X it makes fitting a shell
   backwards physically impossible.
 3 The tang is centred on the width, which is what makes this one
   non-handed part: rotating it 180° about X leaves the tang on +X and
   drops the body below the split plane, giving the lower shell. An
   off-centre tang would need a mirror-image second part.

Simplifications: exactly 180° with no crush height / nip, so the model
cannot show the hoop preload that actually retains it; no crush relief
(the slight wall thinning near the parting faces); no oil hole or
groove, because this rod has no big-end feed drilling to align one with;
lining and steel back are one solid of one averaged density, not two.
"""

from build123d import *

shell_od = 64 * MM                   # = rod.py crank_bore_d (housing bore)
shell_t = 2 * MM                     # wall
shell_id = shell_od - 2 * shell_t    # 60 = the crankpin
shell_w = 36 * MM                    # = rod.py end_w, flush with the faces
tang_w = 4 * MM                      # along Y, centred on the width
tang_h = 1.5 * MM                    # radial, proud of the OD
tang_t = 1.5 * MM                    # along Z, against the parting plane
tang_root = 0.5 * MM                 # buried into the wall: real overlap for
                                     # the union, never a tangent sliver

with BuildPart() as shell:
    # 180 deg half-annulus above the split plane, bore along Y
    with BuildSketch(Plane.XZ.offset(-shell_w / 2)):
        Circle(shell_od / 2)
        Circle(shell_id / 2, mode=Mode.SUBTRACT)
        Rectangle(shell_od + 2, shell_od + 2,
                  align=(Align.CENTER, Align.MIN), mode=Mode.INTERSECT)
    extrude(amount=shell_w)
    # tang at the +X parting end, centred on the width. Rooted tang_root
    # inside the OD: starting it exactly on ø64 would touch the barrel
    # along a single line at Z=0 and leave a 0.035 gap at Z=tang_t
    with BuildSketch(Plane.XZ.offset(-tang_w / 2)):
        with Locations((shell_od / 2 - tang_root, 0)):
            Rectangle(tang_root + tang_h, tang_t,
                      align=(Align.MIN, Align.MIN))
    extrude(amount=tang_w)

part = shell.part
print(f"bearing shell: volume {part.volume / 1000:.2f} cm3, "
      f"mass (trimetal 8.0) {part.volume * 8.0e-3:.0f} g")
assert len(part.solids()) == 1
```

- [ ] **Step 4: Run it to verify it passes**

Run: `cd /home/gabriel/Documentos/open_source/build123d/piston && uv run python check_bearings.py`

Expected: PASS, `OK 3/3 checks passed`, shell ≈ 7.02 cm³ / ≈ 56 g.

Note on the sketch planes: `Plane.XZ`'s normal is −Y, so `.offset(-w/2)` puts the plane at y = +w/2 and `extrude(amount=w)` sweeps it to y = −w/2 — the part comes out centred on Y either way. This is exactly the idiom `rod.py` uses for its big-end annulus, so follow it rather than "fixing" the signs.

- [ ] **Step 5: Render and eyeball it**

```bash
cd /home/gabriel/Documentos/open_source/build123d/piston
uv run --with matplotlib python ~/.claude/skills/build123d-designer/scripts/render_model.py bearing_shell.py --var part --out bearing_shell_render.png
```

Read the PNG. Expect a half sleeve sitting above the split plane with one small square lug at the right-hand parting end, mid-width.

- [ ] **Step 6: Commit**

```bash
cd /home/gabriel/Documentos/open_source/build123d
git add piston/bearing_shell.py piston/check_bearings.py piston/bearing_shell_render.png
git commit -m "Add big-end bearing shell part with locating tang"
```

---

### Task 4: Tang notches in the rod and the cap

**Files:**
- Modify: `piston/rod.py` (constants near line 82; new cut appended after the last `fillet` call; docstring)
- Modify: `piston/rod_cap.py` (constants near line 48; new cut after the `fillet` call; docstring)
- Modify: `piston/check_bearings.py` (add `check_shells_in_housing`)

**Interfaces:**
- Consumes: `bearing_shell.part`, and its `shell_od`, `tang_w`, `tang_h`, `tang_t` from Task 3.
- Produces: `rod.notch_w = 4.4`, `rod.notch_h = 1.9`, `rod.notch_d = 1.6` and the same three names in `rod_cap`. No existing name changes.

- [ ] **Step 1: Write the failing test**

Add to `piston/check_bearings.py`, above the `checks` list:

```python
def check_shells_in_housing():
    """Both shells sit in the ø64 housing with their tangs in the
    notches, and neither fouls the rod or the cap. The lower shell is
    the upper one rotated 180° about X."""
    import bearing_shell as S
    import rod as R
    import rod_cap as C
    print("shells in the housing:")
    assert abs(R.crank_bore_d - S.shell_od) < 1e-9, (
        f"rod housing bore {R.crank_bore_d} != shell OD {S.shell_od}")
    assert abs(C.crank_bore_d - S.shell_od) < 1e-9, (
        f"cap housing bore {C.crank_bore_d} != shell OD {S.shell_od}")

    upper = S.part
    lower = S.part.moved(Rot(180, 0, 0))
    approx_zero("upper shell vs rod", (upper & R.part).volume)
    approx_zero("upper shell vs cap", (upper & C.part).volume)
    approx_zero("lower shell vs cap", (lower & C.part).volume)
    approx_zero("lower shell vs rod", (lower & R.part).volume)

    # positive check: the notch is really there and really engages. The
    # tang's proud volume must be swallowed by the notch, so a probe box
    # over the tang's radial reach finds no housing metal.
    for name, half, sign in (("rod", R.part, 1), ("cap", C.part, -1)):
        box = Box(S.tang_h, S.tang_w, S.tang_t).moved(
            Pos(S.shell_od / 2 + S.tang_h / 2, 0,
                sign * S.tang_t / 2))
        left = (half & box).volume
        print(f"  {name + ' metal in the tang pocket':34s} "
              f"{left:9.3f} mm3")
        assert left < TOL, f"the {name} notch does not clear the tang"

    # the crankpin the shells now define
    print(f"  {'crankpin implied by the shells':34s} ø{S.shell_id}")
    assert abs(S.shell_id - 60) < 1e-9


checks = [check_bushing, check_bush_in_rod, check_shell,
          check_shells_in_housing]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /home/gabriel/Documentos/open_source/build123d/piston && uv run python check_bearings.py`

Expected: FAIL on `check_shells_in_housing`. The tang collides with the un-notched housing, so `upper shell vs rod` reports roughly 9 mm³ (the tang's proud 1.5 × 1.5 × 4) and the assertion trips. This is the whole point of the task — record the number you see.

- [ ] **Step 3: Write the minimal implementation — the rod**

In `piston/rod.py`, add near the other constants (after `rim_fillet`):

```python
notch_clear = 0.2 * MM               # per side, tang to notch
notch_w = 4 * MM + 2 * notch_clear   # = bearing_shell.py tang_w + clearance
notch_h = 1.9 * MM                   # radial, ø64 out to ø67.8
notch_d = 1.6 * MM                   # into the split face (tang_t + 0.1)
```

Then append this as the **last** operation inside the `with BuildPart() as rod:` block, after both `fillet(...)` calls:

```python
    # bearing shell tang notch, +X side of the split face. Cut LAST: this
    # project's rule is that late cuts must not precede filleting (the
    # oil groove and lube bores in piston.py broke fillets when they did).
    # It starts 1 inside the bore so no thin lip is left at the rim, and
    # it lands under the lug block, clear of the ø8 bolt hole at x=39..47.
    with BuildSketch(Plane.XY):
        with Locations((crank_bore_d / 2 - 1, 0)):
            Rectangle(1 + notch_h, notch_w, align=(Align.MIN, Align.CENTER))
    extrude(amount=notch_d, mode=Mode.SUBTRACT)
```

- [ ] **Step 4: Write the minimal implementation — the cap**

In `piston/rod_cap.py`, add after `rim_fillet`:

```python
notch_clear = 0.2 * MM               # = rod.py notch_clear
notch_w = 4 * MM + 2 * notch_clear   # = rod.py notch_w
notch_h = 1.9 * MM                   # = rod.py notch_h
notch_d = 1.6 * MM                   # = rod.py notch_d
```

Then append this as the last operation inside `with BuildPart() as rod_cap:`, after the `fillet(...)` call:

```python
    # bearing shell tang notch: the rod's, mirrored below the split
    # plane. SAME +X side as the rod's on purpose — with both notches
    # together a shell cannot be fitted backwards. Cut last, after the
    # fillet, matching rod.py.
    with BuildSketch(Plane.XY):
        with Locations((crank_bore_d / 2 - 1, 0)):
            Rectangle(1 + notch_h, notch_w, align=(Align.MIN, Align.CENTER))
    extrude(amount=-notch_d, mode=Mode.SUBTRACT)
```

- [ ] **Step 5: Run it to verify it passes**

Run: `cd /home/gabriel/Documentos/open_source/build123d/piston && uv run python check_bearings.py`

Expected: PASS, `OK 4/4 checks passed`, with every overlap line reading 0.000 mm³.

Then confirm both halves are still sound and still close on each other:

```bash
cd /home/gabriel/Documentos/open_source/build123d/piston
uv run python -c "
import sys, types
m = types.ModuleType('ocp_vscode'); m.show = m.show_all = lambda *a, **k: None
sys.modules['ocp_vscode'] = m
import rod, rod_cap
for n, p in (('rod', rod.part), ('cap', rod_cap.part)):
    print(n, 'solids', len(p.solids()), 'valid', p.is_valid)
print('rod & cap overlap', (rod.part & rod_cap.part).volume, 'mm3')
"
```

Expected: one valid solid each, and `rod & cap overlap 0.0 mm3` — they butt on Z=0 and the two notches are on opposite sides of it.

- [ ] **Step 6: Update both design briefs**

In `piston/rod.py`'s docstring, add to feature 9's "Deliberately NOT filleted" list, and add a new feature line before it:

```
 9 Shell tang notch: 4.4 x 1.9 x 1.6 deep in the split face at the +X
   bore edge, on the SAME side as the cap's. It takes 1.9 of the 4 wall
   over the shell, leaving 2.1. Cut after all filleting — a late cut
   here, like the oil groove in the piston, destroys blends it crosses
   if it goes first.
```
(renumber the existing fillet item to 10).

In `piston/rod_cap.py`'s docstring:
- `Mates:` — append `| shell tang notch 4.4 x 1.9 x 1.6 at the +X bore edge, same side as the rod's`.
- Add a feature 5: `Shell tang notch, the rod's mirrored below the split plane. Same +X side on both halves, so the two tangs sit together and a shell cannot go in backwards. Cut after the rim fillet.`
- **Simplifications paragraph:** delete `no bearing shell, no locating tang or dowels,` — that gap is now closed. Keep the rest, and add: `The shells are exactly 180° with no crush height, so the hoop preload that actually retains them is not modelled.`

- [ ] **Step 7: Commit**

```bash
cd /home/gabriel/Documentos/open_source/build123d
git add piston/rod.py piston/rod_cap.py piston/check_bearings.py
git commit -m "Cut shell tang notches in the rod and cap split faces"
```

---

### Task 5: Place the bearings in the assembly

**Files:**
- Modify: `piston/assembly.py:55-62` (imports), `piston/assembly.py:85-87` (placement), `piston/assembly.py:1-42` (system brief)
- Modify: `piston/check_bearings.py` (add `check_assembly`)

**Interfaces:**
- Consumes: `bushing.part`, `bearing_shell.part`, and the unchanged `rod.rod_len`, `piston.pin_z`.
- Produces: `assembly.assembly` — a `Compound` with **15** children, adding the labels `pin_bushing`, `shell_upper`, `shell_lower`.

- [ ] **Step 1: Write the failing test**

Add to `piston/check_bearings.py`, above the `checks` list:

```python
def check_assembly():
    """The bearings are in the assembly, in the right places, and the
    whole group is still interference-free where it matters."""
    import assembly as A
    import pin as PIN
    print("assembly:")
    by_label = {c.label: c for c in A.assembly.children}
    print(f"  {'children':34s} {len(by_label)}")
    for want in ("pin_bushing", "shell_upper", "shell_lower"):
        assert want in by_label, f"{want} is not in the assembly"
    assert len(by_label) == 15, f"{len(by_label)} children, expected 15"

    bush = by_label["pin_bushing"]
    upper = by_label["shell_upper"]
    lower = by_label["shell_lower"]

    # the bush must land on the pin axis, at pin height, in the piston frame
    bc = bush.bounding_box().center()
    print(f"  {'bush centre':34s} {bc}")
    assert abs(bc.X) < 1e-6 and abs(bc.Y) < 1e-6, "bush is off the pin axis"
    assert abs(bc.Z - PIN.pin_z) < 1e-6, (
        f"bush at Z={bc.Z}, pin axis is at {PIN.pin_z}")

    # the pin runs in the bush, not in the rod
    approx_zero("pin vs bush", (by_label["pin"] & bush).volume)
    approx_zero("pin vs rod", (by_label["pin"] & by_label["rod"]).volume)
    approx_zero("bush vs piston", (bush & by_label["piston"]).volume)

    # the shells, as placed
    approx_zero("shell_upper vs rod", (upper & by_label["rod"]).volume)
    approx_zero("shell_lower vs rod_cap",
                (lower & by_label["rod_cap"]).volume)
    approx_zero("shell_upper vs shell_lower", (upper & lower).volume)

    # tangs together on the same side, as designed
    ux = upper.bounding_box().max.X
    lx = lower.bounding_box().max.X
    print(f"  {'tang reach, upper / lower':34s} {ux:.3f} / {lx:.3f}")
    assert abs(ux - lx) < 1e-6, "the two tangs are not on the same side"
    assert ux > 32.0, "no tang is proud of the housing bore"


checks = [check_bushing, check_bush_in_rod, check_shell,
          check_shells_in_housing, check_assembly]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /home/gabriel/Documentos/open_source/build123d/piston && uv run python check_bearings.py`

Expected: FAIL on `check_assembly` — `AssertionError: pin_bushing is not in the assembly` (the assembly still has 12 children).

- [ ] **Step 3: Write the minimal implementation**

In `piston/assembly.py`, add to the imports after `import rod_cap as cap_m`:

```python
import bushing as bush_m
import bearing_shell as shell_m
```

Then replace the two rod placement lines:

```python
rod_loc = Pos(0, 0, piston_m.pin_z - rod_m.rod_len)
place(rod_m.part, "rod", "darkgoldenrod", rod_loc)
place(cap_m.part, "rod_cap", "goldenrod", rod_loc)
```

with:

```python
rod_loc = Pos(0, 0, piston_m.pin_z - rod_m.rod_len)
place(rod_m.part, "rod", "darkgoldenrod", rod_loc)
place(cap_m.part, "rod_cap", "goldenrod", rod_loc)
# bearing elements: both are modelled in the rod's frame, so they ride
# the rod's own transform. The two shells are ONE part — Rot(180,0,0)
# maps (x,y,z) -> (x,-y,-z), which drops the body below the split plane
# and, because the tang is centred on the width, leaves it on +X in the
# cap's notch.
place(bush_m.part, "pin_bushing", "peru", rod_loc)
place(shell_m.part, "shell_upper", "rosybrown", rod_loc)
place(shell_m.part, "shell_lower", "rosybrown", rod_loc * Rot(180, 0, 0))
```

- [ ] **Step 4: Run it to verify it passes**

Run: `cd /home/gabriel/Documentos/open_source/build123d/piston && uv run python check_bearings.py`

Expected: PASS, `OK 5/5 checks passed`, and `assembly: 15 parts, ...` in the assembly's own print line.

If `pin vs bush` or `bush vs piston` reports a non-zero overlap, do not raise `TOL` — that is a real placement bug. Check that `bushing.py`'s `rod_len` still matches `rod.rod_len` (160): the bush is positioned by that literal, so the two must agree.

- [ ] **Step 5: Update the system brief**

In `piston/assembly.py`'s module docstring:

- Opening paragraph: after `full-floating hollow steel pin located by two snap rings`, insert `turning in a pressed bronze small-end bush`; and change `a split steel connecting rod on a ø64 crankpin` to `a split steel connecting rod on a ø60 crankpin through a pair of ø64 shells`.
- Interface 2: change `the ø24 pin turns freely in BOTH the piston bosses and the rod small end (full-floating)` to `the ø24 pin turns freely in BOTH the piston bosses and the small-end BUSH (full-floating) — the bush, not the rod, is the wearing surface`.
- Interface 3: change `split plain bearing on the ø64 crankpin` to `split plain bearing: two ø64/ø60 shells in the rod and cap housings, running on a ø60 crankpin, each located by a tang in a notch on the +X side of the split face`.
- Shared dimensions list — replace the `ø24` and `ø64` entries with:

```
  ø24     pin OD = piston bore = small-end BUSH ID
  ø28     rod small-end bore = bush OD (press fit); ø38 boss over it
  ø64     big-end HOUSING bore = shell OD, rod and cap alike; ø72 OD,
          36 wide, both
  ø60     shell ID = crankpin
```

- Placement paragraph: after `the rod dropped until its small end lands on the pin axis`, add `the bush and both shells riding the rod's own transform (the shells are one part, the lower one rotated 180° about X)`.

- [ ] **Step 6: Render the assembly and compare against the reference sketch**

```bash
cd /home/gabriel/Documentos/open_source/build123d/piston
uv run --with matplotlib python ~/.claude/skills/build123d-designer/scripts/render_model.py assembly.py --var part --out assembly_render.png
```

Read the PNG and check three things against the exploded reference (`~/Imagens/Capturas de tela/Captura de tela de 2026-07-28 13-43-43.png`): the bush is visible in the small end around the pin, the two shells line the big-end bore, and the big end still closes on the cap with the bolt heads flush.

- [ ] **Step 7: Commit**

```bash
cd /home/gabriel/Documentos/open_source/build123d
git add piston/assembly.py piston/check_bearings.py piston/assembly_render.png
git commit -m "Place the pin bush and both bearing shells in the assembly"
```

---

### Task 6: Reconcile the spec with what was measured

**Files:**
- Modify: `docs/superpowers/specs/2026-07-28-pin-bushing-bearing-shells-design.md`

**Interfaces:**
- Consumes: the measured masses and volumes printed by `check_bearings.py` in Tasks 1-5.
- Produces: nothing consumed by code.

The spec carries three hand-estimated figures. Two are verified by Task 1 and Task 3's assertions; the third — "rod gains ~18 g net" — was a rough estimate that ignored the shank overlap into the new boss annulus and the R2 rim fillets, so the real figure will differ.

- [ ] **Step 1: Collect the measured numbers**

```bash
SCRATCH=/tmp/claude-1000/-home-gabriel-Documentos-open-source-build123d/a7ffcd5e-5b55-4fe8-a63f-ecf9838e3816/scratchpad
cd /home/gabriel/Documentos/open_source/build123d/piston
uv run python check_bearings.py | tee $SCRATCH/bearing_checks.txt
# the pre-change rod, for the mass delta. Find the commit before the
# small-end change rather than trusting a HEAD~N guess:
git -C .. log --oneline -- piston/rod.py | head -5
git -C .. show <commit-before-the-small-end-change>:piston/rod.py > $SCRATCH/rod_before.py
```

Then get the before/after rod volume — run the pre-change rod from the scratchpad copy and the current one:

```bash
cd /home/gabriel/Documentos/open_source/build123d/piston
uv run python -c "
import sys, types, pathlib
m = types.ModuleType('ocp_vscode'); m.show = m.show_all = lambda *a, **k: None
sys.modules['ocp_vscode'] = m
sys.path.insert(0, '/tmp/claude-1000/-home-gabriel-Documentos-open-source-build123d/a7ffcd5e-5b55-4fe8-a63f-ecf9838e3816/scratchpad')
import rod_before
print('BEFORE', rod_before.part.volume)
"
uv run python -c "
import sys, types
m = types.ModuleType('ocp_vscode'); m.show = m.show_all = lambda *a, **k: None
sys.modules['ocp_vscode'] = m
import rod
print('AFTER', rod.part.volume)
"
```

If `HEAD~4` is not the pre-change rod (commit count differs because a step was split), find it with `git log --oneline -- piston/rod.py` and use the commit before "Open the rod small end".

- [ ] **Step 2: Update the spec's Verification section**

Replace the estimated figures in the spec with the measured ones:
- In "New parts", confirm or correct the `~52 g` bush and `~56 g each` shell figures.
- In "Verification" item 2, replace `rod gains ~18 g net — the ø38 boss adds more than the ø28 bore removes` with the measured before → after masses and the actual delta.
- Add a line to "Verification" recording that all checks are automated in `piston/check_bearings.py` and how to run it.

- [ ] **Step 3: Commit**

```bash
cd /home/gabriel/Documentos/open_source/build123d
git add docs/superpowers/specs/2026-07-28-pin-bushing-bearing-shells-design.md
git commit -m "Spec: replace estimated masses with measured figures"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| Decision 1 — ø64 housing, shells ø64/ø60, crankpin ø60 | 3 (shell), 4 (asserted in `check_shells_in_housing`) |
| Decision 2 — bore ø28, boss ø38 | 2 |
| Decision 3 — tang and notch | 3 (tang), 4 (notches) |
| Clearance check, `piston.py` unchanged | Already done pre-spec; re-asserted by `bush vs piston` in Task 5 |
| `bushing.py` — dims, chamfers, oil hole, bronze mass | 1 |
| `bearing_shell.py` — dims, tang, axial centring, non-handed | 3 |
| `rod.py` — constants, bore, boss, notch, docstring | 2, 4 |
| `rod_cap.py` — notch, docstring, simplifications edit | 4 |
| `assembly.py` — 15 children, colours, shared dims, brief | 5 |
| Verification 1 — per-part valid/one-solid/mass | 1, 3 (`solid_ok`) |
| Verification 2 — rod and cap still one solid, fillets pass, mass | 2 step 4, 4 step 5, 6 |
| Verification 3 — 15 children, four-view render | 5 |
| Verification 4 — interference sweep | 4, 5 |
| Known caveat — nominal fits, `TOL` not exact zero | 1 (`TOL` constant and its comment) |

No gaps. The one item deliberately deferred is the "record the caveat" line, which lives in the spec already rather than in code.

**Placeholder scan:** no TBD/TODO, no "add error handling", no "similar to Task N" — the notch code block is written out in full in both Task 4 steps because the rod's and the cap's differ only in the sign of the extrude, and a reader may hit either step first.

**Type consistency:** `small_bore_d` / `pin_d` / `bush_t` / `small_od` (Task 2) match their uses in `check_bush_in_rod`. `shell_od` / `shell_id` / `shell_w` / `tang_w` / `tang_h` / `tang_t` / `tang_root` (Task 3) match Task 4's notch constants and both check functions. `notch_w` / `notch_h` / `notch_d` / `notch_clear` are defined identically in `rod.py` and `rod_cap.py`. Labels `pin_bushing` / `shell_upper` / `shell_lower` are used consistently in Task 5's implementation and its check. `approx_zero` / `solid_ok` / `TOL` / `checks` / `main` are defined in Task 1 and used unchanged thereafter. `rod.pin_bore_d` is deleted in Task 2 and grepped for in the same step.
