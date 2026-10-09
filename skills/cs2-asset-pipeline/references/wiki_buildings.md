# Official wiki: Asset Pipeline: Buildings (condensed)

Source: https://cs2.paradoxwikis.com/Asset_Pipeline:_Buildings (Paradox-verified for game 1.5.2f1; read the raw text
via `index.php?title=Asset_Pipeline:_Buildings&action=raw` in a real browser; curl/WebFetch hit a bot wall or only
return a summary). This file is the wiki's rules condensed, each with **Ours:** where our pipeline differs or has
learned more. Where our decompile/in-game findings disagree with the wiki, ours win, but note it.

## Contents
1. Building types: zoned (levels, tri budgets, lots), service (vehicles, helipads, upgrades), signature
2. Main mesh: modelling, foundation, optimisation, UVs, naming
3. Sub-meshes: windows, glass, grass, water
4. Textures: maps, channels, emissive (single / multiple with IDs)
5. LODs
6. Export + full example file list

## 1. Building types

### Zoned (growables)
- Zone types: Residential Low / Medium Row / Medium / High / Mixed / Low Rent; Commercial Low (incl. gas stations,
  motels) / High (incl. hotels); Office Low / High; Industrial Manufacturing (incl. warehouses). Custom zones possible.
- **Levels 1-5.** Vanilla ships 3 meshes (L1, L3, L5); L2/L4 are non-visual upgrades. One mesh per level is allowed.
  Show progression with: footprint vs lot (smaller at low levels), shape complexity, floor count, window/door frame
  decoration, dormers/roof windows, gutters, chimney cowls, terraces/porches, material cost (more wood/raw brick low),
  cleanliness. Re-using a mesh across levels: vary colours, re-prop it, move/rotate it on the lot.
- **Lots:** 1 cell = 8x8 m. Width (along road) x depth. **Min 1x2, max 6x6.** The zone algorithm picks the largest lot:
  deepest first, then widest. If 4x4 space exists and you ship any 4x4 asset, it wins over two 2x2s. Higher land
  value subdivides big plots into smaller lots. **Ship a wide range of lot sizes, and a complete L1-L5 set for every lot
  size you include**, before adding extra variations.
- **Main-mesh triangle guide** (average excl. outliers / soft max; sub-meshes excluded; indicative, not limits):

| Zone | Avg | Soft max |
|---|---|---|
| Low density housing / business / offices | 8,000 | 15,000 |
| Medium density row housing | 6,000 | 14,000 |
| Mixed housing (excl. storefront) | 15,000 | 28,000 |
| Medium density housing | 35,000 | 75,000 |
| High density business | 14,000 | 30,000 |
| High density housing | 50,000 | 95,000 |
| High density offices | 40,000 | 78,000 |
| Specialized industry hubs | 20,000 | 62,000 |
| Industrial manufacturing / warehouses | 3,000 | 6,000 |
| Industrial storage | 15,000 | 35,000 |
| Low rent housing | 15,000 | 25,000 |

### Service buildings
- Lots up to 1000x1000 cells, but bigger = harder to place. Matching a zone style: pick a lot that blends with it.
- **Vehicle bays/garage doors:** base them on vanilla vehicles but close to real sizes; **min 3 m wide; max vehicle
  height 4.6 m** (tram-wire clearance).
- **Helipads** carry large "airspace" collision boxes that can block nearby zones from growing; mind it on low buildings.
- Triangle budget by how often it will be placed: a small fire station many times (optimise), a grand station once
  (higher budget, consider making it unique).
- **Upgrades:**
  - *Operational*: stats only, no mesh/texture.
  - *Extensions*: visible, on the main lot at a fixed position, each placeable once; several per asset, designed to be
    mutually independent. They can delete props where placed, so prop the main building fully. One extension can't
    forbid another.
  - *Sub-buildings*: separate buildings attached to or around the lot (range adjustable, or perimeter-only like the
    Harbor). Their road side always faces the main lot's centre when snapping. Can require another upgrade; can be
    unique. Leave room around the parent for them.
- Ours: a station's "Station Services" is an extension-style upgrade (`references/stations.md`).

### Signature buildings
- Unique anchors; one per zone type in vanilla. Placed by the player, so no zoned lot limits; placed once, so a higher
  triangle soft max. Vanilla has no build cost/upkeep (adjustable) and can carry local or city-wide effects (+ or -).

## 2. Main mesh

- Research first: decide which features are modelled vs textured, the purpose (growable = lower budget, service =
  vehicle spawning) and the lot.
- **Model:** inset windows, real window/door frames, door handles, gutters, chimneys, eave details: anything tied to the
  architectural style. **Generic add-ons (AC units, fire escapes, vent pipes, mailboxes) should be props** (vanilla
  props are fine). Ours: same rule as native trees (SKILL.md §2): plants and street furniture are game prefabs as
  `ObjectSubObjects`, never mesh.
