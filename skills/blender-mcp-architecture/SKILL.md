---
name: blender-mcp-architecture
description: Tips, gotchas and reusable helpers for building detailed architecture / game assets (e.g. Cities Skylines 2 buildings) in a live Blender 5.x session through the Blender MCP (mcp__Blender__execute_blender_code). Use whenever modelling buildings, stations, landmarks or any multi-part scene in Blender via MCP, writing bpy/bmesh build scripts, making procedural materials, or rendering progress shots.
---

# Building detailed architecture in Blender via MCP

Learned building a 200x112 m London-Bridge-style CS2 train station + Shard-style tower, and several real towers.
Reusable code: `helpers/lbt_helpers.py` in this skill folder (materials builder, geometry helpers, isolated QA render).

## How to work with the user
- **Build inline and show renders often.** Users generally want to WATCH progress, so don't hand modelling off to
  background parallel agents. Parallel agents sharing ONE Blender instance also fight over scene state anyway.
- **Pin the style before detailing.** Offer 3-4 options named after real buildings, with pictures (Wikimedia Commons
  image search in the browser pane works; Bing image search returned junk). Expect a pivot anyway: styles often
  get rejected only AFTER they are seen built (e.g. Victorian Gothic + an arched Barlow roof). Build a quick blockout first, since it's cheap
  to change.
- After each part: render 1-2 QA shots, Read them, critique honestly, fix, then SendUserFile the good one.
- Keep projects on a local, always-mounted drive (e.g. under `~/blender/<project>`), not on volumes that may be
  unmounted. Relocating a project means
  `bpy.ops.wm.save_as_mainfile(filepath=new)` plus a path rewrite in scripts.

## MCP mechanics
- Tools: `mcp__Blender__execute_blender_code` (load via ToolSearch). Two registrations may exist (`Blender` and
  `blender`); both hit the same instance.
- Each call gets a **fresh namespace**: always `import bpy`. Return data by assigning a **dict** to `result`
  (a str raises an error).
- `bpy.ops.wm.read_homefile()` / `save_as_mainfile()` via MCP do NOT drop the connection.
- **Write build scripts to disk and `exec(open(path).read())` them.** Each script is idempotent: it clears and rebuilds
  only its own collection (`LBT_<Part>`), and prefixes its meshes/materials. A `build_all.py` runs them in dependency order.
- **exec() shares one namespace.** Runner loops must use unique names (`RUNNER_NAME`, `BUILD_STEP`); scripts
  clobbered `n`, `D`, and `_out` (a helper function), causing baffling errors.
- Use the data API + bmesh + modifiers only. Avoid bpy.ops (context/selection dependent) except rendering.

## Geometry patterns that worked
- **Modules in local space**: build each facade bay/window as a mesh with local X along the facade, front face at
  local y=0 facing -Y, depth toward +Y, z=0 at the base. Instance it with `bpy.data.objects.new(name, shared_mesh)` and
  rotate about Z to face any direction (rot pi/2 faces +X, -pi/2 faces -X, pi faces +Y).
- **Openings without booleans**: build the wall as quads around each opening (piers between openings, strip under
  the sill, a quad fan from the arch curve up to the wall top), then reveal quads from y=0 to y=depth and a glass ngon at
  the back. EXACT booleans with many cutters punched whole bands out of the facade, so avoid them.
- Arches: pointed Gothic arches are two arcs of radius ~0.85w; segmental and round are easy. Build voussoirs as
  offset quads along the arch normal, alternating materials for polychrome work.
- Repetition: shared mesh data (linked instances) + Array modifiers (dentils, cresting, corbels, glazing bars).
  Keep modifiers unapplied.
- Diagrid roofs: two families of parallel lines at +/-38 deg, clipped to the rectangle (slab method), each an oriented box.
- Reusing meshes across scripts: look them up from LIVE objects (`next(o.data for o in coll.objects if ...)`), not by
  name, because rebuilds create `.001` names while old users still exist.
- Escalators: 30 deg, run = rise / tan(30 deg) + 2 x 2.4 m landings. A 11 m rise needs about 24 m, and the deck opening
  needs only about 10 m of it (the lower end runs under the slab).
- Icospheres added with bmesh ops come in with material index 0; remap them afterwards.

## Procedural materials (keep Principled BSDF as output so they can be baked later)
- **Mix node gotcha**: `ShaderNodeMix` has three outputs all NAMED "Result". Link by identifier **`Result_Color`**.
  Linking the first "Result" silently kills every texture.
- Brick on walls modelled in local XZ: Mapping node rotation X = 90 deg. Mapping (Point) applies scale → rotate →
  translate, so give stretch factors in ORIGINAL axes (rain streaks: scale (2.5, 1, 0.08)).
- Bricks become sub-pixel beyond ~20 m, so also add large-scale tonal patches (noise scale ~0.12, OVERLAY) and
  soot/rain streaks, or walls read flat.
