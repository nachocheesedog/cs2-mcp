# Community guidelines: LODs, detail, windows (C:S Modding Discord)

Code-modding (scripting) rules from the same Discord are summarised in `game-internals.md` ("Code-modding
guidelines"); read them before changing the CS2MCP mod.

## Credits

Everything in this file is condensed from guides written by members of the **Cities: Skylines Modding** Discord
community (Knowledgebase → `#resources-asset-creation`). The knowledge and the testing behind it are theirs; this is
only a summary with notes on how our pipeline applies it. Please read the original threads (and thank the authors)
there: they carry images, add-ons and detail this summary leaves out.

| Guide (thread title) | Author(s) / credit |
|---|---|
| "[WIP] Level of Detail: Concept and Creation of LOD Meshes" | Cities: Skylines Modding Knowledgebase, built on tests by **Feindbold** and **kjmci** |
| "Up to Date \| Technical Pipeline \| Using New Windows Shaders" (game 1.6.2f1) | Cities: Skylines Modding Knowledgebase authors |
| "Window layout guidance" (WindowsIlluminationMapRef image, per-zone window sheets) | **Macwelshman** and **TheOcter** |
| "MW_CS2_Window_UV_Distributor" Blender add-on | **Macwelshman** |
| "Asset Creation Pipeline using only Blender" | **cbudd** |
| "Creating LOD1 and LOD2 models with InstaLOD" | **kjmci** |
| "Ultimate Guide to dealing with EmissiveID Textures", "MW CS2 Combiner App" (texture packer) | Cities: Skylines Modding Knowledgebase authors |

Official reference: the Paradox wiki page "Asset Pipeline: Buildings"
(https://cs2.paradoxwikis.com/Asset_Pipeline:_Buildings), condensed with our deviations in `wiki_buildings.md`.

## LOD rules (official technical guideline + the tutorial)
- CS2 uses 3 LODs: LOD0 main, LOD1 mid distance, LOD2 far.
- **LOD1 ≤ 50 % of LOD0's triangles. LOD2 ≤ 500 triangles, the windows sub-mesh EXCLUDED from that count.**
- **LOD1 must keep LOD0's silhouette.** Don't bake LOD0 onto a new LOD1 shape: the silhouette change is noticeable to
  the player. Make LOD1 by *decimating LOD0* and sharing LOD0's textures and UVs.
  - Use several small Decimate passes, applying and fixing between passes. One 50 % pass gives poor results.
  - Or use InstaLOD (free for individuals): Optimize profile, Global Scope OFF, LOD1 "Percent Triangles 50 %",
    LOD2 "Absolute Triangles 500". Keep UV boundaries/seams for big multi-island buildings. Re-import into Blender to
    check the scale before exporting.
- **LOD2 for big or complex buildings (skyscrapers): make a custom low-poly mesh and BAKE LOD0's textures onto it**
  ("Method B"). Decimators struggle with skyscrapers, and texture sharing can't work under 500 tris.
  - Blender: bake in Cycles, "Selected to Active", LOD0 and LOD2 in the same position. Select LOD0, then LOD2, then Bake.
    Give LOD2 its own material. Bake with standard PBR inputs, then REPACK into CS2 maps (MaskMap / ControlMask);
    tools: Substance export profile, the MW CS2 Combiner, or any channel packer.
  - Colour-variation and snow masks: swap them into the diffuse slot and bake them as diffuse.
  - **LOD2 textures need the snow mask** (ControlMask). Follow the official wiki's snow-map notes.
  - Partial bake (diffuse + roughness) is fine for small things; buildings deserve a full bake (diffuse, roughness,
    metal, colour-vars).
- Easiest ("Method A"): the SAME textures for LOD0/1/2. That also saves draw calls, but LOD0's UV layout then constrains
  the LOD topology.
- Verify in the editor: add a colour mask and run an RGB colour test on the mask to confirm the channels import right.
- Check triangle counts in Blender (Statistics overlay) or with `tri_count()` in the export scripts.

**Implemented in a later project's exporter (decimated LOD1):**
- `export_asset.py -- <Asset> lod1 amin=<m2>` builds LOD1 from LOD0's own faces:
  - drops faces under amin m² (fins, mullions, kerbs, stools), but keeps logo and sign letters;
  - keeps the glass as whole plain facets (no window cut), with the same atlas + UVs.
- A `pipeline.sh` loop raises amin (0.8 -> 1.5 -> 3 ...) until LOD1 is at most 45 % of LOD0 (three towers ended at 43 %, 27 %
  and 43 %).
- `lod_build_export.py` uses that object for LOD1 (the box-prism LOD1 remains only as a fallback);
  `compose_lod.py` bakes only LOD2 (ControlMask A = 1 everywhere, like LOD0; by the wiki that means SnowRemove = no snow, a feature that isn't implemented yet).
- Copy this into new projects instead of the Liberty House box-prism LOD1.

**How the older pipeline (including the Liberty House template) compares:**
- Our LOD1 is a hand-made box massing (~60 tris) textured by orthographic elevation renders. That is a BAKE onto a
  new shape, which the guide warns changes the silhouette at mid distance (crowns, setbacks and fins vanish).
- **Fix for new assets:** LOD1 = LOD0 decimated in steps to ~40–50 % while keeping the silhouette elements, using the
  SAME atlas and UVs. Our trim-sheet atlas makes texture sharing trivial.
- LOD2 = our box massing + elevation-render atlas, a valid "custom LOD2 + bake" at ≤ 500 tris. Also add a snow mask:
  compose_lod writes ControlMask alpha = 1 except the roof rect. By the wiki (A white = no snow), that correctly lets snow settle on the roof only.

## Detail and texel density
- CS2 is built for close zoom. Model real geometry for window frames, doors, gutters, fins and so on, rather than
  painting them into the texture as in CS1.
- Region packs (Germany, USA, France) sit at **180–250 px per metre** texel density with 4K textures. They reach it by
  cutting walls into pieces and STACKING UVs.
  - Our trim atlas is 64 px/m (2048 px = 32 m): readable at game distance but soft up close.
  - For hero assets, consider a 4096 atlas (128 px/m) with stacked/tiled UVs, or finer strips for the street level.
- Stay conservative with geometry: cities render millions of tris, and ported architectural models (multi-million
  polys) are a known anti-pattern. Culling removes hidden objects and back sides; don't model what the camera can't see
  (undersides).

## Windows sub-meshes (game 1.6.2f1+)
- Sub-mesh file suffixes (the importer matches FILE names):
  - `_Win` = standard parallax-interior window
  - `_Wim` = a variant ("slightly different effect")
  - `_Wif` = **frosted** (textured overlay, still parallax + night light)
  - `_Wio` = **opaque** (no interior render, but window material and day/night light)
  - `_Gls` = clear glass (see-through, e.g. a station roof); `_Glf` frosted glass; `_Glm` milk glass.
- Since 1.6.2 the default parallax is more neutral and realistic, and the importer's right-hand panel (click the windows
  sub-mesh) has these settings:
  - **Window Color** + **Tint Strength**: a multiply tint; start close to 0.
  - **Interior Light Intensity**: small values; on frosted it also changes the tint.
  - **Decorations**: curtains on/off.
  - These can't be changed after import. Re-import with a new `seed` to try values. Our `cs2_asset_import` takes
    `seed`, but NOT these window params yet (a mod tool gap: add them to AssetImport.cs if we need them).
- **Window UV placement picks the interior + curtain.** The UV square is a 5×5 grid of cells (WindowsIlluminationMapRef):
  - cell 1 (top-left) = "ALWAYS ON"; the others are ROOM 01…, each with its own interior picture and curtain/blind style
    (some have none).
  - The room art differs per zone type: Res Low/Med/High, Com Low/High, Office, Industrial (TheOcter's per-zone sheets).
  - So spread window islands across the ROOM cells, not one strip. Avoid always-on/off cells unless wanted. For offices,
    prefer the no-curtain cells.
  - Macwelshman's "MW_CS2_Window_UV_Distributor" Blender add-on does this and has a "Blank Only" (no curtain) option.
  - SOLVED (decompiled): room id = 5*floor(5v)+floor(5u) with Blender v UP, so id 0 (always on) is the
    BOTTOM-left cell in Blender UV space and 24 (never on) is top-right. A `room_cells` step in the exporter re-maps each island.
- Window base colour should be BLACK. No coloured emissive on buildings: building meshes have no day/night emissive
  switching, so baked emissive glows all day (matches what we found).
- "Any surface in a window mesh always shows the parallax shader." For painted or custom windows, put them in the main
  mesh (with emissive, but that glows day and night).
- Individual windows seem to have a practical maximum size, and very large connected window islands misbehave: split
  big glazing into per-floor, per-bay islands, as our exporter does.

## Other points from #ai-discussion
- Community view: AI-made ASSETS are low-risk (worst case odd textures, or white boxes after removing an asset). AI-made
  CODE mods are riskier: scripts that modify save files can corrupt them. Our CS2 MCP mod never writes save files
  directly; keep it that way, and always ask before saving.
