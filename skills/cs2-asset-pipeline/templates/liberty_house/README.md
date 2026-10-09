# Liberty House export scripts (reference snapshot)

This is the working reference implementation: a stable snapshot of the scripts used to export the Liberty House
asset, to adapt from. Copy them into a new project's
`scripts\`, rename them (`lh_common.py` → `<proj>_common.py`), and fix the `exec(open(...))` paths.

| Script | Runs in | Does | Change for a new asset |
|---|---|---|---|
| `lh_common.py` | Blender | dimensions, storey table, material list (`MAT_SPECS`), `get_mats()`, facade helper `fb()` | all dimensions and materials |
| `build_all.py` | Blender | clears materials, runs the part scripts in order | the part script list |
| `make_atlas.py` | system Python | 2048 trim-sheet atlas + `atlas_layout.json` | `STRIPS`, colours, `PANE`, `TYP`, emissive levels |
| `export_build.py` | Blender | LOD0 main mesh + `_Win` window islands, UVs, rotation | `MAT2STRIP`, `FLOOR_Z`, collections, `ROT180` |
| `lod_render.py` | Blender | orthographic S/E/N/W/Top elevations for the LOD atlas | `PPM` (fit 2048) |
| `compose_lod_atlas.py` | system Python | packs the elevations into LOD atlases + `lod_layout.json` | usually nothing |
| `lod_build_export.py` | Blender | LOD1/LOD2 boxes + plaza, UV projection, **writes all FBX** | the boxes, plaza size |
| `render_icon.py` | Blender (headless ok) | build-menu icon via `render_icon()` | collections, view angle |
| `NA_LibertyHouse_meta.json` | - | build-menu title + description + icon | the text |

Run order: `build_all → make_atlas → export_build → lod_render → compose_lod_atlas → lod_build_export → render_icon`.
The Blender steps go through the Blender MCP (`exec(open(path).read())`) or headless (`blender -b file.blend -P script`).
The system-Python steps run with `python script.py`.
