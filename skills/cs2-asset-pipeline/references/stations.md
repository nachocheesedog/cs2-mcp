# Transit stations with built-in tracks

This is the full recipe and every pitfall from building a large custom through-station asset (**<Name>**): train
and subway tracks underground on two crossing levels, an open pit under a floating glass canopy, and a "Station Services"
upgrade. The example project layout: `station_layout.py` (the single source of truth, §1), `build_station.py`,
`export_station.py`, `make_station_atlas.py`, `build_lods.py`, plus a PLAN.md.

**Example design (the final iteration):**
- **Two crossing levels, mixed modes:**
  - Level A (−20, the open pit): tracks widthwise (train + subway islands).
  - Level B (−32): tracks lengthwise (train + subway islands).
- **Track rules:**
  - A double net can't mix modes: the pair at the mode change is two singles.
  - Island spacing 12 m (8.6 m platforms) in an early version; see "Platform layout rules" below for the final spacing rules.
- **Everything derives from a few constants (`N_ISL`, `ISLAND`, `HW`)** (`track_positions`, `lay_tracks`, `stops`,
  which pick yaw by track axis and island side):
  - PIT_X = last track + 14, asserted equal to HW − 28, so the concourse ring stays 28 m.
  - Canopy half_x = HW − 2; wall line HW − 3; pavilions HW−16..HW−10; columns; planters.
  - Lower-level stairs from both galleries to every lower island centre (added to the gallery breakpoints).
  - The model scripts use `TMW = max(TX) + 6` for the tunnel mouths and `HX − 9` / `HZ − 10` for the door tests.
- **Lot growth:** a design lot of 22×14 became a game lot of 18×24 once everything was derived (LODs 55k / 5.5k / 1.5k).

**Growing the lot asymmetrically:** when spare cells are added ACROSS the lot (e.g. +2 cells on one long side, +1 on the
other), offset the content: `game_offset_x = (z_lo + z_hi) / 2`, applied in `_rot` and in the export TURN. When cells
are added at the BACK (e.g. a back park), use `game_offset_z = -(front_edge + back_edge) / 2` so the content sits forward
of the lot centre, and when placing keep the FRONT edge on the road: centre = front − facing × (depth/2).
- Export lawn rects as `features.lawns` and have the model's edging read them (no hard-coded lawn numbers).
- Before building which way something grows, CONFIRM the axis in the user's own terms (lengthwise vs across the lot);
  it is easy to grow the wrong axis.

**An earlier, simpler spec that also worked:**
- **Train:** 8 tracks, 4 island platforms, 8 stops (one per track side) at −20 m, in an open pit under a floating white
  diagrid canopy.
- **Subway:** 4 tracks, 2 island platforms, 4 stops at −32 m, running across the train tracks.
- **Lot and approach:** game lot 18×21 (design frame rotated 90° so an arch end faces the street). 8 m paved forecourts at
  both arch ends; lawns (Park Areas) on the long sides.
- **Enclosure:** the canopy comes down all round without touching, and glass rises to meet its lip. Brick arch portals
  (3 per end), a glass door per long side.
- **Station Services upgrade:** glass café pavilions, comfort +0.5, reaching the maximum.
- **Props:** 555 sub-objects in total, including the native benches, lamps, trees, fountains, kiosks, café sets and
  uplights.
- **LODs:** 49.8k / 5.0k / 1.4k tris; the glass is a `_Gls` sub-mesh per LOD (vanilla method, no textures).

