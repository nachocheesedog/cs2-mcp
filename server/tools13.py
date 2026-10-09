"""Precision road tools (2026-09-28, after a highway rebuild went wrong: the game's undo restored bulldozed highway pieces
visually but NOT connected, 8 of 9 movements broke). For precision work: threading roads through each other and making sure
conflicting directions never connect.

- road_snapshot / road_restore: save every road piece in an area (curves with heights, types, node ids) to
  server/snapshots/roads_<label>.json, and rebuild exactly that state later: current pieces in the area are cleared and the
  saved ones placed with every end snapped to its junction's true shared point (mean of all edge ends at the node), so
  pieces reconnect to each other and to the untouched roads around them. Use it INSTEAD of undo for anything that removes
  roads before rebuilding (undo can leave pieces unconnected).
- road_profile: side view of one route (existing road ids chained, or draft segments): its height against the terrain and
  every road it crosses (their height at the crossing, vertical clearance), dips where an elevated road drops to the ground
  and climbs again, and a PNG chart.
- join_check: every end of a draft that lands on an existing node - highway joined to a local road at grade, one-way roads
  meeting head-on (all flow in) or head-to-head (all flow out), a two-way road joined to a one-way end. place_roads runs it
  on every dry run and refuses a build with conflicts unless allow_conflicts.
- node_detail (mod 0.40): lane-level movements through one junction.
"""
import json, math, os, time, collections
import tools2

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP_DIR = os.path.join(HERE, "snapshots")
SKIP = ("Pipe", "voltage", "Cable", "Invisible", "Pedestrian", "Path", "Track", "Train", "Tram", "Subway", "Seaway", "Bridge - Train")


def _box(a, pad=0.0):
    if a.get("polygon"):
        xs = [p[0] for p in a["polygon"]]; zs = [p[1] for p in a["polygon"]]
        return {"xmin": min(xs) - pad, "xmax": max(xs) + pad, "zmin": min(zs) - pad, "zmax": max(zs) + pad}
    if a.get("center"):
        c = a["center"]; h = a.get("size", 400) / 2
        return {"xmin": c[0] - h - pad, "xmax": c[0] + h + pad, "zmin": c[1] - h - pad, "zmax": c[1] + h + pad}
    return {k: a[k] + (-pad if k.endswith("min") else pad) for k in ("xmin", "xmax", "zmin", "zmax")}


def _inside(a, x, z):
    if a.get("polygon"): return tools2._in_poly((x, z), a["polygon"])
    b = _box(a); return b["xmin"] <= x <= b["xmax"] and b["zmin"] <= z <= b["zmax"]


def _ends(e): return e["curve"][0], e["curve"][-1]


def _want(e, a):
    t = e["type"]
    if a.get("types") and not any(k.lower() in t.lower() for k in a["types"]): return False
    if not a.get("types") and any(k in t for k in SKIP): return False
    return True


# ----------------------------------------------------------------------------- snapshot / restore
def road_snapshot(rpc, a):
    """Save the roads in an area (box / center+size / polygon; types filter; default all roads, no utilities or tracks)."""
    label = a.get("label") or time.strftime("%Y%m%d_%H%M%S")
    net = rpc("road_network", dict(_box(a, 80), limit=40000), timeout=180)["edges"]
    pieces = [e for e in net if _want(e, a) and all(_inside(a, p[0], p[2]) for p in _ends(e))]
    if a.get("ids"): pieces = [e for e in net if e["id"] in set(a["ids"])]
    ends = collections.defaultdict(list)
    for e in net:                                         # nodes: mean of ALL edge ends there (incl. neighbours outside)
        s, t = _ends(e); ends[e["start_node"]].append(s); ends[e["end_node"]].append(t)
    nodes = {n: [sum(p[i] for p in ps) / len(ps) for i in range(3)] for n, ps in ends.items()
             if any(n in (e["start_node"], e["end_node"]) for e in pieces)}
    pts = [[v[0], v[2]] for v in nodes.values()]
    gh = []
    for i in range(0, len(pts), 200):
        gh += [p[1] for p in rpc("terrain_height", {"points": pts[i:i + 200]}, timeout=120)["points"]]
    ground = {n: g for n, g in zip(nodes, gh)}
    snap = {"label": label, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "area": {k: a[k] for k in ("xmin", "xmax", "zmin", "zmax", "polygon", "center", "size", "types") if k in a},
            "pieces": pieces, "nodes": nodes, "ground": ground}
    os.makedirs(SNAP_DIR, exist_ok=True)
    path = os.path.join(SNAP_DIR, f"roads_{label}.json")
    json.dump(snap, open(path, "w"))
    return {"label": label, "path": path, "pieces": len(pieces), "nodes": len(nodes),
            "types": dict(collections.Counter(e["type"] for e in pieces))}


