# Sheet Metal Stacked PR #2 — Reliefs, Miters, Extends + TTT Hanger Comparison

**Date:** 2026-07-22
**Status:** Approved by Gabriel (user), section by section
**Builds on:** PR #1381 (sheet metal POC, branch `sheet-metal-poc`)
**References:**
- POC design: `2026-07-21-sheet-metal-poc-design.md`
- FreeCAD reference: `/home/gabriel/Documentos/open_source/FreeCAD_SheetMetal/SheetMetalCmd.py`
  (`smBend` line 1241, `smMakeReliefFace` line 447, `smMakeFace` line 555, `smMiter` line 957)
- Original TTT example: `docs/assets/ttt/ttt-23-02-02-sm_hanger.py` (gumyr, 2023)

## Goal

Maintainers are not yet convinced of the POC's value. This PR demonstrates it two ways:

1. **Three flange features** real parts need: bend reliefs, manual miter angles,
   extends — plus registering the standard operations `extrude`, `fillet`, `chamfer`,
   `add`, `mirror` for `BuildSheet`.
2. **A before/after showcase**: Too Tall Toby's sm_hanger rebuilt with the new API,
   next to gumyr's own 2023 `make_brake_formed` version, with a CI-enforced mass
   assertion proving equivalence. Headline: the manual bend-allowance bookkeeping
   (`1.526 * sheet_thickness`, `PolarLine(..., 20.371288916)`) disappears.

**Scope decisions** (per user):
- Hanger-driven set: reliefs + manual miters + extends + registrations + hanger rewrite.
- **Auto-miter is deferred** to a later PR (~250 lines of neighbour detection in
  FreeCAD; deserves its own review). Manual `miter_angle1/2` only.