**Rule 0:** read how VANILLA does it before designing, and never tell the user something is impossible
without checking the game's prefabs. The vanilla references are:
- `TrainStation01` (big station)
- `TrainStation03` (3 platforms)
- `TrainStation01 Services` / `TrainStation03 Services` (the upgrades)
- the W7 More Transport pack stations, whose `.Prefab` files are plain JSON under
  `%LOCALAPPDATA%Low\Colossal Order\Cities Skylines II\.cache\Mods\pdx_mods\81040_19\assets\W7MoreTransportAssetPack\`.
  Replace `$fstrref:"..."` with a string to parse them. `UnityGUID:` refs are vanilla prefabs, `CID:` refs are mod assets.
- `describe` with `describe_components=[...]` (mod ≥ 0.15) resolves them to names on live prefabs.

## 1. One layout script = the single source of truth
`station_layout.py` (system Python) writes `cs2_build/station_layout.json` in BUILDING-LOCAL coordinates:
x across the lot, y up (0 = ground, negative = underground), z along the lot depth (+z = FRONT = road side). It holds:
- `sub_nets`, `sub_objects`, `sub_areas`: pasted into `cs2_asset_configure set` as structured values.
- `blockout`, `features`, `canopy`, `columns`, `pit`: the Blender scripts build the model from the same numbers, so the
  model and the gameplay never drift.
- **CS2 → Blender:** `(x, y, z) → (x, -z, y)`. The CS2 front (+z) is Blender −Y.
- **Placement facing f = (fx, fz):** local +z → world f, and local +x → world (fz, −fx).

## 2. Sub-nets (tracks, walkways)
- `ObjectSubNets.m_SubNets` entry: `{"m_NetPrefab": "<vanilla net name>", "m_BezierCurve": [[a],[b],[c],[d]],
  "m_NodeIndex": [i, j], "m_ParentMesh": [0, 0]}`. Equal node indices connect.
- Use vanilla nets only, so there's no mod dependency:
  - `Twoway Train Track`, `Double Train Track - Twoway` (tracks at centre ±2 m)
  - `Twoway Subway Track`, `Double Subway Track - Twoway`
  - `Invisible Pedestrian Path`
- **4 island platforms / 8 tracks:** single tracks at the edges, doubles between the islands. Track-centre spacing is
  14 m across an island (a ~9 m platform) and 4 m inside a double.
- Run tracks well past the lot (±110 m). The ends become free nodes that city lines connect to (tested: a train line
  connected).
- Sub-nets placed with `cs2_place_building allow_subnets=true` worked, with no crash. Save a copy of the city first anyway.

## 3. Stops (sub-objects)
- `Integrated Passenger Train Stop`: 2.5 m from the track centre on the platform side, y = track −0.2, yaw facing the
  track (±90° when the tracks run along z).
- `Integrated Subway Stop - Middle`: 1.5 m from the track centre, y = track +1, yaw 0/180 for tracks along x.
- **ONE stop per track side,** mid-platform. Two per track don't fit, because trains are long.
- `Pedestrian Spawn Location` goes 1.8 m along the platform from each stop.
- Verify with `cs2_transit_report` over the site: "stops_without_lines" lists each stop by mode.

## 4. Pedestrian space: the vanilla pattern
- **Hangaround Area** on every platform: waiting passengers, which are also spawn points for the stops.
- **Walking Area** polygons for free movement: plaza, galleries, bridges. Each node carries its own y.
- **Invisible Pedestrian Path** ONLY for level changes (escalators, stairs).
- Rules found in game:
  - A path end must lie ON an area border.
  - Neighbouring areas must share vertices where they touch, with no T-junctions (use `rectv()` plus the checker).
  - Polygons go CLOCKWISE seen from above.
  - **The street link is where a walking area reaches the LOT EDGE.** Pedestrian Access Locations go about 0.5 m inside it.
  - A polygon can't have a hole, so a ring = 4 rectangles.
- Symptom when it's wrong: hundreds of "No Pedestrian Access" icons in `cs2_building_status` (the whole network is an
  island).
- **OFFICIAL RULES (wiki "Assets: Common Asset Principles", verified for 1.5.2; read it in a real browser,
  WebFetch gets HTTP 427 there):**
  1. **Areas do NOT join through shared vertices.** "The transition between different areas are achieved by placing
     Pedestrian Access Location markers on the boundary of 2 separate areas." Put a marker on EVERY shared border, and
     several along long borders.
  2. **A path/net joins an area only through a marker.** Nets with pedestrian lanes connect to areas "provided there is
     a Pedestrian Access Location marker connecting the network and the area". So: a marker at every escalator, stair
     and stub end.
  3. **Parent mesh −1 = follow the terrain height.** An underground area or marker left at −1 (the default when
     `m_ParentMeshes` is null) snaps up to the ground. Set `"m_ParentMeshes": [0, ...]` (one per node) on every
     underground area and `m_ParentMesh: 0` on underground markers. Station hangaround nodes: identical heights, same
     mesh.
  4. Nets need no marker for the OUTSIDE link: "it is sufficient to have one node close to the edge of the lot".
  5. Markers attach within an effective radius. Keep spawn and door markers away from the lot edge (routes can jump to
     a neighbour's markers).
  6. Verify with the **Pathfind Debug System** gizmo (Developer Mode, Tab, Gizmo; NOT "Path Debug System"; disable
     "Visualize Queries"). Green = pedestrian lanes.
- `station_layout.py` automates rules 1–3: it finds the edges shared by two areas and drops a marker every ~15 m, adds a
  marker at each path end (skipping the sidewalk end of stubs), and sets `m_ParentMeshes` 0 for areas below y −0.5.
  The example station: 107 border markers + 38 path-end markers.
- The W7 BigUndergroundTrainStation keeps ALL its areas at the surface (y 0). Its underground platforms are served by
  paths plus markers with parent mesh 0/1/2.

## 5. Seeing into an underground hall
- A **Clip Surface** sub-area (a SurfacePrefab with ClipArea, so GeometryFlags.ClipTerrain) removes the terrain over the
  pit. Model the pit walls, floor and tunnel mouths yourself.
- It WORKS on instances placed by the native tool, or by `cs2_place_building` on mod ≥ 0.19 (see §7).

- **Clip EVERY below-grade opening, not just the pit.** Terrain fills everything under the lot except where a Clip
  Surface cuts it. Escalators that dive under the plaza edge before reaching the pit wall were "cut off" (buried) until
  each slot got its own Clip Surface rectangle. Blender's presentation ground block (a solid slab with only the pit cut)
  shows the same thing, so keep it consistent or hidden.
- **Escalators and other structures:**
  - Start a lower escalator at the EDGE of a bridge deck, not its mid-line; otherwise it cuts through the deck and edge
    beam.
  - Keep platform canopies out of the escalator's descent path: split the canopy.
  - Keep benches and lamps off the escalator's landing line.

## 6. Shops = "Station Services" (a service upgrade), not a commercial building
- Vanilla `TrainStation03 Services` is a `BuildingExtensionPrefab` (`m_ExternalLot: false`, its mesh inside the parent
  lot). It has:
  - `ServiceUpgrade.m_Buildings = [station]`
  - `TransportStation.m_ComfortFactor +0.3`
  - `Workplace 4`
  - a `CompanyObject` that is COSMETIC: it picks which company props show, not a trading shop.
- Comfort: `TransportStationData.Combine` adds the values, and `TransportStationAISystem` sets
  `saturate(sum × efficiency)`, capped at 1.0. So station 0.5 + services 0.5 = the maximum.
- Import it with `template="TrainStation03 Services"`. Then set `ServiceUpgrade.m_Buildings`, `m_ForbidMultiple`,
  the comfort, and its own areas and access points (e.g. arcade walking areas to the lot edge).
- `list_building_types` doesn't list extensions. Probe the names: `cs2_asset_configure name="<X> Services" save=false`.
- Real trading shops would need a separate commercial (signature) building on its own lot.

## 7. Import / configure / place: the gotchas, in order
1. First import from a LOADED city. `template=TrainStation01` (ExistingPrefab) gives vanilla train-station gameplay and
   keeps its train build-menu group.
2. One configure call: `remove_components=["Unlockable","UnlockOnBuild","CompanyObject","ObsoleteIdentifiers"]`,
   `add_components=["ObjectSubNets","ObjectSubObjects","ObjectSubAreas"]`, plus `set` for all three lists in the SAME
   call. Adding without setting gives an NRE in GetDependencies.
3. **Sub-areas take effect only on NEW placements**, by the native tool or by `cs2_place_building` on mod ≥ 0.19
   (which mirrors `ObjectToolBaseSystem.UpdateSubAreas`). Older mods create tracks and stops but no areas.
4. Area buffers are filled when the prefab ENTITY is created (`ObjectInitializeSystem`), so after changing areas,
   restart the game, then place a NEW instance. Existing instances keep the areas they were placed with.
5. Re-import (`preset=None`) needs the main menu, and even there an EXTENSION's .Geometry can stay locked ("being used
   by another process"). Then fully restart the game and import at the main menu before loading the city.
6. `set UIObject.m_Group="TransportationTrain"` failed on mod 0.15 because the name is also a FeaturePrefab. The mod
   source now resolves by field type; until that deploys, keep the template's group.
7. Test spot: a straight road flush against the FRONT lot edge (curving roads leave a gap, giving "No Road Access").
   Always dry-run, then check `cs2_building_status` and `cs2_areas`.

## 8. Textures for flat-colour test builds
- One big grid cell per material (5×5 in 1024 px), UVs at the cell centre. Thin strips bleed at distance ("stripes").
- ALWAYS ship `_ControlMask` (RGB 0, A 1). Missing, the importer substitutes an all-white one, which puts speckled
  "static" on everything (worst in rain).
- Glass goes in `<Name>_Gls.fbx` (the importer's glass template; also `_Glf` frosted, `_Glm` milk). SUPERSEDED: we used
  to put its UVs on a pale GLASS_TINT atlas cell. The glass uses no atlas (vanilla method below), and the wiki forbids
  overlapping glass UVs because the shader draws smears and dirt from them. Give it a unique 0-1 unwrap (Smart UV
  Project; unwrap big canopies by hand).
- Colours in Blender Principled are LINEAR. Picking sRGB-looking values washes everything out.
- Decorative dark bands on paving read as odd stripes in game. Avoid them.

## 8b. Modelling lessons from the entrance / Services redesign
- **Design:**
  - An all-open front reads as having no entrance. The fix: a glass curtain wall under the canopy lip, with
    brick arch portals (voussoirs, spandrels, keystone, glass fan, clock, 3D text via `text_mesh`) at the walkway stubs.
  - The canopy sweeps down to the ground on the sides with no entrance (SoFi):
    `roof_y *= 1 - s²` with `s = (|x| - sweep_from) / (half_x - sweep_from)`. The swept side closes itself, so it needs
    no wall, and it gets a footing plus a planted berm.
  - Services became glass pavilions under the sweep (brick wings looked heavy).
- **Build-script gotchas:**
  - A rounded-rectangle outline made by sampling angles has its STRAIGHT edges as single long segments. Resample to
    ~3 m before building per-segment walls or mullions; otherwise whole walls vanish when a skip test hits one segment.
  - SUPERSEDED (see "Glass glitter" below): don't build glass as two reversed faces. Build thin closed slabs
    (thickness, as the wiki asks; needed for camera collision too). Run `bmesh.ops.recalc_face_normals` on closed
    solids built from hand-made prisms.
- **Camera-move artefacts: flicker and shimmer on fences and canopy members.** Causes and
  fixes:
  1. Glass balustrades inside the `_Gls` mesh together with the canopy glass. Transparent layers in ONE mesh aren't
     depth-sorted, so they flicker. Fix: opaque open railings (bronze top rail, dark mid rail, posts every 2.4 m); keep
     `_Gls` for the few big glass surfaces.
  2. A glass sheet running THROUGH the middle of the diagrid members. First fix, glass 4 cm ABOVE the member tops, made
     it WORSE in game: glittering speckles over the whole roof. At game camera distances the depth buffer can't separate
     surfaces a few cm apart. **Rule: keep different surfaces ≥15–20 cm apart.** Final fix: glass 20 cm BELOW the member
     bottoms, smooth-shaded. A concentric-ring glass mesh that is flat-shaded shows a big diagonal "X" crease toward the
     corners; smooth shading hides it.
  3. Glossy, metallic thin round members sparkle (specular aliasing). Fix: roughness ≥0.55, metal ≤0.45.
  4. Coplanar faces of different materials (voussoirs flush with brick piers) z-fight. Fix: 6 cm proud.
     Double-sided glass: offset the reversed face by 3 cm.
  - Missing LODs also add distance shimmer; build LOD1/LOD2 for the final version.
- **VANILLA GLASS METHOD (verified with `cs2_mesh_materials`, mod 0.18):**
  - `BotanicalGarden01`'s glass is a `_Gls` surface with template `DefaultGlassDoubleSided`, shader `BH/GlsShader`, and NO
    textures: the shader defaults do the glass. Its main material uses `DefaultCutout`, so glazing bars come from texture
    alpha rather than modelled members.
  - Do the same: one `<Name>_Gls.fbx`, no `_Gls_*` texture maps. `_Glf` resolves to the SAME template, so a "frosted"
    look only ever came from custom textures, which were hard to see through.
  - The official wiki agrees: no glass textures, no material, thickness instead of single planes, non-overlapping UVs.
    It calls the shader single-sided (no back faces), but our decompile shows a double-sided template; trust the
    decompile.
  - **Water (`_Wat`) enclosed by `_Gls` glass is not rendered** (wiki). Keep fountains and pools out of glass halls, or
    leave the glass open above them.
  - The custom glass textures below were an experiment that did not remove the glitter. Prefer the vanilla method.
- **Glass glitter (from the decompiled `Colossal.AssetPipeline`):**
  - `*_Gls` meshes get the surface `#defaultTransparentDoubleSided` (template `DefaultGlassDoubleSided`), so glass is
    ALREADY double-sided. Never add reversed duplicate faces: two transparent layers a few cm apart glitter.
  - (Experiment only; prefer the vanilla method above.) Glass takes its OWN textures by the pattern `*{_Glass}{Texture}`: `<Name>_Gls_BaseColor/_MaskMap/_Normal/_ControlMask.png`
    (repeat-wrapped). Ship a uniform set: pale tint with alpha ~0.3, smoothness ~0.8, flat normal, and a ControlMask
    with RGB 0, A 1. Without it the glass gets template defaults (suspected white ControlMask, i.e. the same "static").
