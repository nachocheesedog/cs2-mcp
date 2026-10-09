---
name: cs2-asset-pipeline
description: End-to-end workflow for taking a Cities Skylines II (CS2) custom asset from an idea (usually a real building, e.g. a Toronto tower or a train station) through Blender modelling, game-ready export (trim-sheet atlas, LODs, night-lit window sub-mesh, FBX units), import into the running game via the CS2 MCP (cs2_asset_validate / cs2_asset_import / cs2_asset_configure), gameplay setup (signature/service building, households, lot, unlocks, build-menu icon + description), placement and in-game verification - with every known importer pitfall and its fix. Use this whenever the user wants to make, recreate, export, import, re-import, fix or publish a CS2 building/asset/prop/station, asks why a custom asset is invisible, too bright, facing the wrong way, missing an icon, or sitting on grass, or mentions the Asset Editor/Asset Importer - even if they only name one stage of the process.
---

# CS2 asset pipeline: idea → Blender → in game

This is the proven route (first used end to end for the Liberty House reference asset): model in Blender through the Blender MCP, export a game-ready asset
folder with scripts, then import, configure and place it in the running game through the CS2 MCP mod, without any
manual Asset Editor work. Every snag we hit is written down here at the stage where it bites, and in
`references/troubleshooting.md` as symptom → cause → fix.

Companion skill: **blender-mcp-architecture** covers *modelling* (bmesh patterns, materials, QA renders, researching real
buildings). Load it for stage 2. This skill covers everything around it.

## Working practices

- **Work inline and show progress.** Build in the conversation, render often, share the good renders, and critique
  honestly. Don't hand the modelling to background agents: they fight over the one Blender scene.
- **Confirm facts against photos before detailing.** Facade layouts, floor counts and which side a feature is on are
  easy to get wrong (the Liberty House reference needed several facade-layout and floor-count corrections).
  Before detailing, restate the layout in one or two sentences, e.g. "street end dark curtain wall, other three sides
  white 2-storey frames", and get a yes. That costs far less than rebuilding.
- **In game:**
  - Never move the player's camera unless asked (`follow_camera=false`; screenshots without x/z).
  - Never zone unless asked.
  - Pause after big builds.
  - Ask before publishing anything.
- **Files:**
  - Keep projects on a local, always-mounted drive (e.g. `~/blender/<project>`); a volume that can
    disappear mid-write risks corrupting the .blend and the exports.
  - MCP/mod additions must be generic tools, not one-off hacks for a single asset.

## Stage map