- `hem()` does **not** gain the new parameters in this PR.
- Relief API is **explicit opt-in** (approach A), not FreeCAD's auto-on behaviour.
- The rewrite is a **new file alongside** the original, not a replacement.
- Not in scope: Fold/BendLine, corner reliefs, `SheetMetal_Extend` (the standalone
  FreeCAD command that extrudes a sheet face; note it calls `removeSplitter()` —
  if ever ported it must go through BuildSheet's no-clean path instead).

## User-facing API

New keyword-only parameters on `flange()`, all defaulting to "off"; behaviour with
defaults is bit-identical to PR #1381:

```python
flange(edges, length, angle=90, radius=None,
       gap1=0, gap2=0,
       extend1=0, extend2=0,              # NEW: widen the wall beyond the edge ends
       miter_angle1=0, miter_angle2=0,    # NEW: angled end-cuts, degrees
       relief=None, relief_size=None,     # NEW: ReliefType | None, (width, depth)
       bend_position=BendPosition.MATERIAL_OUTSIDE,
       clean=False, mode=Mode.ADD, thickness=None)
```

New enum in `build_enums.py`: `ReliefType` with members `RECTANGLE`, `ROUND`.

All three features work in algebra mode (explicit `thickness`, `topo_parent`
discovery), same as the existing parameters. None of them touch the bend sector, so
fan-face preservation holds by construction.

### Extends (`extend1`, `extend2` ≥ 0)

The negative-gap counterpart of `gap1`/`gap2` (FreeCAD `smBend` args of the same
name, SheetMetalCmd.py:969). The flat wall rectangle is built on the edge extended
by `extend_i` beyond the gap-trimmed ends — internally the wall sees
`gap_i − extend_i`, which may go negative. FreeCAD parity: **only the flat leg
widens; the bend sector keeps the gapped width** (SheetMetalCmd.py:1583 revolves
with `gap1, gap2` only). A wide leg can therefore overhang the bend's sides —
documented caveat.

### Miter angles (`miter_angle1`, `miter_angle2`, degrees, |angle| < 90)

The wall becomes a trapezoid (FreeCAD `smMakeFace` angles, SheetMetalCmd.py:555):
the bend-side edge keeps its width; each far corner is shifted along the edge
direction by `length · tan(angle_i)`. Positive cuts inward, **negative widens
outward** (FreeCAD's auto-miter emits negative angles itself; the hanger's tapered
slope uses two negative angles). `ValueError` if the two cuts consume the entire
far edge — FreeCAD silently collapses to a triangle; erroring is clearer for a
hand-set parameter. Miters cut only the wall, never the bend sector.

### Reliefs (`relief=ReliefType.…`, `relief_size=(width, depth)`)

Explicit opt-in. `relief_size` defaults to `(0.7·t, 0.7·t)` (FreeCAD's
`ReliefFactor` rule with its 0.7 default). At each edge end where `gap_i > 0`, a
notch is cut **into the base sheet, through its thickness**, spanning
`[gap_i − width, gap_i]` along the edge — flush against the wall's side
(FreeCAD placement, SheetMetalCmd.py:1421-1445), so the wall tears free of the
parent sheet cleanly.

- `RECTANGLE`: width × depth rectangular notch.
- `ROUND`: straight sides + semicircular end cap of radius `width/2`, degenerating
  to a single arc when `depth ≤ width/2` (FreeCAD `smMakeReliefFace`,
  SheetMetalCmd.py:447-478).
- Inside `BendPosition` variants add a second rectangular notch of depth = the slab
  offset, mirroring FreeCAD (SheetMetalCmd.py:1428-1431).

Relief solids are cut from the sheet **before** fusing sector + wall, all under
`SkipClean`. Deliberately dropped FreeCAD knobs: `UseReliefFactor` (folded into the
default), `minReliefGap` threshold (explicit opt-in replaces it).

### Operation registrations

`build_common.py` `operations_apply_to`: add `"BuildSheet"` to `"extrude"`,
`"fillet"`, `"chamfer"`, `"add"`, `"mirror"`. Safe and cheap because every one of
them funnels through `BuildSheet._add_to_context`, which forces `clean=False` —
the no-clean guarantee is inherited, not re-implemented.

## Geometry engine changes (`operations_sheet.py`)

`_make_bend` grows the three features:

1. Validation (see Error handling) happens in `flange` before any geometry.
2. Relief notch faces are built on the (untrimmed) bend edge at the gap boundaries,
   extruded through thickness opposite the wall direction, and cut from the sheet.
3. The wall rectangle becomes a trapezoid builder taking
   `(gap1 − extend1, gap2 − extend2, miter_angle1, miter_angle2)` — near edge at the
   (possibly widened) trimmed edge, far corners shifted by `length · tan(angle)`.
4. The bend sector revolve is **unchanged** (gapped width, no miter, no extend).
5. All booleans remain under `SkipClean` with `clean=False`.

## Error handling

All `ValueError`, raised in `flange` validation:

- `extend1 < 0` or `extend2 < 0`
- `|miter_angle1| ≥ 90` or `|miter_angle2| ≥ 90`
- miter cuts consume the entire far edge
  (`(tan α₁ + tan α₂) · length ≥ wall width`)
- `relief` set while both gaps are 0 (nothing to relieve)
- `relief_size` non-positive, or `width > gap_i` at a relieved end
- existing validations (gaps ≥ 0, length > 0, radius ≥ 0, angle range) unchanged

## The hanger rewrite (`docs/assets/ttt/ttt-23-02-02-sm_hanger_buildsheet.py`)

New file alongside the original; both stay runnable. Decomposition:

| Original (2023) | New API |
|---|---|
| Side profile: `BuildLine` polyline + `fillet(7)` + `make_brake_formed(station_widths=[40,40,40,112.52/2,…])` | Base sketch = top plate at z = 65; `flange` the X-end edges down 60° (tapered slope: negative `miter_angle1/2` widening 80 → 112.52, `extend1/2` as needed), then `flange` the slope's free edge a further 30° to vertical (flange-on-flange chaining) |
| Wing: separate `BuildPart`, `1.526 × t` compensation, `PolarLine(…, 20.371288916)` | `flange` on the plate's Y-end edges at 75°, `gap1/gap2` for the 110-wide partial span — no compensation constants |
| Tab: third body, algebra mode, positioned to overlap the plate | Base sketch keeps an 8-wide strip crossing the 30×30 cutout; one `flange(angle=90)` on the strip's end edge; fillet the tip; subtract-sketch the Ø5 hole on the tab face (auto-padded through thickness) |
| Assembly: `add` × 3 + three `mirror`s fusing overlapping bodies | Build the half/quarter, `mirror` inside `BuildSheet`; walls grow from the sheet, nothing overlaps by construction |
| Slots: `extrude(mode=Mode.SUBTRACT)` | Identical calls, now legal inside `BuildSheet` |
| `print` mass; assert commented out | Same structure + **active** `assert abs(mass − 1028) < 10` |

`tests/test_examples.py` auto-discovers every `docs/assets/ttt/*.py`, so the mass
assertion is CI-enforced equivalence, not a claim. Exact dimensions (slope length,
flange lengths, miter angles) are derived from the original's geometry during
implementation; the mass assertion is the arbiter. Follow ttt-dir conventions:
screenshot PNG(s) (force-added; global `*.png` gitignore) and a docs TTT-page entry
if the other examples have one (verify during implementation).

**Escape hatch (flagged in the PR, not hidden):** if some detail cannot be
reproduced within ±10 g by flanges alone, keep `make_brake_formed` for that one
sub-shape inside the same `BuildSheet` — mixing is a feature, and the comparison
remains honest.

## Testing (`tests/test_build_sheet.py`, exact-volume style)

- **Reliefs**: rectangle notch removes exactly `width × depth × thickness`;
  round variant's exact area (rect + semicircle cap; shallow single-arc
  degenerate); notch only at gapped ends; default size `0.7·t`; fan faces still
  outnumber a `clean()`ed copy; all error paths.
- **Miters**: trapezoid wall volume exact
  (`t · length · (W − (tan α₁ + tan α₂) · length / 2)`); negative angles widen;
  degenerate-tip error.
- **Extends**: wall volume reflects widened width while bend-sector volume is
  unchanged (proves the sector keeps gapped width); negative extend error.
- **Registrations**: `extrude(mode=Mode.SUBTRACT)`, `fillet`, `chamfer`, `add`,
  `mirror` each work inside `BuildSheet` and preserve fan-face count.
- **Algebra mode**: new params without a context.
- **Hanger**: covered by `test_examples.py` auto-discovery; no separate unit test.

## Docs

New "Reliefs, miters and extends" subsection in `docs/build_sheet.rst` with one
rendered screenshot (flange with a round relief and a mitered end; same
`save_screenshot` standalone-viewer flow as the box image; `git add -f` the PNG).
Tutorial page untouched.

## Deliverables & PR mechanics

1. Branch `sheet-metal-stack2` off `sheet-metal-poc` in the existing worktree
   (`/home/gabriel/Documentos/open_source/build123d-sheet-metal-poc`); full test
   suite + pylint green.
2. **Draft PR to `gumyr/build123d:dev`** titled as stacked on #1381; body opens
   with "contains #1381 — review commits after `cef2f8db`", shows the two hanger
   scripts side by side with line counts, and calls out the vanished
   `1.526 * sheet_thickness`. Body reviewed by Gabriel before posting; any comment
   linking it from #1381 likewise.
3. Progress ledger (`.superpowers/sdd/progress.md`) and memory updated.
4. This spec stays on local `dev`, never on the PR branch.