- **Fast iteration loop (mod ≥0.17):**
  - Mesh-only changes: `cs2_main_menu`, then `cs2_asset_import preset=None` (no lock at the menu), then
    `cs2_load_save "<test save>"`. About 30 s, no restart, and placed instances update in place.
  - **Prefab entity caching (cost a full test round):** after `cs2_asset_configure` changes
    `ObjectSubNets`, the in-memory prefab entity keeps its OLD sub-net list until a FULL GAME RESTART. A main-menu
    re-import refreshes mesh, lot and areas, but a new native placement still got stale sub-nets (old street stubs), so
    walkways and areas didn't line up: 378 "No Pedestrian Access" icons.
  - Rule: any change to sub-nets, sub-objects or areas means configure, then `cs2_restart_game`, then BULLDOZE any
    existing instance and make a NEW native placement. Verified: after the restart plus a fresh placement, 0 pedestrian
    icons. An instance that's already placed never picks up new nets or areas. Before the user places, verify with `cs2_building_nets` on the new instance (stub positions in local
    coordinates).
  - The main-menu re-import worked ONCE per game session: on the 2nd menu round-trip the main asset's `.Geometry` stayed locked
    (IOException). Then use `cs2_restart_game`.
  - Prefab-entity changes (areas) or a new mod DLL need `cs2_restart_game`. A Steam launch stops at the Paradox Launcher
    (Play click).