- Per-instance variation: `ShaderNodeObjectInfo` → Random (lit/unlit windows per module instance; per-shop brightness).
  Per-pane variation on one big object: a second Brick texture with black/white bricks, then threshold its colour.
  Noise-threshold "lit windows" makes camouflage blobs, so don't use it.
- Window glass that must hide an empty interior: opaque dark glossy glass with an emission "interior" instead of
  transparency. Real see-through glass: alpha ~0.2-0.3, `surface_render_method='BLENDED'`, backface culling off,
  `use_transparent_shadow=True` on roof glass.
- Emissive screens/signs above ~1.0 strength get flattened to white by AgX. Keep them near 0.9.
- Recreating a material (remove + new) empties slots on other collections' meshes, so rebuild dependents afterwards.

## Rendering / QA
- `qa_render()` (in helpers): a private scene `QA_<tag>` links the relevant collections + its own camera and renders with
  `bpy.ops.render.render(write_still=True, scene=name)`, so the main camera is never touched. Copy
  `scene.eevee` properties + view settings from the main scene so QA matches the finals. Delete QA_* scenes before saving.
- Lighting that worked (EEVEE, Blender 5.2): Sky Texture `sky_type='MULTIPLE_SCATTERING'` (the old NISHITA name),
  Background strength ~0.22, dust 0.8; matching Sun lamp (energy ~4.2, angle 0.8 deg); AgX +
  "AgX - Medium High Contrast", exposure -0.45; eevee `use_raytracing=True`, `use_fast_gi=True`. A flat grey world makes
  everything look like a toy, so light early.
- Human-scale shots: camera 1.7 m above the floor, lens 20-24 mm. Heroes: lens 17-22 mm from low, aimed up.
- Presentation-only props (trains) go in their own collection that is excluded from export.

## Recreating a real building ("1:1")
- Engineering papers beat press articles. A structural engineer's paper on a supertall tower can give the column grid, outrigger
  levels and a massing model. Download it with WebFetch (it saves the binary), then extract text and page images with
  python `fitz` (PyMuPDF). Poppler is not installed, so Read cannot open PDFs.
- Get CURRENT construction photos (UrbanToronto/skyrisecities CDN, etc.). A first pass built from articles
  alone is often far off; the photos showed the real details (thin hangers meeting in V's at glazed
  corners, pale faceted pilasters, 18-floor blocks of 6-floor tiers, 2-storey breaks).
- The browser pane can't zoom into a region. To inspect an image, replace the page body via javascript with an
  oversized <img> offset by negative left/top, then screenshot (retry once if the first screenshot times out).
- Count floors, tiers and bays in the photos, then derive a storey table that lands exactly on the published heights.

## CS2 export pipeline that worked
For the whole idea → in-game workflow (export, import, configure, place, troubleshooting), use the **cs2-asset-pipeline**
skill. The notes below are the Blender-side summary.
- The main mesh must use ONE material, so build a 4096 trim-sheet atlas with numpy/PIL. U spans the full width
  (32 m = 4096 px) and wraps; each strip is a V band. Split glass into one quad per floor and map each floor onto a
  one-storey strip. Uniform materials use V = the strip's middle. Louvres put the Z pattern along U.
- LODs: render orthographic albedo/emissive elevations (S/E/N/W/Top) of LOD0 with a material override
  (emission from the atlas, Standard view transform). Pack them into LOD atlases and planar-project UVs onto box
  LODs. The result is tiny (36 tris) and still shows the facade pattern.
- **FBX units: export with `apply_scale_options='FBX_SCALE_NONE', global_scale=1.0, bake_space_transform=True`** (raw vertices in cm;
  the dialog default has bake OFF, which gives metre vertices plus a ×100 node scale that CS2 ignores). CS2's FBXImporter
  applies a fixed scale 0.01 and ignores the file's UnitScaleFactor; `FBX_SCALE_UNITS` wrote metres and the building imported at
  1/100 size = invisible. Check raw extents with a tiny binary-FBX reader, not a Blender round trip (it hides this).
- **Night-lit windows = a separate `<Asset>_Win.fbx`** with NO material: the importer's Windows template matches FILE names
  only (an object named _Win inside the main FBX is ignored). Each connected island = one window, so emit every outward
  glass pane per floor and per ~3 m bay as its own island (no remove_doubles on it); rooms are grouped per 3 m floor and lit
  at random at night. Then set baked window emissive to 0.
- **Front/road side**: the street end must face Blender -Y (a model built facing +Y has to be rotated 180 deg in the exporter).
- **Lot base**: add a paved slab over the whole lot (and in every LOD) or the building sits on raw grass.
- **Every asset needs a build-menu picture + name + description.**
  `render_icon(collections, out_png, az_deg=..., elev_deg=...)` in the helpers renders a square 512 px PNG on a transparent
  background from a private scene with an auto-fitted camera. Front = -Y, so `az_deg` 180±35 views a +Y front (e.g. a SOURCE
  model that the exporter rotates). It runs headless when the MCP is down: `blender.exe -b file.blend -P scripts\render_icon.py`
  (Blender 5.2 at `C:\Program Files\Blender Foundation\Blender 5.2`). Put `<Name>_Icon.png` + `<Name>_meta.json`
  ({title, description, icon}) in `<project root>_build`, not in the asset folder.