- **Pivot bottom-centre; ground level exactly at 0.** Rotation correct on import (rotating later can break animations).
  Ours: lot centre at z = 0, street side at -Y (SKILL.md rule 5/8).
- **Automatic foundation:** the game builds a foundation below ground from the vertices/edges that touch y = 0.
  - Visible underground part (pits, sunken plazas): keep NO edge at exactly 0; move them above or below.
  - Downward-facing polygons at y = 0 cancel the automatic foundation.
  - Ours: our lot slab sits at z -0.05..0.08 (no edge at 0) - check this whenever a building shows an odd plinth or
    is missing one on sloped ground.
- **5 cm rule:** details shallower than ~5 cm go in the normal map (keyholes, cornice ridges); deeper ones are modelled
  (door handles, door panels, frames vs glass). Anything that changes the silhouette is modelled. Thin fences: alpha
  map the infill, model only the frame.
- **Cheap topology:** intersect meshes instead of joining (a beam pushed into the facade; window frames intersecting
  each other). Use angle-based auto smooth, **at least 60 deg**, to make curved items (gutters, round handles, columns)
  cheap. Ours: our exporter writes `mesh_smooth_type='FACE'`; flat-shaded curves need more triangles to look round.
- **UVs:** range **0-1**, enough padding between islands for mipmaps (more next to mirrored islands); separate
  materials with empty space; cluster similar colours; widen gaps between very dark and very light islands. Stack /
  overlap UVs to save space when textures have no repeating visual elements.
  - Ours: the trim atlas "U wraps" and stations used "one big cell per material" because thin strips bled; the
    cs2-publish UV-clamp check (`scripts/uv_range_check.py`) enforces 0-1 before publishing.
- **Naming:** object name == file name == material name (or `<Name>_Mtl` if the software forbids the duplicate).
  **Names and suffixes are case-sensitive.**

## 3. Sub-meshes

General: modelled, UV-mapped and exported separately with the **same pivot, position, rotation and name stem** as the
main mesh. No material. No textures needed (shaders do it). Auto UV is fine for most; window UV placement/orientation
matters.

### Windows (`_Win`, `_Wim`)
- `_Wim` (milky) is recognised, but its parallax is **currently identical to `_Win`** (wiki 1.5.2; 1.6.2 added `_Wif`/
  `_Wio`, see `community_guidelines.md`).
- **The mesh name picks the interior set:** include `Industrial`, `Office`, `ResidentialHigh`, `ResidentialLow`,
  `CommercialHigh` or `CommercialLow` in the name; anything else gets the **Office** interiors. Ours: names like
  `NA_LibertyHouse` therefore show office rooms; a residential tower would need e.g. `NA_LibertyHouseResidentialHigh`
  (untested: the name also becomes the prefab name).
- Interiors are chosen per checksum on the mesh name; 25 room variants per UV map, curtains differ per room; lights
  randomised by occupancy, except 2 cells (always on / always off).
- **Modelling:** one polygon per window opening (one behind a multi-frame window is OK, never one behind several
  windows). Align to the frames (snapping) or scale slightly up so it covers the gap behind the frame.
- **UVs:** use as many room cells as logically possible; islands may cover the red label text of the reference sheet;
  **top edge of each window up in UV space** (orientation = world orientation); **no overlapping UVs**; island size
  doesn't matter (bigger recommended) but **keep the aspect ratio** (scale down, never squash). Nearby windows mapped
  to the same cell read as one room. Ours: exact cell maths in `export.md` "Night-window UV cells".

### Glass (`_Gls`)
- The game's glass shader; **no textures needed**.
- Model separately; **give it thickness, avoid single planes**. The wiki says the shader is single-sided and will not
  render back faces.
  - Ours: the decompile shows `_Gls` gets template `DefaultGlassDoubleSided` (`stations.md`), and two transparent
    layers a few cm apart glittered on a large station canopy. Reconcile: thin closed slabs (also needed for camera
    collision), normals outward, and if they glitter at distance, thicken toward the 15-20 cm depth rule.
- **UVs:** auto-unwrap is fine, but the shader paints smears and dirt from the UVs, so **unwrap large canopies by
  hand**. **No overlapping UV islands** (so never collapse all glass onto one atlas cell).
- Not in the wiki: `_Glf` / `_Glm` variants (ours: `_Glf` resolves to the same template).

### Grass (`_Gra`)
- Projects the terrain texture beneath it onto the surface (a continuation of the ground). Auto UV with minimal
  distortion; no overlaps. Ours: UV scale notes in `export.md` (stock ~0.0055/m).

### Water (`_Wat`)
- Pools and fountains: semi-transparent, animated loop.
- UV with minimal distortion, no overlaps; **the water texture scale follows UV island size**, so maximise UV space.
  Polygonal water surfaces: unwrap by hand and don't split the UV topology.
