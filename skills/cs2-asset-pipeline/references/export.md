# Game-ready export (Blender → CS2 asset folder)

The reference implementation is `templates/liberty_house/` (the working scripts of the Liberty House asset).
Older variants of these scripts used a 4096 atlas and no `_Win`, and exported in metres; re-run any such export with the
settings in §5 before importing.

## Contents
1. Folder layout & naming
2. Trim-sheet atlas (make_atlas.py)
3. LOD0 + window sub-mesh (export_build.py)
4. LOD1 / LOD2 (lod_render.py → compose_lod_atlas.py → lod_build_export.py)
5. FBX export settings
6. Icon + meta
7. Adapting the templates to a new asset

## 1. Folder layout & naming

```
<proj>\cs2\                          project root for the importer: asset folders ONLY
  <Theme>_<Name>\                    folder name == LOD0 file name
    <Theme>_<Name>.fbx               LOD0, one material "<Theme>_<Name>"
    <Theme>_<Name>_Win.fbx           window sub-mesh, no material
    <Theme>_<Name>_LOD1.fbx, _LOD2.fbx
    <Theme>_<Name>[_LODn]_{BaseColor,Normal,MaskMap,ControlMask,Emissive}.png
<proj>\cs2_build\                    intermediates: atlas_layout.json, lod_layout.json, lod_src\, _Icon.png, _meta.json
```
Name segments, in the importer's order (`AssetUtils.ParseName`): `Theme_Name[_L#][_WxD][_Module][_LOD#][_Material]`.
Theme is e.g. `NA` (North America) or `EU`. Keep `Name` a single CamelCase word, because underscores split segments.

Sub-mesh file suffixes the importer knows: `_Win` (windows, interior mapped), `_Wim`/`_Wif`/`_Wio` (milky/frosted/opaque
windows), `_Gls`/`_Glf`/`_Glm` (glass variants), `_Gra`, `_Veg`, `_Wat`, `_Was`, `_Clt`, `_Base` (base surface
with repeating `*_Base_BaseColor...` textures; untested). Sub-meshes carry no material.
Sub-meshes share the main mesh's pivot, position and rotation, and every LOD has its own (`<Name>_LOD1_Gls.fbx`, ...).
The wiki's per-sub-mesh rules (UVs, water/glass, LOD2 windows) are in `wiki_buildings.md` §3 and §5.

## 2. Trim-sheet atlas (system Python, numpy + PIL)

- 2048² at 128 px/m. U spans 16 m and wraps; each material is a horizontal **strip** (a V band) with padding.
- Strip modes, which the UV mapper in export_build.py reads from `atlas_layout.json`:
  - `floor`: glass. Each per-floor glass quad maps its full height onto the strip (one storey = strip height). Add a
    per-floor U shift in whole panes so storeys don't repeat.
  - `uniform`: flat colour + noise. V = strip middle; U runs along the face.
  - `ground`: the plaza. Each 4 m band of the lot slab maps onto the strip (pavers with joints).
- Maps:
  - `BaseColor`: sRGB.
  - `MaskMap`: R = metallic, **G = clear coat** (NOT ambient occlusion), B = 0, A = gloss/smoothness.
    G = 1 puts a glassy lacquer on everything, whatever the smoothness (an airport asset shipped with G = 1 and all its
    ground textures looked too glassy). Keep G = 0 except for glossy paint or glass. Vanilla
    surface masks exported for texture cells must get their G and B zeroed too.
  - Native grass on a building mesh (roof lawns, planters): a `<Name>[_LODn]_Gra.fbx` sub-mesh with no material.
    The game draws it with its Grass template (BH/GraShader), as InternationalAirport01 does its lawns. The stock UVs
    run at about 0.0055 per metre (~183 m per unit); cut the faces on that grid so the UVs stay in [0, 1].
  - `ControlMask`: RGB = colour-variation masks 1-3 (0 = none; see `configure.md` ColorProperties). A = SnowRemove per
    the wiki: white = NO snow, black = full snow. It isn't implemented yet (wiki 1.5.2), so our A = 1 default is harmless.
    Older notes here called A = 1 "snow allowed"; that was backwards. For future-proofing, black on open roofs and white
    under cover.
  - `Normal`: from a height field.
  - `Emissive`: sRGB.
- **Emissive values:** windows 0, because `_Win` handles night lighting. Highlights up to ~0.12 linear.
- A highlight object (e.g. an accent feature or crown light) gets its own strip, so it can be coloured or emissive on its own.

## 3. LOD0 + window sub-mesh (export_build.py)