- **Which edge faces the street:** the game's front is the building's local +z edge (the lot WIDTH edge). To make
  another side the street front, keep modelling in a DESIGN frame and rotate at output:
  - Gameplay lists (nets, objects, areas): `(x, z) → (-z, x)`, `yaw' = yaw - 90`.
  - Lot `[depth, width]`.
  - Meshes in Blender: -90° about Z before the FBX export.
  - Example: lot 14×19 with the arch entrance end as the front.
- **Enclosing a plaza:** sims follow walking areas, not glass. To make them use the doors:
  - Clip the walking areas to the wall outline; rounded corners are sampled on the arc, with points shared between
    neighbouring strips.
  - Give each door a small vestibule area from the wall to the lot edge, plus a street stub.
  - A straight glass fence looked poor. Better: the canopy comes down all round without touching, and the
    glass rises to meet its lip.
- **Arches:** tall brick portals poking above the canopy edge looked "out of place". Arches pulled back
  UNDER the lip, flush in the curtain wall with glass above them, and the name lettering on the canopy edge, read far
  better.
- **Upgrade areas:** when the Services walking areas change, set `ObjectSubNets.m_SubNets: []` to clear old stubs. Areas
  update only on a NEW placement of the upgrade, so remove and re-add the upgrade after re-importing; the station itself
  can stay.
