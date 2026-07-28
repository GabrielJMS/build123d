# Pin bushing and big-end bearing shells

Date: 2026-07-28
Scope: `piston/` — add the two bearing elements the assembly is missing, and
modify the connecting rod and rod cap to receive them.

## Problem

The piston group models the pin joint and the big end as metal-on-metal: the
rod's ø24 small-end bore runs directly on the pin, and its ø64 big-end bore
directly on the crankpin. Real engines put a sacrificial bearing element in
both places — a pressed bronze bush at the small end, a pair of split shells
at the big end. `rod_cap.py`'s docstring already admits this ("no bearing
shell, no locating tang or dowels").

Adding them is not just two new parts: the rod and cap have to give up the
space the bearing occupies, and the big end needs the feature that stops a
shell rotating in its housing.

## Decisions

Three sizing questions were settled before design (all confirmed by the user):

**1. Where the big-end shell wall comes from — the crankpin gives way.**
The drawing's ø64 is read as the machined *housing* bore, which is how a rod
drawing normally dimensions it. Shells are 2 mm wall, ø64 OD / ø60 ID, so the
crankpin becomes ø60. The rod and cap keep their ø64 bore, ø72 OD, 4 mm wall,
I-beam pocket floor arc, lugs and every existing fillet set. Nothing in the
model *is* the crankpin, so ø60 costs no geometry.

The rejected alternative — hold ø64 as the crankpin and open the housing to
ø68 — forces ø72 → ø76 to keep the wall, which pushes the pocket floor arc
out, moves the arch and lug blends, and re-runs every big-end fillet.

**2. The small end gives way, not the pin.** The pin is a hard ø24 (it runs
in the piston bosses too), so the bore opens to ø28 for a 2 mm bush wall and
the boss grows ø34 → ø38 to hold the 5 mm wall the rod docstring argues for
("nearly all reciprocating mass, the most expensive place to be generous" —
that reasoning is about wall, not OD). The alternative, holding ø34 and
letting the wall thin to 3 mm, was rejected.

**3. Shells are located by the classic tang and notch**, not by crush fit
alone — the rod and cap each get a milled notch. This is the anti-rotation and
assembly-proofing scheme `rod_cap.py` currently disclaims.

## Clearance check (done, not assumed)

Growing the small-end boss to ø38 (R19) looked like it would foul the piston's
internal U-rib, described in `piston.py` as an "R18 arch concentric with the
pin" — R19 would cut 1 mm into R18.

It does not. The R18 arch is the *inner profile of the U-towers*, and the
towers only exist at |y| ≥ 20.4; the tunnel through the middle, where the rod
small end actually swings, is open well past ø44. Measured by intersecting the
built piston with a probe cylinder on the pin axis, 36 wide (the rod's
`end_w`):

| probe OD | piston material inside |
|---|---|
| ø34 (today) | 0 mm³ |
| ø38 (new) | 0 mm³ |
| ø44 | 0 mm³ |

Material first appears when the probe band is widened past |y| = 20.4, i.e.
the limit on the small end is the tower gap in Y (rod is 36 wide, ±2.4
clearance), not the arch radius. **`piston.py` needs no change.** A boss
concentric with the pin sweeps the same cylinder at every rod swing angle, so
this radial margin is not a function of crank position.

## New parts

Both are built in the **rod's frame** (big-end centre at the origin, bore
along Y, small end at Z=160), following `rod_cap.py`. The assembly then places
them with the existing `rod_loc` transform.

### `bushing.py` — pin bush

- ø28 OD / ø24 ID × 36 wide, centred at (0, 0, 160), axis along Y.
- Flush with both small-end boss faces (36 = rod `end_w`).
- 0.5 × 45° chamfer on the OD at both ends (press-fit lead-in) and on the ID
  rims (oil lead-in, and it removes the edge that scrapes the pin on
  assembly).
- One ø3.2 radial hole at +Z, coaxial with the rod's existing small-end oil
  hole — without it the drilled path from the boss top dead-ends on the bush
  OD and the pin is never fed.
- Phosphor bronze, 8.8 g/cm³ → **measured 5.824 cm³ / 51.3 g**.

### `bearing_shell.py` — one half-shell, used twice

- ø64 OD / ø60 ID × 36 wide, exactly 180°, built above Z = 0.
- Tang at the +X parting end: 4 wide along Y, **centred axially**, standing
  1.5 proud of the ø64 OD, 1.5 thick in Z against the parting plane.
- Axial centring is what makes this one non-handed part. The lower shell is
  the same solid rotated 180° about X, which maps (x,y,z) → (x,−y,−z): the
  tang stays at +X and the body drops below the split plane. With an
  off-centre tang the pair would have to be mirror images.
- Steel-backed trimetal, 8.0 g/cm³ → **measured 7.021 cm³ / 56.2 g each**.

## Modified parts

### `rod.py`

- `pin_bore_d = 24` becomes `pin_d = 24` (reference — the pin is now reached
  through the bush), plus `bush_t = 2` and `small_bore_d = pin_d + 2 * bush_t`.
  The bore sketch uses `small_bore_d`.
- `small_od` 34 → 38. Consequences, all self-adjusting: the boss bottom moves
  141 → the shank top at Z=150 is still buried 9 deep; the boss rim fillet
  filter keys off `small_od / 2`; the oil hole already starts at
  `small_od / 2 + 1` above the centre and runs `small_od / 2 + 3`, so it
  follows the boss out and still breaks into the bore.