`gather(collections)` walks the evaluated meshes in world space and sends faces two ways:
- **Main bmesh**: every face is tagged with an atlas strip (`MAT2STRIP` maps Blender material → strip). Up-facing faces of
  glass, metal or frame materials become `roof`. Thin edges of glass boxes (< 0.5 m) become `spandrel`.
- **Window bmesh** (outward vertical glass quads only) **with a UV layer** (a UV-less `_Win` crashes the importer's
  WindowsPostProcessor and the windows are skipped). Each quad is cut at every floor line in `FLOOR_Z` and into ~3 m
  bays (`WIN_BAY`), and each piece is emitted as its own island. Inward-facing back panes are dropped
  (`normal · plane-position < 0`).
- Then:
  - `remove_doubles` runs on the main mesh only.
  - `uv_map()` runs on both.
  - `rot180()` runs on both (street end → −Y).
  - The main mesh gets one material, `cs2_material(NAME, ...)`, a Principled BSDF wired to the atlas PNGs for preview.
  - The window mesh gets no material.
- Liberty House: 14,742 tris, 1,438 window islands.

Plaza: model it in the source (16 boxes of 32 × 4 m, z −0.05..0.08, material `LH_Plaza` → `plaza` ground strip) so it
exports with LOD0. Add the same slab to the LOD1/LOD2 boxes.

## 4. LOD1 / LOD2

1. `lod_render.py` (Blender): a private scene with an orthographic camera renders S/E/N/W/Top elevations of LOD0 +
   `_Win`. A material override outputs the atlas BaseColor / Emissive as pure emission (Standard view, black world,
   transparent film), which gives unlit texture data. `PPM` must keep the summed view widths inside 2048
   (Liberty House: 8.8 px/m for 3×32 m + 2×64 m).
2. `compose_lod_atlas.py` (system Python): packs the views into `<Name>_LOD1_*.png` / `_LOD2_*.png` atlases and writes
   `lod_layout.json` (rect per view + bbox).
3. `lod_build_export.py` (Blender):
   - It builds the box massings: LOD1 is podium, band, slab, street block, pavilion, cap and mech; LOD2 is podium + tower.
     Both get the plaza slab.
   - It applies the same `rot180`, then planar-projects UVs by face normal onto the matching view rect.
   - It gives each LOD one material named like its mesh, then **exports all four FBX** (LOD0, `_Win`, LOD1, LOD2).
   - Targets: LOD1 < 50 % of LOD0 triangles; LOD2 ≤ 500.

## 5. FBX export settings (the ones that matter)

```python
sc = bpy.data.scenes.get("X_FBX") or bpy.data.scenes.new("X_FBX")      # temp scene holding only this object
for o in list(sc.collection.objects): sc.collection.objects.unlink(o)
sc.collection.objects.link(ob)
with bpy.context.temp_override(scene=sc, view_layer=sc.view_layers[0]):
    bpy.ops.export_scene.fbx(filepath=path, use_selection=False, object_types={'MESH'},
        apply_unit_scale=True, global_scale=1.0, apply_scale_options='FBX_SCALE_NONE',   # raw vertices in cm
        axis_forward='-Z', axis_up='Y', bake_space_transform=True, use_mesh_modifiers=True,
        mesh_smooth_type='FACE', add_leaf_bones=False, path_mode='STRIP', embed_textures=False, use_custom_props=False)
sc.collection.objects.unlink(ob)
```
`bake_space_transform=True` is the setting that matters most. Blender's dialog default is `False`, which writes
metre vertices plus a ×100 node scale that CS2 drops. Tested on a 10 m cube: default export raw extent 10,
these settings 1000. One object per file; the file name equals the object name. Verify with `cs2_asset_validate`: its FBX reader reports
`unit_scale` and the raw `extent`, which must be in cm (a 86 m tower → ~8600).

## 6. Icon + meta

- `render_icon(collections, out_png, size=512, az_deg, elev_deg, lens=50)` from the blender-mcp-architecture skill's
  `helpers/lbt_helpers.py`.
  - It renders in a private scene with a transparent film and a camera auto-fitted to the bbox, copying EEVEE, world and
    colour settings from "Scene".
  - `az_deg=0` looks at the −Y front. When rendering the *source* model and the exporter rotates it 180°, use ~145° to show
    the street end plus a long side.
- Headless run: `"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b <proj>.blend -P scripts\render_icon.py`.
- `cs2_build\<Name>_meta.json`: `{"title", "description", "icon"}`. The description is 1–3 factual sentences: storeys,
  address or neighbourhood, and the architectural features that make it recognisable.

## 7. Adapting the templates to a new asset