- Model it with the scripts in Blender via MCP (not by hand), so the geometry follows the layout JSON. Check with
  `qa_render` views (front, side, interior eye-level) before exporting.

## 8c. LODs for a big flat-colour asset
- **No elevation bake needed.** When LOD0 uses a flat-colour atlas (one cell per material), LODs are simplified geometry
  with the SAME materials, so the cells and colours match LOD0 exactly. `scripts/build_lods.py` execs `build_station.py`
  (all helpers plus the layout), then rebuilds each part coarser into `CS_Station_LOD1/2` and `CS_Services_LOD1/2`.
- **Recipe:**
  - Diagrid members become flat up-facing ribbons (2 tris instead of 12; ~0.6 m wide at LOD1 so the roof doesn't fade).
    Give them their own mesh key so `recalc_face_normals` can't flip open faces.
  - A coarser glass shell (fewer rings and segments) and 9 m / 24 m glass wall panels.
  - Pit, platforms, galleries and escalators as boxes; arches as brick blocks.
  - Drop railings, mullions, benches, clock and lettering.
- **Glass per LOD:** `<Name>_LOD1_Gls.fbx`. The importer's name order is `Theme_Name[_LOD#][_Material]`, and the
  validator reports `lod: 1, submesh: _Gls`, so distant LODs stay see-through.
- **Textures per LOD:** `<Name>_LOD1_{BaseColor,Normal,MaskMap,ControlMask}.png`, NEAREST-downscaled copies of the LOD0
  atlas at 512. The validator wants at least 512.
- **Result:**
  - LOD0 49.4k tris, LOD1 4.6k (9 %), LOD2 1.3k.
  - Import reported `render_prefabs: 3`.
  - Services: 2.6k / 288 / 192.

## 8d. Native furniture that sims USE + park lawns
- **Vocabulary.** Copy it from a vanilla park: `cs2_asset_configure name=BotanicalGarden01 describe=true
  describe_components=["ObjectSubObjects","ObjectSubAreas","ObjectSubLanes"]`. It gives:
  - Seating: `GardenBench03` (dynamic: sims sit), `TableSetCommercial01..06` and `TableSet02` (café sets).
  - Lighting: `LightpolePark03` (lit at night), `GroundLightMidRange01`.
  - Features and fittings: `SmallFountain02`, `Kiosk01/03`, `Trashbin01`, `FlowerPot01`.
  - Greenery: `TreeCityRandom01`, `BushCityRandom01`.
  - Areas: `Grass Surface Placeholder`, `Tiles Surface 02`.
  - Lanes: `HedgeLow Placeholder`.
  - `cs2_list_object_types` also returns MOD props (Brickworks, BXP, Pack10, branded ones); avoid them.
- **Placement.**
  - Dynamic props connect to the NEAREST area: put benches just inside a walking area or park edge. A prop's front is
    local +z.
  - Parent −1 outdoors on terrain; parent 0 (plus y) on the station's own floors, galleries and platforms.
- **Lawns.**
  - `Park Area` (native: sims idle and sunbathe) plus a `Grass Surface Placeholder` inset 0.3 m.
  - Include `Park Area` in the shared-border marker generation.
- **Don't walk through walls.** Carve solid features out of the walking areas: Services pavilions left walkable gaps
  plus a back lane.
- **Layout that worked well:**
  - Front = an uncluttered paved forecourt setback (lamps at the street edge only).
  - Lawns with benches go on the LONG sides.
  - The lot grows to fit (18×21), with content offset via `game_offset_z`.
- **Mod 0.19:** `cs2_place_building` now mirrors `ObjectToolBaseSystem.UpdateSubAreas` (placeholder resolved to its
  first variant, N+1 closing node, `GetFirstNodeIndex`), so stations can be placed and tested WITHOUT the build menu.
  VERIFIED: 77 areas, 0 pedestrian icons, Park Areas + Grass placeholder + Clip Surfaces all created.

## 8e. Late-stage polish gotchas
- **One ground surface family.** A darker `Pavement Surface 01` rectangle under a rounded building shows its square
  corner outside the curve ("an edge sticking out"). Use the SAME surface as the surrounding walks and forecourts
  (`Tiles Surface 02`) so the only visible edge is the building's own outline.
- **Trim the floor slab to the building outline.** A full-lot paving box pokes out past rounded corners. Build the slab
  as trapezoid strips following the enclosure arc (`plaza_paving()` in `build_station.py`: strips across x, z edges
  from `encl_z(x)`), with escalator slots left open. Use the same function in the LODs.
- **Blender viewport ≠ game when stale collections overlap:**
  - Hide the LOD collections (layer collection `hide_viewport`) and have the exporter read `o.data` directly (hidden
    objects don't evaluate).
  - Hide or remove the old presentation ground: it was a solid block with only the pit cut, which made escalators look
    buried.
  - Reviews happen in Blender too, so keep the viewport truthful.
- **Setbacks and entrances.** A good layout: an uncluttered paved forecourt at BOTH arch ends (8 m each), lawns on the
  long sides, and entrances on all 4 sides (3 arches per end, plus a glass door per long side with a lawn path to the
  side street). Content offset: `game_offset_z = -(front_x + back_x) / 2`.
- **Platform furniture.** Bridge escalators land on the island centre line: keep benches and lamps at `cx ± 3.2` and
  off the landing.

## 8f. Track sets as UPGRADES (add-ons)
Platforms and track sets can ship as upgrades, like Station Services, so the player can add them as needed.
- **Proven by mods.** Check `.cache/Mods/pdx_mods/*/assets/**/*.Prefab` for a `BuildingExtensionPrefab` with
  `m_SubNets`, e.g. `MSS_AddSubwayTracks_*` (1 subway track + stops) and `CentralStation Extra Platforms` (56 nets,
  54 areas, 448 objects). The components:
  - `ServiceUpgrade` (`m_Buildings` = [station])
  - `ObjectSubNets`, `ObjectSubObjects` (stops + markers), `ObjectSubAreas`
  - `TransportStation` (comfort 0)
  - `m_ExternalLot` false, position 0
- **Layout: one source, several prefabs.**
  - Every net, object and area gets `_g` = the group of the prefab that owns it (`CUR[0]`).
  - A border marker between a base area and an add-on area belongs to the ADD-ON, so the base works alone.
  - Path-end markers belong to the path's group.
  - The output splits into `sub_*` (base) and `addons[g] = {name, title, description, sub_*}`.
- **Model:**
  - The add-on's visible parts (island platform + canopy + escalators, or gallery stair DOORWAYS with coloured lintels)
    build in `GROUP = g` into collection `CS_Addon_<g>`.
  - The exporter adds one asset folder per add-on (no LODs; they're small).
  - The base LODs include only base parts.
- **Game:**
  - First import of each add-on: `template="TrainStation03 Services"` from a LOADED city.
  - Then configure: remove `CompanyObject`; add the 3 list components; set `ServiceUpgrade.m_Buildings = ["<Name>"]`,
    `m_ForbidMultiple`, comfort 0 and the lists.
  - Restart; the player builds the upgrades from the station's upgrade panel (native, so areas get created).
  - A `game_cycle.py` script in the project can automate it.
- **Example split (lot 18×21):**
  - Base: the station plus the widthwise train island (2 train tracks).
  - + Subway Platform: widthwise subway island (2 tracks).
  - + Lower Subway Hall: lengthwise, 3 subway tracks (island + side platform).
  - + Lower Train Platform: lengthwise, 1 train track (side platform).
  - + Station Services.
- **If you revert to static tracks:** keep the machinery behind a `STATIC` switch
  (`GRPF(g)` → "base") instead of deleting it.
- **Shared tracks:** a platform face per track side. With odd counts, use a side platform rather than two stops on one
  track.

## 9. Native trees and props
Vanilla props go in as sub-objects by name, like the W7 stations do: `TreeCityRandom01`, `BushCityRandom01`,
`GardenBenchRandom01`, `StreetlightDouble01`, `BikeRackRandom01`, `KioskRandom01`, `TrashbinRandom01`. Add them to the
layout's sub_objects (parent -1 for surface props). No Asset Editor is needed; the editor saves the same component lists.

## 10. The street link
- W7 stations show NO "No Pedestrian Access" icons; the example station had ~327 (the whole network was an island), even with
  walking areas out to the lot edge and access markers 0.5 m inside.
- `ConnectionWarningSystem.AddPathfindElements`: external nodes come from (a) the building's SUB-NETS' connected edges
  and nodes that belong to other owners, and (b) spawn/access locations whose connected lane is external.
- `SpawnLocationConnectionSystem`: an access marker takes the NEAREST pedestrian lane (the owner's lanes first, then
  nets and areas within 32 m). Inside our own walking area, that's our own area lane, so it's internal.
- First fix tried: a short **Invisible Pedestrian Path stub from a walking-area vertex ON the lot edge 2 m out onto the
  sidewalk** at every entrance, so the dead-end local-connects to the road sidewalk. `station_layout.py` adds 6 on the
  end streets; Services adds 2 at the arcade ends.
- Changing sub-nets or areas needs: configure, then a game restart, then a NEW native placement.
- Result of the stubs: 327 → 272 icons, which only helped at the street. The real causes were the §4 wiki rules (no
  markers between areas or at path ends, and underground areas at parent −1 snapping to the terrain). Fixed:
  **VERIFIED in game, 272 → 0 "No Pedestrian Access" icons** (only the expected "No Train Access" icons at the open
  track ends remain, 2 per track end).
- The game log line `Invalid prefab sub-area nodes removed: 8 => 7` = the game drops one collinear node from an area
  (here the subway hangarounds with stair breakpoints). Harmless once markers do the joining.

## Platform layout rules: island + side platforms
- Layout per level: `[side platform 8 m] T T [14 m island] T T [side platform 8 m]`, pairs 8 m apart (`PAIR`),
  track centre -> platform edge `EDGE` = train 1.7 / subway 1.5. Generated by a `lay_level()` helper in the layout script.
- An island with 2 tracks per side REQUIRES side platforms for the outer tracks: a stop can't serve a far track across
  a near one (vanilla W7 does islands between doubles). One stop per track, on the platform touching it.
- Doubles can't mix modes. Each unconnected track end shows a "No Train Access" icon (2 per track) until the player
  joins it to a line - expected.
- Layout JSON `islands_A` entries: {cx, x0, x1 (real platform edges), mode, group, faces[[track, side, mode]]};
  build scripts draw the yellow edge strip only on track-facing sides (side>0 -> x0, else x1-0.4).
- GOTCHA: the concourse galleries span GIN = PIT_X-7.2 .. PIT_X and bridges -GIN..GIN. Every platform centre (the
  bridge-escalator x) must lie INSIDE -GIN..GIN and the galleries must not overhang platforms. Deriving PIT_X from the
  platforms (25.5) put one side-platform escalator off the bridge -> 12 "No Pedestrian Access". Keep PIT_X >= outer
  platform edge + 7.6 (= 30 here).
- GOTCHA: SINGLE track nets are 8 m WIDE (Twoway Train/Subway Track; doubles are 12 m with tracks at +/-2). Parallel
  singles need centres >= 8 m apart or their ballast overlaps visibly. Check widths with
  cs2_list_road_types kind=track before spacing tracks.
- Pairs of singles still look bunched in game even at 8 m: use the DOUBLE net for every same-mode pair (one 12 m
  ballast bed, tracks at +/-2 = the vanilla look). `lay_level(y, along, half, seq)` takes a cross-section list of
  ("plat", w) / ("single", mode) / ("double", mode), centres it and derives stop faces from the neighbours. Mixed pairs
  can't be doubles: with 1 train + 3 subway use `T [island] SS [island] S`.
- Example with 3 doubles per level: `[side 8] ==S== [island] ==T== [island] ==S== [side 8]` = 6 tracks + 6 stops per
  level. The pit grows with it: PIT_X = outer platform edge + 7.6 = 40.3.
- GOTCHA: lower-level stairs start on the gallery at x = GX, z = platform centre - they must stay clear of the plaza
  gallery escalators that land at GX, z +/-30 (27.5..32.5). With 14 m islands the lower side platforms centred at |z|
  28.7 (clash) -> use 20 m islands on that level (side platforms at |z| 34.7). Guard it with an assert in the layout.

## Open-air canopy variant (tried, then reverted)
An open-air version (canopy sides not touching the ground, one parabolic cross-section) was tried and reverted to the
closed glass enclosure + brick arches. Techniques worth keeping:
- Canopy: `sweep_z_from 0` turns the whole cross-section into one parabola (21 m crown -> 7 m long-side lip = an arched
  back); `edge_side` 14 m keeps the street ends high over the gates. Give the plaza slab its own outline
  (`canopy.plaza`) so it doesn't spread over the forecourt when the canopy grows.
- `side_pillars` (layout) = big tapered drums under the long-side lip, every 22 m; they can stand in a 4 m side walk
  (peds pass through them - acceptable at 2.2 m wide).
- Modern gates: low marble plinth, tapered marble pylons with titanium fins, tapering parabolic titanium arch, crossing
  titanium blade with lettering, hung clock + steel halo on the main gate. Fluted columns, entablature and sunburst read
  as too classical.
- New atlas materials (marble, titanium): the atlas grid grows automatically.

## Filling a whole city block + an integrated park
- Before growing a station, save the smaller version FIRST as its own asset (copy the project, replace names in the
  scripts, re-export) so both can exist.
- Measuring the site: query_roads in a box, project every segment into the station's frame (right = (-f.z, f.x),
  fwd = forward) -> road centre lines per side; lot edge = centre - half road width. Example block: 400 x 216 m -> 50 x 27.
- Pit S|T|T|T|S (14 m islands, PIT_X 61.7); lower hall S|S|T|S with 16 m islands (20 m put a stair at |z| 27 -> the
  plaza-escalator assert fired). Station body HD 72, canopy 196 x 142 m.
- PARK GRID: per long side, 6 m paths (cross paths at x = 0/+-60, promenade mid-depth, 4 m edge walk on the side
  street) as Walking Areas + native Park Area lawns in the cells between. All cells use ONE global breakpoint set
  (XB_ALL / ZB_ALL) so shared borders are identical segments on both sides -> the auto-marker pass joins them all.
  Props: jittered tree grid per lawn (TreeCityRandom01 / NA_LondonPlaneTree01 / NA_LindenTree01) with a clearing +
  fountain/gazebo + 4 benches, lamps/benches/bins along paths, kiosks + cafe tables on the side walk, hedge on the edge.
  Result: 1227 sub-objects, 189 sub-areas, 623 access markers.
- **Offline layout checker (`check_layout.py`)** - run after every layout change, before any game cycle:
  rebuilds the game's pedestrian graph (areas joined by Access Location markers on shared borders; paths joined where an
  end lies on a border AND has a marker; path ends outside the lot = street), BFS from the street, reports unreachable
  areas / paths / stops (stops must sit in a reachable Hangaround Area), and checks every Clip Surface corner lies inside
  the enclosure (else the pit shows past the building). Exit 1 on any problem. Sanity-test it by deleting markers.
- A pit "showing up on the edges of the canopy" = the NEW layout's wider pit under the OLD mesh (configure applies to
  new placements immediately, the MESH only after restart + import). Always restart + import before the user judges a
  placement.

