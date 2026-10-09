# Blender → Cities: Skylines II asset pipeline (no Editor GUI)

Proven end to end with a test building ("Liberty House"): model in Blender,
export, import into the running game through the CS2MCP mod, configure it as a signature building, place it.

> A summary of the workflow and the importer pitfalls found so far. Tool reference: [TOOLS.md](TOOLS.md).

## Tools (MCP server `cs2`, mod ≥ 0.12)

| Tool | Where | What |
|---|---|---|
| `cs2_asset_validate` | server, offline | naming, FBX units (must be cm), one material on main meshes, `_Win` file present, textures, clean project root |
| `cs2_asset_import` | mod `asset_import` | calls `Game.UI.Editor.AssetImportPanel.ImportAssets` (the Import button's code) as a job; preset / template / lot; reports prefab + log errors + hints |
| `cs2_asset_configure` | mod `asset_configure` | `copy_from` a vanilla prefab's gameplay components, `add/remove_components`, `set {"Comp.m_Field": v}`, lot, `describe`; **mod 0.14:** `icon` (PNG -> ImageAsset next to the prefab -> `UIObject.m_Icon`), `title` / `description` (LocaleAssets `Assets.NAME/DESCRIPTION[<name>]`, applied live) |
| `cs2_place_building` | mod | place by prefab name (signature buildings: once per city) |
| `cs2_screenshot` | mod | see the result (don't move the user's camera unless asked) |

The bridge can also be called directly with a long timeout (e.g. a scratch `bridge.py`: POST `/rpc`
`{"method": "asset_import", "params": {..., "timeout_s": 1200}}`) - `cs2_raw` has a 90 s client timeout.

## Asset folder layout

```
<project root>\                 e.g. ...\liberty-house\cs2   (asset folders only - intermediates go to a sibling _build)
  NA_LibertyHouse\               folder name == LOD0 file name
    NA_LibertyHouse.fbx          main mesh, ONE material named NA_LibertyHouse, UVs on a trim-sheet atlas
    NA_LibertyHouse_Win.fbx      windows sub-mesh, NO material (separate FILE - see below)
    NA_LibertyHouse_LOD1.fbx / _LOD2.fbx
    NA_LibertyHouse[_LODn]_{BaseColor,Normal,MaskMap,ControlMask,Emissive}.png   (square, power of two)
<project root>_build\            e.g. ...\liberty-house\cs2_build  (NOT inside the asset folder)
  NA_LibertyHouse_meta.json      {"title": "...", "description": "...", "icon": "NA_LibertyHouse_Icon.png"}
  NA_LibertyHouse_Icon.png       build-menu picture: square 512 px, transparent background (render_icon() helper)
```

**Every asset needs a picture, a name and a description.** Without them the build menu shows no picture and no text.
`cs2_asset_import` applies them automatically after each import, from the meta file or from `icon`/`title`/`description`
arguments. `cs2_asset_validate` warns when any of them is missing.

## Rules learned the hard way

1. **Units: centimetres.** The FBXImporter applies scale 0.01 and ignores `UnitScaleFactor` and node transforms.
   Blender export: `apply_unit_scale=True, global_scale=1.0, apply_scale_options='FBX_SCALE_NONE', bake_space_transform=True`.
   The dialog default leaves bake off, which gives metre vertices plus a ×100 node scale, so the building is 1/100 size. (`FBX_SCALE_UNITS` wrote metres →
   the building imported at 1/100 size and looked invisible.) A Blender round-trip import hides this - check raw extents.
2. **Night windows = `<Name>_Win.fbx`.** The importer's `Windows` template + `WindowsPostProcessor` match the FILE name
   (`^.*_Win.fbx$`), not objects inside the main FBX. Each connected island = one window; windows are grouped into rooms per
   floor (floorHeight 3 m) and lit at random at night with parallax interiors. Liberty House cuts every outward glass pane
   per floor and per ~3 m bay, each its own island (don't merge vertices), drops inward-facing back panes. The file joins
   the same render prefab (one root mesh). `_Wim/_Wif/_Wio` = milky / frosted / opaque variants.
3. **Emissive map glows day and night** (no EmissiveProperties are set up). Linear 0.35 was white-hot, 0.05 still too
   bright; use 0 for windows (they come from `_Win`) and ~0.12 for a deliberate highlight.
4. **Front / road side = Blender −Y** for how Liberty House was placed; the street end must face −Y (it was rotated 180°
   in the exporter). Verify on the next asset.
5. **Base:** give the lot a ground surface (Liberty House: a 32 × 64 m paved slab, tiled paver strip, in every LOD), or the
   building sits on raw grass. The importer also knows a `_Base` file suffix (repeating textures) - untested.
6. **Re-import in place with `preset: "None"`**: refreshes meshes and textures, keeps the prefab config (signature,
   households, lot). Fails with "being used by another process" once the game has loaded the building's `.Geometry` →
   quit to the main menu (or restart), re-import, reload the city.
7. **Main menu + a creating preset** → the Find It mod crashes in `ActivatePrefabTool` (the asset is still saved). Import
   new assets from a loaded city.
8. **Prefab lookup:** `PrefabID` includes the asset hash, so `(type, name)` never matches imported assets; the mod scans
   `PrefabSystem.m_Prefabs` by name.
9. **Imported prefabs persist** in `%LOCALAPPDATA%Low\Colossal Order\Cities Skylines II\ImportedData\<Asset>` and load
   with every city.

## Making it a real building

```
cs2_asset_configure name=NA_LibertyHouse copy_from=NA_ResidentialHighSignature01
    remove_components=["Unlockable","BuildingTerraformOverride"] add_components=["BuildingProperties"]
    set={"m_LotWidth":4,"m_LotDepth":8,"BuildingProperties.m_ResidentialProperties":440,
         "UIObject.m_Icon":null,"UIObject.m_Priority":210,"SignatureBuilding.m_UnlockEventImage":null}
```
`copy_from` skips sub-objects/areas/lanes/nets/effects (they belong to the template's geometry). Clear the template's
icon / unlock image or the build menu shows the vanilla building's picture. Removing `Unlockable` = always available.

## Blender side

A typical Blender pipeline: build the model -> bake a trim-sheet atlas -> export LOD0 + `_Win` (rotated so the street
end faces -Y) -> render orthographic elevations for LOD textures -> export LOD1/LOD2 boxes -> render a 512 px
transparent icon (`blender -b <file>.blend -P render_icon.py`) -> write `<Name>_meta.json`. Then `cs2_asset_validate`,
`cs2_asset_import`, `cs2_asset_configure`.

To go the other way (game mesh -> Blender, e.g. for reference), use `cs2_mesh_export` and
[`tools/import_mesh_export.py`](../tools/import_mesh_export.py).

## Still to do

- Check that the icon and locale assets are included when publishing (they sit in the prefab's own `ImportedData/<Name>` folder).
- Station sub-nets (tracks/platforms) from a Blender-exported layout JSON.
- Mod: surface pipeline errors in `asset_import` itself (the server already does), `_Base` sub-mesh test.