Change these:
- `ROOT`, `NAME`, and the common file (`lh_common.py`: dimensions, material list, helpers).
- `MAT2STRIP` and `STRIPS`: one strip per material family.
- The glass pane width (`PANE`) and typical floor height (`TYP`) in make_atlas.
- The collections passed to `gather()`, and `FLOOR_Z`: the storey table.
- `ROT180`, or whichever rotation puts the street side at −Y.
- The plaza size, which equals the lot (cells × 8 m).
- The LOD boxes.
- The icon view angle.

Keep unchanged: the FBX settings, `_Win` as its own file, and zero window emissive.

## Package size: textures must stay VIRTUAL-TEXTURE SAFE (hard lesson)
**A published package crashed EVERY subscriber's game at startup** after a texture "diet":
`FATAL: Can't test if a material is a duplicate with registered materials`.
- **Why:** a Paradox package converts every surface to virtual texturing (512 px tiles). `SurfaceAsset.Save(vt)` SKIPS a
  material if any of its maps is under 512 px, or if the maps of one stack differ in size. Our 64 px constant maps,
  2048/1024 mixes and LOD maps shared from LOD0 disqualified every material. The package's
  `StreamingData~/TS512_MB3.MidMips` came out with 0 materials, and `AtlasMaterialsGrouper.IsDuplicate` then throws.
  - A LOCAL (unpackaged) import works fine, so nothing shows until after publishing.
  - Research had called non-streamed maps "harmless". They are harmless only while at least one material in the package
    qualifies.
- **Rule for anything that gets published:** per surface (each LOD), every map the SAME size, >= 512, power of two,
  8-bit RGBA.
  - Vehicles with `_Emissive0`: the importer generates one `_Emissive` per LOD at Emissive0's size, so every LOD stack
    must be that size too (e.g. 2048 at all LODs).
  - Buildings may step down per LOD (2048 / 1024 / 512) as long as each LOD is uniform.
  - Leave out an all-zero `_Emissive` entirely rather than shrinking it. The importer then adds no glow either.
- **Gates:**
  - Before import: `python ~/.claude/skills/cs2-asset-pipeline/scripts/optimize_textures.py <asset dirs> --check`. It
    only shares pixel-identical, same-size LOD maps.
  - After Build Package, BEFORE Submit: `python ~/.claude/skills/cs2-publish/scripts/package_vt_check.py
    "%LocalLow%/Colossal Order/Cities Skylines II/.packages"`. It must report registered materials, not BROKEN.
- **Still true:** the importer stores every PNG as BC7 with full mips (2048 = 5.59 MB, 1024 = 1.40, 512 = 0.35). It
  never de-duplicates identical files. Re-import never deletes stored textures that are no longer produced; they stay
  unreferenced and are not packaged.