## Small elevated train + subway interchange (example: a small interchange asset)
Script chain: `crossing_layout.py` -> `build_crossing.py` [Blender] -> `export_crossing.py` [Blender] ->
`make_crossing_atlas.py` -> `check_crossing.py` [--with-addons] -> a game script (`first` | `update`).
- Lot 14 x 8, game frame directly (front +z). Elevated train = vanilla `Double Train Track - Twoway` at y 10 with
  `m_Upgrades: ["Elevated"]` (learned from W7TrainStationElevated via describe; set as a list of enum names), nets run
  40 m past the lot ends. Side platforms (6 m) at y 10.9 on a modelled brick-arcade viaduct; escalators up from the
  street through deck slots. Parallel subway double at -12 (base); crossing subway double at -20 = ADD-ON
  (BuildingExtension via template "TrainStation03 Services", ServiceUpgrade -> the base station, comfort 0) with 4
  transfer stairs from the parallel platforms (descending away from the tracks, 14 m runs).
- Imported + configured with NO restart (first import from the loaded city; configure applies to new placements).

## Bus terminal inside a building + subway add-on (example: a mixed-use complex)
Script chain: a `bus_layout.py` -> the tower build's bus-terminal step / `build_subway.py` -> the exporter writes
`<Name>_layout.json` per asset -> a game script.
- **Vanilla pattern (BusStation03, read with describe):**
  - A one-way `Invisible Car Path - 1xOneway` loop enters and leaves at the FRONT, with its ends 3–4 m inside the lot
    edge. That is enough to join the road; no marker is needed.
  - Each bay is a `1xTwoway` connector from a lane node plus a dead-end `1xTwoway` stub pointing at the concourse.
    `Integrated Bus Stop` sits 2.3 m past the stub end: yaw 90 for x>0, 0 for x<0, parent −1.
  - Walking Area concourse between the bay rows, Hangaround strips at the gates, Pedestrian Spawn Locations on the
    concourse (parent 0).
  - Surface decals (Tiles/Pavement Surface areas) are optional.