| # | Stage | Output | Gate before moving on |
|---|---|---|---|
| 1 | Idea & research | refs\ photos, storey table, facade layout | user confirms layout + floor count |
| 2 | Blender model | `<proj>.blend`, idempotent build scripts | QA renders approved |
| 3 | Game-ready export | `cs2\<Theme>_<Name>\` asset folder + `cs2_build\` (atlas, LODs, icon, meta) | `cs2_asset_validate` ok, no warnings you can't explain |
| 4 | Import | prefab in the game's user database | `cs2_asset_import` ok=true |
| 5 | Configure | gameplay components, lot, icon, name, description | `cs2_asset_configure` saved=true |
| 6 | Place & verify | building in the city | screenshots by day + night, user happy |
| 7 | Iterate / publish | re-imports with preset None; publish only on request | user says yes to publishing |

## 1. Idea & research

- Pin down *which* building and its **street side**. That matters later: the street side must face Blender −Y at export.
- Collect current photos (street level, aerial, each facade) into `refs\`. Engineering papers and planning documents beat
  press articles. Construction photos show details that articles miss.
- Count floors from photos and derive a storey table (ground floor height, podium floors, typical floor, amenity level,
  roof) that lands exactly on the published height. Count "26 floors + pool" style descriptions carefully; floor counts are
  easy to get wrong.
- Write the facade layout per side (materials, rhythm, where it changes) and confirm it with the user.
- Choose the lot: CS2 cells are 8 m. Lot = ceil(footprint / 8) per axis. Liberty House was 4×8 cells = 32×64 m.
  - **Zoned (growable) packs** have hard lots: min 1×2, max 6×6 cells, and the zoner always picks the deepest, then
    widest, lot available. Ship a range of lot sizes, with a full level 1-5 set per lot size, and stay near the
    per-zone triangle guide. All of it is in `references/wiki_buildings.md` §1.
  - Service buildings: vehicle doors at least 3 m wide, vehicles up to 4.6 m tall; helipads block nearby zoning.

## 2. Blender model

Load the **blender-mcp-architecture** skill. Key points for game assets:
- Project layout: `~/blender/<proj>/` holding `<proj>.blend`, `scripts\`, `refs\`, `qa\` and `renders\`.
  The asset folder goes in `cs2\<Theme>_<Name>\`; intermediates go in `cs2_build\`.
- One shared `<proj>_common.py` holds the dimensions, material indices and helpers. Keep each part script idempotent
  (it clears and rebuilds its own collection), and have `build_all.py` run them in order.
- Model real geometry for anything that reads at game distance: frames, mullions, bands, rails. The atlas then
  only needs flat colours plus a glass pattern.
- Model glass panes as separate quads per pane or floor where possible; the exporter turns outward glass into window
  islands.
- Official modelling rules (wiki, details in `references/wiki_buildings.md` §2):
  - Model anything tied to the architectural style. Details shallower than ~5 cm go in the normal map unless they change
    the silhouette.
  - Generic add-ons (AC units, fire escapes, vents, mailboxes) are game props, not mesh.
  - Intersect parts instead of joining topology. Use auto-smooth of at least 60° on curved parts.
  - **Foundation:** the game grows a foundation from vertices and edges at exactly z = 0. A building with a visible
    below-grade part must keep no edges at 0. Downward-facing faces at z = 0 cancel the foundation.
- A requested highlight (e.g. "make the crown light stand out") = its own object with its own material, so it maps to its
  own atlas strip.
- **Trees are ALWAYS the game's native trees**, never Blender meshes (native trees sway, season, and cost no atlas space).
  - Record them as data in the build scripts, and the exporter writes `ObjectSubObjects` entries.
  - The recipe and coordinate mapping are in the blender-mcp-architecture skill, section "Trees and vegetation".
  - They appear after a restart plus a new placement.

## 3. Game-ready export

Details and code: `references/export.md`. Adapt the Liberty House scripts in `templates/liberty_house/`; they are
the working reference implementation. Pipeline order:

```
build_all.py (Blender) → make_atlas.py (system Python: numpy+PIL) → export_build.py (Blender) → lod_render.py (Blender)
→ compose_lod_atlas.py (system Python) → lod_build_export.py (Blender: LOD1/LOD2 + writes ALL FBX) → render_icon.py + meta.json
```

The rules that each cost us a round trip:
1. **FBX in centimetres, with the transform baked into the vertices.** Export with `apply_scale_options='FBX_SCALE_NONE',
   global_scale=1.0, apply_unit_scale=True, bake_space_transform=True`. The CS2 importer multiplies raw vertices by 0.01
   and ignores both `UnitScaleFactor` and object (node) transforms.
   - Blender's **dialog defaults are not enough**: `bake_space_transform` is off by default, so the vertices stay in
     metres and the ×100 sits in a node scale the game drops. Tested on a 10 m cube: default export raw extent 10,
     our settings 1000.
   - `FBX_SCALE_UNITS` writes metres plus UnitScaleFactor 100.
   - Either mistake brings the building in at 1/100 size: an empty lot. `cs2_asset_validate` flags both.
2. **One material per main mesh**, named exactly like the mesh and file, UV-mapped onto a trim-sheet atlas
   (`_BaseColor`, `_Normal`, `_MaskMap`, `_ControlMask`, `_Emissive` PNGs, square power of two).
3. **Night windows = a separate FILE `<Name>_Win.fbx` with no material.** The importer matches `_Win` on *file*
   names; an object named `_Win` inside the main FBX is ignored. Each connected island becomes one window, grouped into
   rooms per 3 m floor, and lit at random at night only. So cut every outward glass pane per floor and per ~3 m bay, keep
   the islands unmerged, and drop inward-facing back panes.
4. **Emissive glows day and night**, and brightly. Linear 0.35 was white-hot; 0.05 was still too bright. Set window
   emissive to 0, because `_Win` does the night lighting. Use ≤0.12 for a deliberate highlight.
5. **Street side faces −Y.** The game put the road at the Blender −Y end. Rotate in the exporter (Liberty House: 180°
   about Z for all meshes and LODs) rather than rebuilding the model.
6. **Give the lot a ground surface.** Add a paved slab over the whole lot in every LOD (a tiled paver strip on the atlas).
   Otherwise the building sits on raw grass.
7. **LODs** (community guideline, `references/community_guidelines.md`, read it before making LODs):
   - LOD1 ≤ 50 % of LOD0's triangles and it **must keep LOD0's silhouette**. Make it by decimating LOD0 in small steps,
     sharing LOD0's atlas and UVs. Don't bake onto a new LOD1 shape.
   - LOD2 ≤ 500 tris (the windows sub-mesh doesn't count): a custom low-poly massing with baked textures.
     - Our box massing + orthographic elevation-render atlas qualifies. Keep each view's pixel width inside the atlas:
       lower the pixels-per-metre if the S+E+N+W+Top widths overflow 2048.
     - Include the snow mask.
   - A ~60-tri box LOD1 (used by some older example assets) passes the budget but breaks the silhouette rule.
     New assets: decimated LOD1.
   - **Every sub-mesh needs its LOD versions** (`_LOD1_Win`, `_LOD1_Gls`, `_LOD2_Win`, ...). Without them, night
     lights and glass vanish at distance.
     - Don't simplify the LOD1 window topology, and don't let the LOD1 main mesh cover the `_Win`.
     - `_LOD2_Win` uses exactly the main `_Win` UVs, floats just above the LOD2 surface, and includes only the windows
       visible from afar.
     - Small glass may instead be baked into LOD2's texture in the glass tint.
   - LOD2 textures: 512-1024 px (1024 is the wiki maximum).
7b. **Windows:** suffixes `_Win` / `_Wim`, plus `_Wif` (frosted) and `_Wio` (opaque, no interior) since 1.6.2.
   - Where a window's UV sits in the 5×5 room grid picks its interior and curtain: spread the islands over the ROOM
     cells, not one atlas strip.
   - Window base colour black; no coloured emissive on buildings.
   - **The mesh NAME picks the interior art:** `Industrial`, `Office`, `ResidentialHigh`, `ResidentialLow`,
     `CommercialHigh` or `CommercialLow` in the name; anything else gets office rooms.
   - One polygon per window opening, aligned to the frame (or slightly oversized behind it). UVs: top edge up, true
     aspect ratio, no overlaps.
   - Details: `references/community_guidelines.md`, `references/wiki_buildings.md` §3.
7d. **Glass (`_Gls`) and water (`_Wat`)** are separate files with no material and no textures (the game's shaders).
   - Glass: thin closed slabs, not single planes. Unique, non-overlapping UVs, because the shader draws smears and dirt
     from them. Unwrap big canopies by hand, and never collapse all the glass onto one atlas cell.
   - Water: maximise UV islands (texture scale follows island size). Model the pool floor in the main mesh.
     **Water enclosed by `_Gls` glass is not rendered.**
   - Full glass notes (vanilla method, glitter, decompile) are in `references/stations.md` §8b.
7e. **Texture channels (wiki):**
   - BaseColor alpha is a 50 % alpha CLIP.
   - ControlMask RGB = the 3 colour-variation masks. ControlMask A = SnowRemove: white = NO snow. It isn't implemented
     yet, and our A = 1 default is harmless.
   - MaskMap = metal / coat / unused / gloss. Normal maps are OpenGL.
   - Multiple lights: `_Emissive0-3` + `_EmissiveID0-3` (IDs 1-255, spaced apart, pixel-perfect, max 4 lights per
     triangle).
7c. **Detail/texel density:** region packs use 180–250 px/m (4K, stacked UVs); our 2048 trim atlas is 64 px/m. Model
   real frames, doors and fins; be conservative with tris; don't model unseen undersides.
8. **Pivot** is the lot centre at ground level (min z = 0). Object name, mesh name and file name are identical.
9. **Build-menu picture + name + description for every asset** (always; an asset without them shows a blank or vanilla tile in the build menu). Render `<Name>_Icon.png`
   (square 512 px, transparent background, 3/4 view of the street side) with `render_icon()` from the Blender skill's
   helpers. It runs headless: `"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b <proj>.blend -P
   scripts\render_icon.py`. Write `cs2_build\<Name>_meta.json`:
   `{"title": "...", "description": "...", "icon": "<Name>_Icon.png"}`. Keep both OUT of the asset folder, because the
   importer would treat the PNG as a texture. Keep the description factual: what the real building is, where it is, and
   its signature features.
10. Keep the project root (`cs2\`) to asset folders only, with no loose files.

Then run **`cs2_asset_validate directory=<proj>/cs2/<Theme>_<Name>`**. It checks units, materials, the `_Win` file,
textures, naming and the icon/title/description. Fix every problem, and read every warning.

## 4. Import

Check the game state first: `cs2_status` (bridge version, `game_mode`, `import_running`).

| Situation | Call |
|---|---|
| **First import** of a new asset | From a **loaded city**: `cs2_asset_import directory=... preset=SignatureBuilding` (or Building / CityServiceBuilding / StaticObject ...), or `template=<vanilla prefab>` to clone its gameplay setup. |
| **Every later update** (meshes, textures, LODs, windows) | `preset=None`: re-imports in place and keeps the whole configuration. |
| Update of an asset **already loaded in the city** | Ask the user to quit to the main menu (or start the game fresh) first, then import with `preset=None`, then reload the city. |

Why: at the main menu with a *creating* preset, the Find It mod crashes in `ActivatePrefabTool` (KeyNotFoundException).
The asset is saved, but nothing is reported back. Once a city has loaded the asset, the game holds its `.Geometry` file,
and a re-import fails with "being used by another process". The tool returns `ok:false` plus a `hint` for both cases.

Imports take seconds to minutes (texture compression). The server's own timeout is long; `cs2_raw` gives up after 90 s,
so use the dedicated tool or the repo's `server/bridge_call.py`.

## 5. Configure

`cs2_asset_configure name=<Theme>_<Name> ...`. Recipes are in `references/configure.md`. The common one is an
**always-available residential signature building**:

```
copy_from=NA_ResidentialHighSignature01
remove_components=["Unlockable","BuildingTerraformOverride"]
add_components=["BuildingProperties"]
set={"m_LotWidth":4, "m_LotDepth":8, "BuildingProperties.m_ResidentialProperties":440,
     "UIObject.m_Icon":null, "UIObject.m_Priority":210, "SignatureBuilding.m_UnlockEventImage":null}
