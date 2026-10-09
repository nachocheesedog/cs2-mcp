# Troubleshooting: symptom → cause → fix

Everything below actually happened while building real assets (Liberty House, towers, stations, growable packs).

## Contents
1. In game: looks wrong
2. Import / configure errors
3. Export / validation
4. Blender scripting
5. Tooling & environment

## 1. In game: looks wrong

| Symptom | Cause | Fix |
|---|---|---|
| Lot outline and placement work, but **no building visible** | Raw FBX vertices in metres. The importer scales raw vertices by 0.01 and ignores UnitScaleFactor and node transforms, so the building is 1/100 size. Causes: Blender's **default** export dialog (`bake_space_transform` off, so the ×100 lands in a node scale), or `FBX_SCALE_UNITS` | Export with `apply_scale_options='FBX_SCALE_NONE', global_scale=1.0, apply_unit_scale=True, bake_space_transform=True`. `cs2_asset_validate` reports "object transform not baked" or a UnitScaleFactor problem. Check raw extents (`cs2_asset_validate`, or the repo's `server/fbx_raw.py`), not a Blender re-import, which hides the problem. Re-import with preset None. |
| Building **glows / is white-hot**, even in daytime | The `_Emissive` map renders as light day and night, and is much brighter than Blender suggests | Window emissive 0, with night windows from `_Win.fbx`. Keep deliberate highlights at ≤0.12 linear (0.35 was white-hot, 0.05 "still a bit too bright"). |
| **Windows lit in daytime** | Lit panes baked into the Emissive map | Same as above: move the windows to a `<Name>_Win.fbx` sub-mesh file. |
| **Not all windows light up** / big dark areas at night | Window mesh islands too large, or merged. One island = one window, and rooms are grouped per 3 m floor | Cut outward glass per floor and per ~3 m bay into separate islands. Don't `remove_doubles` the window mesh. Drop inward-facing back panes. |
| `_Win` object present, but **no night windows at all** | The window object was inside the main FBX; the importer only matches `_Win` on FILE names | Export it as its own file `<Name>_Win.fbx`, with no material. The validator flags `_Win` objects inside the main FBX. |
| Lit panes form **vertical stripes** up the facade | Every floor used the same U range of the glass strip, so the same panes lit on every floor | Shift U per floor by whole panes (`shift = ((k*7919) % 11) * pane_width`). |
| **Street side is the wrong end** | The game treats Blender −Y as the front (road side) | Rotate every mesh, including LODs and `_Win`, about Z at export (Liberty House: 180°). |
| Building **sits on grass** | No ground surface on the lot | Add a paved slab over the whole lot (z −0.05..0.08) with a tiled paver strip, in every LOD. |
| Build menu shows the **vanilla building's picture** | `copy_from` copied the template's `UIObject.m_Icon` / `SignatureBuilding.m_UnlockEventImage` | Set both to null, then set our own `icon` (mod ≥ 0.14). |
| **No picture / no description** in the build menu | No icon or locale assets | `<root>_build/<Name>_Icon.png` + `<Name>_meta.json`. `cs2_asset_import` applies them; or run `cs2_asset_configure icon= title= description=`. |
| Building **locked** until a milestone | Template's `Unlockable` component | `remove_components=["Unlockable"]`. |
| **Colour change in Blender doesn't show** in game after re-import | In-game colours come from `make_atlas.py`'s own values, not Blender materials; `build_all` also resets materials from `MAT_SPECS` | Update `MAT_SPECS` + `make_atlas.py`, rerun from `make_atlas` including the LOD re-bake (see SKILL.md §7). |
| New colours up close, **old colours from a distance** | LOD atlases were not re-rendered | Rerun `lod_render → compose_lod_atlas → lod_build_export`. |
| Changing a podium colour also **changed the tower** | Materials shared between parts | Give the part its own material, atlas strip and `MAT2STRIP` entry. |
| Some faces come out **dark grey / wrong colour** | Material missing from `MAT2STRIP`, so it falls back to the `mullion` strip | Add the mapping. |
| Asset **invisible when zoomed out**, or popping | Broken LOD1/LOD2 (size, UVs, units) | Same unit rules for the LOD files; check them in the validator's `meshes` list. |

## 2. Import / configure errors

