#!/usr/bin/env python3
"""CS2 MCP server: exposes Cities: Skylines II to MCP clients (Claude Code) over stdio.

Talks to the CS2MCP game mod's local-only HTTP bridge (http://127.0.0.1:17654). Standard library only.
Tools: status, camera, terrain heights, road types, road query, road placement and a grid planner/placer.
World coordinates are CS2's: x/z horizontal in metres, y up. Zoning cells are 8 m.
"""
import base64, json, math, os, sys, time, urllib.request, urllib.error
import survey, tools2, tools3, tools4, tools5, tools6, tools7, tools8, tools9, tools10, tools11, tools13, tools14, tools15, assets, autofix

BRIDGE = "http://127.0.0.1:17654"
PROTOCOL = "2024-11-05"
CELL = 8.0


# ----------------------------------------------------------------------------- bridge client
class BridgeError(Exception):
    pass


def rpc(method, params=None, timeout=90):
    """Bridge call; edits retry (up to ~20 s) when the player switches tools mid-operation in the game."""
    for attempt in range(10):
        try: return _rpc_once(method, params, timeout)
        except BridgeError as e:
            if "another tool was selected" not in str(e) or attempt == 9: raise
            time.sleep(2.0)


def _rpc_once(method, params=None, timeout=90):
    body = json.dumps({"method": method, "params": params or {}}).encode()
    req = urllib.request.Request(BRIDGE + "/rpc", data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try: data = json.loads(e.read())
        except Exception: raise BridgeError(f"HTTP {e.code}")
        raise BridgeError(data.get("error", f"HTTP {e.code}"))
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise BridgeError(f"cannot reach the CS2MCP mod at {BRIDGE} ({e}). Is Cities: Skylines II running with the CS2MCP mod enabled?")
    if "error" in data: raise BridgeError(data["error"])
    return data.get("result")


tools3.RPC = rpc
assets.RPC = rpc


def health():
    try:
        with urllib.request.urlopen(BRIDGE + "/health", timeout=3) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ----------------------------------------------------------------------------- grid planning
def road_widths():
    return {r["name"]: r["width"] for r in rpc("list_road_types")["road_types"]}


def line_positions(count, types, widths, gap):
    """Centre-line offsets for `count` parallel roads so the clear gap between road edges is exactly `gap`."""
    pos = [0.0]
    for i in range(1, count):
        pos.append(pos[-1] + widths[types[i - 1]] / 2 + gap + widths[types[i]] / 2)
    mid = (pos[0] + pos[-1]) / 2
    return [p - mid for p in pos]


def plan_grid(center, angle_deg, cols, rows, local_type, arterial_type, arterial_every, depth_cells, length_cells,
              pattern, widths):
    """Blocks are `cols` x `rows`. Roads along +u (long side) are spaced for 2 x depth_cells of zoning; cross streets
    along +v are spaced for blocks length_cells long. Every `arterial_every`-th line (and the outer ring) uses the
    arterial type. pattern='grid' (4-way junctions) or 'staggered' (cross streets offset every other row -> T-junctions)."""
    if local_type not in widths: raise BridgeError(f"unknown road type '{local_type}'")
    if arterial_type and arterial_type not in widths: raise BridgeError(f"unknown road type '{arterial_type}'")
    art = arterial_type or local_type
    n_u, n_v = rows + 1, cols + 1                     # lines running along u (one per row boundary), along v
    def is_art(i, n): return bool(arterial_type and arterial_every and (i % arterial_every == 0 or i == n - 1))
    art_v = [is_art(j, n_v) for j in range(n_v)]      # arterial cross streets always run straight through
    types_u = [art if is_art(i, n_u) else local_type for i in range(n_u)]
    types_v = [art if art_v[j] else local_type for j in range(n_v)]
    vpos = line_positions(n_u, types_u, widths, 2 * depth_cells * CELL)   # offsets across v for roads running along u
    upos = line_positions(n_v, types_v, widths, length_cells * CELL)      # offsets along u for cross streets
    a = math.radians(angle_deg); cu, su = math.cos(a), math.sin(a)
    def world(u, v): return [round(center[0] + u * cu - v * su, 3), round(center[1] + u * su + v * cu, 3)]
    segs = []
    # roads along u: split at every cross street (in 'staggered' mode only where a cross street touches that line)
    for i, v in enumerate(vpos):
        stops = []
        for j, u in enumerate(upos):
            if pattern == "grid" or art_v[j] or j in (0, n_v - 1):
                stops.append(u)
            else:   # staggered: local cross street j exists in the row above (i-1..i) if (row + j) even
                touches_below = i > 0 and ((i - 1) + j) % 2 == 0
                touches_above = i < n_u - 1 and (i + j) % 2 == 0
                if touches_below or touches_above: stops.append(u)
        stops = sorted(set(stops))
        for u0, u1 in zip(stops, stops[1:]):
            segs.append({"start": world(u0, v), "end": world(u1, v), "type": types_u[i]})
    # cross streets along v, one piece per row
    for j, u in enumerate(upos):
        for i in range(n_u - 1):
            if pattern == "staggered" and not art_v[j] and j not in (0, n_v - 1) and (i + j) % 2 != 0:
                continue
            segs.append({"start": world(u, vpos[i]), "end": world(u, vpos[i + 1]), "type": types_v[j]})
    span_u, span_v = upos[-1] - upos[0], vpos[-1] - vpos[0]
    total = sum(math.dist(s["start"], s["end"]) for s in segs)
    return segs, {"segments": len(segs), "extent_m": [round(span_u, 1), round(span_v, 1)], "road_length_m": round(total),
                  "block_zoning_depth_cells": depth_cells, "block_length_cells": length_cells, "pattern": pattern,
                  "center": center, "angle_deg": angle_deg}


def place_batches(segs, dry_run, allow_errors, batch=150, hold=False):
    results = []
    if hold: batch = max(1, len(segs))                 # a held preview must be a single operation
    for k in range(0, len(segs), batch):
        chunk = segs[k:k + batch]
        res = rpc("place_roads", {"segments": chunk, "snap_tolerance": 1.5, "dry_run": dry_run, "allow_errors": allow_errors,
                                  "hold": hold, "timeout_s": 90})
        results.append(res)
        if not dry_run and not res.get("applied"):
            break                                      # stop at the first batch the game refused
    return results


# ----------------------------------------------------------------------------- MCP tools
def t_status(_):
    h = health()
    out = {"bridge": h}
    if h.get("ok"):
        out["game"] = rpc("ping")
        if out["game"].get("city_loaded") and not out["game"].get("loading"): out["city"] = rpc("city_info")   # mod 0.33 refuses queries mid-load
    return out


def t_camera(_): return rpc("camera")
def t_terrain(a): return rpc("terrain_height", {"points": a["points"]})
def t_road_types(a): return rpc("list_road_types", {"filter": a.get("filter", ""), "kind": a.get("kind", "all")})
def t_building_types(a): return rpc("list_building_types", {k: a[k] for k in ("filter", "limit", "growables") if k in a})
def t_building_nets(a): return rpc("building_nets", {"id": a["id"]})
def t_areas(a): return rpc("areas", {k: a[k] for k in ("xmin", "zmin", "xmax", "zmax", "kinds", "limit") if k in a})
def t_prefab_components(a): return rpc("prefab_components", {"name": a["name"]})
def t_delete_areas(a): return rpc("delete_areas", {"ids": a["ids"], "dry_run": a.get("dry_run", False), "timeout_s": 90})


def _upgrade_spots(a):
    """Extension upgrades (own lot, e.g. a hearse garage): lot centres flush against the parent's sides and back, front first."""
    par = rpc("building_status", {"id": a["parent"]})["buildings"][0]
    info = rpc("query_buildings", {"xmin": par["position"][0] - 1, "xmax": par["position"][0] + 1,
                                   "zmin": par["position"][1] - 1, "zmax": par["position"][1] + 1, "limit": 50})["buildings"]
    b = next(x for x in info if x["id"] == a["parent"])
    try: xd = rpc("prefab_entity", {"name": a["type"], "component": "BuildingExtensionData"})["fields"]
    except Exception: xd = None
    if xd and not xd.get("m_External"):                           # an in-place extension (extra floors, school halls/wings,
        mp = xd.get("m_Position") or {}                           # playgrounds): it sits ON the parent, offset by m_Position -
        fx, fz = b["forward"]; rx, rz = fz, -fx                   # placed beside it, it is a loose lot with no pedestrian
        ox, oz = mp.get("x", 0), mp.get("z", 0)                   # access (2026-10-08)
        return [([b["position"][0] + rx * -ox + fx * oz, b["position"][2] + rz * -ox + fz * oz], [fx, fz])]
    ext = [t for t in rpc("list_building_types", {"filter": a["type"]})["buildings"] if t["name"] == a["type"]]
    if ext: w, d = ext[0]["lot_m"]
    else:                                                         # extension prefabs without BuildingData (school playgrounds,
        try:                                                      # extension floors/halls): lot from BuildingExtensionData
            ls = rpc("prefab_entity", {"name": a["type"], "component": "BuildingExtensionData"})["fields"]["m_LotSize"]
            w, d = ls["x"] * 8, ls["y"] * 8
        except Exception: return []
        if not w or not d: return []
    W, D = b["lot"][0] * 8, b["lot"][1] * 8
    fx, fz = b["forward"]; rx, rz = fz, -fx                       # front direction and the side axis
    px, pz = b["position"][0], b["position"][2]
    spots = []
    for side in (1, -1):                                          # beside the parent, front edges aligned, then slid back
        for back in (0, 0.5, 1):
            along = D / 2 - d / 2 - back * max(0, D - d)
            spots.append(([px + side * rx * (W / 2 + w / 2) + fx * along, pz + side * rz * (W / 2 + w / 2) + fz * along], [fx, fz]))
    for off in (0, -1, 1):                                        # behind the parent, facing away (its own road at the back)
        o = off * max(0, W - w) / 2
        spots.append(([px - fx * (D / 2 + d / 2) + rx * o, pz - fz * (D / 2 + d / 2) + rz * o], [-fx, -fz]))
    return spots


def t_place_building(a):
    import tools2, tools3
    if a.get("parent") and a.get("x") is None and not a.get("at"):
        spots = _upgrade_spots(a)
        if not spots:                                             # a wing without its own lot: the mod puts it on the parent
            q = {"type": a["type"], "parent": a["parent"], "dry_run": a.get("dry_run", False), "timeout_s": 90,
                 "allow_subnets": bool(a.get("allow_subnets"))}
            return rpc("place_building", q)
        tried = []
        for (x, z), f in spots:
            pre = rpc("place_building", {"type": a["type"], "parent": a["parent"], "x": x, "z": z, "facing": f, "dry_run": True, "timeout_s": 90,
                                         "allow_subnets": bool(a.get("allow_subnets"))})
            tried.append({"x": round(x, 1), "z": round(z, 1), "errors": pre.get("errors")})
            if not pre.get("errors"):
                return t_place_building(dict(a, x=x, z=z, facing=f))
        return {"ok": False, "error": "no free spot next to the parent - give x, z, facing", "tried": tried}
    p = tools3.resolve(a["at"], tools3.RPC) if a.get("at") else [a["x"], a["z"]]
    fr = tools2.framed(a)
    if fr: p = fr.to_world(p)
    q = {"type": a["type"], "x": p[0], "z": p[1], "dry_run": a.get("dry_run", False), "hold": a.get("hold", False),
         "allow_errors": a.get("allow_errors", False), "timeout_s": 90}
    if a.get("parent"): q["parent"] = a["parent"]
    if a.get("allow_subnets"): q["allow_subnets"] = True          # mod 0.33 refuses sub-net buildings (dry runs too) without it
    if a.get("facing"): q["facing"] = a["facing"]
    elif "angle_deg" in a: q["angle_deg"] = a["angle_deg"]
    if not q["dry_run"] and not a.get("allow_subnets"):
        pre = rpc("place_building", dict(q, dry_run=True, hold=False))
        if pre.get("sub_nets"):
            raise BridgeError(f"{a['type']} has {pre['sub_nets']} built-in nets (tracks/quays/roads); applying such buildings crashed the "
                              "game once (native crash, 2026-09-23). Place it in-game by hand, or pass allow_subnets=true after saving.")
    tools2.follow(rpc, a, [p])
    res = rpc("place_building", q)
    if res.get("errors") and a.get("explain_errors", True): res["error_hits"] = tools2.explain_errors(rpc, res)
    return res


def t_query_roads(a):
    return rpc("query_roads", {k: a[k] for k in ("xmin", "zmin", "xmax", "zmax", "limit") if k in a})


def compact(res, a):
    """Drop bulky id lists unless the caller asked for verbose output (token saver)."""
    if a.get("verbose") or not isinstance(res, dict): return res
    out = dict(res)
    ids = out.pop("created_ids", None)
    if ids is not None: out["created_ids_n"] = len(ids)
    return out


def _lattice(segs, a):
    """v0.36: snap a draft onto the zone-cell lattice (kerb-to-kerb 8k between facing parallel roads) against the
    existing straight roads around it, so the built blocks have zero cracks. lattice: 'snap' (default) | 'check' | 'off'."""
    mode = a.get("lattice", "snap")
    if mode == "off" or not segs: return segs, None
    import align
    wid = {k: align.cell_width(v) * 8 for k, v in road_widths().items()}
    xs = [c for sg in segs for c in (sg["start"][0], sg["end"][0])]; zs = [c for sg in segs for c in (sg["start"][1], sg["end"][1])]
    box = {"xmin": min(xs) - 150, "zmin": min(zs) - 150, "xmax": max(xs) + 150, "zmax": max(zs) + 150}
    fixed = [e for e in align._network(rpc, box)
             if math.dist((e["curve"][0][0], e["curve"][0][2]), (e["curve"][3][0], e["curve"][3][2])) >= 0.995 * e["length"]]
    fixed = [dict(e, width=align.cell_width(e["width"]) * 8) for e in fixed]
    new, rep = align.snap_segments(segs, wid, fixed)
    return (new if mode == "snap" else segs), dict(rep, mode=mode)


def t_place_roads(a):
    if a.get("before_after") and not (a.get("dry_run") or a.get("hold")):
        segs0 = [tools2.frame_seg(s, tools2.framed(a)) for s in (tools2.load_list(a, "segments") or [])]
        return tools3.with_before_after(rpc, a, tools2.seg_points(segs0), lambda: t_place_roads(dict(a, before_after=False, follow_camera=False)))
    fr = tools2.framed(a)
    segs = [tools2.frame_seg(dict(s, type=s.get("type") or a.get("type")), fr) for s in (tools2.load_list(a, "segments") or [])]
    if not segs: raise ValueError("segments (or segments_file) required")
    extra = {}
    segs, lat = _lattice(segs, a)
    if lat: extra["lattice"] = lat
    if a.get("deck_height") is not None: tools4.level_segments(rpc, segs, float(a["deck_height"]), a.get("min_elevation", 0.0))
    if a.get("node_snap_m"):
        moved = tools2.snap_to_nodes(rpc, segs, a["node_snap_m"])
        if moved: extra["ends_moved_to_nodes"] = moved[:40]
    tools2.follow(rpc, a, tools2.seg_points(segs))
    # 2026-09-28: direction-safe joins - highway <-> local at grade, one-way head-on / head-to-head (tools13.join_check)
    if a.get("join_check", True):
        try:
            jc = tools13.join_check(rpc, segs, a.get("snap_tolerance", 2.0))
            extra["join_check"] = jc
            bad = [c for c in jc["conflicts"] if not c.get("warn_only")]
            if bad and not (a.get("dry_run") or a.get("hold") or a.get("allow_conflicts")):
                return dict({"applied": False, "refused": "join conflicts (pass allow_conflicts=true only if intended)", "conflicts": bad}, **extra)
        except Exception as ex:
            extra["join_check"] = {"error": str(ex)[:200]}
    if a.get("skip_failing") and not (a.get("dry_run") or a.get("hold")):
        import pave
        res, bad = pave.place_skip_failing(rpc, segs, a.get("snap_tolerance", 2.0))
        out = compact(res or {"applied": False}, a)
        out["refused"] = len(bad)
        if bad: out["refused_segments"] = bad[:50]
        tools2.after_build(rpc, a, segs, out)
        return dict(out, **extra)
    params = {"snap_tolerance": a.get("snap_tolerance", 2.0),
              "dry_run": a.get("dry_run", False), "allow_errors": a.get("allow_errors", False),
              "hold": a.get("hold", False), "no_intersections": a.get("no_intersections", False), "timeout_s": 90}
    res = rpc("place_roads", dict(params, segments=segs))
    # a preview (dry_run / hold) with errors is fixed here, not handed back: segments on an error position are dropped
    # and the preview is redone until it is clean (auto_fix=false turns this off; auto_fix_rounds / auto_fix_radius tune it).
    if res.get("errors") and (a.get("dry_run") or a.get("hold")) and a.get("auto_fix", True):
        segs, res, fix = autofix.run(rpc, segs, params, res, int(a.get("auto_fix_rounds", 4)), float(a.get("auto_fix_radius", autofix.RADIUS)))
        extra["auto_fix"] = fix
    if res.get("errors") and a.get("explain_errors", True): res["error_hits"] = tools2.explain_errors(rpc, res)
    if res.get("error_locations") and not a.get("verbose"): res["error_locations"] = res["error_locations"][:30]
    out = dict(compact(res, a), **extra)
    if res.get("applied"): tools2.after_build(rpc, a, segs, out)
    if a.get("dry_run") and a.get("zoning_check", True) and segs:
        try: out["zoning_forecast"] = _forecast_for(segs)
        except Exception as ex: out["zoning_forecast"] = {"error": str(ex)}
    return out


def t_grid(a):
    widths = road_widths()
    center = a.get("center")
    if not center:
        gp = rpc("camera").get("ground_point")
        if not gp: raise BridgeError("no center given and the camera is not looking at the ground")
        center = [round(gp[0], 1), round(gp[2], 1)]
    segs, info = plan_grid(center, a.get("angle_deg", 0.0), a.get("cols", 4), a.get("rows", 4), a["local_type"],
                           a.get("arterial_type"), a.get("arterial_every", 0), a.get("zoning_depth_cells", 6),
                           a.get("block_length_cells", 12), a.get("pattern", "grid"), widths)
    if a.get("plan_only") or a.get("dry_run"):
        try: info["zoning_forecast"] = _forecast_for(segs)
        except Exception as ex: info["zoning_forecast"] = {"error": str(ex)}
    if a.get("plan_only"):
        info["segments_list"] = segs
        return info
    info["batches"] = place_batches(segs, a.get("dry_run", False), a.get("allow_errors", False), hold=a.get("hold", False))
    if a.get("zone_type") and not a.get("dry_run") and not a.get("hold") and info["batches"] and all(b.get("applied") for b in info["batches"]):
        depth = a.get("zoning_depth_cells", 6) * CELL      # marquee over the whole grid incl. the outer ring's outer side
        ext_u, ext_v = info["extent_m"]
        info["zoning"] = rpc("zone", {"corners": rect_corners(center, ext_u + 2 * depth, ext_v + 2 * depth, a.get("angle_deg", 0.0)),
                                      "zone_type": a["zone_type"], "overwrite": a.get("overwrite_zoning", False), "timeout_s": 90})
    return info


def t_raw(a): return rpc(a["method"], a.get("params", {}))


# ----------------------------------------------------------------------------- v0.2 tools
def rect_corners(center, width, depth, angle_deg):
    """Corners (in order) of a width x depth rectangle centred at `center`, rotated by angle_deg (x/z plane)."""
    a = math.radians(angle_deg); cu, su = math.cos(a), math.sin(a)
    out = []
    for u, v in ((-width / 2, -depth / 2), (width / 2, -depth / 2), (width / 2, depth / 2), (-width / 2, depth / 2)):
        out.append([round(center[0] + u * cu - v * su, 3), round(center[1] + u * su + v * cu, 3)])
    return out


def circle_segments(center, radius, n, clockwise):
    """Ring road as n cubic-Bezier arcs (exact circle approximation: handle = 4/3 * tan(theta/4) * r)."""
    th = 2 * math.pi / n; h = 4 / 3 * math.tan(th / 4) * radius; sgn = -1 if clockwise else 1
    segs = []
    for k in range(n):
        a0, a1 = sgn * k * th, sgn * (k + 1) * th
        p0 = [center[0] + radius * math.cos(a0), center[1] + radius * math.sin(a0)]
        p3 = [center[0] + radius * math.cos(a1), center[1] + radius * math.sin(a1)]
        t0 = [-math.sin(a0) * sgn, math.cos(a0) * sgn]; t3 = [-math.sin(a1) * sgn, math.cos(a1) * sgn]
        c1 = [p0[0] + h * t0[0], p0[1] + h * t0[1]]; c2 = [p3[0] - h * t3[0], p3[1] - h * t3[1]]
        segs.append({"start": [round(v, 3) for v in p0], "end": [round(v, 3) for v in p3],
                     "controls": [[round(v, 3) for v in c1], [round(v, 3) for v in c2]]})
    return segs


def default_center(a):
    if a.get("center"): return a["center"]
    gp = rpc("camera").get("ground_point")
    if not gp: raise BridgeError("no center given and the camera is not looking at the ground")
    return [round(gp[0], 1), round(gp[2], 1)]


def t_circle(a):
    segs = circle_segments(default_center(a), a["radius"], max(4, a.get("pieces", 8)), a.get("clockwise", False))
    for s in segs: s["type"] = a["type"]
    return rpc("place_roads", {"segments": segs, "snap_tolerance": 1.0, "dry_run": a.get("dry_run", False),
                               "allow_errors": a.get("allow_errors", False), "hold": a.get("hold", False), "timeout_s": 90})


def t_zone(a):
    corners = tools2.pts_world(a["corners"], tools2.framed(a)) if a.get("corners") else None
    if not corners:
        corners = rect_corners(default_center(a), a["width"], a["depth"], a.get("angle_deg", 0.0))
    tools2.follow(rpc, a, corners)
    return rpc("zone", {"corners": corners, "zone_type": a["zone_type"], "overwrite": a.get("overwrite", True),
                        "dry_run": a.get("dry_run", False), "hold": a.get("hold", False), "timeout_s": 90})


def t_bulldoze(a):
    p = {k: a[k] for k in ("ids", "xmin", "zmin", "xmax", "zmax", "roads", "buildings", "dry_run", "hold") if k in a}
    if "ids" not in p and not any(k in p for k in ("xmin", "zmin", "xmax", "zmax")):
        raise BridgeError("give ids or an area (xmin/zmin/xmax/zmax)")
    p["timeout_s"] = 90
    if "xmin" in p: tools2.follow(rpc, a, [[p.get("xmin", 0), p.get("zmin", 0)], [p.get("xmax", 0), p.get("zmax", 0)]])
    return rpc("bulldoze", p)


def t_replace(a):
    p = {k: a[k] for k in ("ids", "xmin", "zmin", "xmax", "zmax", "type", "dry_run", "allow_errors", "hold") if k in a}
    p["timeout_s"] = 90
    if "xmin" in p: tools2.follow(rpc, a, [[p.get("xmin", 0), p.get("zmin", 0)], [p.get("xmax", 0), p.get("zmax", 0)]])
    return rpc("replace_roads", p)


def t_undo(a):
    """Undo the newest change, or `count` changes newest-first (each one checked against history before it is undone)."""
    n = max(1, int(a.get("count", 1)))
    done = []
    for k in range(n):
        p = {"timeout_s": 120}
        if k == 0 and "expect_id" in a: p["expect_id"] = a["expect_id"]
        elif k > 0:
            h = rpc("history")["entries_newest_first"]
            if not h: break
            p["expect_id"] = h[0]["id"]
        r = rpc("undo", p)
        done.append({"undo_of": r.get("undo_of"), "applied": r.get("applied", r.get("undone")), "roads": r.get("roads")})
        if not (r.get("applied") or r.get("undone")): break
    return done[0] if n == 1 else {"undone": len(done), "steps": done}


# ----------------------------------------------------------------------------- game districts (v0.6)
def t_list_districts(a): return rpc("list_districts", {"outlines": a.get("outlines", True)})
def t_district_policies(_): return rpc("list_district_policies")


def t_create_district(a):
    if a.get("polygon_file"): a = dict(a, polygon=tools2.load_list(a, "polygon"))
    tools2.follow(rpc, a, a.get("polygon") or [])
    return rpc("create_district", {k: a[k] for k in ("polygon", "name", "policies", "dry_run", "hold", "allow_errors") if k in a} | {"timeout_s": 90})


def t_delete_district(a): return rpc("delete_district", {k: a[k] for k in ("id", "name", "dry_run") if k in a} | {"timeout_s": 90})
def t_rename_district(a): return rpc("rename_district", {k: a[k] for k in ("id", "name", "new_name") if k in a})
def t_set_district_policy(a): return rpc("set_district_policy", {k: a[k] for k in ("id", "name", "policies", "active", "adjustment") if k in a})


# ----------------------------------------------------------------------------- district tools (v0.5)
# Claude designs each district itself: these draft ONE district's streets or rim road inside an outline Claude chose,
# with the angle / spacing / road types Claude picked. Default is a preview (map with the draft overlaid + plan_id);
# nothing is built until build=true or cs2_build_plan. cs2_remove_plan takes a built plan back out.
PLAN_DIR = os.path.join(survey.OUT_DIR, "plans")
_built_this_session = []


def _outline_area(a):
    poly = a.get("polygon")
    if poly:
        xs, zs = [p[0] for p in poly], [p[1] for p in poly]
        pad = 60.0
        return survey.Area(min(xs) - pad, min(zs) - pad, max(xs) + pad, max(zs) + pad), poly
    return survey.area_from(a, camera_ground), None


def _seed(a, area, poly):
    if a.get("seed"): return a["seed"]
    if poly: return [sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly)]
    return [(area.x0 + area.x1) / 2, (area.z0 + area.z1) / 2]


