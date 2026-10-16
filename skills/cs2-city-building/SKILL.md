---
name: cs2-city-building
description: Building and fixing a running Cities Skylines II (CS2) city through the CS2 MCP - a whole new district in one call (cs2_build_district), road layouts that zone perfectly (grids, curved waterfronts, back streets, ribs), zoning a district, demolishing/rebuilding a district safely, highway junctions and interchanges designed per site, city services (healthcare / deathcare / hearses, crematoria placement), and working alongside other CS2 sessions without crashing the game. Use it whenever the user asks to lay out, rebuild, refactor, zone or fix a district, streets, blocks, a waterfront, a highway connection or interchange, or asks why buildings won't grow, why zoning has gaps, why hearses / ambulances are overwhelmed, or where to place a service building - even if they only mention one street or one icon.
---

# CS2 city building through the MCP

Lessons from rebuilding and zoning large districts (curved waterfront districts, deathcare backlogs in
large, dense cities) through the CS2 MCP. Rules marked **STANDING** are recommended practice - follow them unless the
project needs otherwise. Code lives in this repo: the C# game mod in `mod/`, the Python MCP server in `server/`; layout
rules + code map in `docs/ROAD_LAYOUT.md`.

## 0. Fast path - a whole new district in one call (STANDING)

When the user wants a new area built ("build a district here", "fill this land with housing"), do NOT lay
streets, zone and place services step by step - that is dozens of round trips and the session drifts into polishing
details. Use `cs2_build_district` (docs/DISTRICT_BUILDER.md):

1. Pick the outline once: `polygon` (or `center` + `size`) on free land that touches or nearly touches existing
   streets (connectors may leave it by `connect_beyond_m`, default 250). One `cs2_map` of the area is enough to choose
   it - no screenshots.
2. One call: `cs2_build_district polygon=... preset=suburb|residential|downtown|office|industrial name=...
   services=[...]`. Add `preview=true` only when the user wants to see the draft first; then build it with
   `plan_id`. The tool pauses the sim, builds streets -> zero-crack flags -> services -> zoning -> district, restores
   the speed and reports. It zones because a new district was asked for; pass `zoning=false` when the user only wants
   the streets (rule 7 below).
3. Read the report, not a screenshot: `roads.refused`, `check.fix_these` (dead ends a refused street left),
   `check.islands`, `zoning`. Fix only those; the other dead ends are stubs at the outline (room to grow), and cosmetic
   junction findings stay alone unless the user asks.
4. Wrong place or wrong style: `cs2_remove_district` (exact undo while its entries are the newest), then build again
   - cheaper than repairing.

For a known sequence of small edits (policies, a few zones, several buildings) use one `cs2_batch` instead of one call
each. For a long building session, `cs2_settings fast_mode=true` stops builders from moving the camera and checking
after every edit; switch it off for careful detail work. The rules below still decide the details when the user asks
for hand-made layouts.

## 1. Zoning geometry - the rules (STANDING)

The game zones 6 cells (48 m) deep on both sides of every zonable road (not highways, ramps, tracks, bridges), in 8 m
frontage columns. At a right-angle corner one block takes the corner square: no loss.

1. **Grid streets: full 48 m depth everywhere.** Parallel spacing centre-to-centre = `w1/2 + 96 + w2/2`
   (Small 16 m -> 112 m; Small next to Medium 24 m -> 116 m). Closer = shallow blocks; farther = a dead strip.
   Next to a NON-zoning road (highway, ramp, track): only ONE strip -> `w1/2 + 48 + w2/2` (highway half 6 + 48 + medium
   half 12 = 66 m).
2. **No holes inside blocks.** A block may never be more than two depths across between streets. If a street cannot be
   built (rail pier, lot) shift it, bend it round, or add a mid-block street - NEVER drop it (a dropped rib = a 200 m
   block with a hole nothing can grow in).
3. **Every grid corner exactly 90 deg (+-2).** 80-88 still breaks the blocks. Don't run grid streets into a curved
   road at whatever angle they arrive: end the grid on a straight grid-aligned edge street; the wedge between it and the
   curve is the accepted curve zone (parks / curve-following frontage). Curved / waterfront roads may lose some depth.