| Symptom | Cause | Fix |
|---|---|---|
| `IOException ... <Name>_<hash>.Geometry ... being used by another process` | The loaded city holds the asset's geometry file | User quits to the main menu (or restarts the game), import with `preset=None`, reload the city. |
| Import at the main menu: no prefab reported, lot not set; log shows `KeyNotFoundException 'Any'` in FindIt `ActivatePrefabTool` | The Find It mod crashes when the importer activates the placement tool at the main menu. The asset *was* saved | First imports from a loaded city. Main-menu imports only with `preset=None`. |
| `prefab not found: <Name>` from configure/place | `PrefabID` includes the asset hash, so name-only lookups miss imported assets | Fixed in mod 0.12 (scans `PrefabSystem.m_Prefabs` by name). On an older mod, update it. |
| Pipeline log says "Completed ... Errors 0" but things failed | Errors are logged per item, while the group summary still says 0 | The server greps `[ERROR]`/`Exception` lines into `errors` and sets `ok:false`. Read them. |
| `cs2_raw` times out on import | 90 s client timeout | Use `cs2_asset_import` (long timeout) or the repo's `server/bridge_call.py`. |
| `NullReferenceException` in `ObjectSubObjects.GetDependencies` ("Error when initializing prefab") after `add_components` | A freshly added component has null arrays, and the game loops over them unchecked | Add the component and `set` its list in the SAME call. The mod source (post-0.15) now starts added arrays empty. A later `set` re-initializes the prefab cleanly. |
| Dry run clean, but the placed building shows "No Road Access" | The front lot edge doesn't touch the road (curving road, gap of about 10 m); the dry run didn't flag it | Pick a spot with a straight road flush against the front edge. Check `cs2_building_status` after placing. |
| New tool arguments (e.g. `icon`) rejected | This Claude session's MCP server started before the schema change | Start a new session, or call the bridge directly with `bridge_call.py`. |
| Mod feature missing although the code has it | The game loads the mod DLL only at startup | `dotnet build -c Release` deploys; the game must restart. Check the version with `cs2_status`. |

## 3. Export / validation