def _preview_or_build(plan, a, area, kind, extra):
    import pave
    os.makedirs(PLAN_DIR, exist_ok=True)
    plan_id = time.strftime(f"{kind}_%Y%m%d_%H%M%S")
    plan["area"] = [area.x0, area.z0, area.x1, area.z1]
    with open(os.path.join(PLAN_DIR, plan_id + ".json"), "w") as fh: json.dump(plan, fh)
    summary = dict({"plan_id": plan_id, "stats": plan["stats"]}, **extra)
    if not a.get("build"):
        import zoning
        w = survey.World(rpc, area)
        fc = zoning.forecast(w, plan["segments"], road_widths())
        summary["zoning_forecast"] = _forecast_brief(fc, zoning.forecast(w))
        marks = [{"pos": p, "label": ""} for p in (a.get("polygon") or [])] + _forecast_marks(fc)
        img = survey.render_map(w, px=int(a.get("px", 900)), layers=["terrain", "zones", "roads"],
                                plan=[pave.clean(s) for s in plan["segments"]], marks=marks)
        path = survey.save(img, plan_id + ".png")
        summary["next"] = "judge the overlay; adjust and redraft, or cs2_build_plan(plan_id)"
        return image_result(path, "image/png", json.dumps(summary))
    return dict(summary, build=_build_plan(plan_id, plan, a))


def _forecast_brief(fc, base=None):
    """The parts of a zoning forecast worth reading in a plan summary. With base (the same area without the draft) the
    verdict judges only what the draft ADDS - existing problems are listed separately."""
    d = fc.get("draft") or {}
    out = {"draft_full_depth_share": d.get("full_depth_share"), "draft_cells_lost": d.get("cells_lost_to_layout"),
           "full_depth_share_straight": fc.get("full_depth_share_straight"), "full_depth_share_curved": fc.get("full_depth_share_curved"),
           "dead_gap_m2": fc["dead_gaps"]["m2"], "skewed_junctions": fc["skewed_junctions"]["count"],
           "skewed_grid_junctions": fc["skewed_junctions"]["grid"], "marks": "map: H = hole inside a block, G = dead gap, S = skewed junction, Q = corner not square, D = shallow blocks"}
    if base is None:
        out["verdict"] = _verdict(fc); return out
    added_gap = fc["dead_gaps"]["m2"] - base["dead_gaps"]["m2"]
    added_skew = fc["skewed_junctions"]["grid"] - base["skewed_junctions"]["grid"]
    added_holes = fc.get("holes", {}).get("m2", 0) - base.get("holes", {}).get("m2", 0)
    bad = []
    if added_holes > 500: bad.append(f"the draft leaves {added_holes} m2 of holes inside blocks")
    ds = d.get("full_depth_share")
    if ds is not None and ds < 0.9: bad.append(f"the draft's roads only {ds:.0%} full depth")
    if added_gap > 500: bad.append(f"the draft adds {added_gap} m2 of dead gaps")
    if added_skew > 0: bad.append(f"the draft adds {added_skew} skewed grid junctions")
    out.update({"dead_gap_m2_added_by_draft": added_gap, "skewed_grid_junctions_added": added_skew,
                "existing_problems": _verdict(base), "holes_m2_added_by_draft": added_holes, "verdict": "good" if not bad else "; ".join(bad)})
    return out


def _verdict(fc):
    bad = []
    if fc.get("holes", {}).get("count"): bad.append(f"{fc['holes']['count']} hole(s) inside blocks ({fc['holes']['m2']} m2) - add a street / split the block")
    ns = [j for j in fc.get("not_square_junctions", {}).get("list", []) if j.get("grid")]
    if ns: bad.append(f"{len(ns)} grid corners not square (80-88 deg)")
    st = fc.get("full_depth_share_straight")
    if st is not None and st < 0.9: bad.append(f"straight roads only {st:.0%} full depth (target ~100%)")
    if fc["dead_gaps"]["m2"] > 500: bad.append(f"{fc['dead_gaps']['m2']} m2 of dead gaps between parallel roads")
    if fc["skewed_junctions"]["grid"]: bad.append(f"{fc['skewed_junctions']['grid']} skewed grid junctions")
    return "good" if not bad else "; ".join(bad)


def _forecast_marks(fc):
    m = [{"pos": h["pos"], "label": "G"} for h in fc["dead_gaps"]["hotspots"][:6]]
    m += [{"pos": j["pos"], "label": "S"} for j in fc["skewed_junctions"]["worst"][:6]]
    m += [{"pos": h["pos"], "label": "D"} for h in fc["shallow_hotspots"][:6] if h["shallow_columns"] >= 4]
    m += [{"pos": h["pos"], "label": "H"} for h in fc.get("holes", {}).get("list", [])[:8]]
    m += [{"pos": j["pos"], "label": "Q"} for j in fc.get("not_square_junctions", {}).get("list", [])[:8]]
    return m


def t_zoning_check(a):
    """Zoning forecast for an area's roads, optionally with a draft (plan_id or segments) laid over them."""
    import zoning
    draft = a.get("segments")
    if a.get("plan_id"):
        with open(os.path.join(PLAN_DIR, a["plan_id"] + ".json")) as fh: plan = json.load(fh)
        draft = plan["segments"]
        if not any(k in a for k in ("xmin", "center")): a = dict(a, xmin=plan["area"][0], zmin=plan["area"][1], xmax=plan["area"][2], zmax=plan["area"][3])
    area = survey.area_from(a, camera_ground)
    w = survey.World(rpc, area)
    fc = zoning.forecast(w, draft, road_widths())
    fc["verdict"] = _verdict(fc)
    if a.get("map", True):
        import pave
        img = survey.render_map(w, px=int(a.get("px", 900)), layers=["terrain", "zones", "roads", "issues"],
                                plan=[pave.clean(s) for s in draft] if draft else None, marks=_forecast_marks(fc))
        path = survey.save(img, time.strftime("zoning_%H%M%S.png"))
        return image_result(path, "image/png", json.dumps(fc))
    return fc


def t_district_grid(a):
    import pave
    area, poly = _outline_area(a)
    seed = _seed(a, area, poly)
    widths = road_widths()
    local = a.get("local_type", "Medium Road")
    pitch_u = a.get("pitch_u") or widths[local] + 2 * a.get("zoning_depth_cells", 6) * CELL
    pitch_v = a.get("pitch_v") or a.get("block_length_cells", 22) * CELL + widths[local]
    if "angle_deg" in a and ("origin" in a or "u_phase" in a or "v_phase" in a):
        lat = {"angle_deg": a["angle_deg"], "origin": a.get("origin", seed), "u_phase": a.get("u_phase", 0), "v_phase": a.get("v_phase", 0)}
    elif "angle_deg" in a:  # the caller's angle; line positions fitted to the parallel streets nearby (exact pitch to them)
        s = float(a.get("match_radius", 1000))
        try:
            lat = pave.detect_lattice(rpc, survey.Area(seed[0] - s, seed[1] - s, seed[0] + s, seed[1] + s), pitch_u, pitch_v,
                                      angle_deg=a["angle_deg"])
            lat["phase_fit"] = "fitted to parallel streets: " + ", ".join(ax for ax in ("u", "v") if ax + "_phase" in lat)                 if ("u_phase" in lat or "v_phase" in lat) else "no parallel streets nearby - phases 0 (set origin / u_phase / v_phase)"
            lat.setdefault("u_phase", 0.0); lat.setdefault("v_phase", 0.0)
        except ValueError:
            lat = {"angle_deg": a["angle_deg"], "origin": seed, "u_phase": 0, "v_phase": 0,
                   "phase_fit": "too few straight streets nearby - phases 0 from the seed"}
    else:   # line up with the neighbouring streets
        s = float(a.get("match_radius", 1000))
        lat = pave.detect_lattice(rpc, survey.Area(seed[0] - s, seed[1] - s, seed[0] + s, seed[1] + s), pitch_u, pitch_v)
    plan = pave.plan_fill(rpc, area, angle_deg=lat["angle_deg"], origin=tuple(lat["origin"]), u_phase=lat.get("u_phase", 0),
                          v_phase=lat.get("v_phase", 0), pitch_u=pitch_u, pitch_v=pitch_v, local_type=local,
                          arterial_type=a.get("arterial_type"), arterial_every=a.get("arterial_every", 4),
                          max_grade=a.get("max_grade", 0.15), seed=tuple(seed), polygon=poly, rim=False,
                          keep_islands=not a.get("connected_only", True))
    return _preview_or_build(plan, a, area, "grid", {"lattice": lat, "pitch_m": [pitch_u, pitch_v]})


def t_rim_road(a):
    import pave
    area, poly = _outline_area(a)
    seed = _seed(a, area, poly)
    plan = pave.plan_fill(rpc, area, shore_type=a.get("type", "Medium Road"), local_type=a.get("type", "Medium Road"),
                          shore_setback=a.get("setback", 60.0), max_grade=a.get("max_grade", 0.15), seed=tuple(seed),
                          polygon=poly, grid=False, rim=True, min_run_m=a.get("min_length", 150.0))
    return _preview_or_build(plan, a, area, "rim", {})


def t_connect_options(a):
    import pave
    area = survey.area_from(a, camera_ground)
    return pave.connect_options(rpc, area, max_m=a.get("max_m", 250.0), min_gap=a.get("min_gap_m", 35.0),
                                min_angle=a.get("min_angle_deg", 35.0), max_grade=a.get("max_grade", 0.18))


def _build_plan(plan_id, plan, a):
    import pave
    grid = [sg for sg in plan["segments"] if sg.get("phase") == "grid"]
    if grid:
        snapped, lat = _lattice(grid, a)
        if lat:
            it = iter(snapped)
            plan = dict(plan, segments=[next(it) if sg.get("phase") == "grid" else sg for sg in plan["segments"]])
    chunks = pave.chunks(plan, tile=a.get("chunk_m", 1000.0))
    built = refused = edges = 0; bad_all = []
    for phase, segs in chunks:
        if a.get("follow_camera", tools2.settings().get("follow_camera", True)):
            rpc("camera_goto", {"x": sum(s["start"][0] for s in segs) / len(segs), "z": sum(s["start"][1] for s in segs) / len(segs)})
        res, bad = pave.place_skip_failing(rpc, [pave.clean(s) for s in segs])
        built += len(segs) - len(bad); refused += len(bad); bad_all += bad
        edges += (res or {}).get("created_edges", len((res or {}).get("created_ids", [])))
    _built_this_session.append(plan_id)
    out = {"chunks": len(chunks), "segments_built": built, "refused": refused, "edges_created": edges}
    if grid and lat: out["lattice"] = lat
    if bad_all: out["refused_segments"] = bad_all[:30]
    try:                                  # v0.27: unzoned frontage reads as empty land - say how much the plan left
        ar = plan.get("area")
        if ar:
            blocks = rpc("zone_blocks", {"xmin": ar[0], "zmin": ar[1], "xmax": ar[2], "zmax": ar[3], "limit": 20000}).get("blocks", [])
            out["unzoned_cells"] = sum(str(b.get("cells", "")).count(".") for b in blocks)
            if out["unzoned_cells"]:
                out["next"] = ("zone the new frontage (cs2_zone_along / cs2_zone with your zoning palette) when you're ready - "
                               "unzoned cells stay empty grass")
    except Exception: pass
    return out


def _load_plan(plan_id):
    path = os.path.join(PLAN_DIR, plan_id + ".json")
    if not os.path.exists(path): raise BridgeError(f"no saved plan '{plan_id}' in {PLAN_DIR}")
    with open(path) as fh: return json.load(fh)


def t_build_plan(a): return dict(plan_id=a["plan_id"], **_build_plan(a["plan_id"], _load_plan(a["plan_id"]), a))


def t_remove_plan(a):
    """Remove one built plan, or every plan built in this session (plan_id 'session'), newest first."""
    import pave
    ids = list(reversed(_built_this_session)) if a["plan_id"] == "session" else [a["plan_id"]]
    out = {}
    for pid in ids:
        plan = _load_plan(pid)
        out[pid] = pave.remove_built(rpc, plan, survey.Area(*plan["area"]))
        if pid in _built_this_session: _built_this_session.remove(pid)
    return out


def t_screenshot(a):
    """Optionally move the camera, let it settle, capture, and return the JPG as MCP image content.
    clean=true (mod 0.30): no UI, notification icons, overlays or road names (Photo Mode's capture path); save_png keeps
    the full-resolution PNG; supersize renders 1-4x."""
    if "x" in a and "z" in a:
        rpc("camera_goto", {k: a[k] for k in ("x", "z", "zoom", "yaw", "pitch") if k in a})
        time.sleep(a.get("settle_s", 2.0))
    q = {"max_width": a.get("max_width", 1280), "timeout_s": 90}
    for k in ("clean", "frames", "save_png", "supersize"):
        if k in a: q[k] = a[k]
    shot = rpc("screenshot", q)
    with open(shot["path"], "rb") as fh:
        data = base64.b64encode(fh.read()).decode()
    info = {"path": shot["path"], "size": [shot["width"], shot["height"]]}
    for k in ("clean", "png", "png_size"):
        if k in shot: info[k] = shot[k]
    return {"__mcp_content__": [{"type": "image", "data": data, "mimeType": "image/jpeg"}, {"type": "text", "text": json.dumps(info)}]}