4. **No dead gaps** between two parallel roads' zoning edges.
5. **Line new grids up with parallel neighbours** (same angle, phase fitted) so spacing to them is an exact pitch.
6. **Zone LAST.** With demand at 100, zoned frontage grows buildings within 1-2 min and they then block every later
   road ("Overlap Existing"). Build all roads of a district first, then zone everything in one pass.
7. Never zone unless the user asked for it.

8. **Zero cracks: inside a block bounded by 4 streets every zonable cell must line up with its neighbours - no slivers
   between blocks.** Cracks are NOT fixable by zoning (no cells exist there); they come from the road build. Root cause
   (decompiled Game.Zones.BlockSystem.CreateBlocks + Game.Tools.GenerateEdgesSystem): a road's cells are centred on its
   edge and phased from its NODES in 8 m steps; a crossing road's rows start at cellWidth*4 m from its centreline
   (Medium 24 m = 3 cells -> 12 m, Small 16 = 2 -> 8). Odd-cell-width crossings (Medium) leave a 4 m offset -> the
   straddling cells are hidden -> 4 m crack at every avenue-block/side-street-block seam. The game's own road tool fixes
   it with Road flags StartHalfAligned / EndHalfAligned (from CoursePosFlags.HalfAlign on the course ends; each shifts
   that end's cell line by 4 m); a naive scripted road placement never sets them. Also: a mid-block split node restarts
   the cell phase (a seam) - avoid degree-2 nodes inside blocks. A grid with 90 deg corners and 120 m / 96 m segments
   (all multiples of 8) still cracked before the flags were set.
   **Fix:** `cs2_zone_cracks` (plan + measure) / `cs2_road_align` (set flags, re-zone), and every MCP road build aligns
   its new edges automatically. **Align a whole grid at once - never one street:** a single street shifted 4 m lines up
   with the avenue but falls out of line with the back-to-back street behind it (one test: cracks 25 -> 60, undone).
   Aligning a whole 28-edge grid closed the avenue seams, +346 visible cells, buildings kept. Judge with
   align.crack_area (m2 of sandwiched land) + zoned-cell count, not the misaligned-pair counter.
   Remaining cracks = residual edges whose LENGTH is off the 8 m lattice (e.g. 94 m street spacing) -> move the road
   1-4 m (plan() reports length_change_to_fit_m), and mid-block split nodes (remove the node).
   **The simple rule: kerb-to-kerb distance between consecutive crossing roads = exact multiple of 8 m**
   (widths as cellWidth*8), flags set. 0.5 m off = a whole hidden cell. New builds: cs2_place_roads / cs2_build_plan
   snap drafts onto this lattice by default (`lattice` snap|check|off; lines move <= 4 m, existing roads anchor).
   Existing districts: align.lattice_check lists lines to move; align.move_line rebuilds one (clears buildings within
   52 m, rebuilds cross streets, heals T-end split nodes, restores zoning). Moving one line works mechanically but
   neighbouring seams stay - they belong to OTHER off-lattice lines; a grid only closes when all its lines are on the
   lattice, and lines touching curved (squared-end) streets can't be moved by the tool. In built districts the payoff
   per demolition is low: a small repair pass (a few lines, ~100 buildings) cut district crack area only ~1%,
   and a long avenue move can be refused at its highway-ramp end (7 buildings lost access) and had to be undone
   (12 undo steps, flags re-set, 921 cells refilled). move_line now refuses ramp-connected lines. Most remaining crack
   area sits in curved waterfront rib blocks. Verdict: don't repair built districts line by line; lay new grids on the
   lattice (default now) or rebuild a whole grid from scratch if the user wants it perfect.
   Planning a grid re-lay: work in a rotated frame (u along the grid angle, o = x*-sin(a) + z*cos(a)), list what to
   keep (through avenues ending at ramps - never extend those, curved drives, elevated rail piers with >= 14 m street
   clearance, landmarks), and recompute the removal list after every load (road ids change). Needs a fresh manual save
   and the user's go.

### Checking (always, before and after building)
- `cs2_zoning_check` (area, optional `plan_id` / `segments`): per-column depth, `full_depth_share_straight/curved`,
  `holes`, `not_square_junctions`, `dead_gaps`, `skewed_junctions`, verdict + map marks H/Q/G/S/D. Iterate until the
  verdict is "good". Every road builder's `dry_run` / `plan_only` and grid/rim previews carry a `zoning_forecast`
  judging only what the draft adds. Calibrated: good grids read 90-94% straight full depth (game's own blocks ~95%).
- `cs2_inspect sections=[roads.dead_ends, roads.islands]` after every stage - a rebuilt district can end up a multi-km
  island connected to nothing.

## 2. Layout tools and when to use them
- **Straight grid on open land:** `cs2_district_grid` (auto lattice is often poor on awkward land - check the preview;
  a hand grid via `cs2_extend_lines` in a frame is more controllable: `end_at_ids` = the waterfront pieces,
  `end_keep:"end"`, `stop_at_types:["Highway"]`, small `snap_m`/`end_snap_m` (6-8; 30 snapped street ends onto
  unrelated nodes and onto a HIGHWAY)).
- **Streets off a curved spine** (waterfront, highway frontage): `tools12.ribs` (server; call from a script):
  perpendicular streets one pitch apart, anchored to continue existing junctions, bent to meet the target square,
  each dry-run, refused ones retried +-8/16/24 m, reports `blocks_too_wide` (= holes) - fix those.
- **Back street / frontage road:** `cs2_offset_road` without `offset_m` = exact zoning spacing; `source_zones:false`
  for highways. Knots from a highway: trace the carriageway's outer edge in 40 m bands.
- **Waterfront avenue:** `cs2_rim_road` also hugs highways (barriers) - keep only water-following segments (drop
  those <150 m from a highway), save as a plan, `cs2_build_plan`.
- **Whole-island / multi-district service + park placement:** `python server/sites_tiled.py '<json>'`
  (cs2_service_sites over a big area coarsens the occupancy grid -> 0 lots; this scans in <=560 m tiles at a 4 m grid).
  Args beyond service_sites: `students` children|teens + `capacity` (school seats taken from the nearest demand cells -
  without it one school "covers" a whole 1.2 km radius), `spread_m` (+ `within`) for areas with no residents yet,
  `place_now` (builds each pick for real and falls to the next-best lot on refusal: no dry-run flicker; growables appear
  on scanned lots within minutes -> "Overlap Existing"), `cands_file` cache (WINDOWS path - Python can't open /c/...).
  Vanilla PoliceStation02 / MedicalClinic02 / parks carry invisible path nets -> need `allow_subnets` (safe, same class
  as MedicalClinic01). Order: demand services -> spread services -> parks (demand then spread) -> zone (empty cells only).
- **Service upgrades:** extensions whose BuildingExtensionData.m_External is false (school Extension Floors / Halls /
  Wings / Playgrounds, lot = the parent's lot) go ON the parent (x, z, facing = the parent's). Placed beside it they
  become loose lots with "No Pedestrian Access" and can't be bulldozed by id (owned objects; MCP bulldoze skips them).
  Recovery: `cs2_undo` (cap 50 entries), else bulldoze the PARENT (mod >= 0.44 takes its upgrades) and re-place it in
  place. For capacity-only upgrades use `cs2_upgrade_all capacity_only=true` (reads SchoolData / HospitalData /
  Deathcare / Police / Garbage capacity fields; Playground = 0 seats -> skipped).
- **Waterfront back rows:** `cs2_waterfront_backs` dezones blocks whose back faces water with no street behind (so
  houses don't turn their back on the water) and bulldozes only fresh level-1 spawns; established buildings keep their
  cells (dezoning under a building condemns it).
- **Access warnings city-wide:** `cs2_fix_access` (growables -> bulldoze + regrow; services listed). New schools on a
  Gravel Road get no power/water/sewage - pass `road_types` to the site search.
- **Service sites:** `cs2_service_sites type= service=` (healthcare/police/.../deathcare) - greedy by real demand, dry-run
  verified. `cs2_park_sites` (verify) for parks.
- Elevated rail/highway piers block streets underneath: read pier positions (`obstacles categories=[pillar]`), choose
  street positions with >= 11 m clearance; under low ramps ("Not Enough Clearance") streets can't pass at all.

### Fixing an existing district
- `tools12.square_ends(rpc, {box, polygon, clear_buildings})`: every street meeting a through road off-square gets its
  last piece rebuilt as a bend arriving at exactly 90 (exact Bezier tangent of the target; sampled tangents left
  3-6 deg). Clears growables in the new path FIRST (so a failed attempt can still `undo` the old piece - undo only
  works on the newest change). Took 19 off-square corners down to 5 in one district.
- The forecast measures junction angles from exact Bezier end tangents (polyline sampling on curves read 82-88 for true
  90s).
- `tools12.ribs` never re-places ribs at anchors (existing junctions) - that made 14-24 m duplicate streets. When two
  anchored connectors are a non-multiple of the pitch apart, split the gap into equal blocks (e.g. 3 x 80 m: 64 m deep
  = two 4-cell row-house lots) rather than leave a hole.
- Curve-zone wedges (between bent street ends and the curved waterfront): a pocket park if a lot with frontage fits;
  otherwise a short cul-de-sac square off the waterfront into the wedge centre so its frontage zones it.
- Rebuilt / new road pieces lose their zoning - re-zone empty frontage (overwrite false) after every road change.
- square_ends finds T-junctions by geometry (the two most in-line arms = through road, third = street), so it also
  squares a street onto a cul-de-sac or another street; it retries while another session holds the MCP tool ("tool
  busy"). Road ids change on EVERY load - a bulldoze by stale ids silently removes nothing and new roads then land on
  top of the old ones.
- Loading an older manual save undoes later fixes - re-run the checks after every load and redo what's missing.

### Restyling / rezoning a built district
- To change a district's architectural style, rezone per lot by the road it fronts (e.g. side streets -> a Medium Row
  style, avenues -> Medium Residential alternating with Mixed every ~120 m), bulldoze the old-style buildings in batches
  of 40, and let leftover back rows take the style in front of them. Pick one consistent palette per district.
- Mod >= 0.35 batches zoning (one op per 250 quads); on older mods zone per lot rectangle, merged (302 ops = 80 s).
- Shore strips: zone only cells with grade <= 15% and front-connected (1,864 cells in 3.4 s batched); parks on the flat
  spots (parks need allow_subnets), native trees on steep headlands; bulldoze no-road-access / no-pedestrian-access
  buildings afterwards.
- Some styles fail on certain lots: USNE Residential Medium Row got "No Pedestrian Access" on 17/18 flagged lots and
  re-flagged 6 after regrowth while CHI Row had 0/439 - prefer CHI Row on such lots.
- **Every MCP op - dry runs included - borrows the player's tool and flashes a preview, which looks like flickering to
  a player. Never bisect with many dry runs while the user plays; pre-filter hard (tools8._blockers, 2.5 m) and accept
  a few refusals, or ask first.**

## 3. Highways (STANDING)
- **Never join a local road to a highway at grade** (no junction, no end snapped onto a highway/ramp node). Access only
  via proper ramps. After any build near a highway, list nodes shared by highway + local edges: only ramp terminals
  may appear.
- Aux lane before every off-ramp split / after every on-ramp merge.
- Interchanges are designed per site (not templates): `cs2_site_movements` (turn counts from cars' paths),
  `cs2_network_check` (movements possible/missing/detours, lane balance, merge angles, weave, radius vs speed, grade,
  clearance, dead ends/traps), `cs2_design_path` (Dubins ramp geometry + grade/clearance profile; `words` forces the
  side). Build in a TEST save the user picks, never in their main city blindly.
- Lane counts: mod >= 0.29 counts correctly (lanes on long edges are split into EdgeDelta pieces). Speed km/h =
  CarLane.m_SpeedLimit x 1.8.

## 4. Demolition / rebuild safely (crashes happened)
1. User makes a manual save first (autosaves keep only 10 minutes).
2. **Dezone first** (`tools5.rezone districts=[..] from=<all legend names> to='none' only_empty=false`), then
   buildings (batches of 40, 3 s apart, ping after each), then roads (batches of 10-15, 4 s apart). Otherwise
   hundreds of buildings regrow during the clearing window and strand without roads.
3. Keep landmarks, stations, service buildings, their access roads, highways, tracks, and link roads crossing the
   district edge. Skip roads touching building-owned nodes.
4. A mass bulldoze (672 buildings then 60-road batches, no pauses) crashed the game natively.

## 5. Services - deathcare facts
- The dead keep `Dead|RequireTransport` in the hearse and at the crematorium; the hearse icon sits on the CITIZEN
  (building warnings miss it) and appears after `HealthProblem.m_Timer` passes a threshold while waiting.
- Hearses carry 1 body; a crematorium over storage capacity stops dispatching. Crematorium01: 10 hearses, 100 bodies/
  month, storage 20; FR_Crematorium (16x24 m): 5 hearses, 100/month, storage 10 - cheap to sprinkle in dense cores.
- `cs2_service_coverage services=["deathcare"]` (mod >= 0.27) + `health_census` (mod >= 0.29 splits waiting / at
  facility / in transit). A large city can need dozens of crematoria; the real bottleneck is often hearse trips in the dense core.
- New big residential (e.g. a 1,300-resident tower) gets old-age deaths within days - not a bug.
- Adding crematoria can roughly halve bodies waiting in homes. Next bottleneck = storage (crematoria near full,
  big Crematorium01s far over their storage). Levers: Health & Deathcare budget 150% (efficiency 125% -> more processing +
  hearses); CEMETERIES (Cemetery02 112x48: 8 hearses, 1,500 long-term storage; Cemetery01 128x200: 15 hearses, 5,000) -
  hard to fit in a built city (very large search boxes coarsen the grid and miss lots - search in ~1.5 km tiles);
  Hearse Garage upgrade (+5 hearses) via `cs2_place_building type="Crematorium01 Hearse Garage"
  parent=<crematorium id> allow_subnets=true` - auto-placed beside the parent, 10 -> 15 hearses, no crash; the garage
  has 9 built-in path nets (needs allow_subnets + a save); one garage per crematorium ("already has").
  FR_Crematorium has no upgrades.

## 6. Working alongside other CS2 sessions / agents
- If another agent or session drives the same game, only one edits it at a time; announce restarts/imports/deploys to
  it and hold calls while another imports.
- Building sub-nets made of **fence nets** crash even a preview - use fence props. Buildings with built-in nets need
  `allow_subnets` + a recent save; vanilla ones (FR_Crematorium, MedicalClinic01) are fine.
- Mod deploy (`dotnet build -c Release` in `mod/`) only with the game closed; debug-compile with
  `dotnet build -c Debug -o /tmp/cs2mcp_check`. Mod >= 0.29 restores the player's tool + selection after each MCP op.
  Always build from current source and bump the mod version past the installed one; back up the previous build.
- Road/building ids change on every load - re-query, never reuse old ids.
- Mod >= 0.33: place_building of ANY prefab with built-in nets is refused - dry runs included - unless
  allow_subnets=true (FR_Crematorium, MedicalClinic01, Hearse Garage upgrades...). cs2_service_sites / cs2_park_sites
  accept allow_subnets and pass it to their verify dry runs. Undo history is cleared on every city load. Road tools
  (place_roads, ribs, square_ends) are unaffected. Crematorium01 has 6 built-in nets, FR_Crematorium 1 - both need
  allow_subnets (also in cs2_service_sites); an upgrade placed with parent= is checked on the upgrade prefab only
  (Hearse Garage: 9 nets).