| Symptom | Cause | Fix |
|---|---|---|
| LOD elevation atlas overflows (views wider than 2048) | Pixels-per-metre too high for S+E+N+W+Top widths | Lower PPM (Liberty House 9.5 → 8.8), or pack the views in two rows. |
| Validator: "main mesh needs exactly one material" | Source materials leaked into the export object | Collapse to one material named like the mesh; all faces index 0. |
| Validator: "loose files in project root" | Notes, JSON or icons in `cs2\` | Move them to `cs2_build\`. |
| Validator: textures not square / not power of two | Wrong atlas size | 512–4096 square (2048 used). |
| Asset-folder PNG treated as a texture ("unknown suffix") | Icon or other image inside the asset folder | Icons live in `cs2_build\`. |

## 4. Blender scripting

| Symptom | Cause | Fix |
|---|---|---|
| Baffling NameErrors / wrong values after `exec()` of several scripts | `exec()` shares one namespace, so scripts clobber each other's names (e.g. a constant `REVEAL` vs a material index `REVEAL`) | Use unique names per purpose (the material index became `RVL`). Use distinct runner-loop variable names. |
| All procedural textures silently gone | `ShaderNodeMix` has three outputs all named "Result" | Link by identifier `Result_Color`. |
| Recreated material leaves other meshes blank | Removing a material empties slots elsewhere | Rebuild dependents, or get-or-create materials (`get_mats()`). |
| `raise SystemExit` kills the MCP call | It's inside `exec()` | Use conditionals. |
| Blender MCP "Cannot connect to Blender at localhost:9876" | Blender isn't running or the add-on isn't started | For renders or exports, run headless: `blender.exe -b <file>.blend -P <script>.py` (Blender 5.2 in `C:\Program Files\Blender Foundation\Blender 5.2`). Otherwise ask the user to open Blender. |
| `result` error from execute_blender_code | `result` must be a dict | `result = {...}`. |

## 5. Tooling & environment

| Symptom | Cause | Fix |
|---|---|---|
| Python `SyntaxError: (unicode error) \N...` in a string | Windows path or text with `\N` / `\U` in a normal string | Raw strings, forward slashes, or the Edit tool instead of Python-generated edits. |
| JSON args with Windows paths break on the command line | Backslash escaping | Use forward slashes (`C:/Users/...`); the tools accept them. |
| PowerShell multi-file find/replace corrupted scripts | `@(@('a','b'))` flattens, so pairs index characters | Use the Edit tool. |
| sed edit dropped a backslash (`$(CS2Managed)Colossal.UI.dll`) | Shell escaping | Use the Edit tool for anything containing backslashes. |
| Decompiler (`ilspycmd`) refuses to run | Built for an older .NET | `DOTNET_ROLL_FORWARD=Major`. |

## 6. Station sub-areas & pedestrians (and other in-game issues)

| Symptom | Cause | Fix |
|---|---|---|
| Station placed; `cs2_areas` over it returns NOTHING (no walking areas, no Clip Surface, no pavement); the open pit shows grass | `ObjectInitializeSystem` fills a prefab's SubArea/SubAreaNode buffers only when the prefab ENTITY is created. Adding `ObjectSubAreas` to an existing prefab via `asset_configure` never refills them. (Sub-nets and sub-objects use other paths and DO work live.) | Set the areas, then reload the prefab: quit to the main menu and re-import with `preset=None` (or restart the game). Then load the city and check with `cs2_areas`. |
| Dozens of "No Pedestrian Access" icons on a station (`cs2_building_status`) | The building's pedestrian elements form an island with no link to the street (here: the areas didn't exist, see above) | Check that the areas exist first. Rules: path ends and Pedestrian Access Locations must lie ON an area border; neighbouring areas must share border vertices (no T-junctions); polygons are CLOCKWISE seen from above (vanilla/W7 winding). `station_layout.py` has `rectv()` plus a checker. |
| `set` fails: "X is a FeaturePrefab, field needs UIGroupPrefab" | Prefab names aren't unique across types ("TransportationTrain") | Fixed in the mod source (a type-aware lookup, ships in the next deploy). Until then leave the template's value (ExistingPrefab keeps the UIObject group). |
| Odd stripes across flat-coloured floors in game | (a) decorative dark paving bands read as stripes; (b) a flat-colour atlas made of thin strips bleeds between neighbours at lower mips | No bands. The atlas uses big grid cells (one ≥200 px cell per material, UVs at the cell centre). |
| Building placed with `cs2_place_building` has NO sub-areas (walking/hangaround/clip/surfaces) even though the prefab holds them | The mod's placement writes its own CreationDefinition + ObjectDefinition and mirrors only the object tool's sub-nets. Sub-areas are created by `ObjectToolBaseSystem.UpdateSubAreas`, which it doesn't replicate. | Place from the game's build menu (the native object tool) until the mod's placement mirrors UpdateSubAreas or drives the native ObjectToolSystem. |
| Speckled / grainy "static" on every surface (worst in rain), glass renders as grey sparkle | No `<Name>_ControlMask.png`, so the importer substitutes an all-WHITE ControlMask, which switches weathering/wetness overlays on everywhere | Always ship a ControlMask: RGB 0, A 1 (snow allowed), like Liberty House. `make_station_atlas.py` writes one. |
| Import log: `Error importing <Name>_Win. Skipped...` IndexOutOfRangeException in `InteriorMapping.Window.DetermineRoom`, plus `DetectModelNeedsAlphaClip: can't detect, model <Name>_Win has no UV0`; no night windows | The `_Win` mesh was exported WITHOUT a UV layer; the WindowsPostProcessor reads UV0 per window island | Give every window quad its own UV (0..1 square per island is enough), re-export, re-import with preset None |
| "No Pedestrian Access" icons on a residential/commercial asset right after placing it (e.g. 4 icons) | Native BENCH sub-objects (GardenBench03 etc.) carry ActivityLocation + SpawnLocation: their sitting spots need a walkable area; lamps/trees don't | Add a Walking Area over the space the benches stand in, reaching the lot edge, + Pedestrian Access Locations 0.5 m inside the edge (vanilla pattern, stations.md 4). Verified 4 -> 0 icons. ALSO chairs (ChairRandom01 -> Chair01-03, ChairCommercial*), TableSetCommercial* (one house design had 24 icons from garden/terrace chairs). Generic fix in the yard-layout script: seats outside every nav area get a Hangaround patch + a Restricted Spawn Location inside it (links to the building like a row house's back garden), and style-drawn Walking/Hangaround areas get one too |
| Build-menu picture turned into the vanilla template's after `cs2_asset_configure copy_from=...` | copy_from copies UIObject (m_Icon) AFTER the import had applied your icon | Re-run configure with icon/title/description after any copy_from |
| Parking / driveway into a building | Real road nets inside a lot are risky (sub-net placement crashed the game once) | Vanilla ParkingHall04 pattern: `Invisible Road Path - 1xOneway` loop entering 2 m inside the lot edge (auto-joins the street), `ParkingLotDecal0x` sub-objects 5.2 m off the lane (spots face the lane), underground nodes/decals parent mesh 0, Clip Surface over any open ramp cut. Verified connected in game |
| `_Gls` glass sub-mesh looks grey or opaque | The glass mesh had no UVs (or all of them collapsed onto one spot) | Give the glass a unique, non-overlapping 0-1 unwrap (the wiki forbids overlaps, and the shader draws smears and dirt from the UVs). No material and no `_Gls_*` textures: the vanilla method in `stations.md` §8b. (The old fix, UVs on a GLASS_TINT atlas cell, is superseded.) |
| Water (`_Wat`) pool or fountain invisible in game | It sits inside a `_Gls` glass enclosure: water enclosed by glass is not rendered (official wiki) | Open the glass above the water, or move the water outside the glass volume. Also model the pool floor in the main mesh, since water is see-through. |
| Odd plinth/foundation under the building, or a sunken part filled in | The game grows a foundation from vertices/edges at exactly z = 0 (wiki) | Move edges off z = 0 on buildings with visible below-grade parts. Downward-facing faces at z = 0 cancel the foundation. |
| Walking areas exist but the whole pedestrian network is STILL an island, even with shared vertices and areas out to the lot edge (a large station: 272 icons, including rows over the canopy) | Per the official wiki: areas join ONLY through a Pedestrian Access Location on their shared border; paths join areas only through a marker at the path end; underground areas left at parent mesh −1 snap to the TERRAIN height | Markers on every shared border and path end; `m_ParentMeshes` 0 on every underground area node (see stations.md §4, "OFFICIAL RULES"). Re-place natively after a restart. |
| Walking areas exist but the whole pedestrian network is an island ("No Pedestrian Access" on every element) | The areas stop short of the lot edge. The street link is made where a navigation area touches the building LOT boundary (W7/vanilla walking areas reach the lot edge; access markers sit about 0.5 m inside) | Run the entrance walking areas to the lot edge; put Pedestrian Access Locations 0.5 m inside. |
| Station shows hundreds of "No Pedestrian Access" icons although the layout is correct; `cs2_building_nets` shows OLD stub positions | The prefab entity caches sub-nets, sub-objects and areas from game start; configure + menu re-import doesn't refresh them. The placed copy was also old | Configure, `cs2_restart_game`, import, load, then BULLDOZE and place a NEW copy (stations.md §8b). |
| Game hangs (freezes) during a scripted menu/load | `main_menu` / `load_save` sent while the game was still settling after a load or boot | `gamesession.settle()` first (built into `cs2_main_menu` and `load`). If already hung: `taskkill /IM Cities2.exe /F`, then `cs2_restart_game`. |
| Escalators/stairs from street level look "cut off" or buried | Terrain fills the slot under the plaza edge (the Clip Surface only covered the pit) | Add a Clip Surface rectangle over every below-grade opening. |
| A raised plate or darker square corner shows outside a rounded building | A full-lot paving box, or a different ground surface (`Pavement Surface 01`) under the rectangle | Trim the slab to the outline; use the same surface as the surrounding walks (`Tiles Surface 02`). |
| Blender viewport shows clipping or buried parts that the game doesn't | LOD collections or old presentation geometry overlap LOD0 | Hide the LOD and presentation collections; the exporter reads `o.data` so hidden LODs still export. |
| Relaunch never reaches the menu | A Paradox Launcher Play click is needed; a direct `Cities2.exe` launch hangs | Ask the user to click Play. Don't launch the exe directly. |
| Game glitches/hangs right after the cycle's restart | The user clicked **Continue** at the main menu while the script was idling there before its own load, so two loads (or a load + an import) raced. | Game-cycle scripts: 3 s settle at the menu, imports (~1 s), then load at once; if a city is already loading or loaded, ADOPT it (wait for it, no second load_save). Tell the user they don't need to click Continue. |
| Camera can't zoom under a glass roof / into the building (the Botanical Garden lets you) | `Game.Rendering.CameraCollisionSystem` raycasts the mesh TRIANGLES (Physical objects), pairs entering (front) faces with exiting (back) faces, and treats an unpaired hit as solid all the way to the end of the ray. A one-sided glass sheet (our old `dface`) or open ribbon = a solid lid. | Make every surface a CLOSED volume: glass panes = thin slabs (front + back 6 cm apart + edge strips), the roof glass = double shell + rim, LOD ribbons = thin closed strips. Check in Blender: 0 boundary edges per mesh (bmesh `e.is_boundary`). Decompile with `DOTNET_ROLL_FORWARD=Major ilspycmd -t Game.Rendering.CameraCollisionSystem Game.dll`. |
| Model details (lawn edging, beds) sit metres off the game's lawns / paths; grass where paving should be | The prefab's sub-areas / nets / objects came from an OLDER layout than the mesh: the layout changed after the last `asset_configure`, and the restart only re-imported the mesh. | Configure from the CURRENT layout immediately before every restart + import (build this into any game-cycle script). Check the placed instance: `cs2_areas` filtered by owner = building id vs the layout's `sub_areas` (Grass Surface Placeholder becomes "Grass Surface 0x" in game). Then bulldoze + re-place. |
| Rail curves: 40 m kinks inside a "350 m" arc; game accepts but trains crawl | Cubic control length for an arc of angle q is k = 4/3·tan(q/4)·R PER PIECE — using the full turn's q (or the half-turn's q with tan(turn/4)) doubles k and loops the curve. | Build curves as true circular arcs split into <= 10 deg cubic pieces with k = 4/3·tan(q/4)·R; verify every piece's minimum radius numerically (sample curvature) before building. Realistic targets: >= 450 m urban, 900 m+ open line; grades <= 2.5 %. |
| Elevated rail dry run: "Tight Curve" / "Steep Slope" / "Overlap Existing" at road crossings though the route clears them | Every piece END becomes a node (= a pillar) — ends landing on or near a road (or snapped onto it with a big snap_tolerance) break the span. | Keep snap_tolerance 0.5 (exact joints still snap), pick break points >= 14 m from any road centreline (nudge along the arc), use pieces <= ~140 m so the height above terrain is honoured. |