def t_photo_mode(a):
    """The game's Photo Mode properties (mod 0.30): list (optionally one group), set {id: value} (switches the photo-mode
    render volume on), reset=true (all defaults, volume off)."""
    return rpc("photo_mode", {k: a[k] for k in ("set", "reset", "group", "list") if k in a})
def camera_ground():
    gp = rpc("camera").get("ground_point")
    if not gp: raise BridgeError("give an area (xmin/zmin/xmax/zmax or center+size): the camera is not looking at the ground")
    return [round(gp[0], 1), round(gp[2], 1)]


def image_result(path, mime, text):
    with open(path, "rb") as fh: data = base64.b64encode(fh.read()).decode()
    return {"__mcp_content__": [{"type": "image", "data": data, "mimeType": mime}, {"type": "text", "text": text}]}


MAP_KEY = ("roads: white local, yellow medium, orange large, red highway (or green->red by traffic); dark casing = elevated. "
           "cells: pale = zonable but unzoned, colours = zones (darker = built), grey = built unzoned. magenta wash = unused land "
           "within zoning reach. rings: red dead end, pink near-miss connection, orange sharp junction, yellow short link, "
           "black overlapping unconnected nodes. dashed cyan = plan overlay. purple = districts (dashed = draft outlines). contours + brown tint = steep (>25%).")
_map_n = [0]


def t_map(a):
    area = survey.area_from(a, camera_ground)
    w = survey.World(rpc, area)
    facts = survey.check(w) if "issues" in (a.get("layers") or ["issues"]) else None
    dists = None
    if "districts" in (a.get("layers") or ["districts"]):
        try: dists = rpc("list_districts", {"outlines": True}).get("districts")
        except BridgeError: dists = None          # mod older than 0.6
    plan = tools2.load_list(a, "plan")
    img = survey.render_map(w, px=int(a.get("px", 1100)), colour_by=a.get("colour_by", "type"), layers=a.get("layers"),
                            plan=plan, marks=a.get("marks"), facts=facts, districts=dists, outlines=a.get("outlines"))
    _map_n[0] += 1
    path = survey.save(img, f"map_{_map_n[0] % 20}.png")
    summary = {"path": path, "area": [area.x0, area.z0, area.x1, area.z1], "metres_per_pixel": round(area.size / img.size[0], 2),
               "road_edges": len(w.edges), "zone_blocks": len(w.blocks), "zone_legend": w.zones.get("legend", {}), "key": MAP_KEY}
    if facts:
        rd, ul = facts["roads"], facts["unused_land"]
        summary["counts"] = {"dead_ends": rd["dead_ends"]["count"], "near_misses": rd["near_miss_connections"]["count"],
                             "sharp_junctions": rd["sharp_junctions"]["count"], "short_links": rd["short_links_between_junctions"]["count"],
                             "islands": rd["islands"]["count"], "congested": rd["congested"]["count_below_0_75"],
                             "zoning_efficiency": ul["zoning_efficiency"], "unused_m2": ul["flat_unused_m2_within_zoning_reach"]}
    return image_result(path, "image/png", json.dumps(summary))


def t_inspect(a):
    area = survey.area_from(a, camera_ground)
    return tools2.inspect_trimmed(survey.check(survey.World(rpc, area), short_link_m=a.get("short_link_m", 40.0), sharp_deg=a.get("sharp_deg", 40.0),
                        kink_deg=a.get("kink_deg", 8.0), near_miss_m=a.get("near_miss_m", 12.0)), a)


def t_network(a): return tools2.network_filtered(rpc, a)
def t_traffic(a): return tools2.traffic(rpc, dict(a, **survey.area_from(a, camera_ground).params))
def t_zone_summary(a): return tools2.zone_summary(rpc, a if a.get("polygon") else dict(a, **survey.area_from(a, camera_ground).params))
def t_probe(a): return tools2.probe(rpc, a)
def t_frame(a): return tools2.t_frame(a)


def t_ramp(a):
    fr = tools2.framed(a)
    segs, info, aux = tools3.build_ramp(rpc, a, fr)
    if a.get("plan_only"): info["segments_list"] = segs; return info
    if aux and not a.get("dry_run"):
        rr = rpc("replace_roads", {"ids": aux["ids"], "type": a["aux_lane"]["type"], "timeout_s": 90})
        info["aux_lane"]["applied"] = rr.get("applied"); info["aux_lane"]["undo_id"] = rr.get("undo_id")
        if rr.get("applied"):             # the split point now lies on the upgraded edge: re-pick by position (ids changed)
            a2 = dict(a); a2.pop("edge", None); a2.pop("aux_lane", None); a2["at"] = info["carriageway"]["split_at"]; a2.pop("distance_m", None)
            segs, info2, _ = tools3.build_ramp(rpc, a2, None)
            info["carriageway"] = info2["carriageway"]
    return tools2.place_built(rpc, a, segs, info, compact)


def t_extend_lines(a):
    segs, info = tools3.extend_lines(rpc, a, tools2.framed(a))
    return tools2.place_built(rpc, a, segs, info, compact)


def t_zone_along(a): return tools3.zone_along(rpc, a, tools2.framed(a))
def t_obstacles(a): return tools3.obstacles(rpc, dict(a, **survey.area_from(a, camera_ground).params))
def t_pillars(a): return tools3.pillars(rpc, dict(a, **survey.area_from(a, camera_ground).params))
def t_trim(a): return tools3.trim(rpc, a)
def t_refs(a): return tools3.t_refs(rpc, a)
def t_tour(a): return tools3.t_tour(rpc, a)


def t_shore(a):
    if a.get("from") is not None: return tools3.rays(rpc, a)
    return tools3.outline(rpc, dict(a, **survey.area_from(a, camera_ground).params))


def _settings(a):
    cur = tools2.settings()
    if a:
        cur.update({k: v for k, v in a.items() if k in ("follow_camera",)})
        with open(tools2.SETTINGS, "w", encoding="utf-8") as fh: json.dump(cur, fh, indent=1)
    return cur


def _builder(fn):
    def run(a):
        segs, info = fn(a, tools2.framed(a))
        res = tools2.place_built(rpc, a, segs, info, compact)
        if (a.get("dry_run") or a.get("plan_only")) and segs and isinstance(res, dict) and a.get("zoning_check", True):
            try: res["zoning_forecast"] = _forecast_for(segs)       # v0.27: every road builder's preview says how it will zone
            except Exception as ex: res["zoning_forecast"] = {"error": str(ex)}
        return res
    return run


def _forecast_for(segs, pad=150.0):
    import zoning
    xs = [c for s in segs for c in (s["start"][0], s["end"][0])]; zs = [c for s in segs for c in (s["start"][1], s["end"][1])]
    area = survey.Area(min(xs) - pad, min(zs) - pad, max(xs) + pad, max(zs) + pad)
    w = survey.World(rpc, area)
    return _forecast_brief(zoning.forecast(w, segs, road_widths()), zoning.forecast(w))

def t_blocks(a): return rpc("zone_blocks", {k: a[k] for k in ("xmin", "zmin", "xmax", "zmax", "limit") if k in a})
def t_tgrid(a): return rpc("terrain_grid", {k: a[k] for k in ("xmin", "zmin", "xmax", "zmax", "step") if k in a})
def t_commit(a): return rpc("preview_commit", {"allow_errors": a.get("allow_errors", False), "timeout_s": 90})
def t_cancel(_): return rpc("preview_cancel", {"timeout_s": 30})
def t_tool_reset(a): return rpc("tool_reset", {"reason": a.get("reason", "cs2_tool_reset"), "timeout_s": 15})
def t_history(_): return rpc("history")
def t_speed(a): return rpc("set_speed", {"speed": a["speed"]})
def t_goto(a):
    if "distance" in a and "zoom" not in a: a = dict(a, zoom=a["distance"])   # common alias
    return rpc("camera_goto", {k: a[k] for k in ("x", "z", "zoom", "yaw", "pitch") if k in a})
def t_buildings(a): return rpc("query_buildings", {k: a[k] for k in ("xmin", "zmin", "xmax", "zmax", "limit") if k in a})


def _path_test(a):
    """mod 0.41 path_test + path_result: enqueue the dispatch pathfind, poll until the pathfinder answered."""
    import time as _t
    tok = rpc("path_test", {"id": a["id"], "mode": a.get("mode", "hearse")})
    end = _t.time() + float(a.get("wait_s", 8))
    while True:
        r = rpc("path_result", {"token": tok["token"], "release": False})
        if r.get("done") or _t.time() > end:
            if r.get("done"): rpc("path_result", {"token": tok["token"], "release": True})
            return {**tok, **r}
        _t.sleep(0.5)
def t_zone_types(_): return rpc("list_zone_types")


XZ = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}
FRAME = {"type": "object", "description": "optional local frame for all input points: {origin:[x,z], heading_deg}; a point [a,b] = "
         "origin + a along heading (0 deg = +x, 90 = +z) + b to the left of it. Outputs stay in world x/z",
         "properties": {"origin": XZ, "heading_deg": {"type": "number"}}, "required": ["origin"]}
CAM = {"type": "boolean", "description": "move the camera to the change first so the player sees it (default true; never on dry_run)"}
FILE = {"type": "string", "description": "path of a JSON file on disk holding this list (saves sending it inline)"}
ELEV = {"description": "metres above ground: a number, or a profile [[distance_m, elev_m], ...] along the path (negative "
        "distance counts back from the end), linear between points"}
BUILD_OPTS = {"piece_m": {"type": "number", "description": "target length of each road piece (joints = nodes; elevated joints carry pillars). Default 110"},
              "avoid": {"type": "array", "items": XZ, "description": "points to keep joints away from (e.g. where a street or rail passes under)"},
              "avoid_m": {"type": "number", "description": "clearance from avoid points, default 25"},
              "joints": {"type": "array", "items": XZ, "description": "force joints near these points (e.g. where a ramp will branch off)"},
              "step": {"type": "number"}, "smooth": {"type": "boolean", "description": "Catmull-Rom smoothing through the knots (default true); false = straight pieces with joints at the knots"},
              "plan_only": {"type": "boolean", "description": "return the segments without touching the game"},
              "zoning_check": {"type": "boolean", "description": "dry_run / plan_only also return a zoning_forecast (default true)"},
              "save_to": {"type": "string", "description": "also write the segments to this JSON file (reuse with segments_file / plan_file)"},
              "frame": FRAME, "follow_camera": CAM, "camera_zoom": {"type": "number"},
              "dry_run": {"type": "boolean"}, "hold": {"type": "boolean"}, "allow_errors": {"type": "boolean"}, "skip_failing": {"type": "boolean"},
              "snap_tolerance": {"type": "number"}, "explain_errors": {"type": "boolean"}, "verbose": {"type": "boolean"},
              "join_check": {"type": "boolean", "description": "check every end that lands on an existing node: highway <-> local road at grade, one-way head-on / head-to-head (default true; reported on dry runs, a build with conflicts is refused)"},
              "allow_conflicts": {"type": "boolean", "description": "build even when join_check finds conflicts (only if the join is intended)"},
              "check": {"type": "boolean", "description": "after building, report junction problems around the change (default true)"},
              "before_after": {"type": "boolean", "description": "return a screenshot before and after the build"},
              "deck_height": {"type": "number", "description": "absolute height (world y) to keep every new node at - level piers, causeways, viaducts; overrides elevation (ends that snap onto existing roads keep theirs)"},
              "min_elevation": {"type": "number", "description": "with deck_height: never lower than this above ground"}}
AREA = {"xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"}, "zmax": {"type": "number"},
        "center": XZ, "size": {"type": "number"}, "size_z": {"type": "number"}}