- **One layout file for two parents:** write it in a frame tied to the MODEL (the podium centre), with lot-edge points
  as strings `"F<offset>"`. The exporter adds each parent's pivot offset and substitutes the real lot edge
  (`FRONT = LOT_D*4`). A combined asset can reuse the same terminal. Both parents must share the rotation (ROT 0).
- **Service upgrades need a service-building parent:** that is why towers carrying a terminal must be BusStation03
  clones, not office signatures. A tower without one can stay an office signature (`OfficeHighSignature01`, found via
  `cs2_menu_assets filter=Office`; `list_building_types` doesn't list signatures).
- **TRAP:** `"BusStation02 Services"` is a plain `BuildingPrefab` upgrade on its OWN lot, with
  `m_Buildings: [BusStation02]`. Cloning it turns your subway "add-ons" into external upgrades of the vanilla bus station.
  - Always clone `"TrainStation03 Services"` (a `BuildingExtensionPrefab`) for in-building add-ons.
  - Check the class with `cs2_prefab_components` right after importing.
  - Recovery: set `ServiceUpgrade.m_Buildings = []`. With the game CLOSED, move
    `%LOCALAPPDATA%Low\Colossal Order\Cities Skylines II\ImportedData\<Name>` and its loose `<Name>_*` files out, then
    re-import with the right template.
- Vanilla prop names drift; verify every sub-object name with `cs2_list_object_types` before configuring.
  `LightpoleSidewalk01` doesn't exist (use `StreetlightSingle03`), and configure fails on the first unknown name.