```

Then `icon=<png> title=... description=...` (mod ≥ 0.14). `cs2_asset_import` does this automatically from the meta
file after a successful import. Clear the template's `m_Icon` and unlock image, or the build menu shows the vanilla
building's picture. Removing `Unlockable` makes the asset available from the start. Use `describe=true` to see every
field before guessing names.

**Doors and paths (every building people live or work in) - service vehicles depend on them.** Hearses and ambulances
are routed to the building's door spawn markers, not its road frontage. Rules (wiki, Common Asset Principles):
  - Pedestrian Access Location ONLY on a border between two areas, or where an area meets the sidewalk/path (0.5 m
    inside the lot edge). **Never one next to a door** - that cuts every door off, and the building ships with
    "dead never collected" problems.
  - Spawn (door) markers inside the mesh behind the modelled door; nav areas never overlap.
  - **At least one door marker is mandatory.** A hearse is only requested for a building whose prefab has
    `BuildingFlags.HasInsideRoom`, set only by a pedestrian SpawnLocation sub-object with activity mask 0 (Restricted /
    Pedestrian Spawn Location; decompiled HealthProblemSystem + BuildingInitializeSystem.CheckPropFlags). No door marker =
    no hearse ever. Check: `cs2_prefab_entity name=<prefab> component=BuildingData` -> m_Flags contains HasInsideRoom.
    Replacing a vanilla template's sub-objects drops its spawns - add your own.
  - **Gate:** after placing, `cs2_path_test id=<building> mode=hearse` and `mode=ambulance` (mod 0.42) must both say
    found=true for every building type. `cs2_service_access` shows what each door connects to.

**Fixed-position (in-place) upgrades (decompiled Game.Prefabs.BuildingExtensionPrefab):**
the upgrade prefab's OWN fields `m_ExternalLot` (bool), `m_Position` (float3, parent-local), `m_OverrideLotSize`,
`m_OverrideHeight`. A template like "University01 Library" is external (player places it beside the parent); for a fixed
one set `cs2_asset_configure set={"m_ExternalLot": false, "m_Position": [x, y, z]}` (keys without a component = prefab
fields). m_Position = game-local offset of the upgrade's pivot from the base pivot (export frame: game local = (-dx, z, -dy)
from Blender). Keep its footprint EMPTY in the base asset (exporter assigns faces / natives / areas to the upgrade box
first) and run its walk layout separately. This recipe comes from the decompile; verify the upgrade's position in game
after a restart plus a new placement.
**Lot expansion lesson:** when a lot grows, keep the Blender origin and move the lot centre (export `c`) instead -
building scripts hard-code Blender coordinates and silently shift otherwise.

**Transit stations (tracks, platforms, pedestrians, glass pits, upgrades): read `references/stations.md` FIRST.** It is the
full recipe from a large through-station build plus every pitfall. Key traps:
  - Sub-nets, sub-objects and areas only take effect after a GAME RESTART plus a NEW placement.
  - Areas join only through Pedestrian Access Location markers.
  - Underground area nodes need parent mesh 0.
  - Clip the terrain over every below-grade opening.
  - Always ship a `_ControlMask`. Also see the
"Transit stations" section of `references/configure.md`. Copy vanilla: Hangaround Areas on platforms, Walking Areas for free movement, paths only
for level changes. Shops are a `<Station> Services` upgrade. Look up how vanilla does it BEFORE inventing, and never
tell the user something is impossible without checking the game's own prefabs.

## 6. Place & verify

- `cs2_place_building type=<Theme>_<Name>` at a spot the user chooses (`allow_subnets=true` for stations; dry-run
  first). **Mod ≥ 0.19 creates sub-areas like the build menu does** (walking/park areas, surfaces, Clip Surfaces). It
  was verified on a large station: dozens of areas, 0 pedestrian icons. On older mods, the user places from the build menu.
  - When the lot size changes, recompute the centre so the FRONT edge stays on the road:
    centre = front-edge-midpoint − facing × (lot depth ÷ 2).
  A signature building can be placed once per city.
- Screenshot **without moving the camera**. Ask the user to point at it, or to allow a camera move.
- Check:
  - the street side faces the road
  - the plaza, not grass, is under the building
  - there is no unwanted glow by day
  - windows light up at random at night
  - the icon and description show in the build menu
  - the asset is visible at every zoom level (that tests the LODs)
- "The lot shows but no building" almost always means centimetre scale. See troubleshooting.

## 6b. Game session loop (mod ≥ 0.17; you can iterate without the user)

- **Tools:**
  - `cs2_list_saves`, `cs2_load_save "<save>"`, `cs2_main_menu`.
  - `cs2_restart_game`: quit, force-kill if hung, relaunch through Steam, wait for the menu, optionally load a save.
  - `cs2_status` shows `save_name` (0.18+), which tells copies of one city apart; `city_name` can't.
- **Settle before any session change.** The game HUNG twice when `main_menu` or `load_save` arrived while it was
  settling (right after a city load, or right at a fresh boot where `game_mode` still reads "Other"). `gamesession.py`
  now waits for calm (`settle`) first.
- **Force-kill.** Only force-kill a hung game (`taskkill /IM Cities2.exe /F`) when the user reports it or the loop
  detects it.
- **The Paradox Launcher needs a Play click** on every relaunch. A direct `Cities2.exe` launch does NOT work: it spawns
  the launcher and then hangs.
- **Which update needs what:**
  - Mesh/texture-only update: main menu, then `preset=None` import, then reload. It works once per game session; a
    second time the `.Geometry` stays locked, so restart.
  - Sub-nets, sub-objects, areas or lot changes: configure (at the menu is fine), `cs2_restart_game`, import, load,
    bulldoze the old copy, place new. The prefab entity caches those lists until a restart, and placed copies never
    update.
- **Scripted pipelines import whatever is on disk the moment they reach the menu.** Finish the export BEFORE starting
  one.
- **Never discard unsaved user work.** Ask or save before `main_menu` / `load_save` / restart when the user may have
  changed the city. Work in a dedicated test save (a copy of the city), never the player's main save.
- **Inspect vanilla looks** with `cs2_mesh_materials <prefab>` (0.18): per-material template, shader, textures and values.

## 6c. Fast game rounds for big packs
The player usually waits at the main menu while a round runs, so every minute counts. A 400-mesh round went from ~2 h to ~45 min:
- **Texture-only changes never need mesh re-imports.** The importer re-compresses the shared atlas (BC7, all maps, all
  LODs) for EVERY asset, which dominates import time. Instead: re-import ONE reference asset (or only the assets whose
  geometry changed), then with the game CLOSED run `scripts/texture_swap.py <reference asset> [--prefix NA_MyPack]`. A
  `.Surface` stores each texture as `_<Slot>\xFF<16-byte GUID>` (slots BaseColorMap / ControlMask / MaskMap / NormalMap;
  `_Win` surfaces use InteriorAtlas etc. - left alone), so main surfaces get the reference's main GUIDs and `_LOD1/_LOD2`
  surfaces its LOD1 GUIDs, in seconds. Then `dedupe_textures.py <prefix>`. Always copy the new atlas PNGs into every asset
  folder too, so the next real import stays consistent.
- **Only re-import what changed.** Keep a fingerprint per asset folder of what was last imported (e.g. a hash file
  `cs2_build/_game_state/imp_<name>.sha` written by your pack's game-round script, seeded after each round); skip
  unchanged.
- **Only re-configure what changed.** Same trick for `asset_configure` payloads (`cfg_<name>.sha`) and ColorProperties
  (`col_<name>.sha`). Give the script a force flag that redoes everything; clear the state after restoring
  ImportedData from a backup.
- **Don't sleep after imports.** `gamesession.settle(rpc, 5)` after each of 400 imports was ~33 min of pure waiting;
  imports are synchronous RPCs, so `settle(rpc, 1)` is enough.
- **Configure in parallel streams.** 3 configure processes at once overlap the Python/RPC overhead (the mod
  still applies them one at a time on the main thread - safe).
- **Do unplaced prefabs in a loaded city.** Only prefabs with PLACED copies must be configured at the main menu (crash
  rule, troubleshooting "Native crash when configuring..."). New prefabs (first import + setup) can go in while the user
  plays; only their sub-lists need the next restart.
- **Batch the restart.** One round = menu work -> user QUITS -> offline steps (texture_swap, dedupe) -> start = the one
  restart. Never plan a second restart for the offline steps.
- **Expect the offline cleanup to take minutes after a FULL re-import.** Every re-import writes its own copy of every texture
  (hundreds of meshes x 12 maps = tens of GB); `dedupe_textures.py` must read + hash all of it (minutes for a pack that size).
  Tell the user up front; don't start the game until it prints `DEDUPE unique`. The backup folder grows by the same amount -
  prune `_cs2_asset_backups\_texture_dedupe_backup` once the game has been verified.
- **Baked LOD2 (vanilla-style):** flat-colour LOD2s read as blocks; bake a small orthographic top + 4-side picture of LOD0 per
  mesh into ONE shared LOD2 atlas and project the LOD2 UVs onto it (allocate atlas cells, export LOD2-only renders, then
  compose; hundreds of meshes take minutes). texture_swap.py handles `_LOD2_` surfaces separately.
- Future (needs a mod deploy): a batch `asset_configure` RPC (many prefabs per call, one save pass) would cut per-prefab
  overhead further.

## 7. Iterate & publish

- Change the Blender scripts, rerun the pipeline from the first changed step, validate, then re-import with
  `preset=None` (from the main menu if the city has the asset loaded).
- **Colour / material-look changes are not picked up from Blender materials.** The in-game colours come from the atlas
  that `make_atlas.py` paints from its own colour values (Liberty House: the `fill(...)` calls). Blender materials only
  choose which atlas strip a face uses, and colour the icon render. `build_all.py` also deletes and recreates every
  project material from `MAT_SPECS`, which wipes hand edits made in Blender. So for a colour tweak:
  1. Save the .blend. Read the edited material colours (Blender MCP, read-only) and diff them against `MAT_SPECS` and
     `make_atlas.py`.
  2. Check which objects use each material. Materials are usually **shared** between parts (8 of Liberty House's
     podium materials also clad the tower). Ask whether the change applies everywhere, or give the part its own material
     plus atlas strip plus `MAT2STRIP` entry. A material missing from `MAT2STRIP` silently falls back to the mullion strip.
  3. Write the new colours into `MAT_SPECS` and `make_atlas.py`.
  4. Rerun `make_atlas → export_build → lod_render → compose_lod_atlas → lod_build_export → render_icon`. Skip
     `build_all`, since the geometry is unchanged. The LOD re-bake is needed: LOD1/LOD2 textures are renders of the atlas,
     so without it the old colours show from a distance.
  5. Validate, get the user to the main menu, import with `preset=None`, reload, and screenshot close and far.
  - For new projects, keep a single source of truth: have `make_atlas.py` read the colours from `<proj>_common.MAT_SPECS`
    instead of repeating them.
- **Publishing:** there is no tool for it. The user uploads in game, and never without the user's explicit yes for that
  specific upload. Run the **pre-publish checklist** below for EVERY asset before the user opens Share; skipping it
  has shipped assets with problems that then needed a follow-up update.

## 8. Publishing
Load the **cs2-publish** skill. It is the full, mandatory route: readiness gate, UV-clamp and loose-vertex check
(`scripts/uv_range_check.py`), privacy audit (`scripts/publish_audit.py`), AI gallery (reference renders, ChatGPT
prompt, clean gallery JPG + thumbnail), listing text, the in-game Share steps, and updates.

## Related plugin: cs2-modding
The community plugin `cs2-modding@csmodding` (CitiesSkylinesModding/agents-plugins, knowledge-only, MIT) has 4 skills:
cs2-modding, cs2-modding-ui, cs2-modding-setup and cs2-mod-project.
- Use it for CODE-mod work (the CS2MCP mod: ECS systems, prefabs, saves, lifecycle, UI bindings, decompiling) and for
  game MECHANICS (companies/jobs, economy, zoning, services, traffic).
- Use THIS skill for assets (Blender, FBX, atlas, LODs, Asset Importer, configure, placement).

## Reference files

- `references/troubleshooting.md`: every symptom we have seen, with its cause and fix. Read it whenever something looks wrong.
- `references/export.md`: atlas strips and UV modes, window-island cutting, LOD elevation atlas, FBX export code, icon.
- `references/wiki_buildings.md`: the official Paradox wiki "Asset Pipeline: Buildings", condensed, with our deviations:
  zoned levels / lots / triangle guide, service vehicle sizes and upgrades, foundation rule, 5 cm rule, sub-meshes
  (windows, glass, grass, water), texture channels, emissive IDs, LOD sub-meshes, the full export file list.
- `references/community_guidelines.md`: C:S Modding Discord knowledgebase: official LOD budgets, decimate-vs-bake,
  texel density, 1.6.2 window shaders (_Wif/_Wio, tint, light), window UV room-grid layout. Read before LOD/window work.
- `references/configure.md`: configure recipes (residential/office signature, service building, static prop) and
  the build-menu fields.
- `references/stations.md`: transit stations end to end: layout script, sub-nets, stops, walking/hangaround areas,
  Clip Surface pits, Station Services upgrades, placement/reload traps, test-build textures, native props.
- `references/game-internals.md`: the decompiled importer API behind the mod tools (presets, name parsing, file
  suffixes, prefab lookup, icon and locale assets, upload), and how to decompile more.
- `templates/liberty_house/`: the working export scripts with notes on what to change for a new asset.
- Tool sources: this repo (`mod/AssetImport.cs`, `server/assets.py`, `docs/ASSET_PIPELINE.md`).