TOOLS = [
    ("cs2_status", "Check the CS2MCP game bridge: mod version, game mode, whether a city is loaded, money, population, road count.",
     {"type": "object", "properties": {}}, t_status),
    ("cs2_camera", "Camera position/forward and the ground point at the centre of the screen (x,y,z metres). Use ground_point as a default location.",
     {"type": "object", "properties": {}}, t_camera),
    ("cs2_terrain_height", "Sample terrain height at [x,z] points.",
     {"type": "object", "properties": {"points": {"type": "array", "items": XZ}}, "required": ["points"]}, t_terrain),
    ("cs2_list_road_types", "List placeable net prefabs - roads (incl. quays like QuayLarge01, pedestrian streets), train/tram/subway "
     "tracks, and with mod 0.10 also pathways, fences and utilities - including modded ones - with width, max slope, kind and for "
     "tracks track_type + speed_limit. Any listed name works as a type in cs2_place_roads / place_path / place_pair / place_lines / "
     "place_piers. Optional substring filter; kind = all | road | track | pathway | fence | utility.",
     {"type": "object", "properties": {"filter": {"type": "string"}, "kind": {"type": "string", "enum": ["all", "road", "track", "pathway", "fence", "utility"]}}}, t_road_types),
    ("cs2_list_building_types", "Placeable (non-growable) buildings: service buildings, stations, harbours, depots, landmarks... with lot "
     "size (cells and metres), service, transport_station flag and cost. filter = name substring; growables=true to include zoned buildings.",
     {"type": "object", "properties": {"filter": {"type": "string"}, "limit": {"type": "integer"}, "growables": {"type": "boolean"}}}, t_building_types),
    ("cs2_place_building", "Place a building precisely: type (from cs2_list_building_types), lot centre x,z (or at = [x,z] / '@name', "
     "frame accepted), and facing = [dx,dz] direction the front/access side points to (or angle_deg: 0 = +z, 90 = +x). Previews "
     "first and refuses on errors (overlap, no road access, water...) unless allow_errors; dry_run / hold as usual. Undoable. "
     "Then cs2_building_nets shows its platform tracks / internal roads and their free ends to connect to. Mod 0.27: parent = "
     "an existing building id installs `type` as its service upgrade (e.g. 'Crematorium01 Hearse Garage' on a Crematorium01, "
     "hospital wings): the game's own upgrade (parent re-defined in place, existing upgrades kept); wings need no x/z; an "
     "extension with its own lot is put flush beside / behind the parent automatically when x/z are left out.",
     {"type": "object", "properties": {"type": {"type": "string"}, "x": {"type": "number"}, "z": {"type": "number"}, "at": {},
                                       "facing": {"type": "array", "items": {"type": "number"}}, "angle_deg": {"type": "number"},
                                       "frame": FRAME, "dry_run": {"type": "boolean"}, "hold": {"type": "boolean"},
                                       "allow_errors": {"type": "boolean"}, "follow_camera": CAM, "explain_errors": {"type": "boolean"},
                                       "allow_subnets": {"type": "boolean", "description": "UNSAFE: allow buildings with built-in nets (stations, harbours) - crashed the game once; save first"},
                                       "parent": {"type": "string", "description": "mod 0.27: building id to install this upgrade on"}},
      "required": ["type"]}, t_place_building),
    ("cs2_areas", "Areas in a box - surfaces (painted ground materials), building lots, spaces - with id, kind, prefab and outline; "
     "counts per kind+prefab. kinds = [surface, lot, space, district, map_tile, other] (default all but district/map_tile).",
     {"type": "object", "properties": {"xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"}, "zmax": {"type": "number"},
                                       "kinds": {"type": "array", "items": {"type": "string"}}, "limit": {"type": "integer"}}}, t_areas),
    ("cs2_prefab_components", "Diagnose a prefab by name: its C# class, ECS components and authoring components. Placeholders left by "
     "removed mods lack mesh/render components compared with a working prefab of the same kind.",
     {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}, t_prefab_components),
    ("cs2_delete_areas", "Delete areas (e.g. broken surfaces from a removed mod) by id from cs2_areas.",
     {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "string"}}, "dry_run": {"type": "boolean"}}, "required": ["ids"]}, t_delete_areas),
    ("cs2_building_nets", "The nets a building owns (station platform tracks, harbour/yard tracks, internal roads) with end positions and "
     "the free ends (nodes with a single connection) - connect new track/roads to those exact points.",
     {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}, t_building_nets),
    ("cs2_query_roads", "List existing road segments whose ends fall in an x/z box.",
     {"type": "object", "properties": {"xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"},
                                       "zmax": {"type": "number"}, "limit": {"type": "integer"}}}, t_query_roads),
    ("cs2_place_roads", "Place road segments [{start:[x,z], end:[x,z], type?, control?:[x,z] (quadratic curve), controls?:[[x,z],[x,z]] "
     "(cubic curve)}] inline or from segments_file. Endpoints within snap_tolerance of existing nodes/roads connect to them; "
     "an end landing on another new segment of the batch makes a T junction; utilities (pipes, power lines) are ignored. The game previews first; if the "
     "preview has errors nothing is built unless allow_errors. dry_run previews only. Undoable (cs2_undo).",
     {"type": "object", "properties": {
         "segments": {"type": "array", "items": {"type": "object", "properties": {
             "start": XZ, "end": XZ, "type": {"type": "string"}, "control": XZ,
             "elevation": {"description": "metres above ground for bridges/overpasses: number or [start, end]; crossings more than 4 m apart vertically pass over instead of joining"},
             "controls": {"type": "array", "items": XZ, "minItems": 2, "maxItems": 2}},
             "required": ["start", "end"]}},
         "segments_file": FILE, "frame": FRAME, "follow_camera": CAM, "camera_zoom": {"type": "number"},
         "node_snap_m": {"type": "number", "description": "first move ends lying within this many metres of an existing road node onto it (fixes stale coordinates after the game moved a node); reported"},
         "explain_errors": {"type": "boolean", "description": "on errors, report the nearest existing road/building at each error location (default true)"},
         "check": {"type": "boolean", "description": "after building, report junction problems around the change (default true)"},
         "before_after": {"type": "boolean", "description": "return a screenshot before and after the build"},
         "type": {"type": "string", "description": "default road type for segments without one"},
         "snap_tolerance": {"type": "number"}, "dry_run": {"type": "boolean"}, "allow_errors": {"type": "boolean"},
         "hold": {"type": "boolean", "description": "leave the preview on screen; then cs2_preview_commit / cs2_preview_cancel"},
         "no_intersections": {"type": "boolean", "description": "do not join roads this crosses (default: crossings become junctions)"},
         "auto_fix": {"type": "boolean", "description": "dry_run / hold with errors: drop the segments that sit on an error position and preview again until clean (default true); the result lists what was dropped and any group left unconnected. A held preview that cannot be cleaned is cancelled"},
         "auto_fix_rounds": {"type": "integer", "description": "max fix-and-preview rounds (default 4)"},
         "auto_fix_radius": {"type": "number", "description": "a segment closer than this many metres to an error position is dropped (default 12)"},
         "skip_failing": {"type": "boolean", "description": "if the game refuses the batch, find the refused segments by bisecting with dry runs, build the rest and return the refused ones"},
         "verbose": {"type": "boolean", "description": "include created_ids (default: just their count)"}}}, t_place_roads),
    ("cs2_district_grid", "Draft ONE district's street grid inside an outline you chose (polygon [[x,z],...], or an area box). You "
     "pick the character: local_type / arterial_type (+ arterial_every), angle_deg (default: lined up with the neighbouring "
     "streets), block_length_cells (default 22) and zoning_depth_cells (default 6; spacing = road width + 2 x depth x 8 m, so "
     "blocks get full zoning). Streets are clipped to the land under seed (default: outline centre): they avoid water, "
     "cliffs, highways, existing zoning and buildings, and climb at most max_grade (default 0.15). They join existing streets "
     "where they line up; pieces that end up unconnected are dropped unless connected_only=false. Default: a PREVIEW map with "
     "the draft overlaid and a plan_id, nothing built. build=true, or cs2_build_plan(plan_id), builds it and skips segments "
     "the game refuses. Zoning rules (v0.27): streets meet others at >= 80 deg - a line that would hit a road at a skew "
     "bends so its last ~50 m arrive square (dropped if that does not fit; down to 60 deg allowed onto curved roads); "
     "with a given angle_deg the line positions are fitted to parallel streets nearby so the spacing to them is an exact "
     "pitch (no overlap, no dead gap). The preview carries a zoning_forecast (draft full-depth share, dead gaps, skewed "
     "junctions, verdict) and marks G / S / D on the map - redraft until the verdict is good.",
     {"type": "object", "properties": {
         "polygon": {"type": "array", "items": XZ}, "xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"},
         "zmax": {"type": "number"}, "center": XZ, "size": {"type": "number"}, "seed": XZ,
         "local_type": {"type": "string"}, "arterial_type": {"type": "string"}, "arterial_every": {"type": "integer"},
         "angle_deg": {"type": "number"}, "origin": XZ, "u_phase": {"type": "number"}, "v_phase": {"type": "number"},
         "match_radius": {"type": "number"}, "block_length_cells": {"type": "integer"}, "zoning_depth_cells": {"type": "integer"},
         "pitch_u": {"type": "number"}, "pitch_v": {"type": "number"}, "max_grade": {"type": "number"},
         "connected_only": {"type": "boolean"}, "build": {"type": "boolean"}, "px": {"type": "integer"}}}, t_district_grid),
    ("cs2_zoning_check", "Zoning forecast (v0.27) for an area's roads, optionally with a draft laid over them (plan_id from "
     "cs2_district_grid / cs2_rim_road, or segments in place_roads shape) - BEFORE building. The game zones 6 cells (48 m) "
     "deep on both sides of a road in 8 m columns: reports each column's usable depth, full_depth_share for straight "
     "(grid) and curved roads, dead gaps (land between two parallel roads' zoning edges that nothing will ever zone), "
     "skewed junctions (< 80 deg) and a verdict; the map marks G = dead gap, S = skewed junction, D = shallow blocks. "
     "Targets: straight roads ~100% full depth, no dead gaps, no skewed grid junctions; curved / waterfront roads may lose "
     "some. Layout rules that get there: parallel streets exactly road width + 96 m apart (centre to centre, equal types; "
     "w1/2 + w2/2 + 96 otherwise) - closer = shallow blocks, a bit farther = a dead gap; streets meet at 90 deg (bend the "
     "last ~50 m if the line comes in skewed); keep a grid a full double depth back from a curved road.",
     {"type": "object", "properties": {"xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"},
                                       "zmax": {"type": "number"}, "center": XZ, "size": {"type": "number"},
                                       "plan_id": {"type": "string"}, "segments": {"type": "array", "items": {"type": "object"}},
                                       "map": {"type": "boolean"}, "px": {"type": "integer"}}}, t_zoning_check),
    ("cs2_zone_cracks", "Zero-crack check (v0.36): inside street blocks every zone cell must sit on its neighbours' 8 m lattice. "
     "Measures misaligned neighbour cells from the live zone blocks (0 = none) and plans the per-node Road half-align flags "
     "that line each straight road's cells up with its crossing roads (the game's road tool sets them, MCP-built roads "
     "lacked them: 4 m cracks at every Medium-avenue/side-street seam). residual = edges flags cannot fit, with the length "
     "change that would. Read-only.",
     {"type": "object", "properties": {"xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"},
                                       "zmax": {"type": "number"}}, "required": ["xmin", "zmin", "xmax", "zmax"]},
     lambda a: dict(crack_area=__import__("align").crack_area(rpc, a), lattice=__import__("align").lattice_check(rpc, a),
                    flags={k: v for k, v in __import__("align").plan(rpc, a).items() if k not in ("changes", "residual")})),
    ("cs2_road_align", "Fix zone cracks on existing roads (v0.36, needs mod 0.36): sets the planned half-align flags "
     "(cs2_zone_cracks) through the mod's road_align, the game rebuilds those roads' zone blocks, then every new empty "
     "cell is re-zoned with the zone of the nearest old cell. Cells that move lose buildings standing across the old "
     "grid - save first. dry_run = plan only. Undoable (flags) via cs2_undo.",
     {"type": "object", "properties": {"xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"},
                                       "zmax": {"type": "number"}, "dry_run": {"type": "boolean"}, "rezone": {"type": "boolean"},
                                       "settle_s": {"type": "number"}}, "required": ["xmin", "zmin", "xmax", "zmax"]},
     lambda a: __import__("align").apply(rpc, a)),
    ("cs2_site_movements", "Turning demand at a junction / interchange site (v0.27, needs mod 0.27): cars on or approaching the "
     "circle site [x,z] + radius are sampled `samples` times `interval_s` apart, deduped, and each one's entry approach and "
     "the approach its planned path leaves by are counted -> an entry -> exit matrix with shares, cars per approach, and the "
     "approaches (bearing, road type, lanes in / out). The input for designing an interchange for THIS site: heavy movements "
     "get direct / free-flow ramps, light ones loops or nothing. Sample at busy times; more samples = steadier numbers.",
     {"type": "object", "properties": {"site": XZ, "radius": {"type": "number"}, "ring": {"type": "number"},
                                       "samples": {"type": "integer"}, "interval_s": {"type": "number"}, "top": {"type": "integer"}},
      "required": ["site"]}, lambda a: tools10.site_movements(rpc, a)),
    ("cs2_network_check", "Facts about the roads in an area or around a site, optionally with a draft laid over them (segments "
     "in place_roads shape with elevation above ground, or plan_id) - BEFORE building (v0.27). Reports: with site [x,z] + "
     "radius every movement between the roads crossing the circle - reachable or MISSING, path length and detour ratio "
     "(max_detour, default 2.5); dead ends, one-way traps and sources; per road the tightest radius vs its design speed "
     "(design_speed_kmh by kind highway / ramp / arterial / local, default 100 / 60 / 60 / 40; R = v^2 / (127 x 0.25)) and "
     "the steepest grade (max_grade by kind, default 6 / 8 / 8 / 10 %); merge / diverge angles (> 25 deg flagged); weaving "
     "length from an on-ramp merge to the next off-ramp split (weave_min_m, default 400); lane balance - an auxiliary lane "
     "before every off-ramp and after every on-ramp (standing rule); vertical clearance where roads cross without a junction "
     "(min_clearance_m, default 6) and crossings that would make an unintended junction. map=true draws the issues. Lane "
     "directions come from mod 0.27; older mods infer them from type names (flagged).",
     {"type": "object", "properties": {"site": XZ, "radius": {"type": "number"}, "xmin": {"type": "number"}, "zmin": {"type": "number"},
                                       "xmax": {"type": "number"}, "zmax": {"type": "number"}, "center": XZ, "size": {"type": "number"},
                                       "segments": {"type": "array", "items": {"type": "object"}}, "plan_id": {"type": "string"},
                                       "design_speed_kmh": {"type": "object"}, "max_grade": {"type": "object"},
                                       "min_clearance_m": {"type": "number"}, "weave_min_m": {"type": "number"},
                                       "max_detour": {"type": "number"}, "all_movements": {"type": "boolean"},
                                       "approach_kinds": {"type": "array", "items": {"type": "string"}, "description": "which roads count as the site's approaches: highway / ramp / arterial / local (default: highway, ramp, arterial when a highway crosses the circle, else all)"},
                                       "map": {"type": "boolean"}, "px": {"type": "integer"}}},
     lambda a: tools10.network_check(rpc, a, camera_ground)),
    ("cs2_design_path", "Exact geometry for a road YOU designed (v0.27) - ramps, flyovers, loops, connectors. start / end = "
     "{pos:[x,z], heading_deg (0 = +x, 90 = +z), elevation? (m above ground)} or {edge: road id, at:[x,z] (a point on it), "
     "reverse?, branch_deg? + side left/right (leave / join the road at that angle)}. Returns the shortest path whose curves "
     "are never tighter than the design speed allows (Dubins: true circular arcs + straights; radius_m overrides; words = "
     "['LSL','RSR','LSR','RSL','RLR','LRL'] to force a shape, e.g. a 270-deg loop), cut into cubic pieces (piece_m), with "
     "an elevation profile within max_grade that clears every road it crosses by clearance_m (pass 'over' or 'under'), "
     "infeasible crossings listed, and a cs2_network_check of the new pieces. Nothing is built: hand `segments` to "
     "cs2_place_roads (dry_run first). You choose the topology; this does the arithmetic.",
     {"type": "object", "properties": {"start": {"type": "object"}, "end": {"type": "object"}, "type": {"type": "string"},
                                       "design_speed_kmh": {"type": "number"}, "radius_m": {"type": "number"},
                                       "max_grade": {"type": "number"}, "clearance_m": {"type": "number"},
                                       "pass": {"type": "string", "enum": ["over", "under"]}, "piece_m": {"type": "number"},
                                       "words": {"type": "array", "items": {"type": "string"}}},
      "required": ["start", "end"]}, lambda a: tools11.design_path(rpc, a)),
    ("cs2_rim_road", "Draft a smooth road that follows a shoreline, cliff edge or highway, kept `setback` m (default 60 = road "
     "+ one 48 m zoning strip) from it and split wherever it would get too steep. Limit it with polygon or an area box "
     "(the land mass under seed). Default: a preview map with the draft overlaid and a plan_id. build=true, or "
     "cs2_build_plan, builds it. Join it to streets afterwards (cs2_connect_options / cs2_place_roads) - square on (90 deg). "
     "A grid drafted next to it later stays a full double zoning depth (rim half + 96 m + street half) back, so blocks "
     "never overlap. Preview includes a zoning_forecast.",
     {"type": "object", "properties": {
         "polygon": {"type": "array", "items": XZ}, "xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"},
         "zmax": {"type": "number"}, "center": XZ, "size": {"type": "number"}, "seed": XZ, "type": {"type": "string"},
         "setback": {"type": "number"}, "max_grade": {"type": "number"}, "min_length": {"type": "number"},
         "build": {"type": "boolean"}, "px": {"type": "integer"}}}, t_rim_road),
    ("cs2_connect_options", "For every dead end in an area, the ways to join it up: carry straight on to the first road, or "
     "go to the nearest junction. Each option comes with its segment, its length, ok, and the problem if not ok (water, a "
     "building lot, too steep, lands too close to another junction, shallow angle, target is a highway). Facts only; "
     "build the ones you choose with cs2_place_roads.",
     {"type": "object", "properties": {"xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"},
                                       "zmax": {"type": "number"}, "center": XZ, "size": {"type": "number"},
                                       "max_m": {"type": "number"}, "min_gap_m": {"type": "number"},
                                       "min_angle_deg": {"type": "number"}, "max_grade": {"type": "number"}}}, t_connect_options),
    ("cs2_list_districts", "The city's districts: id, name, centre, area (km2), active policies and outline points "
     "(outlines=false for a short list). Draw them with cs2_map (layer 'districts').",
     {"type": "object", "properties": {"outlines": {"type": "boolean"}}}, t_list_districts),
    ("cs2_create_district", "Create a game district from an outline YOU decide: polygon [[x,z], ...] (3+ points in order around it; "
     "no need to repeat the first). Optional name and policies (names from cs2_list_district_policies). Previews first and "
     "refuses if the game reports errors (e.g. overlapping another district) unless allow_errors; dry_run / hold as usual. "
     "Tip: draft it on cs2_map with outlines=[{points, label}] first. Undoable.",
     {"type": "object", "properties": {"polygon": {"type": "array", "items": XZ}, "name": {"type": "string"},
                                       "policies": {"type": "array", "items": {"type": "string"}}, "dry_run": {"type": "boolean"},
                                       "hold": {"type": "boolean"}, "allow_errors": {"type": "boolean"}, "polygon_file": FILE, "follow_camera": CAM}}, t_create_district),
    ("cs2_delete_district", "Delete a district (by id or name). Buildings and roads are untouched. Undo re-creates its outline and name.",
     {"type": "object", "properties": {"id": {"type": "string"}, "name": {"type": "string"}, "dry_run": {"type": "boolean"}}}, t_delete_district),
    ("cs2_rename_district", "Rename a district (by id or current name) to new_name.",
     {"type": "object", "properties": {"id": {"type": "string"}, "name": {"type": "string"}, "new_name": {"type": "string"}},
      "required": ["new_name"]}, t_rename_district),
    ("cs2_list_district_policies", "Names of the policies that can be set on a district.", {"type": "object", "properties": {}}, t_district_policies),
    ("cs2_set_district_policy", "Turn district policies on (active=true, default) or off, by district id or name. adjustment for slider policies.",
     {"type": "object", "properties": {"id": {"type": "string"}, "name": {"type": "string"}, "policies": {"type": "array", "items": {"type": "string"}},
                                       "active": {"type": "boolean"}, "adjustment": {"type": "number"}}, "required": ["policies"]}, t_set_district_policy),
    ("cs2_build_plan", "Build a drafted plan (plan_id from cs2_district_grid / cs2_rim_road) in chunks, with the camera following. "
     "Segments the game refuses are skipped and returned.",
     {"type": "object", "properties": {"plan_id": {"type": "string"}, "chunk_m": {"type": "number"}, "follow_camera": {"type": "boolean"}},
      "required": ["plan_id"]}, t_build_plan),
    ("cs2_remove_plan", "Remove what a built plan put down. It finds the roads by shape, so pieces the game split or re-created "
     "are included. plan_id 'session' removes every plan built in this session, newest first.",
     {"type": "object", "properties": {"plan_id": {"type": "string"}}, "required": ["plan_id"]}, t_remove_plan),
    ("cs2_place_grid", "Plan and place a zoning-optimised road grid. Road spacing gives every block full zoning depth on both sides "
     "(zoning_depth_cells, default 6) using the real road widths; cross streets every block_length_cells. Optional arterials every Nth "
     "line and on the outer ring. pattern 'staggered' offsets cross streets to make T-junctions (better traffic flow than 4-way). "
     "center defaults to the camera's ground point. plan_only returns the segments without touching the game.",
     {"type": "object", "properties": {
         "center": XZ, "angle_deg": {"type": "number"}, "cols": {"type": "integer"}, "rows": {"type": "integer"},
         "local_type": {"type": "string"}, "arterial_type": {"type": "string"}, "arterial_every": {"type": "integer"},
         "zoning_depth_cells": {"type": "integer"}, "block_length_cells": {"type": "integer"},
         "pattern": {"type": "string", "enum": ["grid", "staggered"]}, "plan_only": {"type": "boolean"},
         "zone_type": {"type": "string", "description": "optional: zone the whole grid afterwards (name from cs2_list_zone_types)"},
         "overwrite_zoning": {"type": "boolean"},
         "dry_run": {"type": "boolean"}, "hold": {"type": "boolean"}, "allow_errors": {"type": "boolean"}},
      "required": ["local_type"]}, t_grid),
    ("cs2_place_circle", "Place a ring road / roundabout loop of `type` as true circular arcs (pieces arcs, default 8). "
     "Counter-clockwise by default (right-hand traffic roundabouts); set clockwise for left-hand traffic. center defaults to the camera's ground point.",
     {"type": "object", "properties": {"center": XZ, "radius": {"type": "number"}, "type": {"type": "string"}, "pieces": {"type": "integer"},
                                       "clockwise": {"type": "boolean"}, "dry_run": {"type": "boolean"}, "hold": {"type": "boolean"}, "allow_errors": {"type": "boolean"}},
      "required": ["radius", "type"]}, t_circle),
    ("cs2_list_zone_types", "List zone type names (e.g. residential low/high, commercial, office, industrial variants).",
     {"type": "object", "properties": {}}, t_zone_types),
    ("cs2_zone", "Zone (or dezone with zone_type \"none\") every zoning cell inside an area: either 4 corners [[x,z]x4] in order, or a "
     "rectangle center/width/depth/angle_deg (center defaults to the camera's ground point). overwrite (default true) replaces existing zoning. Undoable.",
     {"type": "object", "properties": {"zone_type": {"type": "string"}, "corners": {"type": "array", "items": XZ, "minItems": 4, "maxItems": 4}, "frame": FRAME,
                                       "center": XZ, "width": {"type": "number"}, "depth": {"type": "number"}, "angle_deg": {"type": "number"},
                                       "overwrite": {"type": "boolean"}, "dry_run": {"type": "boolean"}, "hold": {"type": "boolean"}, "follow_camera": CAM},
      "required": ["zone_type"]}, t_zone),
    ("cs2_bulldoze", "Remove roads and/or buildings: by ids (from cs2_query_roads / cs2_query_buildings) and/or an area box. In an area, "
     "roads (default true) whose midpoint is inside and buildings (default false) whose position is inside are removed. "
     "Undo rebuilds removed roads (buildings cannot be restored). Mod 0.44: a building goes with its sub-nets, sub-areas "
     "and installed upgrades, as the game's bulldozer does (0.43 left big assets' elevated sub-net pillars + sub-objects behind).",
     {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "string"}}, "xmin": {"type": "number"}, "zmin": {"type": "number"},
                                       "xmax": {"type": "number"}, "zmax": {"type": "number"}, "roads": {"type": "boolean"},
                                       "buildings": {"type": "boolean"}, "dry_run": {"type": "boolean"}, "hold": {"type": "boolean"}, "follow_camera": CAM}}, t_bulldoze),
    ("cs2_replace_roads", "Upgrade/downgrade existing roads in place to another type (keeps connections), by ids or area box. Undoable.",
     {"type": "object", "properties": {"type": {"type": "string"}, "ids": {"type": "array", "items": {"type": "string"}},
                                       "xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"}, "zmax": {"type": "number"},
                                       "dry_run": {"type": "boolean"}, "hold": {"type": "boolean"}, "follow_camera": CAM, "allow_errors": {"type": "boolean"}}, "required": ["type"]}, t_replace),
    ("cs2_undo", "Undo the most recent change made through this MCP (place roads / bulldoze / replace / zone). Pass expect_id "
     "(the change's undo_id, or an id from cs2_history) so the wrong change can never be undone.",
     {"type": "object", "properties": {"expect_id": {"type": "integer"},
                                       "count": {"type": "integer", "description": "undo this many changes, newest first (default 1)"}}}, t_undo),
    ("cs2_screenshot", "Take a screenshot of the game and return the image. Optionally move the camera first (x, z, zoom, yaw, "
     "pitch) and wait settle_s seconds (default 2) for it to arrive. max_width scales the returned JPG (default 1280, max 3840). "
     "Mod 0.30: clean=true = a proper game shot like Photo Mode's 'Take photo' - no UI, no notification icons, overlays, road "
     "names or route lines (restored afterwards); frames = frames to wait before capturing (default 30, lets TAA settle); "
     "save_png = path to keep the full-resolution PNG (mod 0.33: a .png under %USERPROFILE%\blender, %USERPROFILE%\cs2-mcp, the game's "
     "Screenshots folder or %TEMP%; more roots in ModsData\CS2MCP\save_png_roots.txt); supersize = 1..4x render scale. Combine with cs2_photo_mode for grading.",
     {"type": "object", "properties": {"x": {"type": "number"}, "z": {"type": "number"}, "zoom": {"type": "number"},
                                       "yaw": {"type": "number"}, "pitch": {"type": "number"},
                                       "settle_s": {"type": "number"}, "max_width": {"type": "integer"},
                                       "clean": {"type": "boolean"}, "frames": {"type": "integer"},
                                       "save_png": {"type": "string"}, "supersize": {"type": "integer"}}}, t_screenshot),
    ("cs2_photo_mode", "The game's Photo Mode settings (mod 0.30), same ids as the Photo Mode panel: Camera (focal length, "
     "aperture...), Lens (DepthOfField, Bloom, Vignette, FilmGrain, MotionBlur...), Color (ColorAdjustments.postExposure / "
     "contrast / saturation / hueShift / colorFilter r,g,b, WhiteBalance.temperature / tint, ShadowsMidtonesHighlights), "
     "Weather (clouds, fog, sky) and Environment ('Time of Day' 0-24). No args = list every property with value/min/max; "
     "group filters the list; set = {id: value, ...} applies them (the photo-mode volume switches on); reset = true restores "
     "all defaults. Always reset after a photo session so the player's view goes back to normal.",
     {"type": "object", "properties": {"set": {"type": "object"}, "reset": {"type": "boolean"}, "group": {"type": "string"},
                                       "list": {"type": "boolean"}}}, t_photo_mode),
    ("cs2_map", "Top-down map image of an area built from game data (not the camera): terrain contours + steep ground, water, "
     "zone cells, building lots, roads (by type or traffic), a coordinate grid, and markers for problems (dead ends, near-miss "
     "connections, sharp junctions, short links) plus a magenta wash on unused land within zoning reach. Look before planning. "
     "plan = draft segments (same shape as cs2_place_roads) drawn as a dashed overlay, to judge a layout before building; "
     "marks = [{pos:[x,z], label}]. Area: xmin/zmin/xmax/zmax, or center + size (m); default camera ground point, 1200 m.",
     {"type": "object", "properties": {"xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"}, "zmax": {"type": "number"},
                                       "center": XZ, "size": {"type": "number"}, "size_z": {"type": "number"}, "px": {"type": "integer"},
                                       "colour_by": {"type": "string", "enum": ["type", "traffic"]},
                                       "layers": {"type": "array", "items": {"type": "string", "enum": ["terrain", "waste", "zones", "buildings", "roads", "issues", "grid", "districts"]}},
                                       "outlines": {"type": "array", "items": {"type": "object"}, "description": "draft district outlines [{points:[[x,z],...], label}] drawn dashed purple"},
                                       "plan": {"type": "array", "items": {"type": "object"}}, "plan_file": FILE,
                                       "marks": {"type": "array", "items": {"type": "object"}}}}, t_map),
    ("cs2_inspect", "Facts-only check-up of an area (changes nothing): dead ends, near-miss connections, overlapping unconnected "
     "nodes, sharp junctions, kinks, short links between junctions, disconnected road islands, congested roads, junction arm "
     "counts, zoning totals, road-frontage depth histogram, and unused land pockets within zoning reach, with coordinates.",
     {"type": "object", "properties": {"xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"}, "zmax": {"type": "number"},
                                       "center": XZ, "size": {"type": "number"}, "size_z": {"type": "number"}, "short_link_m": {"type": "number"}, "sharp_deg": {"type": "number"},
                                       "kink_deg": {"type": "number"}, "near_miss_m": {"type": "number"},
                                       "sections": {"type": "array", "items": {"type": "string"}, "description": "only these parts: roads, zoning, unused_land, or sub-keys like roads.dead_ends / roads.sharp_junctions"},
                                       "limit": {"type": "integer", "description": "max items per list (default 8)"},
                                       "legend": {"type": "boolean", "description": "include the zone legend (default false)"}}}, t_inspect),
    ("cs2_road_network", "Road graph in a box: edges (id, type, width, length, 4 Bezier control points [x,y,z], start/end node "
     "ids, elevation, traffic flow 0..1) and the nodes they use (id, pos, degree, owned arms, outside connection). Utilities are "
     "left out by default (exclude_utilities=false keeps pipes, power lines, tracks). types = name substrings to keep; summary = "
     "per-type counts/length/mean flow only; compact = ends + node ids + flow per edge instead of full curves.",
     {"type": "object", "properties": {"xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"},
                                       "zmax": {"type": "number"}, "limit": {"type": "integer"},
                                       "types": {"type": "array", "items": {"type": "string"}}, "exclude_utilities": {"type": "boolean"},
                                       "min_elevation": {"type": "number"}, "summary": {"type": "boolean"}, "compact": {"type": "boolean"}}}, t_network),
    ("cs2_zone_blocks", "Raw zone blocks in a box: position, direction (toward its road), size [along road, depth] and a cell "
     "string ('#' hidden, '.' empty, letters = zones per legend, upper case = built on).",
     {"type": "object", "properties": {"xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"},
                                       "zmax": {"type": "number"}, "limit": {"type": "integer"}}}, t_blocks),
    ("cs2_terrain_grid", "Terrain heights and water depths on a regular grid (step metres, max 90k samples).",
     {"type": "object", "properties": {"xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"},
                                       "zmax": {"type": "number"}, "step": {"type": "number"}}, "required": ["xmin", "zmin", "xmax", "zmax"]}, t_tgrid),
    ("cs2_preview_commit", "Build the preview left on screen by an operation called with hold=true. Refuses if it has errors unless allow_errors.",
     {"type": "object", "properties": {"allow_errors": {"type": "boolean"}}}, t_commit),
    ("cs2_preview_cancel", "Discard the preview left on screen by an operation called with hold=true.",
     {"type": "object", "properties": {}}, t_cancel),
    ("cs2_tool_reset", "Unstick the MCP build tool (mod 0.25): drops an in-flight or orphaned operation (tool_busy stuck true, "
     "every build command answering 'tool busy') without a restart. Nothing half-applied is kept; a held preview is discarded. "
     "0.25 also has a watchdog that does this automatically ~2 s after the tool loses the active-tool slot.",
     {"type": "object", "properties": {"reason": {"type": "string"}}}, t_tool_reset),
    ("cs2_history", "List the undo history (newest first).", {"type": "object", "properties": {}}, t_history),
    ("cs2_set_speed", "Set simulation speed: 0 = pause, 1, 2 or 3.",
     {"type": "object", "properties": {"speed": {"type": "number"}}, "required": ["speed"]}, t_speed),
    ("cs2_camera_goto", "Move the camera to look at x/z (optionally set zoom distance, and mod 0.30: yaw/pitch view angle in degrees).",
     {"type": "object", "properties": {"x": {"type": "number"}, "z": {"type": "number"}, "zoom": {"type": "number", "description": "camera distance (m); alias: distance"}, "distance": {"type": "number"},
                                       "yaw": {"type": "number", "description": "compass heading of the view, degrees"}, "pitch": {"type": "number", "description": "look-down angle, degrees"}}}, t_goto),
    ("cs2_query_buildings", "List buildings whose position is inside an x/z box (id, type, position).",
     {"type": "object", "properties": {"xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"},
                                       "zmax": {"type": "number"}, "limit": {"type": "integer"}}}, t_buildings),
    ("cs2_traffic", "Traffic facts for an area: length-weighted mean flow overall and per road type, and the worst congestion "
     "clusters (edges below threshold, default 0.5, grouped within cluster_m, default 80 m) with location, types, worst edge "
     "id and severity. Area: box or center+size (default camera).",
     {"type": "object", "properties": dict(AREA, threshold={"type": "number"}, cluster_m={"type": "number"}, limit={"type": "integer"},
                                           types_limit={"type": "integer"})}, t_traffic),
    ("cs2_zone_summary", "Zoning facts for an area box or a polygon: cells and hectares per zone family (residential low / "
     "medium / high / lowrent, mixed, commercial low/high, office, industrial) split built vs empty, shares, and unzoned "
     "cells. by_zone adds per-zone-type rows.",
     {"type": "object", "properties": dict(AREA, polygon={"type": "array", "items": XZ}, by_zone={"type": "boolean"})}, t_zone_summary),
    ("cs2_probe", "Feasibility check for many alternatives at once: dry-runs each candidate on its own (a candidate is one "
     "segment or a list of segments, place_roads shape) and returns ok / error counts / reasons per candidate. explain adds "
     "what each error location is next to; details adds snapping and intersection counts. Builds nothing.",
     {"type": "object", "properties": {"candidates": {"type": "array"}, "candidates_file": FILE, "type": {"type": "string"},
                                       "frame": FRAME, "snap_tolerance": {"type": "number"}, "explain": {"type": "boolean"},
                                       "details": {"type": "boolean"}}}, t_probe),
    ("cs2_frame", "Convert points between world x/z and a local frame {origin, heading_deg} (a = along the heading, b = to its left).",
     {"type": "object", "properties": {"frame": FRAME, "to_world": {"type": "array", "items": XZ}, "to_local": {"type": "array", "items": XZ}},
      "required": ["frame"]}, t_frame),
    ("cs2_place_path", "Build one road along a path YOU give as knots [[x,z], ...]: smoothed through the knots (or straight "
     "pieces with smooth=false), optionally offset sideways (offset m, + = left of travel, with taper_start/taper_end m easing "
     "from 0), cut into cubic pieces about piece_m long with joints kept clear of avoid points, elevation as a number or a "
     "profile along the path. Direction = knot order (reverse flips it). Reports length and tightest radius. Same preview/"
     "error rules as cs2_place_roads.",
     {"type": "object", "properties": dict(BUILD_OPTS, knots={"type": "array", "items": XZ}, type={"type": "string"}, elevation=ELEV,
                                           offset={"type": "number"}, taper_start={"type": "number"}, taper_end={"type": "number"},
                                           reverse={"type": "boolean"}, joints_at_knots={"type": "boolean"}),
      "required": ["knots", "type"]}, _builder(tools2.build_path)),
    ("cs2_place_pair", "Build a one-way pair (divided highway / couplet) from ONE centreline you give as knots: two carriageways "
     "separation m apart (centre to centre, default 24), each drawn in its correct direction for traffic (right, default, or "
     "left) - the forward one follows the knot order. taper_start / taper_end merge the carriageways onto the centreline at "
     "the ends (for Y-splits into a two-way road: taper >= ~3x separation keeps the split angle valid). types = [forward, "
     "return] to use different lane counts (e.g. an extra lane before an off-ramp). Elevation number or profile along the "
     "centreline. Reports each carriageway's end points so you can attach ramps.",
     {"type": "object", "properties": dict(BUILD_OPTS, knots={"type": "array", "items": XZ}, type={"type": "string"},
                                           types={"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 2},
                                           separation={"type": "number"}, traffic={"type": "string", "enum": ["right", "left"]},
                                           elevation=ELEV, taper_start={"type": "number"}, taper_end={"type": "number"}),
      "required": ["knots"]}, _builder(tools2.build_pair)),
    ("cs2_place_lines", "Build a street network from lines YOU lay out: [{points:[[x,z],...], type?}] (straight pieces between "
     "points). Every crossing and every end touching another line (within touch_m, default 1.5) becomes a real junction with "
     "one shared coordinate; ends near existing roads snap as usual. For grids with per-line extents, stubs to a shoreline, "
     "etc. lines_file / frame accepted.",
     {"type": "object", "properties": dict(BUILD_OPTS, lines={"type": "array", "items": {"type": "object"}}, lines_file=FILE,
                                           type={"type": "string"}, touch_m={"type": "number"}, merge_m={"type": "number"})},
     _builder(tools2.build_lines)),
    ("cs2_ramp", "Branch a ramp off (mode 'off') or onto (mode 'on') an existing carriageway. at = a point on/near it (edge "
     "id or road_types to disambiguate; distance_m shifts along it). The carriageway is split exactly there and the ramp "
     "leaves toward `side` (right default) at angle_deg (default 30: smaller angles make 'Invalid Shape'), runs lead_m "
     "straight-ish, then through YOUR knots to its far end (mode 'on': knots come first, the ramp ends at the split). "
     "Elevation: the carriageway's height at the split -> end_elevation (default 0), or a profile. aux_lane = {type, "
     "length_m}: first upgrade the carriageway (whole edges) for that length before an off-ramp / after an on-ramp, e.g. "
     "one lane wider. Checks the junctions afterwards.",
     {"type": "object", "properties": dict(BUILD_OPTS, at={"description": "[x,z] or '@name'"}, edge={"type": "string"},
                                           road_types={"type": "array", "items": {"type": "string"}}, distance_m={"type": "number"},
                                           mode={"type": "string", "enum": ["off", "on"]}, side={"type": "string", "enum": ["right", "left"]},
                                           angle_deg={"type": "number"}, lead_m={"type": "number"}, knots={"type": "array"},
                                           against_edge={"type": "boolean", "description": "two-way road: branch from the lanes travelling against the edge's drawing direction (side is then relative to that travel direction)"},
                                           type={"type": "string"}, elevation=ELEV, end_elevation={"type": "number"},
                                           aux_lane={"type": "object", "properties": {"type": {"type": "string"}, "length_m": {"type": "number"}}}),
      "required": ["at", "knots", "type"]}, t_ramp),
    ("cs2_extend_lines", "Lay grid lines YOU choose (avenues/rows as [{points, type?, name?}], frame welcome) and let the tool "
     "fit them to what exists: where a line crosses an existing road near one of its nodes (snap_m, default 30) it goes "
     "through that node instead of making a new junction beside it; ends near nodes snap on; lines stop stop_gap_m short "
     "of roads whose type matches stop_at_types (e.g. ['Highway']) - at-grade only, elevated roads are passed under; end_at_types "
     "makes lines end exactly on such roads (junction) and drops the stubs beyond; "
     "clip_to_land (default true) stops a line at water, and max_grade at steep ground. Crossings between the new lines "
     "become junctions. Reports per line what was clipped, snapped or reused (unchanged lines just counted).",
     {"type": "object", "properties": dict(BUILD_OPTS, lines={"type": "array", "items": {"type": "object"}}, lines_file=FILE,
                                           type={"type": "string"}, snap_m={"type": "number"}, end_snap_m={"type": "number"},
                                           stop_at_types={"type": "array", "items": {"type": "string"}}, stop_gap_m={"type": "number"},
                                           end_at_types={"type": "array", "items": {"type": "string"}, "description": "end lines exactly ON the first/last crossing with roads of these types (e.g. a waterfront drive), dropping what lies beyond"},
                                           end_at_ids={"type": "array", "items": {"type": "string"}, "description": "like end_at_types, for specific road ids"},
                                           end_keep={"type": "string", "enum": ["auto", "start", "end"], "description": "with end_at_*: keep from the line start to the first end road ('start'), from the last end road to the line end ('end'), or auto (between two end roads / the longer side). Per line too"},
                                           clip_to_land={"type": "boolean"}, max_grade={"type": "number"}, min_piece_m={"type": "number"},
                                           touch_m={"type": "number"}, merge_m={"type": "number"}, report_limit={"type": "integer"})},
     t_extend_lines),
    ("cs2_zone_along", "Zone strips beside roads: the roads with `ids`, or every road in the box (optionally only `types` "
     "substrings) whose midpoint is inside. depth_m (default 48 = 6 cells) from the road edge, starting start_m out, "
     "side both/left/right (of each road's direction). overwrite default false. plan_only returns the quads.",
     {"type": "object", "properties": {"xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"}, "zmax": {"type": "number"},
                                       "ids": {"type": "array", "items": {"type": "string"}}, "types": {"type": "array", "items": {"type": "string"}},
                                       "zone_type": {"type": "string"}, "depth_m": {"type": "number"}, "start_m": {"type": "number"},
                                       "side": {"type": "string", "enum": ["both", "left", "right"]}, "overwrite": {"type": "boolean"},
                                       "dry_run": {"type": "boolean"}, "plan_only": {"type": "boolean"}, "follow_camera": CAM},
      "required": ["zone_type", "xmin", "zmin", "xmax", "zmax"]}, t_zone_along),
    ("cs2_obstacles", "What physically stands in an area, with real footprints (prefab geometry, rotated): buildings (+ their "
     "lots), building parts, pillars of elevated roads, trees, props. grid_m turns it into an occupancy grid (rows of "
     "characters: W water, S steep, R at-grade road, B building, P pillar, L lot, T tree, o prop, . free) - the quick way "
     "to see where a road or block fits. categories filters; names adds prefab names.",
     {"type": "object", "properties": dict(AREA, grid_m={"type": "number"}, categories={"type": "array", "items": {"type": "string"}},
                                           names={"type": "boolean"}, building_props={"type": "boolean"}, roads={"type": "boolean"},
                                           elevated_roads={"type": "boolean"}, terrain={"type": "boolean"}, steep_grade={"type": "number"},
                                           limit={"type": "integer"}, full={"type": "boolean"})}, t_obstacles),
    ("cs2_pillars", "Elevated roads in an area and their pillar positions. With cross = [[x,z],[x,z]] (a street you want to "
     "pass underneath): where it crosses each elevated road, its deck height, the nearest pillar, and if closer than "
     "clearance_m (default 12) the nearest clear spots along the elevated road (shift_m each way).",
     {"type": "object", "properties": dict(AREA, cross={"type": "array"}, clearance_m={"type": "number"},
                                           min_elevation={"type": "number"}, limit={"type": "integer"})}, t_pillars),
    ("cs2_trim", "Cut an existing road at a point and remove one side of the cut: keep 'start' or 'end' (edge direction) "
     "or keep_point = [x,z] on the side to keep. The kept piece is rebuilt with the exact same curve and height, so it "
     "stays connected at its far end. edge / road_types pick the road if several are near. dry_run shows the piece.",
     {"type": "object", "properties": {"at": {"description": "[x,z] or '@name'"}, "edge": {"type": "string"},
                                       "road_types": {"type": "array", "items": {"type": "string"}}, "keep": {"type": "string", "enum": ["start", "end"]},
                                       "keep_point": {"description": "[x,z] or '@name'"}, "radius": {"type": "number"},
                                       "max_offset_m": {"type": "number"}, "allow_errors": {"type": "boolean"},
                                       "dry_run": {"type": "boolean"}, "follow_camera": CAM}, "required": ["at"]}, t_trim),
    ("cs2_shore", "Edges of the buildable land. With from = [x,z]: cast rays (directions_deg, or every_deg around; frame "
     "makes them relative to its heading) and report the distance to water or to ground steeper than max_grade, and the "
     "last good point. Without from: the outline of water (kind 'water') or steep ground (kind 'steep') in an area as "
     "simplified polylines (tolerance_m), longest first.",
     {"type": "object", "properties": dict(AREA, **{"from": {"description": "[x,z] or '@name'"}}, directions_deg={"type": "array", "items": {"type": "number"}},
                                           every_deg={"type": "number"}, max_m={"type": "number"}, step_m={"type": "number"},
                                           max_grade={"type": "number"}, frame=FRAME, kind={"type": "string", "enum": ["water", "steep"]},
                                           tolerance_m={"type": "number"}, min_length_m={"type": "number"}, water_m={"type": "number"},
                                           step={"type": "number"}, limit={"type": "integer"})}, t_shore),
    ("cs2_refs", "Named reference points, kept on disk across sessions: action set with items [{name, pos:[x,z]} or {name, "
     "near:[x,z]} (= the nearest road node, followed if the game moves it), note?], list, delete (names). Use '@name' "
     "anywhere a point is accepted (segments, knots, lines, at, from, corners...).",
     {"type": "object", "properties": {"action": {"type": "string", "enum": ["list", "set", "delete"]},
                                       "items": {"type": "array", "items": {"type": "object"}}, "names": {"type": "array", "items": {"type": "string"}}}}, t_refs),
    ("cs2_tour", "Screenshots of several spots in one call (points [x,z] / '@name', up to 8), or of the most recent edits "
     "made in this session (recent = how many, default 4). zoom, settle_s, max_width (default 900).",
     {"type": "object", "properties": {"points": {"type": "array"}, "recent": {"type": "integer"}, "zoom": {"type": "number"},
                                       "settle_s": {"type": "number"}, "max_width": {"type": "integer"}}}, t_tour),
    ("cs2_shore_points", "Where the water is along a road or path: every spacing_m (default 50) the distance to water on its left "
     "and right (within max_reach_m), the waterline point, the seaward direction, the depth probe_m beyond the waterline and the "
     "land height there. Line = road_ids (existing roads, chained in order) or knots (frame accepted). Builds nothing.",
     {"type": "object", "properties": {"road_ids": {"type": "array", "items": {"type": "string"}}, "knots": {"type": "array", "items": XZ},
                                       "frame": FRAME, "spacing_m": {"type": "number"}, "start_m": {"type": "number"}, "end_m": {"type": "number"},
                                       "max_reach_m": {"type": "number"}, "probe_m": {"type": "number"}}}, lambda a: tools4.shore_points(rpc, a, tools2.framed(a))),
    ("cs2_place_piers", "Piers off a road out over the water: every spacing_m along road_ids (T junctions onto that road) or knots, "
     "straight out on `side` (auto = where water is nearer, else left/right of the line's direction) to the waterline and then "
     "length_m beyond, as `type` (default QuayLarge01 - a quay deck with walls; any road/pathway type works). The deck stays level "
     "at deck_height (absolute y) or the waterline land height + deck_above_m (default 1). head = {length_m, type?} adds a T "
     "crosspiece at the tip. Skips (and reports) candidates with no water within max_reach_m, water shallower than min_depth_m "
     "(default 2) out there, a crossing with another road, or closer than min_gap_m to the previous pier. Same preview rules as "
     "cs2_place_roads; start_m/end_m trim the ends of the line.",
     {"type": "object", "properties": dict(BUILD_OPTS, road_ids={"type": "array", "items": {"type": "string"}}, knots={"type": "array", "items": XZ},
                                           type={"type": "string"}, spacing_m={"type": "number"}, length_m={"type": "number"},
                                           side={"type": "string", "enum": ["auto", "left", "right"]}, max_reach_m={"type": "number"},
                                           min_depth_m={"type": "number"}, min_gap_m={"type": "number"}, deck_above_m={"type": "number"},
                                           head={"type": "object", "properties": {"length_m": {"type": "number"}, "type": {"type": "string"}}},
                                           start_m={"type": "number"}, end_m={"type": "number"},
                                           at={"type": "array", "description": "instead of spacing: piers at the line points nearest to these [x,z] (e.g. opposite cross streets)"})},
     _builder(lambda a, fr: tools4.build_piers(rpc, a, fr))),
    ("cs2_offset_road", "A street parallel to an existing road (road_ids, chained) or a path (knots) at offset_m (+ = left of its "
     "direction, - = right), trimmed by start_m / end_m, with straight connectors back to it every connect_every_m (default 200, "
     "0 = none) and at both ends (connect_ends, default true). For back streets, frontage/service roads, a second row of blocks. "
     "v0.27: leave offset_m out (side left/right) for the zoning spacing - source half width + 96 m + new half width, so the two "
     "streets' 6-cell blocks meet back to back (full depth, no overlap, no dead gap); a given offset_m that overlaps or leaves "
     "a dead gap comes back with a warning (info.zoning). Dry runs include a zoning_forecast.",
     {"type": "object", "properties": dict(BUILD_OPTS, road_ids={"type": "array", "items": {"type": "string"}}, knots={"type": "array", "items": XZ},
                                           offset_m={"type": "number"}, side={"type": "string", "enum": ["left", "right"]},
                                           source_width={"type": "number"}, source_zones={"type": "boolean", "description": "does the source road get zoning? (default: false for highways / ramps / tracks / paths when road_ids are given, else true) - a non-zoning source needs only one 48 m strip"},
                                           type={"type": "string"}, connector_type={"type": "string"},
                                           connect_every_m={"type": "number"}, connect_ends={"type": "boolean"},
                                           start_m={"type": "number"}, end_m={"type": "number"}), "required": ["type"]},
     _builder(lambda a, fr: tools4.build_offset_road(rpc, a, fr))),
    ("cs2_building_access", "Why a building has (no) road access and where the road must go: its lot's front edge (from position, "
     "facing and lot size), the centreline a road of `type` needs (half its width + margin_m outside the front edge), which "
     "existing roads run along the front, and the game's own status (mod 0.10). build = true places that road, extend_m past both "
     "corners (dry_run to preview).",
     {"type": "object", "properties": {"id": {"type": "string"}, "type": {"type": "string"}, "margin_m": {"type": "number"},
                                       "extend_m": {"type": "number"}, "build": {"type": "boolean"}, "dry_run": {"type": "boolean"},
                                       "snap_tolerance": {"type": "number"}}, "required": ["id"]}, lambda a: tools4.building_access(rpc, a)),
    ("cs2_building_status", "Road access and warning icons (the notification bubbles) of buildings: id, ids, or every building in a box; "
     "problems_only keeps those without road access or with icons; icon_counts summarises. Needs mod 0.10.",
     {"type": "object", "properties": dict(AREA, id={"type": "string"}, ids={"type": "array", "items": {"type": "string"}},
                                           problems_only={"type": "boolean"}, limit={"type": "integer"},
                                           icon_details={"type": "boolean", "description": "mod 0.45: per icon its world location and the element it flags (target lane/object/edge, prefab, curve ends, owner chain)"})},
     lambda a: rpc("building_status", {k: v for k, v in a.items() if k in ("id", "ids", "xmin", "zmin", "xmax", "zmax", "problems_only", "limit", "icon_details")})),
    ("cs2_service_access", "Why a hearse / ambulance never comes to a building (mod 0.41): its road edge, every spawn location the game's "
     "path target seeker uses (a spawn with a connected lane switches the road-edge fallback off, so an unreachable door = no "
     "service), what each spawn is connected to (lane kind, owner, access restriction), its sub-areas and lanes, and every "
     "citizen in it with a health problem: flags, timer, HealthcareRequest fail count / cooldown / dispatched handler / path.",
     {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
     lambda a: rpc("service_access", {"id": a["id"]})),
    ("cs2_path_test", "Run the game's own hearse / ambulance dispatch pathfind on demand (mod 0.41) against a building or citizen id: "
     "mode hearse | ambulance (exact HealthcareDispatchSystem query: that service's vehicles in the district -> the target's "
     "doors, drive + walk) or hearse_road | ambulance_road (Road-only destination = reaches the kerb?). Waits for the result "
     "(wait_s, default 8): found, origin facility/vehicle, distance, methods, the lanes the route ends on.",
     {"type": "object", "properties": {"id": {"type": "string"}, "mode": {"type": "string"}, "wait_s": {"type": "number"}}, "required": ["id"]},
     lambda a: _path_test(a)),
    ("cs2_place_arcs", "Concentric circular arcs and radial streets that meet in exact shared junctions - fans, crescents, rings, "
     "radial layouts. center [x,z] (frame accepted: angles then count from its heading); arcs = [{radius, from_deg, to_deg, "
     "type?}] (0 deg = +x / frame heading, counter-clockwise); radials = [{deg, r0, r1, type?}]. Arcs are split where radials "
     "cross them and radials at every arc. True circular Beziers, pieces up to piece_m.",
     {"type": "object", "properties": dict(BUILD_OPTS, center={"description": "[x,z] or '@name'"}, type={"type": "string"},
                                           arcs={"type": "array", "items": {"type": "object"}}, radials={"type": "array", "items": {"type": "object"}}),
      "required": ["center", "arcs"]}, _builder(tools4.build_arcs)),
    ("cs2_place_along_shore", "Lay a net along the waterline - quay walls, promenades, lakeshore drives: from/to = points near the "
     "shore (frame accepted); the path follows the water's edge between them, offset_m from it (+ = inland, - = out over the "
     "water), smoothed with knots every smooth_m (default 30; larger = straighter). type = any net (e.g. QuayMedium01); "
     "elevation / deck_height as usual. Same preview rules as cs2_place_roads.",
     {"type": "object", "properties": dict(BUILD_OPTS, **{"from": {"description": "[x,z] or '@name'"}}, to={"description": "[x,z] or '@name'"},
                                           type={"type": "string"}, offset_m={"type": "number"}, smooth_m={"type": "number"},
                                           tolerance_m={"type": "number"}, pad_m={"type": "number"}, step={"type": "number"}, elevation=ELEV,
                                           join_from={"description": "exact point (e.g. a road node) the path starts on"}, join_to={"description": "exact point the path ends on"}),
      "required": ["from", "to", "type"]}, _builder(lambda a, fr: tools4.build_shore_path(rpc, a, fr))),
    ("cs2_terraform", "Brush the terrain like the in-game terrain tool (mod 0.10): type level | shift | soften | slope, size_m "
     "(brush diameter, default 30), strength (default 1), frames (how long the brush is held, default 90). Where: points "
     "[[x,z] or [x,z,target_y]] or line [[x,z],...] + width_m (brush points laid along and across it). Level target: "
     "target_height (absolute y), or target='sides' = per point the median ground height side_m (default 20) beyond both "
     "edges of the line - erases a berm or fills a trench to match its surroundings. NOT undoable; dry_run checks the tool "
     "and lists brushes. Reports before/after heights.",
     {"type": "object", "properties": {"type": {"type": "string", "enum": ["level", "shift", "soften", "slope"]}, "size_m": {"type": "number"},
                                       "strength": {"type": "number"}, "frames": {"type": "integer"}, "points": {"type": "array"},
                                       "line": {"type": "array", "items": XZ}, "width_m": {"type": "number"}, "side_m": {"type": "number"},
                                       "target": {"type": "string", "enum": ["sides"]}, "target_height": {"type": "number"},
                                       "brush": {"type": "string"}, "frame": FRAME, "dry_run": {"type": "boolean"}, "follow_camera": CAM}},
     lambda a: tools4.terraform(rpc, a, tools2.framed(a))),
    ("cs2_asset_validate", "Offline pre-flight of a CS2 asset folder exported from Blender (no game needed). Checks: folder name == "
     "LOD0 FBX name (<Theme>_<Name>.fbx, LODs _LOD1/_LOD2); FBX in CENTIMETRES with the transform baked (UnitScaleFactor 1, no "
     "node scale - the importer scales raw vertices x0.01 and ignores unit + node transforms, so metres = 1/100 size, invisible; "
     "Blender needs bake_space_transform=True); main meshes have exactly one material named like "
     "the file + _BaseColor/_Normal/_MaskMap PNGs (+ _ControlMask/_Emissive); windows in a SEPARATE <Name>_Win.fbx with no "
     "material (night-lit interior mapping only triggers on the file name); square power-of-two textures; clean project root; build-menu metadata (<root>_build/<Name>_meta.json with title + "
     "description, and <Name>_Icon.png) - without it the asset has no picture or description in game. "
     "Full workflow: cs2-mcp/ASSET_PIPELINE.md.",
     {"type": "object", "properties": {"directory": {"type": "string", "description": "the asset folder, e.g. <project root>/NA_MyAsset"}},
      "required": ["directory"]}, assets.validate),
    ("cs2_asset_import", "Import an asset folder into the running game exactly like the Editor's Asset Importer (mod 0.11: calls "
     "AssetImportPanel.ImportAssets), no editor GUI: validates first, then imports meshes + textures (can take minutes), creates the "
     "object prefab from `preset` (Building | SignatureBuilding | CityServiceBuilding | SpawnableBuilding | StaticObject | ... ; or "
     "`template` = an existing prefab name to clone its gameplay setup -> ExistingPrefab), saves it to the user asset database and "
     "registers it so cs2_place_building can use it by name. lot_width/lot_depth (cells) set the building lot. project_root defaults "
     "to the folder's parent. preset 'None' = re-import meshes/textures IN PLACE and keep the prefab's configuration (use it for "
     "every update after the first import). Returns the created prefab(s), log lines, ok=false + errors + a hint when the game's "
     "pipeline logged errors (e.g. 'being used by another process' = the game holds the loaded .Geometry: quit to the main menu "
     "first). Import from a loaded city, or with preset None at the main menu (Find It crashes otherwise). After a successful import "
     "it applies the build-menu picture + name + description (mod 0.14) from icon/title/description args or, by default, from "
     "<project_root>_build/<Name>_meta.json ({title, description, icon?}) + <Name>_Icon.png - every asset should have them.",
     {"type": "object", "properties": {"directory": {"type": "string"}, "project_root": {"type": "string"},
                                       "preset": {"type": "string"}, "template": {"type": "string"}, "seed": {"type": "string"},
                                       "lot_width": {"type": "integer"}, "lot_depth": {"type": "integer"},
                                       "activate_tool": {"type": "boolean", "description": "leave the game's placement tool active with the new prefab (editor behaviour); default false"},
                                       "force": {"type": "boolean", "description": "import even if validation found problems"},
                                       "timeout_s": {"type": "number"},
                                       "title": {"type": "string", "description": "build-menu name (default: from the meta file)"},
                                       "description": {"type": "string", "description": "build-menu description (default: from the meta file)"},
                                       "icon": {"type": "string", "description": "PNG/JPG build-menu picture (default: <root>_build/<Name>_Icon.png)"},
                                       "meta": {"type": "string", "description": "path of the meta JSON (default <project_root>_build/<Name>_meta.json)"},
                                       "window_light": {"type": "number", "description": "mod 0.34: the Editor import panel's 'Interior Light Intensity' for every *_Win/_Wim/_Wif/_Wio FBX (0..1, game default 0.549, saturated at 1)"},
                                       "window_light_lod": {"type": "number", "description": "mod 0.34: separate intensity for the _LOD1/_LOD2 window files (far LODs read dim)"},
                                       "window_color": {"type": "array", "items": {"type": "number"}, "description": "mod 0.34: window base colour [r, g, b] linear (default dark blue-grey 0.027, 0.05, 0.072)"}},
      "required": ["directory"]}, assets.import_asset),
    ("cs2_prefab_clone", "Duplicate a user (imported) prefab under a new name (mod 0.34): saved next to it in the user asset "
     "database and registered live; the clone REFERENCES the same meshes, so one imported growable house can become its "
     "lot-depth variants (1x2..1x6) without re-importing mesh + textures. Configure the clone afterwards with "
     "cs2_asset_configure (lot, SpawnableBuilding level, sub-objects/areas, m_Meshes offset). Idempotent: returns "
     "exists=true when the name is already a user prefab.",
     {"type": "object", "properties": {"source": {"type": "string"}, "name": {"type": "string"}}, "required": ["source", "name"]},
     lambda a: rpc("prefab_clone", {"source": a["source"], "name": a["name"]})),
    ("cs2_asset_configure", "Edit and save a prefab by name (mod 0.12), e.g. give an imported building its gameplay: copy_from = a vanilla "
     "prefab whose gameplay components to clone (e.g. NA_ResidentialHighSignature01 -> signature residential: zone, level, UI, "
     "unlock; sub-objects/areas/lanes/effects are skipped); add_components / remove_components = component class names "
     "(e.g. BuildingProperties); set = {\"Component.m_Field\": value} or {\"m_Field\": value} on the prefab itself - numbers, "
     "bools, enum names, prefab names for prefab references, lists for arrays (e.g. {\"BuildingProperties.m_ResidentialProperties\": "
     "440, \"m_LotWidth\": 4}); lot_width / lot_depth / access_type shortcuts. describe = true lists every component's public "
     "fields and values. save (default true) writes it back to its asset. Build-menu info (mod 0.14, imported/user assets only): "
     "icon = a PNG/JPG path -> stored as an image asset next to the prefab and set as UIObject.m_Icon (square, transparent "
     "background, ~256-512 px); title / description = localized name + description (Assets.NAME/DESCRIPTION[<name>], saved as "
     "locale assets next to the prefab and shown immediately); locale defaults to en-US. Mod 0.15: describe expands nested entries (sub-nets, sub-objects, Bezier curves) with prefab "
     "names; set accepts structured values - JSON objects set fields by name, lists fill fields in order (float3 = [x,y,z], "
     "Bezier4x3 = [a,b,c,d], quaternion = [x,y,z,w] or {\"yaw_deg\": d}) - e.g. {\"ObjectSubNets.m_SubNets\": [{\"m_NetPrefab\": "
     "\"Double Train Track\", \"m_BezierCurve\": [[-100,-20,0],[-33,-20,0],[33,-20,0],[100,-20,0]], \"m_NodeIndex\": [0,1], "
     "\"m_ParentMesh\": [-1,-1]}]} gives a building built-in tracks (y = height, negative = underground).",
     {"type": "object", "properties": {"name": {"type": "string"}, "copy_from": {"type": "string"},
                                       "copy_components": {"type": "array", "items": {"type": "string"},
                                                           "description": "mod 0.26, with copy_from: copy only these component types (e.g. [\"EmissiveProperties\"] from \"Taxi02 Mesh\" - vehicle light layers + turn-signal curves)"},
                                       "add_components": {"type": "array", "items": {"type": "string"}},
                                       "remove_components": {"type": "array", "items": {"type": "string"}},
                                       "set": {"type": "object"}, "describe": {"type": "boolean"},
                                       "lot_width": {"type": "integer"}, "lot_depth": {"type": "integer"},
                                       "access_type": {"type": "string"}, "save": {"type": "boolean"},
                                       "icon": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"},
                                       "locale": {"type": "string"},
                                       "allow_vanilla": {"type": "boolean", "description": "mod 0.33: edits are refused on prefabs that are not your own imported assets (game / mod-pack prefabs; describe and copy_from as a SOURCE stay allowed). true = runtime-only edit of such a prefab, never saved, lost on restart - a test at the main menu, not in the city"},
                                       "describe_components": {"type": "array", "items": {"type": "string"},
                                                               "description": "with describe: only these components (e.g. [\"ObjectSubNets\"])"}},
      "required": ["name"]}, assets.configure),
    ("cs2_settings", "Persistent server settings (settings.json): follow_camera (default true) = builders move the game camera "
     "to each change. Call with no arguments to read, or give values to change them.",
     {"type": "object", "properties": {"follow_camera": {"type": "boolean"}}}, lambda a: _settings(a)),
    ("cs2_zone_report", "Zoning mix per district (or polygon / box): families with % built, the top zone styles as "
     "[cells, empty cells], empty zoned cells and unzoned cells. One call instead of zone_summary per district.",
     {"type": "object", "properties": dict({"districts": {"type": "array", "items": {"type": "string"}, "description": "district names (default: all)"}, "polygon": {"type": "array", "items": XZ}, "xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"}, "zmax": {"type": "number"}}, styles_top={"type": "integer"})}, lambda a: tools5.zone_report(rpc, a)),
    ("cs2_building_warnings", "Warning icons (notification bubbles) per district x building category (residential / "
     "commercial / mixed / office / industrial / service), as 'n of total (%)', with example ids and buildings without road "
     "access. ignore_icons default ['Leveling Building'] (levelling up is not a problem). Needs mod 0.10+.",
     {"type": "object", "properties": dict({"districts": {"type": "array", "items": {"type": "string"}, "description": "district names (default: all)"}, "polygon": {"type": "array", "items": XZ}, "xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"}, "zmax": {"type": "number"}}, ignore_icons={"type": "array", "items": {"type": "string"}}, examples={"type": "integer"})},
     lambda a: tools5.building_warnings(rpc, a)),
    ("cs2_rezone", "Change one zone style to another ONLY where it is (cell-exact, from the zone blocks): from = name or list, "
     "to = name or 'none'. only_empty (default true) leaves cells with buildings alone - e.g. switch unbuilt high density to "
     "medium when high demand is saturated. Where: districts / polygon / box. dry_run counts cells.",
     {"type": "object", "properties": dict({"districts": {"type": "array", "items": {"type": "string"}, "description": "district names (default: all)"}, "polygon": {"type": "array", "items": XZ}, "xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"}, "zmax": {"type": "number"}}, **{"from": {"description": "zone style name or list"}}, to={"type": "string"},
                                           only_empty={"type": "boolean"}, dry_run={"type": "boolean"}), "required": ["from", "to"]},
     lambda a: tools5.rezone(rpc, a)),
    ("cs2_rezone_buildings", "Replace chosen buildings with another zone: select by districts / polygon / box plus icon (e.g. "
     "'No Customers'), type_regex, zone_regex (mod 0.13: the building's zone style) or category; each lot is rezoned to `to` "
     "or per `map` {regex on building type or zone: zone style}, then the buildings are bulldozed (bulldoze default true - "
     "rezoning alone leaves the old building standing). Signature buildings skipped unless include_signature. dry_run lists.",
     {"type": "object", "properties": dict({"districts": {"type": "array", "items": {"type": "string"}, "description": "district names (default: all)"}, "polygon": {"type": "array", "items": XZ}, "xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"}, "zmax": {"type": "number"}}, icon={"type": "string"}, type_regex={"type": "string"}, zone_regex={"type": "string"},
                                           category={"type": "string"}, to={"type": "string"}, map={"type": "object"}, bulldoze={"type": "boolean"},
                                           include_signature={"type": "boolean"}, dry_run={"type": "boolean"})}, lambda a: tools5.rezone_buildings(rpc, a)),
    ("cs2_zone_palette", "Zone the streets inside an outline by rules, rotating styles so blocks do not repeat: rules = "
     "[{match: {types, direction 'along'|'across' the frame heading, near_point + within_m, near_types + within_m}, zone | "
     "zones + rotate 'street' (per street, street_m) or 'along' (alternate_m)}], first match wins per road. order = which "
     "styles zone first (they claim the corners). Only empty cells unless overwrite. depth_m default 48. plan_only counts.",
     {"type": "object", "properties": {"polygon": {"type": "array", "items": XZ}, "polygon_in_frame": {"type": "boolean", "description": "polygon given in frame coordinates (default: world; the frame then only sets the along/across direction)"}, "frame": FRAME, "rules": {"type": "array", "items": {"type": "object"}},
                                       "order": {"type": "array", "items": {"type": "string"}}, "overwrite": {"type": "boolean"}, "depth_m": {"type": "number"},
                                       "plan_only": {"type": "boolean"}}, "required": ["polygon", "rules"]}, lambda a: tools5.zone_palette(rpc, a)),
    ("cs2_zone_snapshot", "Growth watch: save zoning (built / empty per style) and warning totals per district to "
     "server/snapshots, and diff with the previous snapshot - cells built since, zoned cells still empty, warnings up/down.",
     {"type": "object", "properties": dict({"districts": {"type": "array", "items": {"type": "string"}, "description": "district names (default: all)"}, "polygon": {"type": "array", "items": XZ}, "xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"}, "zmax": {"type": "number"}}, label={"type": "string"})}, lambda a: tools5.zone_snapshot(rpc, a)),
    ("cs2_demand", "The game's demand bars (mod 0.13): residential low / medium / high building demand + household demand, "
     "commercial, industrial, office, storage, each with its demand factors (positive pushes demand up) - e.g. why shops "
     "report No Customers or high density is saturated.",
     {"type": "object", "properties": {"factors": {"type": "boolean"}}}, lambda a: rpc("demand", {"factors": a.get("factors", True)})),
    ("cs2_service_coverage", "City services from the game's own numbers (mod 0.15): per district the resident-weighted "
     "coverage and share of residents under-served for healthcare / police / education / garbage (also fire, park, post, "
     "welfare): crime and garbage over the game's happiness thresholds, children / teens not in school, sick; the facilities "
     "(schools per level, hospitals, police, garbage) with load/capacity and efficiency; and hotspots = clusters of grid_m cells "
     "where many residents are under-served, with a centre where a new facility would help most. thresholds overrides. "
     "Mod 0.27: services=['deathcare'] = bodies waiting for a hearse per district (the icon sits on the citizen, so "
     "building warnings miss it) + hotspots, and crematoria/cemeteries by type with load/capacity, hearses (out), processing "
     "per month and how many are full (a full one stops sending hearses); health_census = city totals of dead waiting, "
     "dead collected, sick, injured, waiting for an ambulance.",
     {"type": "object", "properties": dict({"districts": {"type": "array", "items": {"type": "string"}, "description": "district names (default: all)"}, "polygon": {"type": "array", "items": XZ}, "xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"}, "zmax": {"type": "number"}},
                                           services={"type": "array", "items": {"type": "string"}}, thresholds={"type": "object"},
                                           grid_m={"type": "number"}, min_residents={"type": "integer"}, hotspots={"type": "integer"})},
     lambda a: tools6.service_report(rpc, a)),
    ("cs2_transit_report", "Transit usage (mod 0.15): every passenger line ranked by riders with load (riders / capacity, as in "
     "the line panel), vehicles, waiting passengers and its busiest stops, averaged over `samples` taken interval_s apart; "
     "overcrowded (>=90%) and near-empty (<=10%) lines; the busiest stops (most passengers waiting); stops without lines; "
     "stops and waiting per district. Area filter only affects the district labels.",
     {"type": "object", "properties": dict({"districts": {"type": "array", "items": {"type": "string"}, "description": "district names (default: all)"}, "polygon": {"type": "array", "items": XZ}, "xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"}, "zmax": {"type": "number"}}, samples={"type": "integer"}, interval_s={"type": "number"}, top={"type": "integer"},
                                           lines={"type": "integer"}, include_cargo={"type": "boolean"})},
     lambda a: tools7.transit_report(rpc, a)),
    ("cs2_foot_traffic", "Where people walk and cars drive (mod 0.15): pedestrians / cars / transit vehicles counted on a grid_m "
     "grid (default 100), averaged over samples; busiest walking cells (with distance to the nearest stop), busiest driving "
     "cells, busy walking cells farther than far_from_stop_m (400) from any stop, pedestrian / car totals + walk share per district.",
     {"type": "object", "properties": dict({"districts": {"type": "array", "items": {"type": "string"}, "description": "district names (default: all)"}, "polygon": {"type": "array", "items": XZ}, "xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"}, "zmax": {"type": "number"}}, grid_m={"type": "number"}, samples={"type": "integer"}, interval_s={"type": "number"},
                                           top={"type": "integer"}, far_from_stop_m={"type": "number"})},
     lambda a: tools7.foot_traffic(rpc, a)),
    ("cs2_commute_flows", "Where people travel (mod 0.15): every worker's home -> workplace (+ student -> school) on a grid_m "
     "grid (400): district -> district pairs, trips staying inside each district, and the biggest desire lines longer than "
     "min_distance_m (1500), each marked served_by_line if one line stops within walk_m (450) of both ends - the unserved "
     "big ones are where a new line / station pays off, and big district pairs show which corridors need road or rail capacity.",
     {"type": "object", "properties": dict({"districts": {"type": "array", "items": {"type": "string"}, "description": "district names (default: all)"}, "polygon": {"type": "array", "items": XZ}, "xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"}, "zmax": {"type": "number"}}, grid_m={"type": "number"}, min_distance_m={"type": "number"}, walk_m={"type": "number"},
                                           top={"type": "integer"}, pairs={"type": "integer"}, students={"type": "boolean"})},
     lambda a: tools7.commute_flows(rpc, a)),
    ("cs2_station_sites", "Where to put stations for a mode (Bus / Tram / Subway / Train / Ferry): demand = homes + jobs/schools "
     "(commute data) + pedestrians x walk_weight, in grid_m cells farther than the mode's catchment (radius_m; Bus 350, Tram "
     "450, Subway 700, Train 900) from existing stops of that mode (any_mode: of any mode); uncovered clusters ranked by demand "
     "with greedy catchment-sized suggested sites (snap to a road). Area = districts / polygon / box.",
     {"type": "object", "properties": dict({"districts": {"type": "array", "items": {"type": "string"}, "description": "district names (default: all)"}, "polygon": {"type": "array", "items": XZ}, "xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"}, "zmax": {"type": "number"}}, mode={"type": "string"}, radius_m={"type": "number"}, grid_m={"type": "number"},
                                           any_mode={"type": "boolean"}, walking={"type": "boolean"}, walk_weight={"type": "number"},
                                           min_demand={"type": "number"}, sites_per_gap={"type": "integer"}, top={"type": "integer"})},
     lambda a: tools7.station_sites(rpc, a)),
    ("cs2_list_saves", "Saved games (mod 0.17), newest first: save name, city, modified time, population, autosave flag.",
     {"type": "object", "properties": {"limit": {"type": "integer"}}},
     lambda a: rpc("list_saves", {k: a[k] for k in ("limit",) if k in a})),
    ("cs2_load_save", "Load a saved game by save name (or city name) like the menu's Load button (mod 0.17); works from the "
     "main menu or in game (unsaved progress is lost). wait (default true) polls until the city is loaded.",
     {"type": "object", "properties": {"name": {"type": "string"}, "wait": {"type": "boolean"}}, "required": ["name"]},
     lambda a: __import__("gamesession").load(rpc, a["name"], a.get("wait", True))),
    ("cs2_mesh_materials", "How a prefab's meshes are shaded (mod 0.18, read-only): per render mesh, each material's template "
     "+ shader, float / colour / vector properties and texture slots - e.g. to copy how a vanilla building does its glass.",
     {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
     lambda a: rpc("mesh_materials", {"name": a["name"]})),
    ("cs2_texture_export", "Save a vanilla prefab's textures as PNGs (mod 0.32, read-only for the game): surfaces / lots "
     "(RenderedArea base colour / normal / mask maps + its material's textures) and render-mesh material slots - e.g. reuse "
     "the game's own grass (\"Grass Surface 01\") on a custom mesh where terrain-only surfaces can't render. slot = "
     "substring filter; out_dir = folder for the PNGs.",
     {"type": "object", "properties": {"name": {"type": "string"}, "out_dir": {"type": "string"}, "slot": {"type": "string"}},
      "required": ["name", "out_dir"]},
     lambda a: rpc("texture_export", {k: a[k] for k in ("name", "out_dir", "slot") if k in a})),
    ("cs2_mesh_export", "Export a prefab's render meshes, every LOD, as OBJ + JSON sidecar files for Blender (mod 0.37, "
     "read-only for the game) - e.g. put stock vehicles (SUV, van, sedan, tram) next to our own models and compare geometry. "
     "name = an object prefab (vehicle / building / prop: all its ObjectGeometryPrefab meshes) or a render mesh prefab "
     "(\"... Mesh\"). Writes <out_dir>\\<mesh>_LOD<n>.obj/.mtl/.json + <prefab>_mesh_export.json (manifest with each mesh's "
     "placement in the object). OBJ: metres, RAW game local frame (left-handed, +x right, +y up, +z forward), normals, UV0, "
     "one g/usemtl per submesh; vertex order 1:1 with the sidecar. Sidecar: counts, bounds, submesh->material, bones "
     "(name, parent, BoneType, bind pose, mesh-space position), per-vertex bone indices/weights + per-bone dominant-vertex "
     "bounds (finds wheels / bogies). Data comes from the mesh's .Geometry file (works even when the mesh is not loaded). "
     "lods = LOD numbers to export (0 = full detail; default all). Load into Blender with tools\\import_mesh_export.py "
     "(converts to Z-up, facing -Y).",
     {"type": "object", "properties": {"name": {"type": "string"}, "out_dir": {"type": "string"},
                                       "lods": {"type": "array", "items": {"type": "integer"}}},
      "required": ["name", "out_dir"]},
     lambda a: rpc("mesh_export", {**{k: a[k] for k in ("name", "out_dir", "lods") if k in a}, "timeout_s": 240}, timeout=300)),
    ("cs2_prefab_entity", "Read / write a prefab's runtime ECS component data (mod 0.24) - the data game systems actually "
     "use, e.g. CarData.m_SizeClass / m_EnergyType that vehicle selection filters on (vanilla prefab edits via "
     "cs2_asset_configure are NOT saved and don't reach live entity data). component = ECS struct name (CarData, "
     "TransportDepotData, ...). set = {field: value} (enum names ok). persist (default true when set) stores it in "
     "ModsData\\CS2MCP\\prefab_overrides.json and the mod re-applies it every game start; remove=true drops a stored override.",
     {"type": "object", "properties": {"name": {"type": "string"}, "component": {"type": "string"},
                                       "set": {"type": "object"}, "persist": {"type": "boolean"}, "remove": {"type": "boolean"}},
      "required": ["name", "component"]},
     lambda a: rpc("prefab_entity", {k: a[k] for k in ("name", "component", "set", "persist", "remove") if k in a})),
    ("cs2_prefab_overrides", "List the persisted prefab entity overrides the mod re-applies at every game start (mod 0.24).",
     {"type": "object", "properties": {}}, lambda a: rpc("prefab_overrides", {})),
    ("cs2_prefab_create", "Create a new EMPTY user prefab of a Game.Prefabs type (mod 0.38), saved to the user asset database "
     "(loads at every game start, like an imported asset) and registered live - e.g. type=BrandPrefab for a new company "
     "brand. Fill it with cs2_asset_configure: set {\"m_Companies\": [company prefab names], \"m_BrandColors\": "
     "[[r,g,b,a] x3 linear]}, icon = logo PNG (the company logo in info panels), title = the brand name shown in game.",
     {"type": "object", "properties": {"type": {"type": "string"}, "name": {"type": "string"}, "folder": {"type": "string"}},
      "required": ["type", "name"]},
     lambda a: rpc("prefab_create", {k: a[k] for k in ("type", "name", "folder") if k in a})),
    ("cs2_brand_info", "Company brands (mod 0.38): BrandPrefabs with their CompanyPrefabs, and CompanyPrefabs (zone, output "
     "resource, brand count) whose name matches filter. A company draws its brand at random from its company prefab's brands.",
     {"type": "object", "properties": {"filter": {"type": "string"}}}, lambda a: rpc("brand_info", {"filter": a.get("filter", "")})),
    ("cs2_brand_bind", "Persist 'every company renting this building prefab carries this brand' (mod 0.38; "
     "ModsData\\CS2MCP\\brand_bindings.json): a pump re-stamps CompanyData.m_Brand of the renters every ~256 frames in a "
     "loaded city, so a signature shop always shows its own brand name + logo. remove=true drops the binding.",
     {"type": "object", "properties": {"building": {"type": "string"}, "brand": {"type": "string"}, "remove": {"type": "boolean"}},
      "required": ["building", "brand"]},
     lambda a: rpc("brand_bind", {k: a[k] for k in ("building", "brand", "remove") if k in a})),
    ("cs2_brand_bindings", "List the persisted building -> brand bindings (mod 0.38).",
     {"type": "object", "properties": {}}, lambda a: rpc("brand_bindings", {})),
    ("cs2_main_menu","Return to the main menu (mod 0.17; unsaved progress is lost) - e.g. to re-import an asset whose geometry "
     "the loaded city holds. Waits for the city to settle first (a menu request during post-load settling hung the game).",
     {"type": "object", "properties": {}}, lambda a: __import__("gamesession").main_menu(rpc)),
    ("cs2_restart_game", "Full game restart loop (mod 0.17 + process control): quit_game, wait for the process to exit, "
     "force-kill it if it hangs on exit (known game shutdown hang), relaunch via Steam, wait for the main menu, then "
     "optionally load `save`. Needed to load new mod builds or to reload prefab entities (sub-areas) after cs2_asset_configure. "
     "ONLY with the user's go-ahead: it closes the running game (unsaved progress is lost).",
     {"type": "object", "properties": {"save": {"type": "string", "description": "save to load after the main menu (e.g. the test city)"}}},
     lambda a: __import__("gamesession").restart(rpc, a.get("save"))),
    ("cs2_service_budget", "Service budgets (mod 0.16), like the Budget panel: every city service's budget slider (%), the "
     "building efficiency that budget gives (the game's curve), estimated upkeep, and the city's monthly balance / income / "
     "expenses. set = {service name: percent 50..150} moves sliders (e.g. {\"Education\": 100}); preview = percentages to see "
     "the efficiency each would give without changing anything. cs2_service_coverage's efficiency_factors show when "
     "ServiceBudget is what holds a service back.",
     {"type": "object", "properties": {"set": {"type": "object"}, "preview": {"type": "array", "items": {"type": "number"}}}},
     lambda a: rpc("service_budget", {k: a[k] for k in ("set", "preview") if k in a})),
    ("cs2_attractiveness", "Park 'niceness' (mod 0.16) - the game's attractiveness exactly as the building info panel shows it: "
     "base x efficiency x maintenance step x (1 + terrain bonus: forest + beach/shore + height). mode 'buildings' (default): "
     "every park / landmark in the area with its factors, and the ones held back by low maintenance or efficiency. mode "
     "'spots': the terrain bonus on a step grid (default 50 m) in the area, best first - dry land only, shore_m keeps spots "
     "within that distance of water; with type, the attractiveness that park would have at each spot.",
     {"type": "object", "properties": dict({"districts": {"type": "array", "items": {"type": "string"}, "description": "district names (default: all)"}, "polygon": {"type": "array", "items": XZ}, "xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"}, "zmax": {"type": "number"}},
                                           mode={"type": "string", "enum": ["buildings", "spots"]}, type={"type": "string"}, step={"type": "number"},
                                           shore_m={"type": "number"}, dry_only={"type": "boolean"}, top={"type": "integer"})},
     lambda a: tools8.attractiveness(rpc, a)),
    ("cs2_park_sites", "Where a park fits (mod 0.16): lots of `type` (or lot_m [width along road, depth]) along the at-grade "
     "roads in an area, front touching the road and facing it, every 4 m of the lot on free, dry, not-steep ground (trees "
     "get cleared; allow adds more cell kinds). Ranked by terrain attractiveness bonus + residents within 400 m with park "
     "coverage below low_park_coverage (residents_weight), non-overlapping (spacing_m). Each site's x, z, facing go straight "
     "into cs2_place_building. road_types limits which roads to front on. With type (and verify, default true) every returned "
     "site has been dry-run through place_building, so the game accepts it; refused spots are replaced by the next best.",
     {"type": "object", "properties": dict({"districts": {"type": "array", "items": {"type": "string"}, "description": "district names (default: all)"}, "polygon": {"type": "array", "items": XZ}, "xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"}, "zmax": {"type": "number"}},
                                           type={"type": "string"}, lot_m={"type": "array", "items": {"type": "number"}}, road_types={"type": "array", "items": {"type": "string"}},
                                           step_m={"type": "number"}, spacing_m={"type": "number"}, top={"type": "integer"}, coverage={"type": "boolean"},
                                           low_park_coverage={"type": "number"}, residents_weight={"type": "number"}, allow={"type": "string"}, grid_m={"type": "number"},
                                           verify={"type": "boolean"}, allow_subnets={"type": "boolean", "description": "mod 0.33: needed to dry-run buildings with built-in nets (save first)"})},
     lambda a: tools8.park_sites(rpc, a)),
    ("cs2_service_sites", "Where the next service building should go (v0.27): free lots for `type` along at-grade roads (flat, "
     "dry, free), ranked by the demand `service` has within radius_m - healthcare / police / fire / education / park / post / "
     "welfare: residents under-served (cs2_service_coverage thresholds); garbage: residents over the baseline; education: "
     "children + teens not in school; deathcare: bodies waiting for a hearse (mod 0.27; older mods: residents farther than "
     "radius_m from any deathcare building). Greedy picks: each takes the most demand, then what it covers is discounted "
     "(served_share, default 1) so the next pick goes to another gap. Keeps min_gap_m (300) from existing facilities of the "
     "same kind / type. Every site is dry-run through place_building first (verify, default true), so the game accepts it. "
     "Returns x, z, facing for cs2_place_building + demand covered and covered_pct.",
     {"type": "object", "properties": dict({"districts": {"type": "array", "items": {"type": "string"}, "description": "district names (default: all)"}, "polygon": {"type": "array", "items": XZ}, "xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"}, "zmax": {"type": "number"}},
                                           type={"type": "string"}, service={"type": "string"}, top={"type": "integer"}, radius_m={"type": "number"},
                                           min_gap_m={"type": "number"}, served_share={"type": "number"}, verify={"type": "boolean"},
                                           avoid_type={"type": "boolean"}, thresholds={"type": "object"}, lot_m={"type": "array", "items": {"type": "number"}},
                                           road_types={"type": "array", "items": {"type": "string"}}, step_m={"type": "number"}, spacing_m={"type": "number"},
                                           allow={"type": "string"}, grid_m={"type": "number"}, max_tries={"type": "integer"},
                                           allow_subnets={"type": "boolean", "description": "mod 0.33: needed for types with built-in nets, e.g. FR_Crematorium (the verify dry-runs are refused without it; save first)"}),
      "required": ["type"]},
     lambda a: tools9.service_sites(rpc, a)),
    ("cs2_sites_tiled", "Service / park sites over a WHOLE island or many districts (2026-10-08): cs2_service_sites over a big "
     "area coarsens its occupancy grid and finds ~0 lots; this scans <=560 m tiles at a 4 m grid, then picks greedily over the "
     "whole area. Same args as cs2_service_sites plus: students children|teens + capacity (school seats subtracted from the "
     "nearest demand - without it one school 'covers' a whole radius), spread_m (+ within polygon) = space facilities evenly "
     "where nobody lives yet, place_now = build each pick for real and fall to the next-best lot when refused (no dry-run "
     "flicker), cands_file = cache of free lots (Windows path). Vanilla police / clinics / parks need allow_subnets.",
     {"type": "object", "properties": {"districts": {"type": "array", "items": {"type": "string"}}, "polygon": {"type": "array", "items": XZ},
                                       "xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"}, "zmax": {"type": "number"},
                                       "type": {"type": "string"}, "service": {"type": "string"}, "top": {"type": "integer"}, "radius_m": {"type": "number"},
                                       "min_gap_m": {"type": "number"}, "served_share": {"type": "number"}, "students": {"type": "string"},
                                       "capacity": {"type": "number"}, "spread_m": {"type": "number"}, "within": {"type": "array", "items": XZ},
                                       "place_now": {"type": "boolean"}, "verify": {"type": "boolean"}, "allow_subnets": {"type": "boolean"},
                                       "tile_m": {"type": "number"}, "cands_file": {"type": "string"}},
      "required": ["type"]},
     lambda a: __import__("sites_tiled").run(a)),
    ("cs2_waterfront_backs", "Lots that turn their BACK on the water (2026-10-08): zone blocks whose back edge runs into water "
     "within probe_m (40) with no street behind (a road within road_m (12) of the probe line keeps them). Dezones those "
     "columns front to back and bulldozes the growables on them that are below keep_level (default 2 = only fresh level-1 "
     "spawns; established buildings keep their cells zoned so they are not condemned). dry_run counts blocks / cells / "
     "buildings per district. Where: districts / polygon / box.",
     {"type": "object", "properties": {"districts": {"type": "array", "items": {"type": "string"}}, "polygon": {"type": "array", "items": XZ},
                                       "xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"}, "zmax": {"type": "number"},
                                       "probe_m": {"type": "number"}, "road_m": {"type": "number"}, "keep_level": {"type": "integer"},
                                       "bulldoze": {"type": "boolean"}, "dry_run": {"type": "boolean"}}},
     lambda a: tools15.waterfront_backs(rpc, a)),
    ("cs2_upgrade_all", "Install every fitting service upgrade on many buildings at once (2026-10-08): types (parent building "
     "types, e.g. ['ElementarySchool03','FR_HighSchool']) or ids, optional upgrades (names; default = every build-menu entry "
     "named '<type> ...' / '<type>_Ext...'), districts / box. Lot upgrades (playgrounds, wings, halls) get a free spot beside "
     "the parent like cs2_place_building parent= (in-place extensions - BuildingExtensionData.m_External false: extra floors, "
     "school halls, playgrounds - go ON the parent; placed beside it they are loose lots without pedestrian access); ones "
     "already installed are skipped. Returns counts per upgrade (ok / "
     "refused / already / error) + the problems. dry_run previews only.",
     {"type": "object", "properties": {"types": {"type": "array", "items": {"type": "string"}}, "ids": {"type": "array", "items": {"type": "string"}},
                                       "upgrades": {"type": "array", "items": {"type": "string"}}, "districts": {"type": "array", "items": {"type": "string"}},
                                       "xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"}, "zmax": {"type": "number"},
                                       "capacity_only": {"type": "boolean", "description": "only upgrades that add capacity (school seats, beds, hearses, storage, patrol cars, cells, garbage trucks)"},
                                       "dry_run": {"type": "boolean"}}},
     lambda a: tools15.upgrade_all(rpc, a, t_place_building)),
    ("cs2_fix_access", "Fix buildings with access warnings (2026-10-08): growables with icons (default ['No Pedestrian Access'];"
     " also 'No Road Access' / 'No Car Access') are bulldozed so the lot regrows (zoning kept); service / signature buildings "
     "with the warning are only listed - the usual cause is an extension placed beside instead of on the parent: rebuild "
     "in place, then cs2_upgrade_all capacity_only. Where: districts / polygon / box (default whole city). dry_run lists.",
     {"type": "object", "properties": {"icons": {"type": "array", "items": {"type": "string"}}, "districts": {"type": "array", "items": {"type": "string"}},
                                       "polygon": {"type": "array", "items": XZ}, "xmin": {"type": "number"}, "zmin": {"type": "number"},
                                       "xmax": {"type": "number"}, "zmax": {"type": "number"}, "dry_run": {"type": "boolean"}}},
     lambda a: tools15.fix_access(rpc, a)),
    ("cs2_missing_assets", "Leftovers of uninstalled mods (mod 0.20) - why the 'subscribe to the missing mods' popup shows on "
     "every load: the game keeps assets from mods that are gone as obsolete placeholders and saves them again while anything "
     "uses them. Lists each obsolete asset with what uses it (buildings, building extensions, vehicles, other; id, position, "
     "owner) and the transit lines whose vehicle choice points at one. clear_vehicle_models = reset those lines to automatic "
     "vehicle choice; delete_vehicles = remove vehicles built from missing models (depots send new ones). Remove buildings / "
     "extensions with cs2_bulldoze by id (and place a replacement). After cleanup, save + reload: the popup should be gone.",
     {"type": "object", "properties": {"clear_vehicle_models": {"type": "boolean"}, "delete_vehicles": {"type": "boolean"}, "limit": {"type": "integer"}}},
     lambda a: rpc("obsolete_prefabs", {k: a[k] for k in ("clear_vehicle_models", "delete_vehicles", "limit") if k in a})),
    ("cs2_menu_assets", "What the build menus offer (mod 0.20): every asset with a menu entry whose menu group (e.g. 'Train', "
     "'Road', 'Park') or name matches, with has_icon (false = the blank-icon entries), source (base game / 'mod <id>' / local "
     "folder), file and in_city (objects using it now), plus counts per source - to find which mod fills a menu with "
     "unused, icon-less entries.",
     {"type": "object", "properties": {"group": {"type": "string"}, "filter": {"type": "string"}, "no_icon_only": {"type": "boolean"},
                                       "count_use": {"type": "boolean"}, "limit": {"type": "integer"}}},
     lambda a: rpc("menu_assets", {k: a[k] for k in ("group", "filter", "no_icon_only", "count_use", "limit") if k in a})),
    ("cs2_list_object_types", "Placeable trees and props (mod 0.16) - non-building static objects - by name filter; kind "
     "'tree' | 'prop' | 'all'; size_m = [width, depth, height]. Parks / plazas are buildings: cs2_list_building_types.",
     {"type": "object", "properties": {"filter": {"type": "string"}, "kind": {"type": "string"}, "limit": {"type": "integer"}}},
     lambda a: rpc("list_object_types", {k: a[k] for k in ("filter", "kind", "limit") if k in a})),
    ("cs2_place_objects", "Place many parks / trees / props / small buildings in one previewed, undoable operation (mod 0.16). "
     "items = [{type, x, z, angle_deg | facing [dx,dz]}], and/or generate them: along = {shore: {from, to} (follows the "
     "waterline) | road_ids | knots, spacing_m, offset_m (shore: + = inland; road: sideways), side left|right|both, facing "
     "'water'|'inland'|'along'|'road'|'away'|angle, start_m, end_m}; scatter = {polygon, spacing_m} (staggered, random "
     "rotation). types = list (cycled, or random_types with seed), jitter_m. Spots on water, roads (+clearance_m) or "
     "buildings are skipped (avoid false to keep them). plan_only lists the items; dry_run previews in game. Buildings with "
     "built-in roads/tracks must use cs2_place_building.",
     {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object"}}, "along": {"type": "object"}, "scatter": {"type": "object"},
                                       "type": {"type": "string"}, "types": {"type": "array", "items": {"type": "string"}}, "random_types": {"type": "boolean"},
                                       "seed": {"type": "integer"}, "jitter_m": {"type": "number"}, "clearance_m": {"type": "number"}, "avoid": {"type": "boolean"},
                                       "plan_only": {"type": "boolean"}, "dry_run": {"type": "boolean"}, "allow_errors": {"type": "boolean"},
                                       "batch": {"type": "integer"}, "show": {"type": "integer"}}},
     lambda a: tools8.place_objects(rpc, a)),
    ("cs2_road_snapshot", "Save every road piece in an area (box / center+size / polygon; types filter, default all roads - no "
     "utilities or tracks; or ids) with exact curves, heights and junction points to server/snapshots/roads_<label>.json. "
     "Take one BEFORE removing roads to rebuild them; restore with cs2_road_restore (not undo - undo can bring pieces back "
     "unconnected).",
     {"type": "object", "properties": {"label": {"type": "string"}, "xmin": {"type": "number"}, "zmin": {"type": "number"}, "xmax": {"type": "number"},
                                       "zmax": {"type": "number"}, "center": XZ, "size": {"type": "number"}, "polygon": {"type": "array", "items": XZ},
                                       "types": {"type": "array", "items": {"type": "string"}}, "ids": {"type": "array", "items": {"type": "string"}}}},
     lambda a: tools13.road_snapshot(rpc, a)),
    ("cs2_road_restore", "Rebuild a cs2_road_snapshot exactly: clears the roads now in its area (same filter; keep_ids never "
     "cleared) and places the saved pieces with every end snapped to its junction's shared point, so they reconnect to each "
     "other and to the roads around them. Pauses the sim while it works. dry_run = counts only.",
     {"type": "object", "properties": {"label": {"type": "string"}, "dry_run": {"type": "boolean"}, "keep_ids": {"type": "array", "items": {"type": "string"}},
                                       "pause": {"type": "boolean"}, "allow_errors": {"type": "boolean"}}, "required": ["label"]},
     lambda a: tools13.road_restore(rpc, a)),
    ("cs2_road_profile", "Side view of a route - existing road_ids chained in order, or draft segments (place_roads shape): "
     "height above ground along it, max grade, every road it crosses with that road's height and the vertical clearance "
     "(problem when < min_clearance_m, default 5.5), dips (an elevated route coming down to the ground and climbing again) "
     "and a chart PNG. Use before building anything that must thread over or under other roads.",
     {"type": "object", "properties": {"road_ids": {"type": "array", "items": {"type": "string"}}, "segments": {"type": "array", "items": {"type": "object"}},
                                       "min_clearance_m": {"type": "number"}, "box": {"type": "object"}}},
     lambda a: tools13.road_profile(rpc, a)),
    ("cs2_node_detail", "One junction in detail (mod 0.40): id or the nearest node to at [x,z] (radius): every road arm (type, "
     "which end, heading, elevation, car lanes in / out) and every car movement through the node (from arm -> to arm, lanes, "
     "turn flags), plus issues: highway joined to a local road at grade, u-turns on a highway, traps (lanes arrive, nothing "
     "leaves), sources, head-on / head-to-head nodes.",
     {"type": "object", "properties": {"id": {"type": "string"}, "at": XZ, "radius": {"type": "number"}}},
     lambda a: tools13.node_detail(rpc, a)),
    ("cs2_highway_fork", "Proper highway Y (one two-way trunk -> two two-way branches) as the game's own interchanges do it: the "
     "trunk separates into one-way carriageways, the outbound one diverges into a leg per branch, the inbound one is fed by a "
     "merge, every leg rejoins its branch's end node, and the one crossing (outbound-left x inbound-right) is a flyover. "
     "trunk / branches = {edge, at [x,z] near the end that faces the junction}; split / merge [x,z]; deck_m (viaduct height), "
     "sep_m, diverge_deg, flyover_m, piece_m, design_speed_kmh. Checks: join_check + profiles of the crossing legs. "
     "plan_only = geometry only; default = dry run in game; build=true builds. Removes nothing - clear the old junction "
     "first (cs2_road_snapshot, bulldoze; cs2_road_restore to go back).",
     {"type": "object", "properties": {"trunk": {"type": "object"}, "branches": {"type": "array", "items": {"type": "object"}},
                                       "split": XZ, "merge": XZ, "deck_m": {"type": "number"}, "sep_m": {"type": "number"},
                                       "diverge_deg": {"type": "number"}, "flyover_m": {"type": "number"}, "piece_m": {"type": "number"},
                                       "design_speed_kmh": {"type": "number"}, "plan_only": {"type": "boolean"}, "build": {"type": "boolean"}},
      "required": ["trunk", "branches", "split"]},
     lambda a: tools14.highway_fork(rpc, a)),
    ("cs2_raw", "Call a bridge method directly (debugging): ping, city_info, camera, camera_goto, set_speed, terrain_height, list_road_types, list_zone_types, query_roads, query_buildings, place_roads, bulldoze, replace_roads, zone, undo, history, screenshot, road_network, zone_blocks, terrain_grid, preview_commit, preview_cancel.",
     {"type": "object", "properties": {"method": {"type": "string"}, "params": {"type": "object"}}, "required": ["method"]}, t_raw),
]
HANDLERS = {name: fn for name, _, _, fn in TOOLS}


