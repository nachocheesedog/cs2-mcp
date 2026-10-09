# Road layout for zoning — rules the tools follow

Learned from a waterfront district built entirely with these tools (then found sparse and awkward), and calibrated
against the game's own zone blocks in several vanilla-style suburbs.

## How the game zones
- Every zonable road (not highways, ramps, tracks, bridges/tunnels) gets a zone block on **each side, 6 cells = 48 m
  deep**, in **8 m frontage columns**.
- Where two roads' blocks overlap, the overlap is hidden: each loses depth.
- At a **right-angle** junction the corner square goes wholly to one block: nothing is lost.
- At a **skewed** junction rectangular blocks can't cover the wedge: triangles stay unzoned and cells get hidden.

## Rules
1. **Parallel streets: centre to centre = w1/2 + 96 m + w2/2.** Blocks meet back to back.
   - Closer: blocks overlap → shallow zoning on both (more road per zoned cell).
   - A bit farther (up to one more street + 96 m): a **dead gap** between the zoning edges that nothing will ever fill.
   - `cs2_offset_road` uses this spacing when `offset_m` is left out and warns on anything else.
   - `cs2_district_grid` pitch = road width + 96 (`zoning_depth_cells` 6).
2. **Junctions at 90°.** A street coming in skewed bends so its last ~50 m (one zoning depth + margin) arrive square.
   Grid junctions under 80° are defects. Onto curved / waterfront roads, down to 60° is tolerated.
3. **Grid next to a curved road: keep a full double depth back** (rim half + 96 m + street half), so the grid's blocks
   never overlap the curved road's. The strip between may be imperfect — that's the accepted curve loss.
4. **Line a new grid up with its parallel neighbours** (same angle, phase fitted so the spacing to them is an exact
   pitch). `cs2_district_grid` does this automatically, also when you give `angle_deg`.
5. **Straight (grid) roads target ~100% full depth, no dead gaps, no skewed junctions.** Curved / waterfront roads may lose
   some.
6. **Zone what you build.** Unzoned frontage looks like empty land (over a thousand zonable cells left
   unzoned were the main reason that district looked sparse). Build results report the unzoned
   cells a plan left.

7. **No holes inside blocks.** Every block is at most two zoning depths across (96 m + road) in BOTH directions reachable
   by some street; if a street can't be built (rail piers, lots) the block must still be split (shift the street, bend it
   around the pier, or add a mid-block street) - never just drop it. A dropped street = a hole nothing can grow in.
8. **Grid corners exactly 90° (±2°).** 80–88° looks square on a map but still breaks the blocks. Don't end grid
   streets on a curved road at whatever angle they arrive: end the grid on a straight, grid-aligned edge street, and
   treat the strip between it and the curve as the accepted curve zone (parks, curve-following frontage).

## Tools
- `cs2_zoning_check` — the forecast (now also `holes` and `not_square_junctions`, map marks H / Q) for an area's roads, optionally with a draft (plan_id / segments): depth per
  column, `full_depth_share_straight` / `_curved`, dead gaps, skewed junctions, verdict; map marks G / S / D.
- `cs2_district_grid`, `cs2_rim_road` previews and every road builder's `dry_run` / `plan_only` carry a
  `zoning_forecast`. Redraft until the verdict says `good`.
- Calibration: on well-built grids the forecast gives 90–94% full depth on straight roads (the game's own blocks give
  ~95% there); the remaining gap is mostly columns the game hides outright at junctions.

## Code
- `server/zoning.py` — the forecast (Voronoi of centrelines, crossing vs parallel rule, dead-gap march, junction angles
  by node id).
- `server/pave.py` `plan_fill` — perpendicular-approach connectors (`perp_deg`, `min_angle_curved`, `approach_m`), rim
  clearance; `detect_lattice(angle_deg=)` phase fit.
- `server/tools4.py` `build_offset_road` — zoning spacing default + warning.

# Junctions and interchanges — designed per site

Standard interchanges can be downloaded as assets; the value here is designing each one for its own traffic and site.
Claude designs the topology; the tools measure and do the arithmetic. The loop:

1. **Measure** — `cs2_site_movements site=[x,z] radius=…` (mod 0.27): turning demand as an entry → exit matrix from
   the cars' planned paths, sampled over time. Heavy movements get direct / free-flow ramps; light ones loops or nothing.
2. **Check what's there** — `cs2_network_check site=… radius=…`: movements possible / MISSING / long detours between the
   site's approaches (highway + arterial approaches by default when a highway crosses), lane balance (aux lane before
   every off-ramp, after every on-ramp), merge/diverge angles, weaving length, radius vs design speed,
   grade, clearance, dead ends, one-way traps.
3. **Design each ramp** — `cs2_design_path start={edge, at, branch_deg, side} end={…} design_speed_kmh=…`: exact Dubins
   geometry (true arcs, never tighter than the design radius; `words` forces the shape, e.g. `['LSL']` to stay on one
   side, a 270° loop), elevation within `max_grade` clearing each crossing by `clearance_m` (over / under). It warns when
   the path crosses the road it joins (wrong side / turn direction) and when a crossing can't get clearance.
4. **Check the whole draft** — `cs2_network_check segments=[…all new pieces…]`, then `cs2_place_roads dry_run`, then
   build **in a test save**, then measure again (step 1) and adjust.

Defaults (all parameters): design speed highway 100 / ramp 60 / arterial 60 / local 40 km/h, R = v² / (127 × 0.25);
grade 6 / 8 / 8 / 10 %; clearance 6 m (design_path 7 m); weave ≥ 400 m; merges/diverges ≤ 25°.

Known limits (2026-09-26): lane counts from mod 0.27/0.28 double-count long edges (each lane is split into EdgeDelta pieces 0-0.5 / 0.5-1) - fixed in mod 0.29, the server corrects named highway types meanwhile; speed_kmh = CarLane.m_SpeedLimit x 1.8; turn restrictions
at nodes are not modelled (all turns except a U-turn on the same road are assumed possible); site_movements needs mod
0.27; Dubins paths are shortest-path shapes — obstacles other than crossing roads (buildings, water, terrain) are not
avoided, so check the map / dry run.
