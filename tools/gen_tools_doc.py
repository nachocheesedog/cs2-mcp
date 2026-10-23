"""Regenerate docs/TOOLS.md from the server's tool registry: python tools/gen_tools_doc.py"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
import cs2_mcp

CATEGORIES = [
    ("Game, session and camera", "status camera camera_goto set_speed settings list_saves load_save main_menu restart_game history tool_reset raw"),
    ("Seeing the city", "screenshot photo_mode map tour inspect traffic demand zone_summary zone_report building_warnings building_status"),
    ("Raw data and catalogues", "terrain_height terrain_grid road_network zone_blocks query_roads street_names query_buildings list_road_types list_zone_types list_building_types list_object_types list_districts list_district_policies obstacles pillars areas building_nets refs frame shore shore_points"),
    ("Roads and networks", "rename_streets place_roads place_grid place_circle place_path place_pair place_lines place_arcs extend_lines offset_road rim_road ramp trim replace_roads district_grid connect_options build_plan remove_plan probe place_piers place_along_shore building_access design_path network_check site_movements road_align road_snapshot road_restore road_profile node_detail highway_fork path_test"),
    ("Zoning", "zone zone_along zone_palette rezone rezone_buildings zone_snapshot zoning_check zone_cracks waterfront_backs"),
    ("Buildings, objects, terrain and areas", "place_building place_objects bulldoze terraform delete_areas"),
    ("Districts", "create_district delete_district rename_district set_district_policy"),
    ("City services and transit", "service_coverage service_budget service_sites park_sites attractiveness transit_report foot_traffic commute_flows station_sites sites_tiled service_access upgrade_all fix_access"),
    ("Previews and undo", "preview_commit preview_cancel undo"),
    ("Assets and prefabs", "asset_validate asset_import asset_configure prefab_clone prefab_components prefab_entity prefab_overrides mesh_materials mesh_export texture_export missing_assets menu_assets prefab_create brand_info brand_bind brand_bindings"),
]

def params(schema):
    rows = []
    req = set(schema.get("required", []))
    for k, v in (schema.get("properties") or {}).items():
        t = v.get("type", "")
        if isinstance(t, list): t = "/".join(t)
        if t == "array" and isinstance(v.get("items"), dict) and v["items"].get("type"): t = v["items"]["type"] + "[]"
        d = (v.get("description") or "").replace("|", "\|").replace("\n", " ")
        rows.append(f"| `{k}`{' *' if k in req else ''} | {t} | {d} |")
    return rows

tools = {t[0]: t for t in cs2_mcp.TOOLS}
seen, out = set(), []
out += ["# Tool reference", "",
        f"All {len(tools)} tools the `cs2` MCP server exposes, generated from the server's registry by "
        "`tools/gen_tools_doc.py`. `*` = required parameter.", "",
        "Conventions: world coordinates are the game's (x/z horizontal in metres, y up); zoning cells are 8 m. "
        "Every editing tool accepts `dry_run` (preview + error reasons, nothing built) and most accept `hold` "
        "(keep the preview until `cs2_preview_commit` / `cs2_preview_cancel`). Edits return an `undo_id` for `cs2_undo`.", ""]
out.append("## Contents"); out.append("")
for title, _ in CATEGORIES: out.append(f"- [{title}](#{title.lower().replace(',', '').replace(' ', '-')})")
out.append("")
for title, names in CATEGORIES:
    out += [f"## {title}", ""]
    for n in names.split():
        name = "cs2_" + n
        if name not in tools: continue
        seen.add(name)
        _, desc, schema, _ = tools[name]
        out += [f"### `{name}`", "", desc.strip(), ""]
        rows = params(schema)
        if rows: out += ["| Parameter | Type | Description |", "|---|---|---|", *rows, ""]
missing = [n for n in tools if n not in seen]
if missing:
    out += ["## Other", ""]
    for name in missing:
        _, desc, schema, _ = tools[name]
        out += [f"### `{name}`", "", desc.strip(), ""]
        rows = params(schema)
        if rows: out += ["| Parameter | Type | Description |", "|---|---|---|", *rows, ""]
open(os.path.join(HERE, "..", "docs", "TOOLS.md"), "w", encoding="utf-8", newline="\n").write("\n".join(out))
print(f"{len(tools)} tools, {len(missing)} uncategorised: {missing}")