def _snap_segments(snap):
    segs = []
    for e in snap["pieces"]:
        c = e["curve"]; A = snap["nodes"][e["start_node"]]; B = snap["nodes"][e["end_node"]]
        da = (A[0] - c[0][0], A[2] - c[0][2]); db = (B[0] - c[-1][0], B[2] - c[-1][2])
        segs.append({"start": [A[0], A[2]], "end": [B[0], B[2]],
                     "controls": [[c[1][0] + da[0], c[1][2] + da[1]], [c[2][0] + db[0], c[2][2] + db[1]]], "type": e["type"],
                     "elevation": [round(A[1] - snap["ground"][e["start_node"]], 2), round(B[1] - snap["ground"][e["end_node"]], 2)]})
    return segs


def road_restore(rpc, a):
    """Rebuild a snapshot: clear the current roads in its area (same filter), place the saved pieces snapped to their
    junction points. dry_run = counts only (nothing cleared). keep_ids = current roads never to clear."""
    import cs2_mcp
    path = os.path.join(SNAP_DIR, f"roads_{a['label']}.json")
    snap = json.load(open(path))
    area = dict(snap["area"], types=snap["area"].get("types"))
    segs = _snap_segments(snap)
    net = rpc("road_network", dict(_box(area, 80), limit=40000), timeout=180)["edges"]
    keep = set(a.get("keep_ids") or [])
    cur = [e for e in net if e["id"] not in keep and _want(e, area) and all(_inside(area, p[0], p[2]) for p in _ends(e))]
    out = {"label": a["label"], "saved_pieces": len(segs), "current_pieces_to_clear": len(cur)}
    if a.get("dry_run"): return out
    speed = a.get("pause", True)
    if speed: rpc("set_speed", {"speed": 0})
    ids = [e["id"] for e in cur]
    for i in range(0, len(ids), 10):
        rpc("bulldoze", {"ids": ids[i:i + 10], "buildings": False, "roads": True, "timeout_s": 120}); time.sleep(3)
    out["cleared"] = len(ids)
    dry = cs2_mcp.t_place_roads({"segments": segs, "dry_run": True, "lattice": "off", "snap_tolerance": 2.0, "zoning_check": False,
                                 "follow_camera": False, "join_check": False})
    out["dry_errors"] = dry.get("errors", 0)
    res = cs2_mcp.t_place_roads({"segments": segs, "lattice": "off", "snap_tolerance": 2.0, "zoning_check": False, "follow_camera": False,
                                 "allow_errors": bool(dry.get("errors")) and a.get("allow_errors", True), "join_check": False})
    out["applied"] = bool(res.get("applied")); out["build_errors"] = res.get("errors", 0)
    if speed: rpc("set_speed", {"speed": a.get("resume_speed", 1)})
    return out