- Full CS2 workflow + MCP import tools: this repo's `docs/ASSET_PIPELINE.md` (import/configure/place, no Editor).
- **Emissive is bright in game, day and night**: linear 0.35 lit windows were white-hot; 0.05 still "a bit too bright"; 0.02 windows / 0.12 highlight worked.
- Export FBX from a temp scene: `with bpy.context.temp_override(scene=sc, view_layer=sc.view_layers[0])`, with
  object/mesh/material/file names identical. Round-trip re-import to verify names, material, size and minz = 0.
- Keep the importer's Project Root clean (asset folders only) and put intermediates in a sibling `_build` folder.
- The Asset Importer UI is GUI-only, but the code behind it is callable from a mod (found via ilspycmd on
  Game.dll, `DOTNET_ROLL_FORWARD=Major`): `Game.UI.Editor.AssetImportPanel.ImportAssets(projectRoot, dir, seed, Preset,
  template, world, log)` is public static; upload = `Game.UI.Menu.PdxAssetUploadHandle.BeginSubmit/FinalizeSubmit`
  (uses the in-game Paradox login; always confirm with the user before publishing). Candidates for CS2 MCP tools.

## Shell gotchas
- PowerShell: `@(@('a','b'))` FLATTENS to `@('a','b')`. Iterating "pairs" then indexes characters, and `.Replace(char, char)`
  silently corrupted five scripts. Never do multi-file find/replace with nested PowerShell arrays; use the Edit tool.
- Never `raise SystemExit` in a script you `exec()` inside Blender via MCP. Use conditionals instead.

## Trees and vegetation in game assets: ALWAYS native
- **Never model trees in Blender for a CS2 asset.** No icosphere canopies, trunks or bush blobs. Use the game's own tree
  prefabs as `ObjectSubObjects` sub-objects. They sway, change with the seasons, match the city and cost no atlas space.
- **In the build script,** record each tree as data, e.g. `NATIVE.append({"m_Object": ..., "src": [x, y, z],
  "yaw_deg": ..., "on_mesh": z > 0.05})`, and write it to `cs2_build/native_objects_src.json`.
- **The exporter** converts to building-local game coords: `m_Position = [-(x - cx), z, -(y - cy)]`, `yaw = -yaw`.
  Here (cx, cy) is the pivot shift and Blender −Y = the front = game local +z.
  - **X IS MIRRORED:** game local +x = Blender −x. Verified in game. With `x - cx`
    pool-deck trees landed on the far side, inside the tower, and poked out through its facade.
  - Before a full planting pass, sanity-check with one asymmetric prop. It uses
  `m_ParentMesh` 0 plus y for trees standing on the building (decks, planters, roof gardens) and −1 for terrain.
- **In game:** `cs2_asset_configure add_components=["ObjectSubObjects"] set={"ObjectSubObjects.m_SubObjects": [...]}`
  (list entries: m_Object, m_Position, m_Rotation {yaw_deg}, m_ParentMesh, m_GroupIndex -1, m_Probability 100). Sub-objects
  appear after a game restart plus a NEW placement.
- **Names:** `cs2_list_object_types kind=tree`. Good urban picks are `NA_LondonPlaneTree01`, `NA_LindenTree01` and
  `TreeCityRandom01`; `GreenBushWild01/02` and `FlowerBushWild01/02` for shrubs. For benches, lamps and bike racks,
  prefer native props too (`GardenBench03`, `LightpolePark03`, `BikeRackRandom01`); see cs2-asset-pipeline stations.md §8d/§9.
- Planters, soil and lawns can stay modelled. Only the living plants are native.
- **No stand-in tree meshes either, not even presentation-only cones** - always use native trees. In the .blend, mark native spots with EMPTIES only, e.g. cone empties named
  `NativeTree_<n>_<prefab>`, so the live window shows where the trees go without modelled trees.

## Cities: Skylines II asset facts
- Grid 8 m. Train track lane 4 m (model ≤3.75 m); 7.5 m between facing platform edges; platforms ≥160 m, +0.85 m;
  catenary corridor 10 m wide, ≥7.7 m clearance. The vanilla big station is 25x14 cells.
- Import: FBX 2018, 1 unit = 1 m, pivot bottom-centre, `<Theme>_<Name>_<Level>_<LotSize>_<Module>_<LOD>_<Material>`
  naming, one material per main mesh, square power-of-two PNG textures 512-4096 (`_BaseColor/_ControlMask/_MaskMap/
  _Normal/_Emissive`), LOD1 <50% of LOD0 triangles, LOD2 ≤500 triangles. The Editor (Asset Mods, Dec 2025) is still in beta.
- Throughput: through-running beats termini; there are no signals, so give every platform clean pedestrian access
  (a concourse with escalators to each island).