- **`pocket_z1` becomes derived — found during implementation, not designed
  up front.** With `small_od` at 38 the boss/shank blend arc bottoms out at
  Z=141, which put it exactly 3.0 above the I-beam pocket top at Z=138 —
  exactly `2 × shank_fillet`. The two R1.5 fillets then meet tangentially
  with no face between them and OCC refuses the *entire* blanket fillet.
  The failure is razor-thin and so reads as a mystery: R=1.5 fails where
  R=1.497 succeeds, every edge subgroup passes alone, and no single edge
  removal fixes it (`boss+pocket` and `boss+arch` fail, `pocket+arch`
  passes). Fixed dimensionally rather than by weakening the radius or
  hacking the edge filter:

  ```python
  pocket_z1 = rod_len - small_od / 2 - 3 * shank_fillet    # 136.5
  ```

  3 × the radius leaves a real face between the two blends, and deriving it
  means the next change to `small_od` cannot silently recreate the
  collision. Cost: the pocket is 1.5 shorter, so the rod carries a little
  more metal near the boss. `shank_fillet` moved up the constant block to
  be defined before this line.

  The general rule worth remembering: **two edges that will both be
  filleted must stay more than 2 × the radius apart.** At exactly 2 × R the
  fillets are tangent and the operation fails.
- Tang notch, **cut last, after every fillet** — the project's established
  late-cut rule (the oil groove and pin lube bores in `piston.py` broke
  fillets when cut early). 4.4 wide along Y (0.2 clearance per side), 1.9
  radial (ø64 out to ø67.8), 1.6 deep in Z from the split face, at the +X
  bore edge. Leaves ~2.1 mm of the 4 mm wall over the shell. It sits under the
  lug block, clear of the ø8 bolt hole (x = 39..47).

### `rod_cap.py`

- Geometry otherwise untouched: same ø64 bore, ø72 OD, 36 wide, lugs, bolt
  holes, rim fillets.
- The same notch, mirrored below Z = 0 (z = −1.6..0), same +X side. Same side
  on both halves is deliberate: a shell then physically cannot be fitted
  backwards.
- Docstring: drop "no bearing shell, no locating tang or dowels" from the
  simplifications — that gap is closed.

### `assembly.py`

- 12 → 15 children: bush at `rod_loc`; shells at `rod_loc` and
  `rod_loc * Rot(180, 0, 0)`.
- Colours distinct from the rod's `darkgoldenrod` and the cap's `goldenrod`:
  bush `peru`, shells `rosybrown`.
- Shared-dimension list corrected, since these are exactly the entries the
  change invalidates:
  - ø24 — pin OD = piston bore = **bush ID**; the rod small-end bore is ø28
    (= bush OD, press fit)
  - ø64 — big-end **housing** bore = shell OD, rod and cap; the crankpin is
    ø60 = shell ID
- Interface 2 (pin joint) and 3 (big end) in the system brief gain the bearing
  element; interface 2 stays full-floating (the pin turns in the bush and in
  the piston bosses).

## Verification

All of it is automated in `piston/check_bearings.py` — five checks, run with
`cd piston && uv run python check_bearings.py`. It builds the solids and
asserts on measured geometry, because this project has no pytest suite for
the CAD parts. Result: **OK 5/5**.

1. Each new part: render, `is_valid`, exactly one solid, volume and mass
   matched against the hand figures — bush 51.3 g vs ~52 estimated, shell
   56.2 g vs ~56. Both inside the 5% band.
2. `rod.py` and `rod_cap.py`: one valid solid each, all existing fillet
   sections still pass. Measured masses (steel 7.85):

   | part | before | after | delta |
   |---|---|---|---|
   | rod | 84.39 cm³ / 662.4 g | 85.77 cm³ / 673.3 g | **+10.9 g** |
   | cap | 26.86 cm³ / 210.9 g | 26.85 cm³ / 210.8 g | −0.1 g (the notch) |

   The rod's +10.9 g replaces the spec's original ~18 g estimate, which
   ignored both the shank's overlap into the new boss annulus and the R2 rim
   fillets. It also now includes the metal the shortened pocket leaves
   behind.

   Bearing metal added to the group: 51.3 + 2 × 56.2 = **163.7 g**.
3. Assembly: 15 children, rendered from four views; the bush reads as a ring
   inside the small-end boss and the ø60 shell bore inside the big end.
4. Boolean interference sweep, reported as volumes: bush∩pin, bush∩rod,
   bush∩piston, shell∩rod, shell∩cap, shell∩shell, pin∩rod, and a positive
   tang-pocket probe on each half. **All 0.000 mm³.** The tang check is a
   genuine red/green: before the notches were cut it reported 9.070 mm³ of
   tang-into-rod interference.

### Known modelling caveat to record

Everything in this group is modelled nominal-on-nominal, so the sweep above
proves *envelope* correctness, not fits: the two press fits (bush in boss,
shells in housing) read as zero clearance rather than as interference, and the
tang shows 0.2 loose in a notch that should be nearly snug. This is consistent
with the rest of the group (the pin is ø24 in a ø24 bore) and is kept
deliberately — but those zeros must not be read as toleranced fits.

Also unchanged and still simplified: shells are exactly 180° with no crush/nip
relief, and have no oil hole, because this rod has no big-end oil feed
drilling to align one with.