# ----------------------------------------------------------------------------- MCP stdio loop
import threading, importlib, importlib.util, glob
_OUT = threading.Lock()


def send(msg):
    with _OUT:
        sys.stdout.write(json.dumps(msg) + "\n"); sys.stdout.flush()


# hot reload: when any server/*.py changes, reload the helper modules + this file's tool table and tell the client the
# tool list changed - new tools become callable in the running session, no new Claude Code session needed.
_HERE = os.path.dirname(os.path.abspath(__file__))
_HELPERS = ("geom", "survey", "tools2", "tools3", "tools4", "tools5", "tools6", "tools7", "tools8", "tools9", "tools10", "tools11", "tools13", "tools14", "tools15", "sites_tiled", "zoning", "assets")


def _mtimes():
    return {f: os.path.getmtime(f) for f in glob.glob(os.path.join(_HERE, "*.py"))}


def _reload():
    global TOOLS, HANDLERS
    mine = [n for n, m in list(sys.modules.items())                 # every module loaded from this folder, helpers first
            if getattr(m, "__file__", None) and os.path.dirname(os.path.abspath(m.__file__)) == _HERE and n not in ("__main__", "cs2_mcp", "cs2_mcp_live")]
    for m in sorted(mine, key=lambda n: _HELPERS.index(n) if n in _HELPERS else -1):
        importlib.reload(sys.modules[m])
    spec = importlib.util.spec_from_file_location("cs2_mcp_live", os.path.join(_HERE, "cs2_mcp.py"))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    TOOLS, HANDLERS = mod.TOOLS, mod.HANDLERS