- **settings.json `sharedAssets`** (a LOD binding LOD0's texture file) works locally, but is only VT-safe when the shared
  maps have the same size as the rest of that LOD's stack. Don't use it on a published asset unless `--check` passes and
  the built package verifies.
- **Retired approach:** shrinking constant or LOD maps below 512 px or mixing sizes within a stack (the crashing "diet").
  Use `scripts/optimize_textures.py --check` instead.

## Night-window (_Win) UV cells (decompile-verified, game 1.6.2f1)
- **Room cell:** the UV square is a 5x5 grid. `RoomID = 5*floor(5v) + floor(5u)`, with Blender v pointing up, so
  Blender bottom-left is RoomID 0. It is read from the FIRST vertex of each triangle; keep every vertex strictly inside
  one cell, about 0.02-0.03 inset.
- **Special cells:** RoomID 0 is ALWAYS ON (about 0.5 brightness even by day). RoomID 24 is never on.
- **Curtains:** the decoration tile is RoomID mod 4. Office sheet: 0 half-drawn venetian, 1 blank, 2 vertical blinds,
  3 full venetian.
- **Interior picture:** the room counter mod 16, from the atlas the name keyword selects. The UVs don't choose it.
- **Grouping:** floors come from height. Panes in the same cell and on the same floor, less than 2 m apart, merge into
  one room, which lights as a unit. Use different cells for independent rooms.
- **Hygiene:** keep _Win as a single mesh with a single material.
- **Our bug:** Blender's default UVs put every pane in RoomID 0, i.e. always on, day and night. Set the UVs explicitly
  in the exporter's window-mesh step.

## Vehicles: build the game body, don't decimate it
A published car pack looked "wonky" in game: smushed, no clean straight lines or pillars, windows not flat. Cause: the ~2,000-tri game bodies were a voxel depth hull of the full model, collapse-decimated. Textured
Cycles renders hid it (the baked normal map repaints the shading); a **plain clay render of the exported FBX**
(e.g. a `clay_fbx.py` helper with `--wire --flat`) showed lumpy soap bars. Always clay-check game meshes.
What works (a `body_fields.py` -> `body_build.py` -> `export_cars.py` chain):
- **Structured loft**: rings at Y stations with the same named slots (floor, wheel-well wall, arch lip, side lines,
  belt, side-glass bottom/top, roof edge, top, centre); slots that don't exist at a station collapse (cost nothing).
  One smooth curve per feature line; straight A-pillar line; side glass slots on each window's fitted PLANE; stations
  at arch corners, pillar corners, window ends, top-profile breaks, then filled to the triangle target.
- **Nose / tail**: star sections of the hull (rays in the plane y = const) toward the BULK end face, closed with
  horizontal bands - never a pole fan, never starting inside a wheel arch.
- **Sensors / mirrors**: clean solids (box / edge-chamfered k-DOP / flat slab), not decimated hulls.
- **View-projection atlas pitfalls** (one orthographic view per face, shared by all LODs): choose views per QUAD
  before triangulating, no view flatter than ~70 deg to the face (stretched streaks), neighbour smoothing + small-island
  flips, a bonus for the front/rear views so seams land on plain paint, not on light bars or spoilers.
- **Normal map**: clamp the baked tilt (~30 deg). Where the low surface sits cm off the full model, cage rays hit
  side walls (grille dividers, plate recesses, decals) and bake dark stripes / blobs / dark lettering.
- **Later fixes** (each was a visible defect in the comparison sheets):
  - Wheel-arch ENDS are always stations, sampled 1.5 cm outside the arch; the arch lip is also found from the SIDE
    (walk down from the widest point above 0.3 m until the side drops into the wheel well) - thin splash flaps under
    an arch mouth otherwise hide the arch start and fan triangles into a spike.
  - Caps: each slot reaches toward the end face at its OWN (x, z) - a hood edge that curves in plan stays a curve.
  - Swept creases: at top-profile corners with curvature >= 30 /m (spoiler lips) the top slots slide along y to the
    crease at their own x. Gentle curves are NOT swept (1 cm field noise would add wobble).
  - Sensor budget: split by area, then leftover handed out one extra part at a time (largest cluster first), so
    camera stems, mirror arms and pod brackets get modelled instead of the roof unit hoarding the budget.
  - Sensor/mirror solids (a `kdop.py fit_best` helper): candidates box, edge-chamfered boxes, flat slab, domed slab,
    8-sided prisms; score = vertex overshoot + COVER (part points -> solid surface) + triangle cost. Overshoot alone
    let a slab keep only the top 4 cm of an open-shell part (a black roof band vanished). Prisms need a
    25 % better score (flat caps turn rounded mirrors into pucks). Thin "tube" trim lying flush on a pod's main body
    is skipped (it rendered as diagonal scratches); free-standing rails stay.
  - Side glass quads are pinned to the side views (a rear-leaning quarter-glass face took the rear view and showed
    white pillar texels).
  - Coarse LODs: clamp their baked normal tilt harder (8 deg) and blur it lightly (masked 9x9) - LOD1 surfaces sit cm
    off the full model and their cage bakes draw contour rings. Check `mesh_check` in the export report: prism/slab
    outlines with near-duplicate ring points made zero-area faces + non-manifold edges (merge ring points < 2 mm).

## Vehicles: build like stock cars (measured from the game's own Car09 / Van01 / Car07 / Taxi01)
Stock facts (a `geo_decode.py` script reads vanilla .Geometry straight from Content/Game/Blob.cok, offline): LOD0
2,000-2,470 tris; ~60 % of body tris in the front + rear 20 % of the length (grille, lamp pockets, intakes, plate
modelled); cheap flat floor (8-11 % down-facing); big flat mid panels of long triangles; wheels 16-sided = 60 tris
(caps + tread, LOD1 28); one submesh, same shader/slots as custom cars. A pipeline that reproduces that:
- loft stations dense then DECIMATED by interpolation error (keep pillars / arch ends / window ends / creases);
- floor one full-width quad per gap, arch liners kept;
- ends = relief from an 8 mm depth + material grid of the full model: material regions deeper/prouder > 8 mm get
  outline + inset walls (dark swatch), greedy error-driven vertices to ~250 tris per end (triangle + shapely + contourpy);
- texture views: ENERGY pass (face cost area x (1 - facing), seam cost 0.09 per m, single-face + whole-blob moves) -
  no more T/F patchwork on sloped hoods / hatches.