## Brick/tile texture shows only horizontal lines on some faces (in game only; Blender looks fine)
- **Symptom:** whole faces, or everything on one side of a vertical line, look like horizontal siding or streaks. The
  same export renders correctly in Blender.
- **Cause:** the CS2 importer sets every texture's wrap mode to **Clamp** (`DefaultTextureImporter`, `wrapMode =
  TextureWrapMode.Clamp`). A tile-mode UV with U outside [0, 1] sticks to the edge column, so only the V variation
  (the courses) stays visible. Blender repeats textures, which hides the problem.
- **Fix:** in the exporter, cut each face at whole-number U values and shift each piece back into [0, 1] (an `emit()`
  step in `export_build.py`). Then check that every UV of every FBX lies in [0, 1] before importing
  (`scripts/uv_range_check.py`).
- **Not the cause** (checked): tangents averaged across welded corners, UV half-precision, loose vertices.
- **Verified in game:** the fix removes the streaks.

## See-through "gaps" in a facade in game
- **Symptom:** triangular or trapezoid holes in the siding in game (you see the ground or the interior through them),
  while Blender renders look fine.
- **Cause:** faces with reversed winding. CS2 culls back faces, and Blender's default viewport and EEVEE show both
  sides. On one tower the mirrored half of each face's top triangle was built with the same vertex order as the other half,
  so its normal pointed inward. The exporter's "outward glass → _Win" test also failed on those faces, leaving them as
  dark plain glass.
- **Fix:** keep the same (u, z) winding on mirrored geometry (swap the vertex order for the mirrored half).
- **QA:** render the EXPORTED meshes with backface culling ON, with _Win in red:
  e.g. a `qa_export.py` helper run as `blender -b <proj>.blend -P scripts/qa_export.py -- <Name> 50 cull`. Holes then
  show directly.
  Also count glass faces facing the object's centre.
- **Related exporter rules:**
  - Judge "outward" per sub-volume (per slab), not per object: joint faces between slabs are real exterior where one
    slab rises above the other.
  - Don't cut window rooms into glass that rises past the roof (screens and crowns have nothing behind them). Clip
    the window pieces at the roof line and keep the rest as plain glass.

## Ground paint shows only in patches in game; Blender renders look complete
- **Symptom:** a depot asset's teal guide line and arrowheads (flat quads/tris a few cm above the lot) showed in some
  places and not others: corners and some runs visible, random stretches missing. Some white lines were also missing.
  Every Blender render looked perfect.
- **Real cause: flipped faces.** The build script ran `bmesh.ops.recalc_face_normals` over the whole mesh. Loose
  single-sided faces have no inside or outside, so it flipped about half of them DOWN (111 of 224 guide faces). CS2
  culls back faces; Blender renders both sides by default, which hid the problem.
- **Fix:** recalculate normals only on closed parts. Then force every ground-paint face (flat paint materials, or
  anything below 0.1 m) to face +Z (`if f.normal.z < 0: f.normal_flip()`). Do the same for any other single-sided
  sheet: signs, canopies made of planes, decals built as mesh.
- **Check:** re-import the EXPORTED FBX and count faces of the paint materials, or UV band, at ground level with
  normal z < 0. It must be 0. Also QA-render the export with `material.use_backface_culling = True`, which shows the
  holes exactly as the game does.
- **Misdiagnosis first:** we suspected texture MIP washout, because each thin face was mapped onto a small swatch next
  to black atlas space. That was a real, smaller risk, and we kept the fix ("palette v2": each flat paint colour gets a
  big solid band of the atlas, and paint faces sit in a tiny UV circle at its centre, so every mip reads the right
  colour). But check face orientation FIRST whenever "some pieces show, some don't".

## An upgrade shows no name or description in the upgrade panel
- **Cause:** prefabs with `ServiceUpgrade` are titled and described by `Assets.UPGRADE_NAME[<name>]` /
  `Assets.UPGRADE_DESCRIPTION[<name>]` (decompiled `PrefabUISystem.GetTitleAndDescription`), not by
  `Assets.NAME` / `Assets.DESCRIPTION`.
- **Fix:** CS2MCP mod 0.31 `SaveLocalization` writes both key pairs for upgrade prefabs. Re-apply the meta
  (`cs2_asset_configure name=<upgrade> title=... description=... icon=...`).
- Write upgrade descriptions like the native ones: say what the upgrade ADDS and how to use it (e.g. "connect rail
  lines to either end").

## Sub-net roads at the SIDE or BACK of a lot never connect ("No Car Access" with a side street right there)
- Cause (decompiled): `ReferencesSystem.AllowConnection` drops any sub-net-node -> road link that crosses a lot
  side/back edge unless the building has Left/Right/BackAccess, and `ValidateConnection` also rejects end tangents that
  face a side the access type doesn't allow. `m_AccessType = Front` (the default, and what signature templates copy)
  allows the front only. Distance is NOT the issue (any road within pathHalfWidth + searchDistance (+8 m for a zoneable
  road) qualifies).
- Fix: `cs2_asset_configure name=<X> access_type=RightCorner` (road along local -X), `LeftCorner` (+X),
  `LeftAndRightCorner`, `FrontAndBack`, `All`. Keep end nodes ~2 m inside the edge, last segment perpendicular to it, at
  road height, and closer to that road than to the front road. If the wrong side connects, swap Left/Right.
  Game restart + new placement needed. Belt and braces: also give the building a front entrance.

## Lot paving shows as grass on some placements only
- Cause: the modelled lot slab sat 0.02 m above the base; on some sites the game leaves the
  flattened terrain ~0.02 m ABOVE the building position, so the terrain drew over the slab (another site was fine).
- Fix: slab at ~0.08 m (kerb-ish), ramp surfaces blended from slab level down, ground natives (benches, lamps, trees
  in the lot) placed on the mesh at slab height (parent 0), LOD slabs at the same height. Check with
  `cs2_terrain_height` inside the lot vs the building's position y.
| Native grass/pavement surface ("Grass Surface 01", Tiles, etc.) as a sub-area doesn't show on a raised deck or roof | Surface areas are decals with `RenderedArea.m_DecalLayerMask = Terrain`; they project onto terrain only. There are no grass decal props either. | On terrain-level lots, use the surface sub-area. On building meshes, export the vanilla texture with `cs2_texture_export name="Grass Surface 01"` (mod 0.32; writes BaseColor/Normal/MaskMap PNGs) and bake it into your atlas strip in `make_atlas.py`. |
| **Invalid Shape** when placing a building whose sub-areas are fine by eye (easy to misblame on nets; it cost 3 rounds once) | `Game.Areas.ValidationHelpers.CheckShape`: every area node must be >= minNodeDistance from every non-adjacent edge of its polygon (Walking/Hangaround = Space: 0.5 m; Surface 0.375 m; Lot 4 m). Extra collinear edge vertices 0.5 m apart sat EXACTLY on the limit and float error after rotation tipped it. Nets have a separate check. | Keep polygon vertices >= 2 m apart (merge breakpoints). Check layouts offline before import: `area_check` logic = min distance node-to-other-edge per polygon, run in your layout script. |

## Night window lights switch off too soon when zooming out
- Cause: the lit windows (_Win) live on LOD0 only; the LOD1/LOD2 swap drops them. The swap distance comes from the
  mesh prefab's `LodProperties.m_Bias`. SIGN: NEGATIVE = keep detail longer. (CalculateLodLimit(mpp, bias) scales
  mpp by 2^-bias -> a positive bias RAISES every batch's MinLod threshold -> LOD0 drops out SOONER; verified in game:
  +1 made the lights vanish earlier.) -1 ~ 2x the distance, -0.5 ~ 1.4x.
- Fix: `cs2_asset_configure name="<Name> Mesh" set={"LodProperties.m_Bias": -1.0}` - applies live (no restart seen);
  re-check after a preset-None re-import (the mesh prefab may be regenerated).
| Night window lights vanish when zooming out; other buildings still glow | Only LOD0 has a `_Win` sub-mesh, so the LOD1/LOD2 meshes have no windows | Ship `<Name>_LOD1_Win.fbx` and `<Name>_LOD2_Win.fbx`. The importer groups files by module and reads the LOD from the name, so they become the LOD window sub-meshes (verified in the decompiled AssetImportPipeline). LOD1 = a copy of `_Win` if LOD1 keeps the glass; LOD2 = a coarse quad grid on the box faces (generated in `lod_build_export.py`). |
| Night windows look sparse / faint (whole floors dark) compared with vanilla towers | The importer takes each window's ROOM id from its UV: id = 5*floor(5v)+floor(5u) (5x5 cells, Blender v up); id 0 = ALWAYS lit, 24 = NEVER lit, others random. Atlas-mapped `_Win` UVs bunch islands into a few cells (some never lit). | Re-map every `_Win` island into one cell: ~35 % id 0, the rest 1..23, never 24 (a `room_cells` step in the exporter; 50 % on for LOD2 windows). The Editor's "Interior Light Intensity" (default 0.549, max 1) = mod 0.34 `cs2_asset_import window_light`. |

## Growable shows only its props/yard, no building
Cause: the import ran "ok" but the asset folder had NO FBX (a helper render mode wiped the folder, textures got copied
back) -> RenderPrefab with `m_GeometryAsset: null`, `m_VertexCount: 0`, no .Geometry files in ImportedData.
Check: grep the `<Name> Mesh_*.Prefab` for m_VertexCount; `ls cs2/<Name>/*.fbx` before importing.
Fix: re-export, then `asset_import preset=None` IN the loaded city works when there is no geometry yet (nothing locked);
the mesh keeps its CID, so every configured/cloned building picks it up.

## Zone / prefab names vanish or overwrite each other
The mod's `SaveLocalization` (asset_configure title/description) deletes EVERY LocaleAsset in the prefab's folder
(subPath) and writes one merged file named after the last prefab. Prefabs sharing a folder (the 3 TO zones, depth clones)
end up in ONE .loc - fine as long as each call merges; a failed merge loses the others. After titling several prefabs in one
folder, check the single .loc contains all `Assets.NAME[...]` / `Assets.DESCRIPTION[...]` keys. Hand-written zone prefabs
have NO locale at all until titled - subscribers without the CS2MCP mod would see raw keys.

## "No Pedestrian Access" from parking decals
`ParkingLotDecal0x` sub-objects create parking SPACES; with no driveway road (Invisible Road Path) into the lot they are
unreachable and each space raises an icon (2 decals = 12 icons). Either build the vanilla ParkingHall
driveway (see "Parking / driveway into a building" above) or leave the decals out.

## UI-engine crash (cohtml DedicatedHeapAllocator::Allocate in View_Advance)
Native crash while the UI draws: the UI heap ran out. Seen minutes after a live title/description write (the mod adds a
live localisation source, the whole UI rebinds) with many "ResourceHandler: Invalid host locations map" UI errors from other
packs. Prefer writing titles at the main menu; they load from the saved .loc at the next start anyway.

## "No Pedestrian Access" on custom growables
Copy the vanilla NA_ResidentialMediumRow01 pattern exactly: Restricted Spawn Location at the door; a Walking Area from just
outside the door (door height, m_ParentMeshes 0) down to the lot front edge (ground, -1); ONE Pedestrian Access Location
0.5 m INSIDE the lot front edge with IDENTITY rotation (not on the edge, not yawed). **NEVER an access location next to the
door** - vanilla has none there, and a marker beside the door leaves hearses + ambulances with no route to any
resident (see "Dead waiting for hearse" below). Back door: walking area into the garden Hangaround Area, joined by an access point where they
overlap (an area-to-area border, which is allowed); never one at the back door itself, never on the back lot edge (no road there). Area winding: shoelace in game x/z < 0 like vanilla. Placed buildings keep
their old sub-lists: restart, then bulldoze + regrow.

## Native crash when configuring a prefab that has PLACED copies in the loaded city
asset_configure set of ObjectSubAreas / ObjectSubObjects / m_Meshes on growables that had grown in the city crashed the
game ("Got a UNKNOWN while executing native code", mono). Rule: configure sub-lists/meshes only at the MAIN MENU or before
any copy exists; in a loaded city only touch prefabs with no placed instances. Mesh re-imports (preset None) were fine.

## Textured ground looks lit "from the side" / wrong shading on reused vanilla textures
Cause: normal maps exported from the game (cs2_texture_export, `*_material_NormalMap.png`) are ALREADY in the game's
packed form: R = 1.0 everywhere, G = B = Y, A = X. The importer packs every `*_Normal` again ((R,G,B,A) -> (1, G, G, R),
TextureImporter.CompressBC), so X becomes +1 on every pixel -> each tile shades like a wall. Blender renders can't show
it (materials there never read _Normal).
Fix: convert back to a standard tangent-space map before packing it into an atlas: x = A, y = G,
z = sqrt(1 - (2x-1)^2 - (2y-1)^2) * 0.5 + 0.5, A = 1 (do it in `make_atlas.py` when loading the exported map). Gate: in every textured block of
`*_Normal.png`, mean R and G within 0.45-0.55 and A = 1. Base colour / mask maps from the export are fine as they are.

## Package huge (GBs) for a multi-asset pack on one shared atlas
Every import stores its own copy of every texture: 100 imports = 530 .Texture files / 2.96 GB, only 5 unique
(28 MB). With the GAME CLOSED run `python scripts/dedupe_textures.py <prefix>`: points every .Surface at one canonical
texture per identical content (GUID bytes = .cid hex with nibbles swapped) and moves duplicates to
..\_texture_dedupe_backup. A 100-asset pack went 3.0 GB -> 65 MB. Re-run after every import round. Refs to _InteriorAtlas etc.
in window surfaces point at the game's built-in textures - they are not in ImportedData and are fine.
- After a preset-None RE-IMPORT the surfaces point at their ORIGINAL texture GUIDs again - files an earlier
  dedupe had moved to the backup -> textureless buildings. dedupe_textures.py now restores still-referenced textures from
  the backup before deduping; always verify afterwards: 0 surface refs pointing at files in a backup folder.
- The game loads user assets from the WHOLE "Cities Skylines II\" LocalLow folder, not just ImportedData: a
  backup folder placed there (e.g. a `_superseded\` folder) got loaded again (old growables + a removed zone reappeared).
  Keep all asset backups OUTSIDE it (e.g. `~/blender/_cs2_asset_backups`).

## Game CRASHES during cs2_asset_import (connection reset, Cities2.exe gone)
- Symptom: import of an asset with an _Emissive map; Player.log ends in `ErrorTexture:CreateTexture` <- `Helpers:DrawErrorTriangleWithColor` <- `GatherTriangles` <- `EmissiveBakingPostProcessor:Execute` on a thread-pool worker.
- Cause: a triangle whose UV footprint covers > 4 distinct light ids (emissive ALPHA) -> the importer creates a Texture2D off the main thread -> native crash. Typical: one face with UVs in two distant atlas cells (e.g. half on a lamp tile, half on a flat colour cell), or big decimated LOD triangles over a lamp strip.
- Fix/gate: run an emissive checker over the exported FBXs before importing (e.g. `blender -b -P <proj>/scripts/emissive_check.py -- <asset folders>` that counts distinct emissive-alpha ids under each triangle's UV footprint and prints OK only when every triangle covers <= 4). Never import into the user's running city without asking, and announce it to any other CS2 sessions working on the same game.

## Dead "waiting for hearse" / ambulances never come to a custom building
- **Symptom:** bodies wait forever (icon on the citizen), sick never picked up; players report it per asset pack.
- **Cause:** a **Pedestrian Access Location placed inside a Walking Area next to a door spawn**. Hearse/ambulance dispatch
  routes to the building's door spawns (a connected spawn disables the road-edge fallback); that marker cut the door off,
  so the pathfind fails and the request retries forever (`ServiceRequest.m_FailCount` climbs).
- **Rule (wiki, Common Asset Principles):** access locations ONLY on the border between two areas, or where an area meets a
  path/sidewalk (lot edge). Never at the door end of a walk. Spawn (door) markers go inside the mesh behind the door.
- **Check:** `cs2_path_test id=<building> mode=hearse` (mod 0.42) must say found=true; `cs2_service_access` shows door
  connections + request fail counts. Test every new asset type once before publishing.
- **Verify in a LIVED-IN city, not only a fresh test block:** an updated growable pack passed 139/139 in a freshly zoned
  test area, but in a long-running city ~15 % of the updated shops / corner buildings still had no hearse route, while
  row houses passed 9/9 and a vanilla control passed 36/40 (fails = vanilla rows). Cause: every failing building carried
  its access markers TWICE (6 instead of 3, pairs 0.1-0.25 m apart) - an old pre-update building that got the new marker
  set added on top of its old one (cs2_service_access "markers", mod 0.43). After bulldozing all ~1,900 old growables and
  letting the update regrow in the same streets: 675/675 shops+corners and 200/200 houses had routes.
  Lesson: after an update that changes sub-objects, OLD placed buildings can end up with duplicated markers; tell players
  to bulldoze + regrow. Entity ids are NOT creation order (reassigned at load) - don't use them to tell old from new.
  Count timeouts separately in path tests (a busy city can take > 12 s - use wait_s 40).
- **Second cause, same symptom: no door marker at all.** HealthProblemSystem only creates a hearse request when the prefab
  has `BuildingFlags.HasInsideRoom`, which needs a pedestrian SpawnLocation sub-object with activity mask 0. cs2_path_test
  bypasses that check (it routes fine to a door-less building), so ALSO check `cs2_prefab_entity name=<prefab>
  component=BuildingData` -> m_Flags contains HasInsideRoom (check every building in a pack).
| Re-placing a big asset after `cs2_bulldoze ids=[building]` fails with hundreds of "Overlap Existing" at its own elevated track / road nodes | The MCP bulldoze removed the building entity but left its sub-objects (trees, props) and the pillars of its elevated sub-nets behind as orphans. Unpausing doesn't clean them up. Seen on a large airport asset. | On mods before 0.44: don't bulldoze buildings that have sub-nets / sub-objects through the MCP. Reload the save to undo it (the save was untouched), then have the USER bulldoze in game with the bulldozer tool, which cascades properly, and place afterwards. FIXED in mod 0.44 (ported from BulldozeToolSystem.AddEntity / AddSubNets: sub-nets, sub-areas and upgrades go with the building); verified with a full airport re-place through the MCP (0 pillars left, 0 errors). A dry run reports sub_nets / sub_areas / upgrades: re-install any upgrade afterwards (extensions with taxiway joins from the game's upgrade menu - the MCP parent= path gives 'Invalid Shape'). |
| "No Pedestrian Access" icon MID-LANE on one building path (escalator / stair) although an offline graph says it is reachable | ConnectionWarningSystem flags a pedestrian lane whose PathNodes don't reach an external lane in both directions. The icon sits mid-lane (IconLocation 128). Seen only on the steepest path, a 13.4 m rise over 24 m in one run. Paths ending on borders shared by two areas worked fine, so that isn't the cause. | Locate it first: screenshot the spot (the mod can't report icon positions). Keep rises per path modest (about 0.4 or less, one level per run, like the working ones), or drop the redundant path and keep its model as scenery. Decompile: `Game.Simulation.ConnectionWarningSystem`. |
| A big lot's lawns look flat and greener than the map's grass around them (a visible rectangle) | "Grass Surface 01" sub-areas are a trimmed-lawn decal, not the map's terrain grass. | Leave lawns undecorated: with no surface area the lot shows the map's own terrain grass (and its seasons), which blends in better. Test live by deleting the owned surface areas with cs2_delete_areas, then drop them from the layout. |
| Loading an autosave crashes the game (Player.log: CRITICAL System update error during Deserialize->PostDeserialize, NullReferenceException in Game.Simulation.ClimateSystem.ApplyWeatherEffects) | Seen 4 times. Every crashing autosave was written while the game sat PAUSED right after a cs2_load_save. Saves written while the simulation ran loaded fine. Probably the saved weather-effect references are not valid yet after a fresh load. | After cs2_load_save, never leave the game paused: set_speed 0 only for the seconds an edit needs, then speed 1. After EVERY load / restart, verify the process is alive (tasklist Cities2.exe) and city_loaded, and grep Player.log for CRITICAL; if it crashed, load an older save written during play, never one of the paused-after-load autosaves. |