def _watch():
    seen = _mtimes()
    while True:
        time.sleep(2)
        try:
            now = _mtimes()
            if now != seen:
                time.sleep(0.5); now = _mtimes()        # let a multi-file save finish
                _reload(); seen = now
                send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
                sys.stderr.write(f"cs2 server: reloaded, {len(TOOLS)} tools\n"); sys.stderr.flush()
        except Exception as e:                         # half-written / broken file: keep the old tools, retry on the next change
            sys.stderr.write(f"cs2 server: reload failed: {e}\n"); sys.stderr.flush(); seen = _mtimes()


def handle(msg):
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", PROTOCOL), "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": "cs2", "version": "0.15.0"}}
    if method == "ping": return {}
    if method == "tools/list":
        return {"tools": [{"name": n, "description": d, "inputSchema": s} for n, d, s, _ in TOOLS]}
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in HANDLERS: raise KeyError(f"unknown tool {name}")
        try:
            out = HANDLERS[name](args)
            if isinstance(out, dict) and "__mcp_content__" in out: return {"content": out["__mcp_content__"]}
            return {"content": [{"type": "text", "text": json.dumps(out, indent=1)}]}
        except (BridgeError, KeyError, ValueError, TypeError) as e:
            return {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True}
    raise NotImplementedError(method)


def main():
    threading.Thread(target=_watch, daemon=True).start()
    for line in sys.stdin:
        line = line.strip()
        if not line: continue
        try: msg = json.loads(line)
        except json.JSONDecodeError: continue
        if "id" not in msg: continue                  # notifications (initialized, cancelled) need no reply
        try: send({"jsonrpc": "2.0", "id": msg["id"], "result": handle(msg)})
        except Exception as e: send({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32603, "message": str(e)}})


if __name__ == "__main__":
    main()