# ----------------------------------------------------------------------------- profile
def _pts(segs_or_curves, step=4.0):
    """[(x, z, y_or_None, elev_or_None)] along cubic pieces; existing curves carry y, drafts carry elevation."""
    out = []
    for s in segs_or_curves:
        if "curve" in s:
            c = s["curve"]; P = [(p[0], p[2], p[1]) for p in c]
        else:
            a, b = s["start"], s["end"]; cc = s.get("controls") or [[a[0] + (b[0] - a[0]) / 3, a[1] + (b[1] - a[1]) / 3], [a[0] + 2 * (b[0] - a[0]) / 3, a[1] + 2 * (b[1] - a[1]) / 3]]
            el = s.get("elevation") or [0, 0]
            P = [(a[0], a[1], None), (cc[0][0], cc[0][1], None), (cc[1][0], cc[1][1], None), (b[0], b[1], None)]
        L = sum(math.dist(P[i][:2], P[i + 1][:2]) for i in range(3)); n = max(2, int(L / step))
        for k in range(n + (1 if s is segs_or_curves[-1] else 0)):
            t = k / n
            x = sum(w * p[0] for w, p in zip(((1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3), P))
            z = sum(w * p[1] for w, p in zip(((1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3), P))
            if "curve" in s:
                y = sum(w * p[2] for w, p in zip(((1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3), P)); out.append((x, z, y, None))
            else:
                out.append((x, z, None, el[0] + (el[1] - el[0]) * t))
    return out


def _seg_x(p, q, r, s):
    d = (q[0] - p[0]) * (s[1] - r[1]) - (q[1] - p[1]) * (s[0] - r[0])
    if abs(d) < 1e-9: return None
    t = ((r[0] - p[0]) * (s[1] - r[1]) - (r[1] - p[1]) * (s[0] - r[0])) / d
    u = ((r[0] - p[0]) * (q[1] - p[1]) - (r[1] - p[1]) * (q[0] - p[0])) / d
    return (t, u) if 0 <= t <= 1 and 0 <= u <= 1 else None


def road_profile(rpc, a):
    """Side view of a route: road_ids (existing, chained in order) or segments (draft, place_roads shape). Returns samples
    (distance, road height above ground), terrain, the roads it crosses with their height and the vertical clearance,
    dips (an elevated route touching the ground between elevated stretches) and a chart PNG."""
    net = None
    if a.get("road_ids"):
        box = a.get("box")
        if not box:
            net_all = rpc("road_network", {"xmin": -20000, "xmax": 20000, "zmin": -20000, "zmax": 20000, "limit": 200000}, timeout=240)["edges"]
        else:
            net_all = rpc("road_network", dict(box, limit=40000), timeout=180)["edges"]
        byid = {e["id"]: e for e in net_all}
        route = [byid[i] for i in a["road_ids"] if i in byid]
        pts = _pts(route); net = net_all
    else:
        route = a["segments"]; pts = _pts(route)
    xs = [p[0] for p in pts]; zs = [p[1] for p in pts]
    if net is None:
        net = rpc("road_network", {"xmin": min(xs) - 60, "xmax": max(xs) + 60, "zmin": min(zs) - 60, "zmax": max(zs) + 60, "limit": 40000}, timeout=180)["edges"]
    gh = []
    for i in range(0, len(pts), 200):
        gh += [p[1] for p in rpc("terrain_height", {"points": [[p[0], p[1]] for p in pts[i:i + 200]]}, timeout=120)["points"]]
    dist = [0.0]
    for i in range(1, len(pts)): dist.append(dist[-1] + math.dist(pts[i - 1][:2], pts[i][:2]))
    if pts and pts[0][2] is None:
        # drafts: the game runs the road straight between its piece ends (height = ground at each END + elevation), it does
        # not follow the terrain in between - interpolate from the piece ends, not from the ground under every sample
        ends_ = [(sg["start"], sg["end"], (sg.get("elevation") or [0, 0])) for sg in route]
        eg = [p[1] for p in rpc("terrain_height", {"points": [list(e[0]) for e in ends_] + [list(e[1]) for e in ends_]}, timeout=120)["points"]]
        n_ = len(ends_); y = []; k = 0
        for sgi, (a_, b_, el) in enumerate(ends_):
            y0, y1 = eg[sgi] + el[0], eg[n_ + sgi] + el[1]
            L = math.dist(a_, b_) or 1.0
            while k < len(pts) and (sgi == n_ - 1 or math.dist(pts[k][:2], a_) <= math.dist(a_, b_) + 1e-6 and math.dist(pts[k][:2], b_) > 0.5):
                t = min(1.0, math.dist(pts[k][:2], a_) / L); y.append(y0 + (y1 - y0) * t); k += 1
                if k < len(pts) and math.dist(pts[k][:2], b_) < 0.5 and sgi < n_ - 1: break
        while len(y) < len(pts): y.append(y[-1] if y else gh[len(y)])
    else:
        y = [p[2] if p[2] is not None else g + p[3] for p, g in zip(pts, gh)]
    above = [yy - g for yy, g in zip(y, gh)]
    mine = {e["id"] for e in route if "id" in e}
    mynodes = {n for e in route if "id" in e for n in (e["start_node"], e["end_node"])}
    crossings = []; seen = set()
    for e in net:
        if e["id"] in mine or any(k in e["type"] for k in ("Pipe", "voltage", "Cable", "Invisible")): continue
        if e["start_node"] in mynodes or e["end_node"] in mynodes: continue      # connected at a junction, not a crossing
        ep = _pts([e], step=6.0)
        for i in range(len(pts) - 1):
            for j in range(len(ep) - 1):
                r = _seg_x(pts[i][:2], pts[i + 1][:2], ep[j][:2], ep[j + 1][:2])
                if not r: continue
                t, u = r; d = dist[i] + t * (dist[i + 1] - dist[i]); ry = y[i] + t * (y[i + 1] - y[i]); oy = ep[j][2] + u * (ep[j + 1][2] - ep[j][2])
                if d < 8 or d > dist[-1] - 8: continue           # the route's own end joining that road, not a crossing
                if (e["id"], round(d / 5)) in seen: continue
                seen.add((e["id"], round(d / 5)))
                crossings.append({"at_m": round(d, 1), "road": e["id"], "type": e["type"], "route_y": round(ry, 1), "other_y": round(oy, 1),
                                  "clearance_m": round(abs(ry - oy), 1), "route_is": "over" if ry > oy else "under",
                                  "problem": abs(ry - oy) < a.get("min_clearance_m", 5.5)})
    # dips: the route is elevated (> 4 m), comes down near the ground (< 1.5 m), then goes back up (> 4 m)
    dips, state, low = [], "?", None
    for d, h in zip(dist, above):
        if h > 4:
            if state == "down" and low is not None: dips.append({"from_m": round(low[0], 0), "to_m": round(d, 0), "lowest_m": round(low[1], 1)})
            state, low = "up", None
        elif h < 1.5 and state == "up": state, low = "down", (d, h)
        elif state == "down" and low and h < low[1]: low = (low[0], h)
    out = {"length_m": round(dist[-1], 0), "max_above_ground_m": round(max(above), 1), "min_above_ground_m": round(min(above), 1),
           "max_grade_pct": round(max((abs(y[i + 1] - y[i]) / max(dist[i + 1] - dist[i], 0.1) * 100 for i in range(len(y) - 1)), default=0), 1),
           "crossings": sorted(crossings, key=lambda c: c["at_m"]), "dips": dips,
           "samples": [[round(d, 0), round(h, 1)] for d, h in list(zip(dist, above))[:: max(1, len(dist) // 60)]]}
    try:
        from PIL import Image, ImageDraw
        W, H = 1200, 420; m = 50
        dmax = max(dist[-1], 1); ymin = min(min(gh), min(y)) - 3; ymax = max(max(y), max(gh)) + 8
        X = lambda d: m + (W - 2 * m) * d / dmax; Y = lambda v: H - m - (H - 2 * m) * (v - ymin) / (ymax - ymin)
        im = Image.new("RGB", (W, H), (250, 250, 247)); dr = ImageDraw.Draw(im)
        dr.polygon([(X(0), H - m)] + [(X(d), Y(g)) for d, g in zip(dist, gh)] + [(X(dmax), H - m)], fill=(214, 204, 180))
        dr.line([(X(d), Y(v)) for d, v in zip(dist, y)], fill=(30, 90, 220), width=4)
        for c in crossings:
            col = (220, 40, 40) if c["problem"] else (40, 40, 40)
            dr.ellipse((X(c["at_m"]) - 5, Y(c["other_y"]) - 5, X(c["at_m"]) + 5, Y(c["other_y"]) + 5), fill=col)
            dr.text((X(c["at_m"]) + 6, Y(c["other_y"]) - 14), f'{c["clearance_m"]}m', fill=col)
        for dp in dips: dr.rectangle((X(dp["from_m"]), m, X(dp["to_m"]), H - m), outline=(230, 120, 0), width=2)
        dr.text((m, 12), f"route profile  {out['length_m']} m   max grade {out['max_grade_pct']}%   blue = route, sand = ground, dots = crossing roads (red < clearance), orange = dip", fill=(20, 20, 20))
        os.makedirs(SNAP_DIR, exist_ok=True)
        pth = os.path.join(SNAP_DIR, "profile_%s.png" % time.strftime("%H%M%S")); im.save(pth); out["chart"] = pth
    except Exception as ex:
        out["chart_error"] = str(ex)
    return out


# ----------------------------------------------------------------------------- join check
def _oneway(t): return "Oneway" in t or "oneway" in t.lower()


def join_check(rpc, segs, tol=3.0):
    """Conflicts where draft ends land on existing nodes: highway <-> local road at grade, one-way head-on / head-to-head,
    and draft pieces meeting each other head-on."""
    if not segs: return {"conflicts": []}
    xs = [p for s in segs for p in (s["start"][0], s["end"][0])]; zs = [p for s in segs for p in (s["start"][1], s["end"][1])]
    net = rpc("road_network", {"xmin": min(xs) - 40, "xmax": max(xs) + 40, "zmin": min(zs) - 40, "zmax": max(zs) + 40, "limit": 20000}, timeout=120)["edges"]
    node_arms = collections.defaultdict(list)                 # node pos -> [(type, flows 'in'|'out'|'both')]
    for e in net:
        if any(k in e["type"] for k in ("Pipe", "voltage", "Cable", "Invisible")): continue
        s, t = _ends(e); ow = _oneway(e["type"])
        node_arms[e["start_node"]].append((e["type"], "out" if ow else "both", (s[0], s[2])))
        node_arms[e["end_node"]].append((e["type"], "in" if ow else "both", (t[0], t[2])))
    npos = {n: (sum(p[2][0] for p in v) / len(v), sum(p[2][1] for p in v) / len(v)) for n, v in node_arms.items()}
    # draft ends grouped with the existing node (or with each other) they land on
    groups = collections.defaultdict(list)
    for i, s in enumerate(segs):
        ow = _oneway(s.get("type", ""))
        for end, p, flow in (("start", s["start"], "out"), ("end", s["end"], "in")):
            best, bd = None, tol + 30
            for n, q in npos.items():
                d = math.dist(p, q)
                if d < bd: best, bd = n, d
            key = best if best is not None and bd <= tol + 12 else ("draft", round(p[0] / tol), round(p[1] / tol))
            groups[key].append((s.get("type", ""), flow if ow else "both", i, end))
    conflicts = []
    for key, dr in groups.items():
        arms = [(t, f) for t, f, _ in node_arms.get(key, [])] if not isinstance(key, tuple) else []
        allarms = arms + [(t, f) for t, f, _, _ in dr]
        if len(allarms) < 2: continue
        hw = [t for t, _ in allarms if "Highway" in t]; loc = [t for t, _ in allarms if "Highway" not in t and "Ramp" not in t]
        where = npos.get(key) if not isinstance(key, tuple) else segs[dr[0][2]][dr[0][3]]
        if hw and loc: conflicts.append({"conflict": "highway joins a local road at grade", "at": [round(v, 1) for v in where], "types": sorted(set(hw + loc))})
        flows = [f for _, f in allarms]
        if all(f == "in" for f in flows): conflicts.append({"conflict": "head-on: every one-way arm flows INTO this point", "at": [round(v, 1) for v in where]})
        if all(f == "out" for f in flows): conflicts.append({"conflict": "head-to-head: every one-way arm flows OUT of this point", "at": [round(v, 1) for v in where]})
        if "both" in flows and any(f in ("in", "out") for f in flows) and hw:
            conflicts.append({"conflict": "two-way highway joined to a one-way piece (check the direction)", "at": [round(v, 1) for v in where], "warn_only": True})
    return {"conflicts": conflicts, "draft_ends_on_existing_nodes": sum(1 for k in groups if not isinstance(k, tuple))}


def node_detail(rpc, a):
    p = {k: a[k] for k in ("id", "at", "radius") if k in a}
    r = rpc("node_detail", p, timeout=60)
    arms = r.get("arms") or []
    # a plain 2-arm continuation (lanes in on one arm = lanes out on the other) has no node lanes in the game: the edges'
    # lanes join directly, so the mod's trap/source flags there are false - drop them and say so
    if len(arms) == 2 and not r.get("movements") and arms[0]["car_lanes_in"] == arms[1]["car_lanes_out"] and arms[0]["car_lanes_out"] == arms[1]["car_lanes_in"]:
        r["issues"] = [i for i in r.get("issues", []) if not str(i.get("issue", "")).startswith(("trap", "source"))]
        r["note"] = "continuation node: the two roads' lanes join directly (no node lanes), traffic flows straight through"
    return r
