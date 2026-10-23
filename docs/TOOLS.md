# Tool reference

All 126 tools the `cs2` MCP server exposes, generated from the server's registry by `tools/gen_tools_doc.py`. `*` = required parameter.

Conventions: world coordinates are the game's (x/z horizontal in metres, y up); zoning cells are 8 m. Every editing tool accepts `dry_run` (preview + error reasons, nothing built) and most accept `hold` (keep the preview until `cs2_preview_commit` / `cs2_preview_cancel`). Edits return an `undo_id` for `cs2_undo`.

## Contents

- [Game, session and camera](#game-session-and-camera)
- [Seeing the city](#seeing-the-city)
- [Raw data and catalogues](#raw-data-and-catalogues)
- [Roads and networks](#roads-and-networks)
- [Zoning](#zoning)
- [Buildings, objects, terrain and areas](#buildings-objects-terrain-and-areas)
- [Districts](#districts)
- [City services and transit](#city-services-and-transit)
- [Previews and undo](#previews-and-undo)
- [Assets and prefabs](#assets-and-prefabs)

## Game, session and camera

### `cs2_status`

Check the CS2MCP game bridge: mod version, game mode, whether a city is loaded, money, population, road count.

### `cs2_camera`

Camera position/forward and the ground point at the centre of the screen (x,y,z metres). Use ground_point as a default location.

### `cs2_camera_goto`

Move the camera to look at x/z (optionally set zoom distance, and mod 0.30: yaw/pitch view angle in degrees).

| Parameter | Type | Description |
|---|---|---|
| `x` | number |  |
| `z` | number |  |
| `zoom` | number | camera distance (m); alias: distance |
| `distance` | number |  |
| `yaw` | number | compass heading of the view, degrees |
| `pitch` | number | look-down angle, degrees |

### `cs2_set_speed`

Set simulation speed: 0 = pause, 1, 2 or 3.

| Parameter | Type | Description |
|---|---|---|
| `speed` * | number |  |

### `cs2_settings`

Persistent server settings (settings.json): follow_camera (default true) = builders move the game camera to each change. Call with no arguments to read, or give values to change them.

| Parameter | Type | Description |
|---|---|---|
| `follow_camera` | boolean |  |

### `cs2_list_saves`

Saved games (mod 0.17), newest first: save name, city, modified time, population, autosave flag.

| Parameter | Type | Description |
|---|---|---|
| `limit` | integer |  |

### `cs2_load_save`

Load a saved game by save name (or city name) like the menu's Load button (mod 0.17); works from the main menu or in game (unsaved progress is lost). wait (default true) polls until the city is loaded.

| Parameter | Type | Description |
|---|---|---|
| `name` * | string |  |
| `wait` | boolean |  |

### `cs2_main_menu`

Return to the main menu (mod 0.17; unsaved progress is lost) - e.g. to re-import an asset whose geometry the loaded city holds. Waits for the city to settle first (a menu request during post-load settling hung the game).

### `cs2_restart_game`

Full game restart loop (mod 0.17 + process control): quit_game, wait for the process to exit, force-kill it if it hangs on exit (known game shutdown hang), relaunch via Steam, wait for the main menu, then optionally load `save`. Needed to load new mod builds or to reload prefab entities (sub-areas) after cs2_asset_configure. ONLY with the user's go-ahead: it closes the running game (unsaved progress is lost).

| Parameter | Type | Description |
|---|---|---|
| `save` | string | save to load after the main menu (e.g. the test city) |

### `cs2_history`

List the undo history (newest first).

### `cs2_tool_reset`

Unstick the MCP build tool (mod 0.25): drops an in-flight or orphaned operation (tool_busy stuck true, every build command answering 'tool busy') without a restart. Nothing half-applied is kept; a held preview is discarded. 0.25 also has a watchdog that does this automatically ~2 s after the tool loses the active-tool slot.

| Parameter | Type | Description |
|---|---|---|
| `reason` | string |  |

### `cs2_raw`

Call a bridge method directly (debugging): ping, city_info, camera, camera_goto, set_speed, terrain_height, list_road_types, list_zone_types, query_roads, query_buildings, place_roads, bulldoze, replace_roads, zone, undo, history, screenshot, road_network, zone_blocks, terrain_grid, preview_commit, preview_cancel.

| Parameter | Type | Description |
|---|---|---|
| `method` * | string |  |
| `params` | object |  |

## Seeing the city

### `cs2_screenshot`

Take a screenshot of the game and return the image. Optionally move the camera first (x, z, zoom, yaw, pitch) and wait settle_s seconds (default 2) for it to arrive. max_width scales the returned JPG (default 1280, max 3840). Mod 0.30: clean=true = a proper game shot like Photo Mode's 'Take photo' - no UI, no notification icons, overlays, road names or route lines (restored afterwards); frames = frames to wait before capturing (default 30, lets TAA settle); save_png = path to keep the full-resolution PNG (mod 0.33: a .png under %USERPROFILE%lender, %USERPROFILE%\cs2-mcp, the game's Screenshots folder or %TEMP%; more roots in ModsData\CS2MCP\save_png_roots.txt); supersize = 1..4x render scale. Combine with cs2_photo_mode for grading.

| Parameter | Type | Description |
|---|---|---|
| `x` | number |  |
| `z` | number |  |
| `zoom` | number |  |
| `yaw` | number |  |
| `pitch` | number |  |
| `settle_s` | number |  |
| `max_width` | integer |  |
| `clean` | boolean |  |
| `frames` | integer |  |
| `save_png` | string |  |
| `supersize` | integer |  |

### `cs2_photo_mode`

The game's Photo Mode settings (mod 0.30), same ids as the Photo Mode panel: Camera (focal length, aperture...), Lens (DepthOfField, Bloom, Vignette, FilmGrain, MotionBlur...), Color (ColorAdjustments.postExposure / contrast / saturation / hueShift / colorFilter r,g,b, WhiteBalance.temperature / tint, ShadowsMidtonesHighlights), Weather (clouds, fog, sky) and Environment ('Time of Day' 0-24). No args = list every property with value/min/max; group filters the list; set = {id: value, ...} applies them (the photo-mode volume switches on); reset = true restores all defaults. Always reset after a photo session so the player's view goes back to normal.

| Parameter | Type | Description |
|---|---|---|
| `set` | object |  |
| `reset` | boolean |  |
| `group` | string |  |
| `list` | boolean |  |

### `cs2_map`

Top-down map image of an area built from game data (not the camera): terrain contours + steep ground, water, zone cells, building lots, roads (by type or traffic), a coordinate grid, and markers for problems (dead ends, near-miss connections, sharp junctions, short links) plus a magenta wash on unused land within zoning reach. Look before planning. plan = draft segments (same shape as cs2_place_roads) drawn as a dashed overlay, to judge a layout before building; marks = [{pos:[x,z], label}]. Area: xmin/zmin/xmax/zmax, or center + size (m); default camera ground point, 1200 m.

| Parameter | Type | Description |
|---|---|---|
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `center` | number[] |  |
| `size` | number |  |
| `size_z` | number |  |
| `px` | integer |  |
| `colour_by` | string |  |
| `layers` | string[] |  |
| `outlines` | object[] | draft district outlines [{points:[[x,z],...], label}] drawn dashed purple |
| `plan` | object[] |  |
| `plan_file` | string | path of a JSON file on disk holding this list (saves sending it inline) |
| `marks` | object[] |  |

### `cs2_tour`

Screenshots of several spots in one call (points [x,z] / '@name', up to 8), or of the most recent edits made in this session (recent = how many, default 4). zoom, settle_s, max_width (default 900).

| Parameter | Type | Description |
|---|---|---|
| `points` | array |  |
| `recent` | integer |  |
| `zoom` | number |  |
| `settle_s` | number |  |
| `max_width` | integer |  |

### `cs2_inspect`

Facts-only check-up of an area (changes nothing): dead ends, near-miss connections, overlapping unconnected nodes, sharp junctions, kinks, short links between junctions, disconnected road islands, congested roads, junction arm counts, zoning totals, road-frontage depth histogram, and unused land pockets within zoning reach, with coordinates.

| Parameter | Type | Description |
|---|---|---|
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `center` | number[] |  |
| `size` | number |  |
| `size_z` | number |  |
| `short_link_m` | number |  |
| `sharp_deg` | number |  |
| `kink_deg` | number |  |
| `near_miss_m` | number |  |
| `sections` | string[] | only these parts: roads, zoning, unused_land, or sub-keys like roads.dead_ends / roads.sharp_junctions |
| `limit` | integer | max items per list (default 8) |
| `legend` | boolean | include the zone legend (default false) |

### `cs2_traffic`

Traffic facts for an area: length-weighted mean flow overall and per road type, and the worst congestion clusters (edges below threshold, default 0.5, grouped within cluster_m, default 80 m) with location, types, worst edge id and severity. Area: box or center+size (default camera).

| Parameter | Type | Description |
|---|---|---|
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `center` | number[] |  |
| `size` | number |  |
| `size_z` | number |  |
| `threshold` | number |  |
| `cluster_m` | number |  |
| `limit` | integer |  |
| `types_limit` | integer |  |

### `cs2_demand`

The game's demand bars (mod 0.13): residential low / medium / high building demand + household demand, commercial, industrial, office, storage, each with its demand factors (positive pushes demand up) - e.g. why shops report No Customers or high density is saturated.

| Parameter | Type | Description |
|---|---|---|
| `factors` | boolean |  |

### `cs2_zone_summary`

Zoning facts for an area box or a polygon: cells and hectares per zone family (residential low / medium / high / lowrent, mixed, commercial low/high, office, industrial) split built vs empty, shares, and unzoned cells. by_zone adds per-zone-type rows.

| Parameter | Type | Description |
|---|---|---|
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `center` | number[] |  |
| `size` | number |  |
| `size_z` | number |  |
| `polygon` | array[] |  |
| `by_zone` | boolean |  |

### `cs2_zone_report`

Zoning mix per district (or polygon / box): families with % built, the top zone styles as [cells, empty cells], empty zoned cells and unzoned cells. One call instead of zone_summary per district.

| Parameter | Type | Description |
|---|---|---|
| `districts` | string[] | district names (default: all) |
| `polygon` | array[] |  |
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `styles_top` | integer |  |

### `cs2_building_warnings`

Warning icons (notification bubbles) per district x building category (residential / commercial / mixed / office / industrial / service), as 'n of total (%)', with example ids and buildings without road access. ignore_icons default ['Leveling Building'] (levelling up is not a problem). Needs mod 0.10+.

| Parameter | Type | Description |
|---|---|---|
| `districts` | string[] | district names (default: all) |
| `polygon` | array[] |  |
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `ignore_icons` | string[] |  |
| `examples` | integer |  |

### `cs2_building_status`

Road access and warning icons (the notification bubbles) of buildings: id, ids, or every building in a box; problems_only keeps those without road access or with icons; icon_counts summarises. Needs mod 0.10.

| Parameter | Type | Description |
|---|---|---|
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `center` | number[] |  |
| `size` | number |  |
| `size_z` | number |  |
| `id` | string |  |
| `ids` | string[] |  |
| `problems_only` | boolean |  |
| `limit` | integer |  |
| `icon_details` | boolean | mod 0.45: per icon its world location and the element it flags (target lane/object/edge, prefab, curve ends, owner chain) |

## Raw data and catalogues

### `cs2_terrain_height`

Sample terrain height at [x,z] points.

| Parameter | Type | Description |
|---|---|---|
| `points` * | array[] |  |

### `cs2_terrain_grid`

Terrain heights and water depths on a regular grid (step metres, max 90k samples).

| Parameter | Type | Description |
|---|---|---|
| `xmin` * | number |  |
| `zmin` * | number |  |
| `xmax` * | number |  |
| `zmax` * | number |  |
| `step` | number |  |

### `cs2_road_network`

Road graph in a box: edges (id, type, width, length, 4 Bezier control points [x,y,z], start/end node ids, elevation, traffic flow 0..1) and the nodes they use (id, pos, degree, owned arms, outside connection). Utilities are left out by default (exclude_utilities=false keeps pipes, power lines, tracks). types = name substrings to keep; summary = per-type counts/length/mean flow only; compact = ends + node ids + flow per edge instead of full curves.

| Parameter | Type | Description |
|---|---|---|
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `limit` | integer |  |
| `types` | string[] |  |
| `exclude_utilities` | boolean |  |
| `min_elevation` | number |  |
| `summary` | boolean |  |
| `compact` | boolean |  |

### `cs2_zone_blocks`

Raw zone blocks in a box: position, direction (toward its road), size [along road, depth] and a cell string ('#' hidden, '.' empty, letters = zones per legend, upper case = built on).

| Parameter | Type | Description |
|---|---|---|
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `limit` | integer |  |

### `cs2_query_roads`

List existing road segments whose ends fall in an x/z box.

| Parameter | Type | Description |
|---|---|---|
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `limit` | integer |  |

### `cs2_street_names`

Streets in an x/z box (needs a rebuilt mod): each street's internal id (the game's road aggregate), the name the game shows (the player's custom name, else the rendered label), whether it is a custom name, how many road edges and metres it has and its road types. Edges outside any street (highways, ramps) are only counted. edges=true also lists each street's edge ids (match them with cs2_query_roads).

| Parameter | Type | Description |
|---|---|---|
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `limit` | integer | max streets returned (default 200) |
| `edges` | boolean | also list each street's edge ids |

### `cs2_query_buildings`

List buildings whose position is inside an x/z box (id, type, position).

| Parameter | Type | Description |
|---|---|---|
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `limit` | integer |  |

### `cs2_list_road_types`

List placeable net prefabs - roads (incl. quays like QuayLarge01, pedestrian streets), train/tram/subway tracks, and with mod 0.10 also pathways, fences and utilities - including modded ones - with width, max slope, kind and for tracks track_type + speed_limit. Any listed name works as a type in cs2_place_roads / place_path / place_pair / place_lines / place_piers. Optional substring filter; kind = all | road | track | pathway | fence | utility.

| Parameter | Type | Description |
|---|---|---|
| `filter` | string |  |
| `kind` | string |  |

### `cs2_list_zone_types`

List zone type names (e.g. residential low/high, commercial, office, industrial variants).

### `cs2_list_building_types`

Placeable (non-growable) buildings: service buildings, stations, harbours, depots, landmarks... with lot size (cells and metres), service, transport_station flag and cost. filter = name substring; growables=true to include zoned buildings.

| Parameter | Type | Description |
|---|---|---|
| `filter` | string |  |
| `limit` | integer |  |
| `growables` | boolean |  |

### `cs2_list_object_types`

Placeable trees and props (mod 0.16) - non-building static objects - by name filter; kind 'tree' | 'prop' | 'all'; size_m = [width, depth, height]. Parks / plazas are buildings: cs2_list_building_types.

| Parameter | Type | Description |
|---|---|---|
| `filter` | string |  |
| `kind` | string |  |
| `limit` | integer |  |

### `cs2_list_districts`

The city's districts: id, name, centre, area (km2), active policies and outline points (outlines=false for a short list). Draw them with cs2_map (layer 'districts').

| Parameter | Type | Description |
|---|---|---|
| `outlines` | boolean |  |

### `cs2_list_district_policies`

Names of the policies that can be set on a district.

### `cs2_obstacles`

What physically stands in an area, with real footprints (prefab geometry, rotated): buildings (+ their lots), building parts, pillars of elevated roads, trees, props. grid_m turns it into an occupancy grid (rows of characters: W water, S steep, R at-grade road, B building, P pillar, L lot, T tree, o prop, . free) - the quick way to see where a road or block fits. categories filters; names adds prefab names.

| Parameter | Type | Description |
|---|---|---|
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `center` | number[] |  |
| `size` | number |  |
| `size_z` | number |  |
| `grid_m` | number |  |
| `categories` | string[] |  |
| `names` | boolean |  |
| `building_props` | boolean |  |
| `roads` | boolean |  |
| `elevated_roads` | boolean |  |
| `terrain` | boolean |  |
| `steep_grade` | number |  |
| `limit` | integer |  |
| `full` | boolean |  |

### `cs2_pillars`

Elevated roads in an area and their pillar positions. With cross = [[x,z],[x,z]] (a street you want to pass underneath): where it crosses each elevated road, its deck height, the nearest pillar, and if closer than clearance_m (default 12) the nearest clear spots along the elevated road (shift_m each way).

| Parameter | Type | Description |
|---|---|---|
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `center` | number[] |  |
| `size` | number |  |
| `size_z` | number |  |
| `cross` | array |  |
| `clearance_m` | number |  |
| `min_elevation` | number |  |
| `limit` | integer |  |

### `cs2_areas`

Areas in a box - surfaces (painted ground materials), building lots, spaces - with id, kind, prefab and outline; counts per kind+prefab. kinds = [surface, lot, space, district, map_tile, other] (default all but district/map_tile).

| Parameter | Type | Description |
|---|---|---|
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `kinds` | string[] |  |
| `limit` | integer |  |

### `cs2_building_nets`

The nets a building owns (station platform tracks, harbour/yard tracks, internal roads) with end positions and the free ends (nodes with a single connection) - connect new track/roads to those exact points.

| Parameter | Type | Description |
|---|---|---|
| `id` * | string |  |

### `cs2_refs`

Named reference points, kept on disk across sessions: action set with items [{name, pos:[x,z]} or {name, near:[x,z]} (= the nearest road node, followed if the game moves it), note?], list, delete (names). Use '@name' anywhere a point is accepted (segments, knots, lines, at, from, corners...).

| Parameter | Type | Description |
|---|---|---|
| `action` | string |  |
| `items` | object[] |  |
| `names` | string[] |  |

### `cs2_frame`

Convert points between world x/z and a local frame {origin, heading_deg} (a = along the heading, b = to its left).

| Parameter | Type | Description |
|---|---|---|
| `frame` * | object | optional local frame for all input points: {origin:[x,z], heading_deg}; a point [a,b] = origin + a along heading (0 deg = +x, 90 = +z) + b to the left of it. Outputs stay in world x/z |
| `to_world` | array[] |  |
| `to_local` | array[] |  |

### `cs2_shore`

Edges of the buildable land. With from = [x,z]: cast rays (directions_deg, or every_deg around; frame makes them relative to its heading) and report the distance to water or to ground steeper than max_grade, and the last good point. Without from: the outline of water (kind 'water') or steep ground (kind 'steep') in an area as simplified polylines (tolerance_m), longest first.

| Parameter | Type | Description |
|---|---|---|
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `center` | number[] |  |
| `size` | number |  |
| `size_z` | number |  |
| `from` |  | [x,z] or '@name' |
| `directions_deg` | number[] |  |
| `every_deg` | number |  |
| `max_m` | number |  |
| `step_m` | number |  |
| `max_grade` | number |  |
| `frame` | object | optional local frame for all input points: {origin:[x,z], heading_deg}; a point [a,b] = origin + a along heading (0 deg = +x, 90 = +z) + b to the left of it. Outputs stay in world x/z |
| `kind` | string |  |
| `tolerance_m` | number |  |
| `min_length_m` | number |  |
| `water_m` | number |  |
| `step` | number |  |
| `limit` | integer |  |

### `cs2_shore_points`

Where the water is along a road or path: every spacing_m (default 50) the distance to water on its left and right (within max_reach_m), the waterline point, the seaward direction, the depth probe_m beyond the waterline and the land height there. Line = road_ids (existing roads, chained in order) or knots (frame accepted). Builds nothing.

| Parameter | Type | Description |
|---|---|---|
| `road_ids` | string[] |  |
| `knots` | array[] |  |
| `frame` | object | optional local frame for all input points: {origin:[x,z], heading_deg}; a point [a,b] = origin + a along heading (0 deg = +x, 90 = +z) + b to the left of it. Outputs stay in world x/z |
| `spacing_m` | number |  |
| `start_m` | number |  |
| `end_m` | number |  |
| `max_reach_m` | number |  |
| `probe_m` | number |  |

## Roads and networks

### `cs2_rename_streets`

Rename streets (needs a rebuilt mod): renames=[{id, new_name}] with ids from cs2_street_names (the street, not an edge). Sets the player's custom name, the same as the info panel. dry_run=true only reports was/name without changing anything.

| Parameter | Type | Description |
|---|---|---|
| `renames` * | object[] |  |
| `dry_run` | boolean |  |

### `cs2_place_roads`

Place road segments [{start:[x,z], end:[x,z], type?, control?:[x,z] (quadratic curve), controls?:[[x,z],[x,z]] (cubic curve)}] inline or from segments_file. Endpoints within snap_tolerance of existing nodes/roads connect to them; an end landing on another new segment of the batch makes a T junction; utilities (pipes, power lines) are ignored. The game previews first; if the preview has errors nothing is built unless allow_errors. dry_run previews only. Undoable (cs2_undo).

| Parameter | Type | Description |
|---|---|---|
| `segments` | object[] |  |
| `segments_file` | string | path of a JSON file on disk holding this list (saves sending it inline) |
| `frame` | object | optional local frame for all input points: {origin:[x,z], heading_deg}; a point [a,b] = origin + a along heading (0 deg = +x, 90 = +z) + b to the left of it. Outputs stay in world x/z |
| `follow_camera` | boolean | move the camera to the change first so the player sees it (default true; never on dry_run) |
| `camera_zoom` | number |  |
| `node_snap_m` | number | first move ends lying within this many metres of an existing road node onto it (fixes stale coordinates after the game moved a node); reported |
| `explain_errors` | boolean | on errors, report the nearest existing road/building at each error location (default true) |
| `check` | boolean | after building, report junction problems around the change (default true) |
| `before_after` | boolean | return a screenshot before and after the build |
| `type` | string | default road type for segments without one |
| `snap_tolerance` | number |  |
| `dry_run` | boolean |  |
| `allow_errors` | boolean |  |
| `hold` | boolean | leave the preview on screen; then cs2_preview_commit / cs2_preview_cancel |
| `no_intersections` | boolean | do not join roads this crosses (default: crossings become junctions) |
| `skip_failing` | boolean | if the game refuses the batch, find the refused segments by bisecting with dry runs, build the rest and return the refused ones |
| `verbose` | boolean | include created_ids (default: just their count) |

### `cs2_place_grid`

Plan and place a zoning-optimised road grid. Road spacing gives every block full zoning depth on both sides (zoning_depth_cells, default 6) using the real road widths; cross streets every block_length_cells. Optional arterials every Nth line and on the outer ring. pattern 'staggered' offsets cross streets to make T-junctions (better traffic flow than 4-way). center defaults to the camera's ground point. plan_only returns the segments without touching the game.

| Parameter | Type | Description |
|---|---|---|
| `center` | number[] |  |
| `angle_deg` | number |  |
| `cols` | integer |  |
| `rows` | integer |  |
| `local_type` * | string |  |
| `arterial_type` | string |  |
| `arterial_every` | integer |  |
| `zoning_depth_cells` | integer |  |
| `block_length_cells` | integer |  |
| `pattern` | string |  |
| `plan_only` | boolean |  |
| `zone_type` | string | optional: zone the whole grid afterwards (name from cs2_list_zone_types) |
| `overwrite_zoning` | boolean |  |
| `dry_run` | boolean |  |
| `hold` | boolean |  |
| `allow_errors` | boolean |  |

### `cs2_place_circle`

Place a ring road / roundabout loop of `type` as true circular arcs (pieces arcs, default 8). Counter-clockwise by default (right-hand traffic roundabouts); set clockwise for left-hand traffic. center defaults to the camera's ground point.

| Parameter | Type | Description |
|---|---|---|
| `center` | number[] |  |
| `radius` * | number |  |
| `type` * | string |  |
| `pieces` | integer |  |
| `clockwise` | boolean |  |
| `dry_run` | boolean |  |
| `hold` | boolean |  |
| `allow_errors` | boolean |  |

### `cs2_place_path`

Build one road along a path YOU give as knots [[x,z], ...]: smoothed through the knots (or straight pieces with smooth=false), optionally offset sideways (offset m, + = left of travel, with taper_start/taper_end m easing from 0), cut into cubic pieces about piece_m long with joints kept clear of avoid points, elevation as a number or a profile along the path. Direction = knot order (reverse flips it). Reports length and tightest radius. Same preview/error rules as cs2_place_roads.

| Parameter | Type | Description |
|---|---|---|
| `piece_m` | number | target length of each road piece (joints = nodes; elevated joints carry pillars). Default 110 |
| `avoid` | array[] | points to keep joints away from (e.g. where a street or rail passes under) |
| `avoid_m` | number | clearance from avoid points, default 25 |
| `joints` | array[] | force joints near these points (e.g. where a ramp will branch off) |
| `step` | number |  |
| `smooth` | boolean | Catmull-Rom smoothing through the knots (default true); false = straight pieces with joints at the knots |
| `plan_only` | boolean | return the segments without touching the game |
| `zoning_check` | boolean | dry_run / plan_only also return a zoning_forecast (default true) |
| `save_to` | string | also write the segments to this JSON file (reuse with segments_file / plan_file) |
| `frame` | object | optional local frame for all input points: {origin:[x,z], heading_deg}; a point [a,b] = origin + a along heading (0 deg = +x, 90 = +z) + b to the left of it. Outputs stay in world x/z |
| `follow_camera` | boolean | move the camera to the change first so the player sees it (default true; never on dry_run) |
| `camera_zoom` | number |  |
| `dry_run` | boolean |  |
| `hold` | boolean |  |
| `allow_errors` | boolean |  |
| `skip_failing` | boolean |  |
| `snap_tolerance` | number |  |
| `explain_errors` | boolean |  |
| `verbose` | boolean |  |
| `join_check` | boolean | check every end that lands on an existing node: highway <-> local road at grade, one-way head-on / head-to-head (default true; reported on dry runs, a build with conflicts is refused) |
| `allow_conflicts` | boolean | build even when join_check finds conflicts (only if the join is intended) |
| `check` | boolean | after building, report junction problems around the change (default true) |
| `before_after` | boolean | return a screenshot before and after the build |
| `deck_height` | number | absolute height (world y) to keep every new node at - level piers, causeways, viaducts; overrides elevation (ends that snap onto existing roads keep theirs) |
| `min_elevation` | number | with deck_height: never lower than this above ground |
| `knots` * | array[] |  |
| `type` * | string |  |
| `elevation` |  | metres above ground: a number, or a profile [[distance_m, elev_m], ...] along the path (negative distance counts back from the end), linear between points |
| `offset` | number |  |
| `taper_start` | number |  |
| `taper_end` | number |  |
| `reverse` | boolean |  |
| `joints_at_knots` | boolean |  |

### `cs2_place_pair`

Build a one-way pair (divided highway / couplet) from ONE centreline you give as knots: two carriageways separation m apart (centre to centre, default 24), each drawn in its correct direction for traffic (right, default, or left) - the forward one follows the knot order. taper_start / taper_end merge the carriageways onto the centreline at the ends (for Y-splits into a two-way road: taper >= ~3x separation keeps the split angle valid). types = [forward, return] to use different lane counts (e.g. an extra lane before an off-ramp). Elevation number or profile along the centreline. Reports each carriageway's end points so you can attach ramps.

| Parameter | Type | Description |
|---|---|---|
| `piece_m` | number | target length of each road piece (joints = nodes; elevated joints carry pillars). Default 110 |
| `avoid` | array[] | points to keep joints away from (e.g. where a street or rail passes under) |
| `avoid_m` | number | clearance from avoid points, default 25 |
| `joints` | array[] | force joints near these points (e.g. where a ramp will branch off) |
| `step` | number |  |
| `smooth` | boolean | Catmull-Rom smoothing through the knots (default true); false = straight pieces with joints at the knots |
| `plan_only` | boolean | return the segments without touching the game |
| `zoning_check` | boolean | dry_run / plan_only also return a zoning_forecast (default true) |
| `save_to` | string | also write the segments to this JSON file (reuse with segments_file / plan_file) |
| `frame` | object | optional local frame for all input points: {origin:[x,z], heading_deg}; a point [a,b] = origin + a along heading (0 deg = +x, 90 = +z) + b to the left of it. Outputs stay in world x/z |
| `follow_camera` | boolean | move the camera to the change first so the player sees it (default true; never on dry_run) |
| `camera_zoom` | number |  |
| `dry_run` | boolean |  |
| `hold` | boolean |  |
| `allow_errors` | boolean |  |
| `skip_failing` | boolean |  |
| `snap_tolerance` | number |  |
| `explain_errors` | boolean |  |
| `verbose` | boolean |  |
| `join_check` | boolean | check every end that lands on an existing node: highway <-> local road at grade, one-way head-on / head-to-head (default true; reported on dry runs, a build with conflicts is refused) |
| `allow_conflicts` | boolean | build even when join_check finds conflicts (only if the join is intended) |
| `check` | boolean | after building, report junction problems around the change (default true) |
| `before_after` | boolean | return a screenshot before and after the build |
| `deck_height` | number | absolute height (world y) to keep every new node at - level piers, causeways, viaducts; overrides elevation (ends that snap onto existing roads keep theirs) |
| `min_elevation` | number | with deck_height: never lower than this above ground |
| `knots` * | array[] |  |
| `type` | string |  |
| `types` | string[] |  |
| `separation` | number |  |
| `traffic` | string |  |
| `elevation` |  | metres above ground: a number, or a profile [[distance_m, elev_m], ...] along the path (negative distance counts back from the end), linear between points |
| `taper_start` | number |  |
| `taper_end` | number |  |

### `cs2_place_lines`

Build a street network from lines YOU lay out: [{points:[[x,z],...], type?}] (straight pieces between points). Every crossing and every end touching another line (within touch_m, default 1.5) becomes a real junction with one shared coordinate; ends near existing roads snap as usual. For grids with per-line extents, stubs to a shoreline, etc. lines_file / frame accepted.

| Parameter | Type | Description |
|---|---|---|
| `piece_m` | number | target length of each road piece (joints = nodes; elevated joints carry pillars). Default 110 |
| `avoid` | array[] | points to keep joints away from (e.g. where a street or rail passes under) |
| `avoid_m` | number | clearance from avoid points, default 25 |
| `joints` | array[] | force joints near these points (e.g. where a ramp will branch off) |
| `step` | number |  |
| `smooth` | boolean | Catmull-Rom smoothing through the knots (default true); false = straight pieces with joints at the knots |
| `plan_only` | boolean | return the segments without touching the game |
| `zoning_check` | boolean | dry_run / plan_only also return a zoning_forecast (default true) |
| `save_to` | string | also write the segments to this JSON file (reuse with segments_file / plan_file) |
| `frame` | object | optional local frame for all input points: {origin:[x,z], heading_deg}; a point [a,b] = origin + a along heading (0 deg = +x, 90 = +z) + b to the left of it. Outputs stay in world x/z |
| `follow_camera` | boolean | move the camera to the change first so the player sees it (default true; never on dry_run) |
| `camera_zoom` | number |  |
| `dry_run` | boolean |  |
| `hold` | boolean |  |
| `allow_errors` | boolean |  |
| `skip_failing` | boolean |  |
| `snap_tolerance` | number |  |
| `explain_errors` | boolean |  |
| `verbose` | boolean |  |
| `join_check` | boolean | check every end that lands on an existing node: highway <-> local road at grade, one-way head-on / head-to-head (default true; reported on dry runs, a build with conflicts is refused) |
| `allow_conflicts` | boolean | build even when join_check finds conflicts (only if the join is intended) |
| `check` | boolean | after building, report junction problems around the change (default true) |
| `before_after` | boolean | return a screenshot before and after the build |
| `deck_height` | number | absolute height (world y) to keep every new node at - level piers, causeways, viaducts; overrides elevation (ends that snap onto existing roads keep theirs) |
| `min_elevation` | number | with deck_height: never lower than this above ground |
| `lines` | object[] |  |
| `lines_file` | string | path of a JSON file on disk holding this list (saves sending it inline) |
| `type` | string |  |
| `touch_m` | number |  |
| `merge_m` | number |  |

### `cs2_place_arcs`

Concentric circular arcs and radial streets that meet in exact shared junctions - fans, crescents, rings, radial layouts. center [x,z] (frame accepted: angles then count from its heading); arcs = [{radius, from_deg, to_deg, type?}] (0 deg = +x / frame heading, counter-clockwise); radials = [{deg, r0, r1, type?}]. Arcs are split where radials cross them and radials at every arc. True circular Beziers, pieces up to piece_m.

| Parameter | Type | Description |
|---|---|---|
| `piece_m` | number | target length of each road piece (joints = nodes; elevated joints carry pillars). Default 110 |
| `avoid` | array[] | points to keep joints away from (e.g. where a street or rail passes under) |
| `avoid_m` | number | clearance from avoid points, default 25 |
| `joints` | array[] | force joints near these points (e.g. where a ramp will branch off) |
| `step` | number |  |
| `smooth` | boolean | Catmull-Rom smoothing through the knots (default true); false = straight pieces with joints at the knots |
| `plan_only` | boolean | return the segments without touching the game |
| `zoning_check` | boolean | dry_run / plan_only also return a zoning_forecast (default true) |
| `save_to` | string | also write the segments to this JSON file (reuse with segments_file / plan_file) |
| `frame` | object | optional local frame for all input points: {origin:[x,z], heading_deg}; a point [a,b] = origin + a along heading (0 deg = +x, 90 = +z) + b to the left of it. Outputs stay in world x/z |
| `follow_camera` | boolean | move the camera to the change first so the player sees it (default true; never on dry_run) |
| `camera_zoom` | number |  |
| `dry_run` | boolean |  |
| `hold` | boolean |  |
| `allow_errors` | boolean |  |
| `skip_failing` | boolean |  |
| `snap_tolerance` | number |  |
| `explain_errors` | boolean |  |
| `verbose` | boolean |  |
| `join_check` | boolean | check every end that lands on an existing node: highway <-> local road at grade, one-way head-on / head-to-head (default true; reported on dry runs, a build with conflicts is refused) |
| `allow_conflicts` | boolean | build even when join_check finds conflicts (only if the join is intended) |
| `check` | boolean | after building, report junction problems around the change (default true) |
| `before_after` | boolean | return a screenshot before and after the build |
| `deck_height` | number | absolute height (world y) to keep every new node at - level piers, causeways, viaducts; overrides elevation (ends that snap onto existing roads keep theirs) |
| `min_elevation` | number | with deck_height: never lower than this above ground |
| `center` * |  | [x,z] or '@name' |
| `type` | string |  |
| `arcs` * | object[] |  |
| `radials` | object[] |  |

### `cs2_extend_lines`

Lay grid lines YOU choose (avenues/rows as [{points, type?, name?}], frame welcome) and let the tool fit them to what exists: where a line crosses an existing road near one of its nodes (snap_m, default 30) it goes through that node instead of making a new junction beside it; ends near nodes snap on; lines stop stop_gap_m short of roads whose type matches stop_at_types (e.g. ['Highway']) - at-grade only, elevated roads are passed under; end_at_types makes lines end exactly on such roads (junction) and drops the stubs beyond; clip_to_land (default true) stops a line at water, and max_grade at steep ground. Crossings between the new lines become junctions. Reports per line what was clipped, snapped or reused (unchanged lines just counted).

| Parameter | Type | Description |
|---|---|---|
| `piece_m` | number | target length of each road piece (joints = nodes; elevated joints carry pillars). Default 110 |
| `avoid` | array[] | points to keep joints away from (e.g. where a street or rail passes under) |
| `avoid_m` | number | clearance from avoid points, default 25 |
| `joints` | array[] | force joints near these points (e.g. where a ramp will branch off) |
| `step` | number |  |
| `smooth` | boolean | Catmull-Rom smoothing through the knots (default true); false = straight pieces with joints at the knots |
| `plan_only` | boolean | return the segments without touching the game |
| `zoning_check` | boolean | dry_run / plan_only also return a zoning_forecast (default true) |
| `save_to` | string | also write the segments to this JSON file (reuse with segments_file / plan_file) |
| `frame` | object | optional local frame for all input points: {origin:[x,z], heading_deg}; a point [a,b] = origin + a along heading (0 deg = +x, 90 = +z) + b to the left of it. Outputs stay in world x/z |
| `follow_camera` | boolean | move the camera to the change first so the player sees it (default true; never on dry_run) |
| `camera_zoom` | number |  |
| `dry_run` | boolean |  |
| `hold` | boolean |  |
| `allow_errors` | boolean |  |
| `skip_failing` | boolean |  |
| `snap_tolerance` | number |  |
| `explain_errors` | boolean |  |
| `verbose` | boolean |  |
| `join_check` | boolean | check every end that lands on an existing node: highway <-> local road at grade, one-way head-on / head-to-head (default true; reported on dry runs, a build with conflicts is refused) |
| `allow_conflicts` | boolean | build even when join_check finds conflicts (only if the join is intended) |
| `check` | boolean | after building, report junction problems around the change (default true) |
| `before_after` | boolean | return a screenshot before and after the build |
| `deck_height` | number | absolute height (world y) to keep every new node at - level piers, causeways, viaducts; overrides elevation (ends that snap onto existing roads keep theirs) |
| `min_elevation` | number | with deck_height: never lower than this above ground |
| `lines` | object[] |  |
| `lines_file` | string | path of a JSON file on disk holding this list (saves sending it inline) |
| `type` | string |  |
| `snap_m` | number |  |
| `end_snap_m` | number |  |
| `stop_at_types` | string[] |  |
| `stop_gap_m` | number |  |
| `end_at_types` | string[] | end lines exactly ON the first/last crossing with roads of these types (e.g. a waterfront drive), dropping what lies beyond |
| `end_at_ids` | string[] | like end_at_types, for specific road ids |
| `end_keep` | string | with end_at_*: keep from the line start to the first end road ('start'), from the last end road to the line end ('end'), or auto (between two end roads / the longer side). Per line too |
| `clip_to_land` | boolean |  |
| `max_grade` | number |  |
| `min_piece_m` | number |  |
| `touch_m` | number |  |
| `merge_m` | number |  |
| `report_limit` | integer |  |

### `cs2_offset_road`

A street parallel to an existing road (road_ids, chained) or a path (knots) at offset_m (+ = left of its direction, - = right), trimmed by start_m / end_m, with straight connectors back to it every connect_every_m (default 200, 0 = none) and at both ends (connect_ends, default true). For back streets, frontage/service roads, a second row of blocks. v0.27: leave offset_m out (side left/right) for the zoning spacing - source half width + 96 m + new half width, so the two streets' 6-cell blocks meet back to back (full depth, no overlap, no dead gap); a given offset_m that overlaps or leaves a dead gap comes back with a warning (info.zoning). Dry runs include a zoning_forecast.

| Parameter | Type | Description |
|---|---|---|
| `piece_m` | number | target length of each road piece (joints = nodes; elevated joints carry pillars). Default 110 |
| `avoid` | array[] | points to keep joints away from (e.g. where a street or rail passes under) |
| `avoid_m` | number | clearance from avoid points, default 25 |
| `joints` | array[] | force joints near these points (e.g. where a ramp will branch off) |
| `step` | number |  |
| `smooth` | boolean | Catmull-Rom smoothing through the knots (default true); false = straight pieces with joints at the knots |
| `plan_only` | boolean | return the segments without touching the game |
| `zoning_check` | boolean | dry_run / plan_only also return a zoning_forecast (default true) |
| `save_to` | string | also write the segments to this JSON file (reuse with segments_file / plan_file) |
| `frame` | object | optional local frame for all input points: {origin:[x,z], heading_deg}; a point [a,b] = origin + a along heading (0 deg = +x, 90 = +z) + b to the left of it. Outputs stay in world x/z |
| `follow_camera` | boolean | move the camera to the change first so the player sees it (default true; never on dry_run) |
| `camera_zoom` | number |  |
| `dry_run` | boolean |  |
| `hold` | boolean |  |
| `allow_errors` | boolean |  |
| `skip_failing` | boolean |  |
| `snap_tolerance` | number |  |
| `explain_errors` | boolean |  |
| `verbose` | boolean |  |
| `join_check` | boolean | check every end that lands on an existing node: highway <-> local road at grade, one-way head-on / head-to-head (default true; reported on dry runs, a build with conflicts is refused) |
| `allow_conflicts` | boolean | build even when join_check finds conflicts (only if the join is intended) |
| `check` | boolean | after building, report junction problems around the change (default true) |
| `before_after` | boolean | return a screenshot before and after the build |
| `deck_height` | number | absolute height (world y) to keep every new node at - level piers, causeways, viaducts; overrides elevation (ends that snap onto existing roads keep theirs) |
| `min_elevation` | number | with deck_height: never lower than this above ground |
| `road_ids` | string[] |  |
| `knots` | array[] |  |
| `offset_m` | number |  |
| `side` | string |  |
| `source_width` | number |  |
| `source_zones` | boolean | does the source road get zoning? (default: false for highways / ramps / tracks / paths when road_ids are given, else true) - a non-zoning source needs only one 48 m strip |
| `type` * | string |  |
| `connector_type` | string |  |
| `connect_every_m` | number |  |
| `connect_ends` | boolean |  |
| `start_m` | number |  |
| `end_m` | number |  |

### `cs2_rim_road`

Draft a smooth road that follows a shoreline, cliff edge or highway, kept `setback` m (default 60 = road + one 48 m zoning strip) from it and split wherever it would get too steep. Limit it with polygon or an area box (the land mass under seed). Default: a preview map with the draft overlaid and a plan_id. build=true, or cs2_build_plan, builds it. Join it to streets afterwards (cs2_connect_options / cs2_place_roads) - square on (90 deg). A grid drafted next to it later stays a full double zoning depth (rim half + 96 m + street half) back, so blocks never overlap. Preview includes a zoning_forecast.

| Parameter | Type | Description |
|---|---|---|
| `polygon` | array[] |  |
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `center` | number[] |  |
| `size` | number |  |
| `seed` | number[] |  |
| `type` | string |  |
| `setback` | number |  |
| `max_grade` | number |  |
| `min_length` | number |  |
| `build` | boolean |  |
| `px` | integer |  |

### `cs2_ramp`

Branch a ramp off (mode 'off') or onto (mode 'on') an existing carriageway. at = a point on/near it (edge id or road_types to disambiguate; distance_m shifts along it). The carriageway is split exactly there and the ramp leaves toward `side` (right default) at angle_deg (default 30: smaller angles make 'Invalid Shape'), runs lead_m straight-ish, then through YOUR knots to its far end (mode 'on': knots come first, the ramp ends at the split). Elevation: the carriageway's height at the split -> end_elevation (default 0), or a profile. aux_lane = {type, length_m}: first upgrade the carriageway (whole edges) for that length before an off-ramp / after an on-ramp, e.g. one lane wider. Checks the junctions afterwards.

| Parameter | Type | Description |
|---|---|---|
| `piece_m` | number | target length of each road piece (joints = nodes; elevated joints carry pillars). Default 110 |
| `avoid` | array[] | points to keep joints away from (e.g. where a street or rail passes under) |
| `avoid_m` | number | clearance from avoid points, default 25 |
| `joints` | array[] | force joints near these points (e.g. where a ramp will branch off) |
| `step` | number |  |
| `smooth` | boolean | Catmull-Rom smoothing through the knots (default true); false = straight pieces with joints at the knots |
| `plan_only` | boolean | return the segments without touching the game |
| `zoning_check` | boolean | dry_run / plan_only also return a zoning_forecast (default true) |
| `save_to` | string | also write the segments to this JSON file (reuse with segments_file / plan_file) |
| `frame` | object | optional local frame for all input points: {origin:[x,z], heading_deg}; a point [a,b] = origin + a along heading (0 deg = +x, 90 = +z) + b to the left of it. Outputs stay in world x/z |
| `follow_camera` | boolean | move the camera to the change first so the player sees it (default true; never on dry_run) |
| `camera_zoom` | number |  |
| `dry_run` | boolean |  |
| `hold` | boolean |  |
| `allow_errors` | boolean |  |
| `skip_failing` | boolean |  |
| `snap_tolerance` | number |  |
| `explain_errors` | boolean |  |
| `verbose` | boolean |  |
| `join_check` | boolean | check every end that lands on an existing node: highway <-> local road at grade, one-way head-on / head-to-head (default true; reported on dry runs, a build with conflicts is refused) |
| `allow_conflicts` | boolean | build even when join_check finds conflicts (only if the join is intended) |
| `check` | boolean | after building, report junction problems around the change (default true) |
| `before_after` | boolean | return a screenshot before and after the build |
| `deck_height` | number | absolute height (world y) to keep every new node at - level piers, causeways, viaducts; overrides elevation (ends that snap onto existing roads keep theirs) |
| `min_elevation` | number | with deck_height: never lower than this above ground |
| `at` * |  | [x,z] or '@name' |
| `edge` | string |  |
| `road_types` | string[] |  |
| `distance_m` | number |  |
| `mode` | string |  |
| `side` | string |  |
| `angle_deg` | number |  |
| `lead_m` | number |  |
| `knots` * | array |  |
| `against_edge` | boolean | two-way road: branch from the lanes travelling against the edge's drawing direction (side is then relative to that travel direction) |
| `type` * | string |  |
| `elevation` |  | metres above ground: a number, or a profile [[distance_m, elev_m], ...] along the path (negative distance counts back from the end), linear between points |
| `end_elevation` | number |  |
| `aux_lane` | object |  |

### `cs2_trim`

Cut an existing road at a point and remove one side of the cut: keep 'start' or 'end' (edge direction) or keep_point = [x,z] on the side to keep. The kept piece is rebuilt with the exact same curve and height, so it stays connected at its far end. edge / road_types pick the road if several are near. dry_run shows the piece.

| Parameter | Type | Description |
|---|---|---|
| `at` * |  | [x,z] or '@name' |
| `edge` | string |  |
| `road_types` | string[] |  |
| `keep` | string |  |
| `keep_point` |  | [x,z] or '@name' |
| `radius` | number |  |
| `max_offset_m` | number |  |
| `allow_errors` | boolean |  |
| `dry_run` | boolean |  |
| `follow_camera` | boolean | move the camera to the change first so the player sees it (default true; never on dry_run) |

### `cs2_replace_roads`

Upgrade/downgrade existing roads in place to another type (keeps connections), by ids or area box. Undoable.

| Parameter | Type | Description |
|---|---|---|
| `type` * | string |  |
| `ids` | string[] |  |
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `dry_run` | boolean |  |
| `hold` | boolean |  |
| `follow_camera` | boolean | move the camera to the change first so the player sees it (default true; never on dry_run) |
| `allow_errors` | boolean |  |

### `cs2_district_grid`

Draft ONE district's street grid inside an outline you chose (polygon [[x,z],...], or an area box). You pick the character: local_type / arterial_type (+ arterial_every), angle_deg (default: lined up with the neighbouring streets), block_length_cells (default 22) and zoning_depth_cells (default 6; spacing = road width + 2 x depth x 8 m, so blocks get full zoning). Streets are clipped to the land under seed (default: outline centre): they avoid water, cliffs, highways, existing zoning and buildings, and climb at most max_grade (default 0.15). They join existing streets where they line up; pieces that end up unconnected are dropped unless connected_only=false. Default: a PREVIEW map with the draft overlaid and a plan_id, nothing built. build=true, or cs2_build_plan(plan_id), builds it and skips segments the game refuses. Zoning rules (v0.27): streets meet others at >= 80 deg - a line that would hit a road at a skew bends so its last ~50 m arrive square (dropped if that does not fit; down to 60 deg allowed onto curved roads); with a given angle_deg the line positions are fitted to parallel streets nearby so the spacing to them is an exact pitch (no overlap, no dead gap). The preview carries a zoning_forecast (draft full-depth share, dead gaps, skewed junctions, verdict) and marks G / S / D on the map - redraft until the verdict is good.

| Parameter | Type | Description |
|---|---|---|
| `polygon` | array[] |  |
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `center` | number[] |  |
| `size` | number |  |
| `seed` | number[] |  |
| `local_type` | string |  |
| `arterial_type` | string |  |
| `arterial_every` | integer |  |
| `angle_deg` | number |  |
| `origin` | number[] |  |
| `u_phase` | number |  |
| `v_phase` | number |  |
| `match_radius` | number |  |
| `block_length_cells` | integer |  |
| `zoning_depth_cells` | integer |  |
| `pitch_u` | number |  |
| `pitch_v` | number |  |
| `max_grade` | number |  |
| `connected_only` | boolean |  |
| `build` | boolean |  |
| `px` | integer |  |

### `cs2_connect_options`

For every dead end in an area, the ways to join it up: carry straight on to the first road, or go to the nearest junction. Each option comes with its segment, its length, ok, and the problem if not ok (water, a building lot, too steep, lands too close to another junction, shallow angle, target is a highway). Facts only; build the ones you choose with cs2_place_roads.

| Parameter | Type | Description |
|---|---|---|
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `center` | number[] |  |
| `size` | number |  |
| `max_m` | number |  |
| `min_gap_m` | number |  |
| `min_angle_deg` | number |  |
| `max_grade` | number |  |

### `cs2_build_plan`

Build a drafted plan (plan_id from cs2_district_grid / cs2_rim_road) in chunks, with the camera following. Segments the game refuses are skipped and returned.

| Parameter | Type | Description |
|---|---|---|
| `plan_id` * | string |  |
| `chunk_m` | number |  |
| `follow_camera` | boolean |  |

### `cs2_remove_plan`

Remove what a built plan put down. It finds the roads by shape, so pieces the game split or re-created are included. plan_id 'session' removes every plan built in this session, newest first.

| Parameter | Type | Description |
|---|---|---|
| `plan_id` * | string |  |

### `cs2_probe`

Feasibility check for many alternatives at once: dry-runs each candidate on its own (a candidate is one segment or a list of segments, place_roads shape) and returns ok / error counts / reasons per candidate. explain adds what each error location is next to; details adds snapping and intersection counts. Builds nothing.

| Parameter | Type | Description |
|---|---|---|
| `candidates` | array |  |
| `candidates_file` | string | path of a JSON file on disk holding this list (saves sending it inline) |
| `type` | string |  |
| `frame` | object | optional local frame for all input points: {origin:[x,z], heading_deg}; a point [a,b] = origin + a along heading (0 deg = +x, 90 = +z) + b to the left of it. Outputs stay in world x/z |
| `snap_tolerance` | number |  |
| `explain` | boolean |  |
| `details` | boolean |  |

### `cs2_place_piers`

Piers off a road out over the water: every spacing_m along road_ids (T junctions onto that road) or knots, straight out on `side` (auto = where water is nearer, else left/right of the line's direction) to the waterline and then length_m beyond, as `type` (default QuayLarge01 - a quay deck with walls; any road/pathway type works). The deck stays level at deck_height (absolute y) or the waterline land height + deck_above_m (default 1). head = {length_m, type?} adds a T crosspiece at the tip. Skips (and reports) candidates with no water within max_reach_m, water shallower than min_depth_m (default 2) out there, a crossing with another road, or closer than min_gap_m to the previous pier. Same preview rules as cs2_place_roads; start_m/end_m trim the ends of the line.

| Parameter | Type | Description |
|---|---|---|
| `piece_m` | number | target length of each road piece (joints = nodes; elevated joints carry pillars). Default 110 |
| `avoid` | array[] | points to keep joints away from (e.g. where a street or rail passes under) |
| `avoid_m` | number | clearance from avoid points, default 25 |
| `joints` | array[] | force joints near these points (e.g. where a ramp will branch off) |
| `step` | number |  |
| `smooth` | boolean | Catmull-Rom smoothing through the knots (default true); false = straight pieces with joints at the knots |
| `plan_only` | boolean | return the segments without touching the game |
| `zoning_check` | boolean | dry_run / plan_only also return a zoning_forecast (default true) |
| `save_to` | string | also write the segments to this JSON file (reuse with segments_file / plan_file) |
| `frame` | object | optional local frame for all input points: {origin:[x,z], heading_deg}; a point [a,b] = origin + a along heading (0 deg = +x, 90 = +z) + b to the left of it. Outputs stay in world x/z |
| `follow_camera` | boolean | move the camera to the change first so the player sees it (default true; never on dry_run) |
| `camera_zoom` | number |  |
| `dry_run` | boolean |  |
| `hold` | boolean |  |
| `allow_errors` | boolean |  |
| `skip_failing` | boolean |  |
| `snap_tolerance` | number |  |
| `explain_errors` | boolean |  |
| `verbose` | boolean |  |
| `join_check` | boolean | check every end that lands on an existing node: highway <-> local road at grade, one-way head-on / head-to-head (default true; reported on dry runs, a build with conflicts is refused) |
| `allow_conflicts` | boolean | build even when join_check finds conflicts (only if the join is intended) |
| `check` | boolean | after building, report junction problems around the change (default true) |
| `before_after` | boolean | return a screenshot before and after the build |
| `deck_height` | number | absolute height (world y) to keep every new node at - level piers, causeways, viaducts; overrides elevation (ends that snap onto existing roads keep theirs) |
| `min_elevation` | number | with deck_height: never lower than this above ground |
| `road_ids` | string[] |  |
| `knots` | array[] |  |
| `type` | string |  |
| `spacing_m` | number |  |
| `length_m` | number |  |
| `side` | string |  |
| `max_reach_m` | number |  |
| `min_depth_m` | number |  |
| `min_gap_m` | number |  |
| `deck_above_m` | number |  |
| `head` | object |  |
| `start_m` | number |  |
| `end_m` | number |  |
| `at` | array | instead of spacing: piers at the line points nearest to these [x,z] (e.g. opposite cross streets) |

### `cs2_place_along_shore`

Lay a net along the waterline - quay walls, promenades, lakeshore drives: from/to = points near the shore (frame accepted); the path follows the water's edge between them, offset_m from it (+ = inland, - = out over the water), smoothed with knots every smooth_m (default 30; larger = straighter). type = any net (e.g. QuayMedium01); elevation / deck_height as usual. Same preview rules as cs2_place_roads.

| Parameter | Type | Description |
|---|---|---|
| `piece_m` | number | target length of each road piece (joints = nodes; elevated joints carry pillars). Default 110 |
| `avoid` | array[] | points to keep joints away from (e.g. where a street or rail passes under) |
| `avoid_m` | number | clearance from avoid points, default 25 |
| `joints` | array[] | force joints near these points (e.g. where a ramp will branch off) |
| `step` | number |  |
| `smooth` | boolean | Catmull-Rom smoothing through the knots (default true); false = straight pieces with joints at the knots |
| `plan_only` | boolean | return the segments without touching the game |
| `zoning_check` | boolean | dry_run / plan_only also return a zoning_forecast (default true) |
| `save_to` | string | also write the segments to this JSON file (reuse with segments_file / plan_file) |
| `frame` | object | optional local frame for all input points: {origin:[x,z], heading_deg}; a point [a,b] = origin + a along heading (0 deg = +x, 90 = +z) + b to the left of it. Outputs stay in world x/z |
| `follow_camera` | boolean | move the camera to the change first so the player sees it (default true; never on dry_run) |
| `camera_zoom` | number |  |
| `dry_run` | boolean |  |
| `hold` | boolean |  |
| `allow_errors` | boolean |  |
| `skip_failing` | boolean |  |
| `snap_tolerance` | number |  |
| `explain_errors` | boolean |  |
| `verbose` | boolean |  |
| `join_check` | boolean | check every end that lands on an existing node: highway <-> local road at grade, one-way head-on / head-to-head (default true; reported on dry runs, a build with conflicts is refused) |
| `allow_conflicts` | boolean | build even when join_check finds conflicts (only if the join is intended) |
| `check` | boolean | after building, report junction problems around the change (default true) |
| `before_after` | boolean | return a screenshot before and after the build |
| `deck_height` | number | absolute height (world y) to keep every new node at - level piers, causeways, viaducts; overrides elevation (ends that snap onto existing roads keep theirs) |
| `min_elevation` | number | with deck_height: never lower than this above ground |
| `from` * |  | [x,z] or '@name' |
| `to` * |  | [x,z] or '@name' |
| `type` * | string |  |
| `offset_m` | number |  |
| `smooth_m` | number |  |
| `tolerance_m` | number |  |
| `pad_m` | number |  |
| `elevation` |  | metres above ground: a number, or a profile [[distance_m, elev_m], ...] along the path (negative distance counts back from the end), linear between points |
| `join_from` |  | exact point (e.g. a road node) the path starts on |
| `join_to` |  | exact point the path ends on |

### `cs2_building_access`

Why a building has (no) road access and where the road must go: its lot's front edge (from position, facing and lot size), the centreline a road of `type` needs (half its width + margin_m outside the front edge), which existing roads run along the front, and the game's own status (mod 0.10). build = true places that road, extend_m past both corners (dry_run to preview).

| Parameter | Type | Description |
|---|---|---|
| `id` * | string |  |
| `type` | string |  |
| `margin_m` | number |  |
| `extend_m` | number |  |
| `build` | boolean |  |
| `dry_run` | boolean |  |
| `snap_tolerance` | number |  |

### `cs2_design_path`

Exact geometry for a road YOU designed (v0.27) - ramps, flyovers, loops, connectors. start / end = {pos:[x,z], heading_deg (0 = +x, 90 = +z), elevation? (m above ground)} or {edge: road id, at:[x,z] (a point on it), reverse?, branch_deg? + side left/right (leave / join the road at that angle)}. Returns the shortest path whose curves are never tighter than the design speed allows (Dubins: true circular arcs + straights; radius_m overrides; words = ['LSL','RSR','LSR','RSL','RLR','LRL'] to force a shape, e.g. a 270-deg loop), cut into cubic pieces (piece_m), with an elevation profile within max_grade that clears every road it crosses by clearance_m (pass 'over' or 'under'), infeasible crossings listed, and a cs2_network_check of the new pieces. Nothing is built: hand `segments` to cs2_place_roads (dry_run first). You choose the topology; this does the arithmetic.

| Parameter | Type | Description |
|---|---|---|
| `start` * | object |  |
| `end` * | object |  |
| `type` | string |  |
| `design_speed_kmh` | number |  |
| `radius_m` | number |  |
| `max_grade` | number |  |
| `clearance_m` | number |  |
| `pass` | string |  |
| `piece_m` | number |  |
| `words` | string[] |  |

### `cs2_network_check`

Facts about the roads in an area or around a site, optionally with a draft laid over them (segments in place_roads shape with elevation above ground, or plan_id) - BEFORE building (v0.27). Reports: with site [x,z] + radius every movement between the roads crossing the circle - reachable or MISSING, path length and detour ratio (max_detour, default 2.5); dead ends, one-way traps and sources; per road the tightest radius vs its design speed (design_speed_kmh by kind highway / ramp / arterial / local, default 100 / 60 / 60 / 40; R = v^2 / (127 x 0.25)) and the steepest grade (max_grade by kind, default 6 / 8 / 8 / 10 %); merge / diverge angles (> 25 deg flagged); weaving length from an on-ramp merge to the next off-ramp split (weave_min_m, default 400); lane balance - an auxiliary lane before every off-ramp and after every on-ramp (standing rule); vertical clearance where roads cross without a junction (min_clearance_m, default 6) and crossings that would make an unintended junction. map=true draws the issues. Lane directions come from mod 0.27; older mods infer them from type names (flagged).

| Parameter | Type | Description |
|---|---|---|
| `site` | number[] |  |
| `radius` | number |  |
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `center` | number[] |  |
| `size` | number |  |
| `segments` | object[] |  |
| `plan_id` | string |  |
| `design_speed_kmh` | object |  |
| `max_grade` | object |  |
| `min_clearance_m` | number |  |
| `weave_min_m` | number |  |
| `max_detour` | number |  |
| `all_movements` | boolean |  |
| `approach_kinds` | string[] | which roads count as the site's approaches: highway / ramp / arterial / local (default: highway, ramp, arterial when a highway crosses the circle, else all) |
| `map` | boolean |  |
| `px` | integer |  |

### `cs2_site_movements`

Turning demand at a junction / interchange site (v0.27, needs mod 0.27): cars on or approaching the circle site [x,z] + radius are sampled `samples` times `interval_s` apart, deduped, and each one's entry approach and the approach its planned path leaves by are counted -> an entry -> exit matrix with shares, cars per approach, and the approaches (bearing, road type, lanes in / out). The input for designing an interchange for THIS site: heavy movements get direct / free-flow ramps, light ones loops or nothing. Sample at busy times; more samples = steadier numbers.

| Parameter | Type | Description |
|---|---|---|
| `site` * | number[] |  |
| `radius` | number |  |
| `ring` | number |  |
| `samples` | integer |  |
| `interval_s` | number |  |
| `top` | integer |  |

### `cs2_road_align`

Fix zone cracks on existing roads (v0.36, needs mod 0.36): sets the planned half-align flags (cs2_zone_cracks) through the mod's road_align, the game rebuilds those roads' zone blocks, then every new empty cell is re-zoned with the zone of the nearest old cell. Cells that move lose buildings standing across the old grid - save first. dry_run = plan only. Undoable (flags) via cs2_undo.

| Parameter | Type | Description |
|---|---|---|
| `xmin` * | number |  |
| `zmin` * | number |  |
| `xmax` * | number |  |
| `zmax` * | number |  |
| `dry_run` | boolean |  |
| `rezone` | boolean |  |
| `settle_s` | number |  |

### `cs2_road_snapshot`

Save every road piece in an area (box / center+size / polygon; types filter, default all roads - no utilities or tracks; or ids) with exact curves, heights and junction points to server/snapshots/roads_<label>.json. Take one BEFORE removing roads to rebuild them; restore with cs2_road_restore (not undo - undo can bring pieces back unconnected).

| Parameter | Type | Description |
|---|---|---|
| `label` | string |  |
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `center` | number[] |  |
| `size` | number |  |
| `polygon` | array[] |  |
| `types` | string[] |  |
| `ids` | string[] |  |

### `cs2_road_restore`

Rebuild a cs2_road_snapshot exactly: clears the roads now in its area (same filter; keep_ids never cleared) and places the saved pieces with every end snapped to its junction's shared point, so they reconnect to each other and to the roads around them. Pauses the sim while it works. dry_run = counts only.

| Parameter | Type | Description |
|---|---|---|
| `label` * | string |  |
| `dry_run` | boolean |  |
| `keep_ids` | string[] |  |
| `pause` | boolean |  |
| `allow_errors` | boolean |  |

### `cs2_road_profile`

Side view of a route - existing road_ids chained in order, or draft segments (place_roads shape): height above ground along it, max grade, every road it crosses with that road's height and the vertical clearance (problem when < min_clearance_m, default 5.5), dips (an elevated route coming down to the ground and climbing again) and a chart PNG. Use before building anything that must thread over or under other roads.

| Parameter | Type | Description |
|---|---|---|
| `road_ids` | string[] |  |
| `segments` | object[] |  |
| `min_clearance_m` | number |  |
| `box` | object |  |

### `cs2_node_detail`

One junction in detail (mod 0.40): id or the nearest node to at [x,z] (radius): every road arm (type, which end, heading, elevation, car lanes in / out) and every car movement through the node (from arm -> to arm, lanes, turn flags), plus issues: highway joined to a local road at grade, u-turns on a highway, traps (lanes arrive, nothing leaves), sources, head-on / head-to-head nodes.

| Parameter | Type | Description |
|---|---|---|
| `id` | string |  |
| `at` | number[] |  |
| `radius` | number |  |

### `cs2_highway_fork`

Proper highway Y (one two-way trunk -> two two-way branches) as the game's own interchanges do it: the trunk separates into one-way carriageways, the outbound one diverges into a leg per branch, the inbound one is fed by a merge, every leg rejoins its branch's end node, and the one crossing (outbound-left x inbound-right) is a flyover. trunk / branches = {edge, at [x,z] near the end that faces the junction}; split / merge [x,z]; deck_m (viaduct height), sep_m, diverge_deg, flyover_m, piece_m, design_speed_kmh. Checks: join_check + profiles of the crossing legs. plan_only = geometry only; default = dry run in game; build=true builds. Removes nothing - clear the old junction first (cs2_road_snapshot, bulldoze; cs2_road_restore to go back).

| Parameter | Type | Description |
|---|---|---|
| `trunk` * | object |  |
| `branches` * | object[] |  |
| `split` * | number[] |  |
| `merge` | number[] |  |
| `deck_m` | number |  |
| `sep_m` | number |  |
| `diverge_deg` | number |  |
| `flyover_m` | number |  |
| `piece_m` | number |  |
| `design_speed_kmh` | number |  |
| `plan_only` | boolean |  |
| `build` | boolean |  |

### `cs2_path_test`

Run the game's own hearse / ambulance dispatch pathfind on demand (mod 0.41) against a building or citizen id: mode hearse | ambulance (exact HealthcareDispatchSystem query: that service's vehicles in the district -> the target's doors, drive + walk) or hearse_road | ambulance_road (Road-only destination = reaches the kerb?). Waits for the result (wait_s, default 8): found, origin facility/vehicle, distance, methods, the lanes the route ends on.

| Parameter | Type | Description |
|---|---|---|
| `id` * | string |  |
| `mode` | string |  |
| `wait_s` | number |  |

## Zoning

### `cs2_zone`

Zone (or dezone with zone_type "none") every zoning cell inside an area: either 4 corners [[x,z]x4] in order, or a rectangle center/width/depth/angle_deg (center defaults to the camera's ground point). overwrite (default true) replaces existing zoning. Undoable.

| Parameter | Type | Description |
|---|---|---|
| `zone_type` * | string |  |
| `corners` | array[] |  |
| `frame` | object | optional local frame for all input points: {origin:[x,z], heading_deg}; a point [a,b] = origin + a along heading (0 deg = +x, 90 = +z) + b to the left of it. Outputs stay in world x/z |
| `center` | number[] |  |
| `width` | number |  |
| `depth` | number |  |
| `angle_deg` | number |  |
| `overwrite` | boolean |  |
| `dry_run` | boolean |  |
| `hold` | boolean |  |
| `follow_camera` | boolean | move the camera to the change first so the player sees it (default true; never on dry_run) |

### `cs2_zone_along`

Zone strips beside roads: the roads with `ids`, or every road in the box (optionally only `types` substrings) whose midpoint is inside. depth_m (default 48 = 6 cells) from the road edge, starting start_m out, side both/left/right (of each road's direction). overwrite default false. plan_only returns the quads.

| Parameter | Type | Description |
|---|---|---|
| `xmin` * | number |  |
| `zmin` * | number |  |
| `xmax` * | number |  |
| `zmax` * | number |  |
| `ids` | string[] |  |
| `types` | string[] |  |
| `zone_type` * | string |  |
| `depth_m` | number |  |
| `start_m` | number |  |
| `side` | string |  |
| `overwrite` | boolean |  |
| `dry_run` | boolean |  |
| `plan_only` | boolean |  |
| `follow_camera` | boolean | move the camera to the change first so the player sees it (default true; never on dry_run) |

### `cs2_zone_palette`

Zone the streets inside an outline by rules, rotating styles so blocks do not repeat: rules = [{match: {types, direction 'along'|'across' the frame heading, near_point + within_m, near_types + within_m}, zone | zones + rotate 'street' (per street, street_m) or 'along' (alternate_m)}], first match wins per road. order = which styles zone first (they claim the corners). Only empty cells unless overwrite. depth_m default 48. plan_only counts.

| Parameter | Type | Description |
|---|---|---|
| `polygon` * | array[] |  |
| `polygon_in_frame` | boolean | polygon given in frame coordinates (default: world; the frame then only sets the along/across direction) |
| `frame` | object | optional local frame for all input points: {origin:[x,z], heading_deg}; a point [a,b] = origin + a along heading (0 deg = +x, 90 = +z) + b to the left of it. Outputs stay in world x/z |
| `rules` * | object[] |  |
| `order` | string[] |  |
| `overwrite` | boolean |  |
| `depth_m` | number |  |
| `plan_only` | boolean |  |

### `cs2_rezone`

Change one zone style to another ONLY where it is (cell-exact, from the zone blocks): from = name or list, to = name or 'none'. only_empty (default true) leaves cells with buildings alone - e.g. switch unbuilt high density to medium when high demand is saturated. Where: districts / polygon / box. dry_run counts cells.

| Parameter | Type | Description |
|---|---|---|
| `districts` | string[] | district names (default: all) |
| `polygon` | array[] |  |
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `from` * |  | zone style name or list |
| `to` * | string |  |
| `only_empty` | boolean |  |
| `dry_run` | boolean |  |

### `cs2_rezone_buildings`

Replace chosen buildings with another zone: select by districts / polygon / box plus icon (e.g. 'No Customers'), type_regex, zone_regex (mod 0.13: the building's zone style) or category; each lot is rezoned to `to` or per `map` {regex on building type or zone: zone style}, then the buildings are bulldozed (bulldoze default true - rezoning alone leaves the old building standing). Signature buildings skipped unless include_signature. dry_run lists.

| Parameter | Type | Description |
|---|---|---|
| `districts` | string[] | district names (default: all) |
| `polygon` | array[] |  |
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `icon` | string |  |
| `type_regex` | string |  |
| `zone_regex` | string |  |
| `category` | string |  |
| `to` | string |  |
| `map` | object |  |
| `bulldoze` | boolean |  |
| `include_signature` | boolean |  |
| `dry_run` | boolean |  |

### `cs2_zone_snapshot`

Growth watch: save zoning (built / empty per style) and warning totals per district to server/snapshots, and diff with the previous snapshot - cells built since, zoned cells still empty, warnings up/down.

| Parameter | Type | Description |
|---|---|---|
| `districts` | string[] | district names (default: all) |
| `polygon` | array[] |  |
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `label` | string |  |

### `cs2_zoning_check`

Zoning forecast (v0.27) for an area's roads, optionally with a draft laid over them (plan_id from cs2_district_grid / cs2_rim_road, or segments in place_roads shape) - BEFORE building. The game zones 6 cells (48 m) deep on both sides of a road in 8 m columns: reports each column's usable depth, full_depth_share for straight (grid) and curved roads, dead gaps (land between two parallel roads' zoning edges that nothing will ever zone), skewed junctions (< 80 deg) and a verdict; the map marks G = dead gap, S = skewed junction, D = shallow blocks. Targets: straight roads ~100% full depth, no dead gaps, no skewed grid junctions; curved / waterfront roads may lose some. Layout rules that get there: parallel streets exactly road width + 96 m apart (centre to centre, equal types; w1/2 + w2/2 + 96 otherwise) - closer = shallow blocks, a bit farther = a dead gap; streets meet at 90 deg (bend the last ~50 m if the line comes in skewed); keep a grid a full double depth back from a curved road.

| Parameter | Type | Description |
|---|---|---|
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `center` | number[] |  |
| `size` | number |  |
| `plan_id` | string |  |
| `segments` | object[] |  |
| `map` | boolean |  |
| `px` | integer |  |

### `cs2_zone_cracks`

Zero-crack check (v0.36): inside street blocks every zone cell must sit on its neighbours' 8 m lattice. Measures misaligned neighbour cells from the live zone blocks (0 = none) and plans the per-node Road half-align flags that line each straight road's cells up with its crossing roads (the game's road tool sets them, MCP-built roads lacked them: 4 m cracks at every Medium-avenue/side-street seam). residual = edges flags cannot fit, with the length change that would. Read-only.

| Parameter | Type | Description |
|---|---|---|
| `xmin` * | number |  |
| `zmin` * | number |  |
| `xmax` * | number |  |
| `zmax` * | number |  |

### `cs2_waterfront_backs`

Lots that turn their BACK on the water (2026-10-08): zone blocks whose back edge runs into water within probe_m (40) with no street behind (a road within road_m (12) of the probe line keeps them). Dezones those columns front to back and bulldozes the growables on them that are below keep_level (default 2 = only fresh level-1 spawns; established buildings keep their cells zoned so they are not condemned). dry_run counts blocks / cells / buildings per district. Where: districts / polygon / box.

| Parameter | Type | Description |
|---|---|---|
| `districts` | string[] |  |
| `polygon` | array[] |  |
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `probe_m` | number |  |
| `road_m` | number |  |
| `keep_level` | integer |  |
| `bulldoze` | boolean |  |
| `dry_run` | boolean |  |

## Buildings, objects, terrain and areas

### `cs2_place_building`

Place a building precisely: type (from cs2_list_building_types), lot centre x,z (or at = [x,z] / '@name', frame accepted), and facing = [dx,dz] direction the front/access side points to (or angle_deg: 0 = +z, 90 = +x). Previews first and refuses on errors (overlap, no road access, water...) unless allow_errors; dry_run / hold as usual. Undoable. Then cs2_building_nets shows its platform tracks / internal roads and their free ends to connect to. Mod 0.27: parent = an existing building id installs `type` as its service upgrade (e.g. 'Crematorium01 Hearse Garage' on a Crematorium01, hospital wings): the game's own upgrade (parent re-defined in place, existing upgrades kept); wings need no x/z; an extension with its own lot is put flush beside / behind the parent automatically when x/z are left out.

| Parameter | Type | Description |
|---|---|---|
| `type` * | string |  |
| `x` | number |  |
| `z` | number |  |
| `at` |  |  |
| `facing` | number[] |  |
| `angle_deg` | number |  |
| `frame` | object | optional local frame for all input points: {origin:[x,z], heading_deg}; a point [a,b] = origin + a along heading (0 deg = +x, 90 = +z) + b to the left of it. Outputs stay in world x/z |
| `dry_run` | boolean |  |
| `hold` | boolean |  |
| `allow_errors` | boolean |  |
| `follow_camera` | boolean | move the camera to the change first so the player sees it (default true; never on dry_run) |
| `explain_errors` | boolean |  |
| `allow_subnets` | boolean | UNSAFE: allow buildings with built-in nets (stations, harbours) - crashed the game once; save first |
| `parent` | string | mod 0.27: building id to install this upgrade on |

### `cs2_place_objects`

Place many parks / trees / props / small buildings in one previewed, undoable operation (mod 0.16). items = [{type, x, z, angle_deg | facing [dx,dz]}], and/or generate them: along = {shore: {from, to} (follows the waterline) | road_ids | knots, spacing_m, offset_m (shore: + = inland; road: sideways), side left|right|both, facing 'water'|'inland'|'along'|'road'|'away'|angle, start_m, end_m}; scatter = {polygon, spacing_m} (staggered, random rotation). types = list (cycled, or random_types with seed), jitter_m. Spots on water, roads (+clearance_m) or buildings are skipped (avoid false to keep them). plan_only lists the items; dry_run previews in game. Buildings with built-in roads/tracks must use cs2_place_building.

| Parameter | Type | Description |
|---|---|---|
| `items` | object[] |  |
| `along` | object |  |
| `scatter` | object |  |
| `type` | string |  |
| `types` | string[] |  |
| `random_types` | boolean |  |
| `seed` | integer |  |
| `jitter_m` | number |  |
| `clearance_m` | number |  |
| `avoid` | boolean |  |
| `plan_only` | boolean |  |
| `dry_run` | boolean |  |
| `allow_errors` | boolean |  |
| `batch` | integer |  |
| `show` | integer |  |

### `cs2_bulldoze`

Remove roads and/or buildings: by ids (from cs2_query_roads / cs2_query_buildings) and/or an area box. In an area, roads (default true) whose midpoint is inside and buildings (default false) whose position is inside are removed. Undo rebuilds removed roads (buildings cannot be restored). Mod 0.44: a building goes with its sub-nets, sub-areas and installed upgrades, as the game's bulldozer does (0.43 left big assets' elevated sub-net pillars + sub-objects behind).

| Parameter | Type | Description |
|---|---|---|
| `ids` | string[] |  |
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `roads` | boolean |  |
| `buildings` | boolean |  |
| `dry_run` | boolean |  |
| `hold` | boolean |  |
| `follow_camera` | boolean | move the camera to the change first so the player sees it (default true; never on dry_run) |

### `cs2_terraform`

Brush the terrain like the in-game terrain tool (mod 0.10): type level | shift | soften | slope, size_m (brush diameter, default 30), strength (default 1), frames (how long the brush is held, default 90). Where: points [[x,z] or [x,z,target_y]] or line [[x,z],...] + width_m (brush points laid along and across it). Level target: target_height (absolute y), or target='sides' = per point the median ground height side_m (default 20) beyond both edges of the line - erases a berm or fills a trench to match its surroundings. NOT undoable; dry_run checks the tool and lists brushes. Reports before/after heights.

| Parameter | Type | Description |
|---|---|---|
| `type` | string |  |
| `size_m` | number |  |
| `strength` | number |  |
| `frames` | integer |  |
| `points` | array |  |
| `line` | array[] |  |
| `width_m` | number |  |
| `side_m` | number |  |
| `target` | string |  |
| `target_height` | number |  |
| `brush` | string |  |
| `frame` | object | optional local frame for all input points: {origin:[x,z], heading_deg}; a point [a,b] = origin + a along heading (0 deg = +x, 90 = +z) + b to the left of it. Outputs stay in world x/z |
| `dry_run` | boolean |  |
| `follow_camera` | boolean | move the camera to the change first so the player sees it (default true; never on dry_run) |

### `cs2_delete_areas`

Delete areas (e.g. broken surfaces from a removed mod) by id from cs2_areas.

| Parameter | Type | Description |
|---|---|---|
| `ids` * | string[] |  |
| `dry_run` | boolean |  |

## Districts

### `cs2_create_district`

Create a game district from an outline YOU decide: polygon [[x,z], ...] (3+ points in order around it; no need to repeat the first). Optional name and policies (names from cs2_list_district_policies). Previews first and refuses if the game reports errors (e.g. overlapping another district) unless allow_errors; dry_run / hold as usual. Tip: draft it on cs2_map with outlines=[{points, label}] first. Undoable.

| Parameter | Type | Description |
|---|---|---|
| `polygon` | array[] |  |
| `name` | string |  |
| `policies` | string[] |  |
| `dry_run` | boolean |  |
| `hold` | boolean |  |
| `allow_errors` | boolean |  |
| `polygon_file` | string | path of a JSON file on disk holding this list (saves sending it inline) |
| `follow_camera` | boolean | move the camera to the change first so the player sees it (default true; never on dry_run) |

### `cs2_delete_district`

Delete a district (by id or name). Buildings and roads are untouched. Undo re-creates its outline and name.

| Parameter | Type | Description |
|---|---|---|
| `id` | string |  |
| `name` | string |  |
| `dry_run` | boolean |  |

### `cs2_rename_district`

Rename a district (by id or current name) to new_name.

| Parameter | Type | Description |
|---|---|---|
| `id` | string |  |
| `name` | string |  |
| `new_name` * | string |  |

### `cs2_set_district_policy`

Turn district policies on (active=true, default) or off, by district id or name. adjustment for slider policies.

| Parameter | Type | Description |
|---|---|---|
| `id` | string |  |
| `name` | string |  |
| `policies` * | string[] |  |
| `active` | boolean |  |
| `adjustment` | number |  |

## City services and transit

### `cs2_service_coverage`

City services from the game's own numbers (mod 0.15): per district the resident-weighted coverage and share of residents under-served for healthcare / police / education / garbage (also fire, park, post, welfare): crime and garbage over the game's happiness thresholds, children / teens not in school, sick; the facilities (schools per level, hospitals, police, garbage) with load/capacity and efficiency; and hotspots = clusters of grid_m cells where many residents are under-served, with a centre where a new facility would help most. thresholds overrides. Mod 0.27: services=['deathcare'] = bodies waiting for a hearse per district (the icon sits on the citizen, so building warnings miss it) + hotspots, and crematoria/cemeteries by type with load/capacity, hearses (out), processing per month and how many are full (a full one stops sending hearses); health_census = city totals of dead waiting, dead collected, sick, injured, waiting for an ambulance.

| Parameter | Type | Description |
|---|---|---|
| `districts` | string[] | district names (default: all) |
| `polygon` | array[] |  |
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `services` | string[] |  |
| `thresholds` | object |  |
| `grid_m` | number |  |
| `min_residents` | integer |  |
| `hotspots` | integer |  |

### `cs2_service_budget`

Service budgets (mod 0.16), like the Budget panel: every city service's budget slider (%), the building efficiency that budget gives (the game's curve), estimated upkeep, and the city's monthly balance / income / expenses. set = {service name: percent 50..150} moves sliders (e.g. {"Education": 100}); preview = percentages to see the efficiency each would give without changing anything. cs2_service_coverage's efficiency_factors show when ServiceBudget is what holds a service back.

| Parameter | Type | Description |
|---|---|---|
| `set` | object |  |
| `preview` | number[] |  |

### `cs2_service_sites`

Where the next service building should go (v0.27): free lots for `type` along at-grade roads (flat, dry, free), ranked by the demand `service` has within radius_m - healthcare / police / fire / education / park / post / welfare: residents under-served (cs2_service_coverage thresholds); garbage: residents over the baseline; education: children + teens not in school; deathcare: bodies waiting for a hearse (mod 0.27; older mods: residents farther than radius_m from any deathcare building). Greedy picks: each takes the most demand, then what it covers is discounted (served_share, default 1) so the next pick goes to another gap. Keeps min_gap_m (300) from existing facilities of the same kind / type. Every site is dry-run through place_building first (verify, default true), so the game accepts it. Returns x, z, facing for cs2_place_building + demand covered and covered_pct.

| Parameter | Type | Description |
|---|---|---|
| `districts` | string[] | district names (default: all) |
| `polygon` | array[] |  |
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `type` * | string |  |
| `service` | string |  |
| `top` | integer |  |
| `radius_m` | number |  |
| `min_gap_m` | number |  |
| `served_share` | number |  |
| `verify` | boolean |  |
| `avoid_type` | boolean |  |
| `thresholds` | object |  |
| `lot_m` | number[] |  |
| `road_types` | string[] |  |
| `step_m` | number |  |
| `spacing_m` | number |  |
| `allow` | string |  |
| `grid_m` | number |  |
| `max_tries` | integer |  |
| `allow_subnets` | boolean | mod 0.33: needed for types with built-in nets, e.g. FR_Crematorium (the verify dry-runs are refused without it; save first) |

### `cs2_park_sites`

Where a park fits (mod 0.16): lots of `type` (or lot_m [width along road, depth]) along the at-grade roads in an area, front touching the road and facing it, every 4 m of the lot on free, dry, not-steep ground (trees get cleared; allow adds more cell kinds). Ranked by terrain attractiveness bonus + residents within 400 m with park coverage below low_park_coverage (residents_weight), non-overlapping (spacing_m). Each site's x, z, facing go straight into cs2_place_building. road_types limits which roads to front on. With type (and verify, default true) every returned site has been dry-run through place_building, so the game accepts it; refused spots are replaced by the next best.

| Parameter | Type | Description |
|---|---|---|
| `districts` | string[] | district names (default: all) |
| `polygon` | array[] |  |
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `type` | string |  |
| `lot_m` | number[] |  |
| `road_types` | string[] |  |
| `step_m` | number |  |
| `spacing_m` | number |  |
| `top` | integer |  |
| `coverage` | boolean |  |
| `low_park_coverage` | number |  |
| `residents_weight` | number |  |
| `allow` | string |  |
| `grid_m` | number |  |
| `verify` | boolean |  |
| `allow_subnets` | boolean | mod 0.33: needed to dry-run buildings with built-in nets (save first) |

### `cs2_attractiveness`

Park 'niceness' (mod 0.16) - the game's attractiveness exactly as the building info panel shows it: base x efficiency x maintenance step x (1 + terrain bonus: forest + beach/shore + height). mode 'buildings' (default): every park / landmark in the area with its factors, and the ones held back by low maintenance or efficiency. mode 'spots': the terrain bonus on a step grid (default 50 m) in the area, best first - dry land only, shore_m keeps spots within that distance of water; with type, the attractiveness that park would have at each spot.

| Parameter | Type | Description |
|---|---|---|
| `districts` | string[] | district names (default: all) |
| `polygon` | array[] |  |
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `mode` | string |  |
| `type` | string |  |
| `step` | number |  |
| `shore_m` | number |  |
| `dry_only` | boolean |  |
| `top` | integer |  |

### `cs2_transit_report`

Transit usage (mod 0.15): every passenger line ranked by riders with load (riders / capacity, as in the line panel), vehicles, waiting passengers and its busiest stops, averaged over `samples` taken interval_s apart; overcrowded (>=90%) and near-empty (<=10%) lines; the busiest stops (most passengers waiting); stops without lines; stops and waiting per district. Area filter only affects the district labels.

| Parameter | Type | Description |
|---|---|---|
| `districts` | string[] | district names (default: all) |
| `polygon` | array[] |  |
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `samples` | integer |  |
| `interval_s` | number |  |
| `top` | integer |  |
| `lines` | integer |  |
| `include_cargo` | boolean |  |

### `cs2_foot_traffic`

Where people walk and cars drive (mod 0.15): pedestrians / cars / transit vehicles counted on a grid_m grid (default 100), averaged over samples; busiest walking cells (with distance to the nearest stop), busiest driving cells, busy walking cells farther than far_from_stop_m (400) from any stop, pedestrian / car totals + walk share per district.

| Parameter | Type | Description |
|---|---|---|
| `districts` | string[] | district names (default: all) |
| `polygon` | array[] |  |
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `grid_m` | number |  |
| `samples` | integer |  |
| `interval_s` | number |  |
| `top` | integer |  |
| `far_from_stop_m` | number |  |

### `cs2_commute_flows`

Where people travel (mod 0.15): every worker's home -> workplace (+ student -> school) on a grid_m grid (400): district -> district pairs, trips staying inside each district, and the biggest desire lines longer than min_distance_m (1500), each marked served_by_line if one line stops within walk_m (450) of both ends - the unserved big ones are where a new line / station pays off, and big district pairs show which corridors need road or rail capacity.

| Parameter | Type | Description |
|---|---|---|
| `districts` | string[] | district names (default: all) |
| `polygon` | array[] |  |
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `grid_m` | number |  |
| `min_distance_m` | number |  |
| `walk_m` | number |  |
| `top` | integer |  |
| `pairs` | integer |  |
| `students` | boolean |  |

### `cs2_station_sites`

Where to put stations for a mode (Bus / Tram / Subway / Train / Ferry): demand = homes + jobs/schools (commute data) + pedestrians x walk_weight, in grid_m cells farther than the mode's catchment (radius_m; Bus 350, Tram 450, Subway 700, Train 900) from existing stops of that mode (any_mode: of any mode); uncovered clusters ranked by demand with greedy catchment-sized suggested sites (snap to a road). Area = districts / polygon / box.

| Parameter | Type | Description |
|---|---|---|
| `districts` | string[] | district names (default: all) |
| `polygon` | array[] |  |
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `mode` | string |  |
| `radius_m` | number |  |
| `grid_m` | number |  |
| `any_mode` | boolean |  |
| `walking` | boolean |  |
| `walk_weight` | number |  |
| `min_demand` | number |  |
| `sites_per_gap` | integer |  |
| `top` | integer |  |

### `cs2_sites_tiled`

Service / park sites over a WHOLE island or many districts (2026-10-08): cs2_service_sites over a big area coarsens its occupancy grid and finds ~0 lots; this scans <=560 m tiles at a 4 m grid, then picks greedily over the whole area. Same args as cs2_service_sites plus: students children|teens + capacity (school seats subtracted from the nearest demand - without it one school 'covers' a whole radius), spread_m (+ within polygon) = space facilities evenly where nobody lives yet, place_now = build each pick for real and fall to the next-best lot when refused (no dry-run flicker), cands_file = cache of free lots (Windows path). Vanilla police / clinics / parks need allow_subnets.

| Parameter | Type | Description |
|---|---|---|
| `districts` | string[] |  |
| `polygon` | array[] |  |
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `type` * | string |  |
| `service` | string |  |
| `top` | integer |  |
| `radius_m` | number |  |
| `min_gap_m` | number |  |
| `served_share` | number |  |
| `students` | string |  |
| `capacity` | number |  |
| `spread_m` | number |  |
| `within` | array[] |  |
| `place_now` | boolean |  |
| `verify` | boolean |  |
| `allow_subnets` | boolean |  |
| `tile_m` | number |  |
| `cands_file` | string |  |

### `cs2_service_access`

Why a hearse / ambulance never comes to a building (mod 0.41): its road edge, every spawn location the game's path target seeker uses (a spawn with a connected lane switches the road-edge fallback off, so an unreachable door = no service), what each spawn is connected to (lane kind, owner, access restriction), its sub-areas and lanes, and every citizen in it with a health problem: flags, timer, HealthcareRequest fail count / cooldown / dispatched handler / path.

| Parameter | Type | Description |
|---|---|---|
| `id` * | string |  |

### `cs2_upgrade_all`

Install every fitting service upgrade on many buildings at once (2026-10-08): types (parent building types, e.g. ['ElementarySchool03','FR_HighSchool']) or ids, optional upgrades (names; default = every build-menu entry named '<type> ...' / '<type>_Ext...'), districts / box. Lot upgrades (playgrounds, wings, halls) get a free spot beside the parent like cs2_place_building parent= (in-place extensions - BuildingExtensionData.m_External false: extra floors, school halls, playgrounds - go ON the parent; placed beside it they are loose lots without pedestrian access); ones already installed are skipped. Returns counts per upgrade (ok / refused / already / error) + the problems. dry_run previews only.

| Parameter | Type | Description |
|---|---|---|
| `types` | string[] |  |
| `ids` | string[] |  |
| `upgrades` | string[] |  |
| `districts` | string[] |  |
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `capacity_only` | boolean | only upgrades that add capacity (school seats, beds, hearses, storage, patrol cars, cells, garbage trucks) |
| `dry_run` | boolean |  |

### `cs2_fix_access`

Fix buildings with access warnings (2026-10-08): growables with icons (default ['No Pedestrian Access']; also 'No Road Access' / 'No Car Access') are bulldozed so the lot regrows (zoning kept); service / signature buildings with the warning are only listed - the usual cause is an extension placed beside instead of on the parent: rebuild in place, then cs2_upgrade_all capacity_only. Where: districts / polygon / box (default whole city). dry_run lists.

| Parameter | Type | Description |
|---|---|---|
| `icons` | string[] |  |
| `districts` | string[] |  |
| `polygon` | array[] |  |
| `xmin` | number |  |
| `zmin` | number |  |
| `xmax` | number |  |
| `zmax` | number |  |
| `dry_run` | boolean |  |

## Previews and undo

### `cs2_preview_commit`

Build the preview left on screen by an operation called with hold=true. Refuses if it has errors unless allow_errors.

| Parameter | Type | Description |
|---|---|---|
| `allow_errors` | boolean |  |

### `cs2_preview_cancel`

Discard the preview left on screen by an operation called with hold=true.

### `cs2_undo`

Undo the most recent change made through this MCP (place roads / bulldoze / replace / zone). Pass expect_id (the change's undo_id, or an id from cs2_history) so the wrong change can never be undone.

| Parameter | Type | Description |
|---|---|---|
| `expect_id` | integer |  |
| `count` | integer | undo this many changes, newest first (default 1) |

## Assets and prefabs

### `cs2_asset_validate`

Offline pre-flight of a CS2 asset folder exported from Blender (no game needed). Checks: folder name == LOD0 FBX name (<Theme>_<Name>.fbx, LODs _LOD1/_LOD2); FBX in CENTIMETRES with the transform baked (UnitScaleFactor 1, no node scale - the importer scales raw vertices x0.01 and ignores unit + node transforms, so metres = 1/100 size, invisible; Blender needs bake_space_transform=True); main meshes have exactly one material named like the file + _BaseColor/_Normal/_MaskMap PNGs (+ _ControlMask/_Emissive); windows in a SEPARATE <Name>_Win.fbx with no material (night-lit interior mapping only triggers on the file name); square power-of-two textures; clean project root; build-menu metadata (<root>_build/<Name>_meta.json with title + description, and <Name>_Icon.png) - without it the asset has no picture or description in game. Full workflow: cs2-mcp/ASSET_PIPELINE.md.

| Parameter | Type | Description |
|---|---|---|
| `directory` * | string | the asset folder, e.g. <project root>/NA_MyAsset |

### `cs2_asset_import`

Import an asset folder into the running game exactly like the Editor's Asset Importer (mod 0.11: calls AssetImportPanel.ImportAssets), no editor GUI: validates first, then imports meshes + textures (can take minutes), creates the object prefab from `preset` (Building | SignatureBuilding | CityServiceBuilding | SpawnableBuilding | StaticObject | ... ; or `template` = an existing prefab name to clone its gameplay setup -> ExistingPrefab), saves it to the user asset database and registers it so cs2_place_building can use it by name. lot_width/lot_depth (cells) set the building lot. project_root defaults to the folder's parent. preset 'None' = re-import meshes/textures IN PLACE and keep the prefab's configuration (use it for every update after the first import). Returns the created prefab(s), log lines, ok=false + errors + a hint when the game's pipeline logged errors (e.g. 'being used by another process' = the game holds the loaded .Geometry: quit to the main menu first). Import from a loaded city, or with preset None at the main menu (Find It crashes otherwise). After a successful import it applies the build-menu picture + name + description (mod 0.14) from icon/title/description args or, by default, from <project_root>_build/<Name>_meta.json ({title, description, icon?}) + <Name>_Icon.png - every asset should have them.

| Parameter | Type | Description |
|---|---|---|
| `directory` * | string |  |
| `project_root` | string |  |
| `preset` | string |  |
| `template` | string |  |
| `seed` | string |  |
| `lot_width` | integer |  |
| `lot_depth` | integer |  |
| `activate_tool` | boolean | leave the game's placement tool active with the new prefab (editor behaviour); default false |
| `force` | boolean | import even if validation found problems |
| `timeout_s` | number |  |
| `title` | string | build-menu name (default: from the meta file) |
| `description` | string | build-menu description (default: from the meta file) |
| `icon` | string | PNG/JPG build-menu picture (default: <root>_build/<Name>_Icon.png) |
| `meta` | string | path of the meta JSON (default <project_root>_build/<Name>_meta.json) |
| `window_light` | number | mod 0.34: the Editor import panel's 'Interior Light Intensity' for every *_Win/_Wim/_Wif/_Wio FBX (0..1, game default 0.549, saturated at 1) |
| `window_light_lod` | number | mod 0.34: separate intensity for the _LOD1/_LOD2 window files (far LODs read dim) |
| `window_color` | number[] | mod 0.34: window base colour [r, g, b] linear (default dark blue-grey 0.027, 0.05, 0.072) |

### `cs2_asset_configure`

Edit and save a prefab by name (mod 0.12), e.g. give an imported building its gameplay: copy_from = a vanilla prefab whose gameplay components to clone (e.g. NA_ResidentialHighSignature01 -> signature residential: zone, level, UI, unlock; sub-objects/areas/lanes/effects are skipped); add_components / remove_components = component class names (e.g. BuildingProperties); set = {"Component.m_Field": value} or {"m_Field": value} on the prefab itself - numbers, bools, enum names, prefab names for prefab references, lists for arrays (e.g. {"BuildingProperties.m_ResidentialProperties": 440, "m_LotWidth": 4}); lot_width / lot_depth / access_type shortcuts. describe = true lists every component's public fields and values. save (default true) writes it back to its asset. Build-menu info (mod 0.14, imported/user assets only): icon = a PNG/JPG path -> stored as an image asset next to the prefab and set as UIObject.m_Icon (square, transparent background, ~256-512 px); title / description = localized name + description (Assets.NAME/DESCRIPTION[<name>], saved as locale assets next to the prefab and shown immediately); locale defaults to en-US. Mod 0.15: describe expands nested entries (sub-nets, sub-objects, Bezier curves) with prefab names; set accepts structured values - JSON objects set fields by name, lists fill fields in order (float3 = [x,y,z], Bezier4x3 = [a,b,c,d], quaternion = [x,y,z,w] or {"yaw_deg": d}) - e.g. {"ObjectSubNets.m_SubNets": [{"m_NetPrefab": "Double Train Track", "m_BezierCurve": [[-100,-20,0],[-33,-20,0],[33,-20,0],[100,-20,0]], "m_NodeIndex": [0,1], "m_ParentMesh": [-1,-1]}]} gives a building built-in tracks (y = height, negative = underground).

| Parameter | Type | Description |
|---|---|---|
| `name` * | string |  |
| `copy_from` | string |  |
| `copy_components` | string[] | mod 0.26, with copy_from: copy only these component types (e.g. ["EmissiveProperties"] from "Taxi02 Mesh" - vehicle light layers + turn-signal curves) |
| `add_components` | string[] |  |
| `remove_components` | string[] |  |
| `set` | object |  |
| `describe` | boolean |  |
| `lot_width` | integer |  |
| `lot_depth` | integer |  |
| `access_type` | string |  |
| `save` | boolean |  |
| `icon` | string |  |
| `title` | string |  |
| `description` | string |  |
| `locale` | string |  |
| `allow_vanilla` | boolean | mod 0.33: edits are refused on prefabs that are not your own imported assets (game / mod-pack prefabs; describe and copy_from as a SOURCE stay allowed). true = runtime-only edit of such a prefab, never saved, lost on restart - a test at the main menu, not in the city |
| `describe_components` | string[] | with describe: only these components (e.g. ["ObjectSubNets"]) |

### `cs2_prefab_clone`

Duplicate a user (imported) prefab under a new name (mod 0.34): saved next to it in the user asset database and registered live; the clone REFERENCES the same meshes, so one imported growable house can become its lot-depth variants (1x2..1x6) without re-importing mesh + textures. Configure the clone afterwards with cs2_asset_configure (lot, SpawnableBuilding level, sub-objects/areas, m_Meshes offset). Idempotent: returns exists=true when the name is already a user prefab.

| Parameter | Type | Description |
|---|---|---|
| `source` * | string |  |
| `name` * | string |  |

### `cs2_prefab_components`

Diagnose a prefab by name: its C# class, ECS components and authoring components. Placeholders left by removed mods lack mesh/render components compared with a working prefab of the same kind.

| Parameter | Type | Description |
|---|---|---|
| `name` * | string |  |

### `cs2_prefab_entity`

Read / write a prefab's runtime ECS component data (mod 0.24) - the data game systems actually use, e.g. CarData.m_SizeClass / m_EnergyType that vehicle selection filters on (vanilla prefab edits via cs2_asset_configure are NOT saved and don't reach live entity data). component = ECS struct name (CarData, TransportDepotData, ...). set = {field: value} (enum names ok). persist (default true when set) stores it in ModsData\CS2MCP\prefab_overrides.json and the mod re-applies it every game start; remove=true drops a stored override.

| Parameter | Type | Description |
|---|---|---|
| `name` * | string |  |
| `component` * | string |  |
| `set` | object |  |
| `persist` | boolean |  |
| `remove` | boolean |  |

### `cs2_prefab_overrides`

List the persisted prefab entity overrides the mod re-applies at every game start (mod 0.24).

### `cs2_mesh_materials`

How a prefab's meshes are shaded (mod 0.18, read-only): per render mesh, each material's template + shader, float / colour / vector properties and texture slots - e.g. to copy how a vanilla building does its glass.

| Parameter | Type | Description |
|---|---|---|
| `name` * | string |  |

### `cs2_mesh_export`

Export a prefab's render meshes, every LOD, as OBJ + JSON sidecar files for Blender (mod 0.37, read-only for the game) - e.g. put stock vehicles (SUV, van, sedan, tram) next to our own models and compare geometry. name = an object prefab (vehicle / building / prop: all its ObjectGeometryPrefab meshes) or a render mesh prefab ("... Mesh"). Writes <out_dir>\<mesh>_LOD<n>.obj/.mtl/.json + <prefab>_mesh_export.json (manifest with each mesh's placement in the object). OBJ: metres, RAW game local frame (left-handed, +x right, +y up, +z forward), normals, UV0, one g/usemtl per submesh; vertex order 1:1 with the sidecar. Sidecar: counts, bounds, submesh->material, bones (name, parent, BoneType, bind pose, mesh-space position), per-vertex bone indices/weights + per-bone dominant-vertex bounds (finds wheels / bogies). Data comes from the mesh's .Geometry file (works even when the mesh is not loaded). lods = LOD numbers to export (0 = full detail; default all). Load into Blender with tools\import_mesh_export.py (converts to Z-up, facing -Y).

| Parameter | Type | Description |
|---|---|---|
| `name` * | string |  |
| `out_dir` * | string |  |
| `lods` | integer[] |  |

### `cs2_texture_export`

Save a vanilla prefab's textures as PNGs (mod 0.32, read-only for the game): surfaces / lots (RenderedArea base colour / normal / mask maps + its material's textures) and render-mesh material slots - e.g. reuse the game's own grass ("Grass Surface 01") on a custom mesh where terrain-only surfaces can't render. slot = substring filter; out_dir = folder for the PNGs.

| Parameter | Type | Description |
|---|---|---|
| `name` * | string |  |
| `out_dir` * | string |  |
| `slot` | string |  |

### `cs2_missing_assets`

Leftovers of uninstalled mods (mod 0.20) - why the 'subscribe to the missing mods' popup shows on every load: the game keeps assets from mods that are gone as obsolete placeholders and saves them again while anything uses them. Lists each obsolete asset with what uses it (buildings, building extensions, vehicles, other; id, position, owner) and the transit lines whose vehicle choice points at one. clear_vehicle_models = reset those lines to automatic vehicle choice; delete_vehicles = remove vehicles built from missing models (depots send new ones). Remove buildings / extensions with cs2_bulldoze by id (and place a replacement). After cleanup, save + reload: the popup should be gone.

| Parameter | Type | Description |
|---|---|---|
| `clear_vehicle_models` | boolean |  |
| `delete_vehicles` | boolean |  |
| `limit` | integer |  |

### `cs2_menu_assets`

What the build menus offer (mod 0.20): every asset with a menu entry whose menu group (e.g. 'Train', 'Road', 'Park') or name matches, with has_icon (false = the blank-icon entries), source (base game / 'mod <id>' / local folder), file and in_city (objects using it now), plus counts per source - to find which mod fills a menu with unused, icon-less entries.

| Parameter | Type | Description |
|---|---|---|
| `group` | string |  |
| `filter` | string |  |
| `no_icon_only` | boolean |  |
| `count_use` | boolean |  |
| `limit` | integer |  |

### `cs2_prefab_create`

Create a new EMPTY user prefab of a Game.Prefabs type (mod 0.38), saved to the user asset database (loads at every game start, like an imported asset) and registered live - e.g. type=BrandPrefab for a new company brand. Fill it with cs2_asset_configure: set {"m_Companies": [company prefab names], "m_BrandColors": [[r,g,b,a] x3 linear]}, icon = logo PNG (the company logo in info panels), title = the brand name shown in game.

| Parameter | Type | Description |
|---|---|---|
| `type` * | string |  |
| `name` * | string |  |
| `folder` | string |  |

### `cs2_brand_info`

Company brands (mod 0.38): BrandPrefabs with their CompanyPrefabs, and CompanyPrefabs (zone, output resource, brand count) whose name matches filter. A company draws its brand at random from its company prefab's brands.

| Parameter | Type | Description |
|---|---|---|
| `filter` | string |  |

### `cs2_brand_bind`

Persist 'every company renting this building prefab carries this brand' (mod 0.38; ModsData\CS2MCP\brand_bindings.json): a pump re-stamps CompanyData.m_Brand of the renters every ~256 frames in a loaded city, so a signature shop always shows its own brand name + logo. remove=true drops the binding.

| Parameter | Type | Description |
|---|---|---|
| `building` * | string |  |
| `brand` * | string |  |
| `remove` | boolean |  |

### `cs2_brand_bindings`

List the persisted building -> brand bindings (mod 0.38).