- **Model the surface under it in the main mesh** (pool floor/walls), since water is see-through.
- **Water enclosed by a `_Gls` sub-mesh is not rendered.**

## 4. Textures

- Maps: `_BaseColor`, `_ControlMask`, `_MaskMap`, `_Normal`, `_Emissive`. PNG, power of two, **512-4096**, **all the same
  resolution** (per surface). Virtual texturing downsizes per user settings. Ours: VT-safety rule in `export.md`.
- Texture sharing across assets works best for assets seen together (service building + its upgrades).
- Style: vanilla is "clean"; grunge is OK but avoid repeating patterns on roofs and walls.

| Map | Channel | Meaning |
|---|---|---|
| BaseColor | RGB | diffuse |
| | A | **opacity, alpha CLIP at 50 %** (opaque or fully transparent; white = visible) |
| ControlMask | R, G, B | **colour masks 1-3** (multiply, mutually exclusive; black = none, white = full). See [Assets: Setting Up Color Variations]. Ours: `configure.md` ColorProperties |
| | A | **SnowRemove: white = NO snow, black = full snow. Not implemented yet** (future-proofing only). Paint white under roofs/terraces/balconies/stairs, black on open roofs |
| MaskMap | R | metallic |
| | G | coat (extra gloss layer) |
| | B | unused, keep black |
| | A | glossiness (= 1 - roughness) |
| Normal | RGB | **OpenGL** convention (Y+, Blender's convention) |
| Emissive | RGB | colour + brightness |

- Ours on ControlMask: we ship RGB 0 / A 1, i.e. no colour variation and (per the wiki) "no snow". Our older notes
  called A = 1 "snow allowed"; by the wiki it means snow removed. Harmless until SnowRemove ships.
- **Emissive, two exclusive pipelines:**
  - *Single*: one `_Emissive.png`, one controllable purpose (usually decorative light).
  - *Multiple*: up to 4 pairs `_Emissive0..3.png` + `_EmissiveID0..3.png`. ID map = grayscale light ID 1-255 (0 = no
    light); space IDs apart (e.g. every 10; neighbours can merge). Pixels sharing an ID are one controllable light.
    **Max 4 lights per triangle** (including neighbours sharing vertices), roughly no more than 4 per UV island;
    overlapping lights need another map pair. Emissive maps may be dilated; **ID maps must be pixel-perfect, no
    anti-aliasing**. Ours: vehicles use `_Emissive0`; the importer then makes one `_Emissive` per LOD (`export.md`).

## 5. LODs
- Two LODs are required. The LOD2 switch distance depends on graphics settings, mesh size and lot size.
- **LOD1:** < 50 % of LOD0's triangles, made FROM the main mesh; remove small features (handles, ornaments, rooftop
  details, pipes, gutters), simplify frames but keep the topology and silhouette; decimate OK if UVs survive; **shares
  LOD0's textures**.
  - **LOD1 needs LOD1 sub-meshes too** (`_LOD1_Win`, `_LOD1_Gls`, ...). Don't simplify the window sub-mesh topology
    (its UVs drive the shader), and make sure the simplified LOD1 main mesh doesn't cover or gap around the `_Win`.
- **LOD2:** silhouette only, **max 500 tris**; custom mesh with baked textures **512-1024 px (1024 max)**, or share the
  main textures if the UVs fit.
  - Low-poly `_LOD2_Gra` / `_LOD2_Wat` are supported.
  - `_LOD2_Win`: may be virtually the main window mesh (night lights at distance); only windows visible from afar;
    **floating just above the LOD2 surface**; **exactly the same UVs as the main `_Win`**.
  - Small glass/grass can be dropped from LOD2: **bake the glass into the LOD2 texture in a colour close to the glass
    tint** instead of a `_LOD2_Gls`.
  - Ours: a shared baked LOD2 atlas for big packs and stepped 2048/1024/512 LOD maps: keep LOD2 maps at 1024 or below.

## 6. Export
- Main mesh: exactly one material named like the mesh (or `_Mtl`). Sub-meshes: no material.
- **FBX 2018** per the wiki. Ours: Blender writes FBX 7.4 and the importer has taken it on every asset; the settings that
  matter are in `export.md` section 5.
- Full example set for `ExampleAsset`:
  - LOD0: `ExampleAsset.fbx`, `_Win`, `_Gls`, `_Gra`, `_Wat`
  - LOD1: `ExampleAsset_LOD1.fbx`, `_LOD1_Win`, `_LOD1_Gls`, `_LOD1_Gra`, `_LOD1_Wat`
  - LOD2: `ExampleAsset_LOD2.fbx`, `_LOD2_Win`, (`_LOD2_Gls`; the wiki table has a typo here), `_LOD2_Gra`, `_LOD2_Wat`
  - Textures: `ExampleAsset_{BaseColor,ControlMask,MaskMap,Normal,Emissive}.png` (LOD1 shares them),
    `ExampleAsset_LOD2_{...}.png`.
