# Changelog

Mod versions are in `mod/Bridge.cs` (`Version`). The server checks the mod's version (via `ping`) and falls back to older behaviour
where it can, so a newer server works with an older mod.

## Unreleased
- `cs2_place_roads auto_fix`: a preview with errors is fixed by the server — draft segments within
  `auto_fix_radius` (12 m) of an error position are dropped and the preview is redone, up to `auto_fix_rounds` (4) times — so the caller only
  sees a clean preview. The result reports `auto_fix` (rounds, dropped segments, groups left unconnected to the existing network); a held
  preview that cannot be cleaned is cancelled. Server only (`server/autofix.py`, test: `server/test_autofix.py`).

## 0.38 – 0.45 — skills, precision road tools, services, brands
- **Skills library** (`skills/`): four Claude Code skills — city building, the Blender → game asset pipeline, Blender
  architecture modelling, and Paradox Mods publishing — with their helper scripts. See the README.
- Roads: `cs2_road_snapshot` / `cs2_road_restore` (save an area's exact road geometry and rebuild it later),
  `cs2_road_profile` (side view: height above ground, grades, clearances), `cs2_node_detail` (one junction: every arm,
  lane and car movement), `cs2_highway_fork` (a proper elevated highway Y the way the game's own interchanges do it),
  direction-safe joins (no highway ↔ local at-grade joins, no head-on one-ways).
- Services: `cs2_service_access` (why a hearse or ambulance never arrives: spawn locations, lane connections) and
  `cs2_path_test` (run the game's own dispatch pathfind on demand), `cs2_sites_tiled` (service / park sites over a whole
  island), `cs2_upgrade_all` (install fitting upgrades on many buildings at once).
- Zoning: `cs2_waterfront_backs` (lots that turn their back on the water), `cs2_fix_access` (rebuild growables with
  access warnings).
- Prefabs: `cs2_prefab_create` (new empty user prefab), company brands `cs2_brand_info` / `cs2_brand_bind` /
  `cs2_brand_bindings`.
- `cs2_building_warnings icon_details`: where each icon is and which lane / edge / object it flags.
- 0.39: no more "Cached ServiceUpkeepData not found" error popups after prefab work — the mod repairs the game's upkeep
  cache after prefab changes and after every load, and reports prefabs with duplicate Money upkeep entries.
- 0.44: bulldozing a building removes it the way the game's bulldozer does — with its sub-nets, sub-areas and
  installed upgrades — so big assets no longer leave orphan pillars or tracks behind.

## 0.37 — mesh export
- `cs2_mesh_export`: a prefab's render meshes at every LOD as OBJ/MTL, plus a JSON sidecar (submesh materials, bones,
  skin weights, placements). It reads the game's `.Geometry` files with the game's own decompression and never touches
  loaded meshes. `tools/import_mesh_export.py` loads the export into Blender with the correct axes and winding.

## 0.36 — zero zone cracks
- `road_align` / `cs2_road_align` and `cs2_zone_cracks`. These fix the 4 m slivers between zone blocks by setting the
  half-alignment flags the game's own road tool sets. Builders align the edges they place automatically.

## 0.35 — batched zoning
- `zone` takes up to 1000 quads as one operation: one preview, one undo entry and no tool flicker. The server
  batches `cs2_zone_along`, `cs2_zone_palette`, `cs2_rezone` and `cs2_rezone_buildings`.

## 0.34 — import window lights, prefab clone
- `cs2_asset_import` gains `window_light`, `window_light_lod` and `window_color`. New `cs2_prefab_clone` for lot-size
  variants that share a mesh.

## 0.33 — safety hardening
- The bridge refuses browser-originated requests (`Origin` / `Sec-Fetch-*` → 403) and non-JSON bodies (415). Body and
  JSON-depth caps. One thread per request.
- Timed-out commands are dropped, or abandoned without being applied.
- Undo is scoped to one city load. The save name is tracked reliably on load.
- No edits while a save is loading.
- `place_building` refuses prefabs with built-in nets unless `allow_subnets` is set (dry runs too).
- `asset_configure` edits only user assets. Vanilla edits are runtime-only.
- `screenshot save_png` writes only inside allowed folders.
- The definition sweep now uses the game's own query, so it no longer deletes pending simulation definitions.

## 0.30 — clean screenshots
- `cs2_screenshot clean=true` (Photo Mode capture path, no UI or overlays, optional supersampling), `cs2_photo_mode`,
  and `cs2_camera_goto` with yaw and pitch.

## 0.27–0.29 — deathcare, upgrades, interchanges
- Deathcare in `cs2_service_coverage`. `cs2_place_building parent=` installs service upgrades. `cs2_site_movements`,
  `cs2_network_check` and `cs2_design_path` (Dubins paths with grade and clearance profiles) help design junctions for
  a specific site. The player's tool and prefab are restored after each operation.
- Server: `cs2_service_sites` and `cs2_zoning_check`. Every builder's dry run returns a zoning forecast.

## 0.17–0.26 — session control, prefab data, robustness
- `cs2_list_saves`, `cs2_load_save`, `cs2_main_menu`, `cs2_restart_game`.
- `cs2_missing_assets` (leftovers of uninstalled mods) and `cs2_menu_assets`.
- `cs2_prefab_entity` / `cs2_prefab_overrides`: persistent ECS prefab-data edits, re-applied at every start.
- District policies go through the end-frame barrier. A tool-busy watchdog and `cs2_tool_reset`.

## 0.15–0.16 — services and detailing
- `cs2_service_coverage`, `cs2_transit_report`, `cs2_foot_traffic`, `cs2_commute_flows`, `cs2_station_sites`,
  `cs2_service_budget`, `cs2_attractiveness`, `cs2_park_sites`, `cs2_list_object_types`, `cs2_place_objects`.
- Server hot reload.

## 0.11–0.14 — asset pipeline, city health
- `cs2_asset_validate`, `cs2_asset_import` and `cs2_asset_configure`: Blender → game without the Editor GUI. Build-menu
  icon, title and description.
- `cs2_zone_report`, `cs2_building_warnings`, `cs2_rezone`, `cs2_rezone_buildings`, `cs2_zone_palette`,
  `cs2_zone_snapshot`, `cs2_demand`.

## 0.8–0.10 — geometry helpers and waterfronts
- Ramps, extending lines, trimming, obstacles and pillars, shore rays, `@name` references, camera tours. Piers, quays
  along a shoreline, offset streets, building access, arcs, terraforming.
- Tracks and every drawable net can be placed. `cs2_place_building` and `cs2_building_nets`.

## 0.2–0.7 — foundations
- The bridge, the editing tool with preview, error reasons, hold, commit and cancel, and undo with an `expect_id`
  guard.
- Roads: straight, curved, grid, circle, crossings split into junctions, elevation. Zoning, bulldoze, replace.
- Perception: road network, zone blocks, terrain grid, screenshots, `cs2_map`, `cs2_inspect`.
- Districts and policies. District grids, rim roads, connect options, plans and plan removal. One-way pairs, paths,
  lines, traffic, zone summaries, probing.
