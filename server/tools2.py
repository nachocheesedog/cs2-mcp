"""v0.7 tools: file inputs, local frames, filtered/summary perception, traffic + zoning summaries, feasibility probes,
error explanations and geometry builders (paths, one-way pairs, street networks with junction bookkeeping).

Facts and construction mechanics only: every layout decision (where a road goes, its type, spacing, elevation) is the
caller's. Nothing here chooses a design.
"""
import json, math, os
import geom, survey

UTIL_NOTE = "exclude_utilities drops edges without traffic data: pipes, power lines and tracks"


# ----------------------------------------------------------------------------- inputs
def load_list(a, key):
    """a[key], or JSON from a[key + '_file'] (a list, or an object holding `key`)."""
    if a.get(key) is not None: return a[key]
    path = a.get(key + "_file")
    if not path: return None
    with open(os.path.expandvars(os.path.expanduser(path))) as fh: data = json.load(fh)
    if isinstance(data, dict): data = data.get(key, data.get("segments"))
    if not isinstance(data, list): raise ValueError(f"{path}: expected a JSON list (or an object with '{key}')")
    return data


class Frame:
    """Local frame: along = heading_deg (0 = +x, 90 = +z), across = 90 deg to the left of along. p = origin + a*U + b*V."""
    def __init__(self, f):
        self.o = f["origin"]; h = math.radians(f.get("heading_deg", 0.0))
        self.U = (math.cos(h), math.sin(h)); self.V = (-math.sin(h), math.cos(h))
    def to_world(self, p): return [round(self.o[0] + self.U[0] * p[0] + self.V[0] * p[1], 3), round(self.o[1] + self.U[1] * p[0] + self.V[1] * p[1], 3)]
    def to_local(self, p):
        dx, dz = p[0] - self.o[0], p[1] - self.o[1]
        return [round(dx * self.U[0] + dz * self.U[1], 2), round(dx * self.V[0] + dz * self.V[1], 2)]


def framed(a):
    return Frame(a["frame"]) if a.get("frame") else None


def pts_world(pts, fr):
    import tools3
    pts = [tools3.resolve(p, tools3.RPC) for p in pts]
    return [fr.to_world(p) for p in pts] if fr else [list(map(float, p)) for p in pts]


def t_frame(a):
    fr = Frame(a["frame"])
    out = {}
    if a.get("to_world"): out["world"] = [fr.to_world(p) for p in a["to_world"]]
    if a.get("to_local"): out["local"] = [fr.to_local(p) for p in a["to_local"]]
    return out


# ----------------------------------------------------------------------------- camera
SETTINGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")


def settings():
    """Persistent server settings (settings.json next to this file), re-read on every call so changes apply at once."""
    try:
        with open(SETTINGS, encoding="utf-8") as fh: return json.load(fh)
    except (OSError, ValueError): return {}


def fast():
    """settings.json fast_mode (v0.46): builders skip the camera move, the post-build junction check and the dry-run
    zoning forecast unless a call asks for them."""
    return bool(settings().get("fast_mode"))


ZONE_BATCH = 250


def zone_many(rpc, items, overwrite=True, dry_run=False, timeout_s=120):
    """Zone many quads: items = [(corners, zone_type)]. Mod >= 0.35 takes them as `areas` batches (one tool op per
    ZONE_BATCH quads - no per-quad tool flicker); older mods get one call per quad. Returns (ok, refused) quad counts.
    Quads in one batch may overlap only if they agree on the zone type."""
    if not items: return 0, 0
    try: ver = tuple(int(x) for x in str(rpc("ping", {}).get("mod_version", "0")).split(".")[:2])
    except Exception: ver = (0, 0)
    ok = fail = 0
    if ver >= (0, 35):
        for i in range(0, len(items), ZONE_BATCH):
            chunk = items[i:i + ZONE_BATCH]
            res = rpc("zone", {"areas": [{"corners": c, "zone_type": z} for c, z in chunk], "overwrite": overwrite,
                               "dry_run": dry_run, "timeout_s": timeout_s})
            if res.get("applied") or dry_run: ok += len(chunk)
            else: fail += len(chunk)
        return ok, fail
    for c, z in items:
        res = rpc("zone", {"corners": c, "zone_type": z, "overwrite": overwrite, "dry_run": dry_run, "timeout_s": 90})
        if res.get("applied") or dry_run: ok += 1
        else: fail += 1
    return ok, fail


def follow(rpc, a, pts, default=None):
    if default is None:
        s = settings(); default = s.get("follow_camera", True) and not s.get("fast_mode")
    if a.get("dry_run") or not a.get("follow_camera", default) or not pts: return
    xs = [p[0] for p in pts]; zs = [p[1] for p in pts]
    span = max(max(xs) - min(xs), max(zs) - min(zs))
    p = {"x": (min(xs) + max(xs)) / 2, "z": (min(zs) + max(zs)) / 2}
    if a.get("camera_zoom"): p["zoom"] = a["camera_zoom"]
    elif span > 0: p["zoom"] = max(300.0, min(4000.0, span * 1.3))
    try: rpc("camera_goto", p)
    except Exception: pass


def seg_points(segs):
    out = []
    for s in segs: out += [s["start"], s["end"]]
    return out


# ----------------------------------------------------------------------------- place_roads pre/post processing
def snap_to_nodes(rpc, segs, radius):
    """Move segment ends that are within `radius` m of an existing road node (but further than a normal snap) onto it.
    Fixes stale coordinates after the game moved a node."""
    pts = seg_points(segs)
    xs = [p[0] for p in pts]; zs = [p[1] for p in pts]
    net = rpc("road_network", {"xmin": min(xs) - radius, "zmin": min(zs) - radius, "xmax": max(xs) + radius, "zmax": max(zs) + radius, "limit": 30000})
    road_nodes = set()
    for e in net["edges"]:
        if "flow" in e: road_nodes.update((e["start_node"], e["end_node"]))
    nodes = [(n["id"], n["pos"][0], n["pos"][2]) for n in net["nodes"] if n["id"] in road_nodes]
    moved = []
    for s in segs:
        for k in ("start", "end"):
            p = s[k]; best = None
            for nid, x, z in nodes:
                d = math.dist(p, (x, z))
                if d <= radius and (best is None or d < best[0]): best = (d, nid, x, z)
            if best and best[0] > 0.05:
                if k == "start" and s.get("controls"): s["controls"][0] = [s["controls"][0][0] + best[2] - p[0], s["controls"][0][1] + best[3] - p[1]]
                if k == "end" and s.get("controls"): s["controls"][1] = [s["controls"][1][0] + best[2] - p[0], s["controls"][1][1] + best[3] - p[1]]
                s[k] = [round(best[2], 3), round(best[3], 3)]
                moved.append({"from": [round(v, 1) for v in p], "node": best[1], "moved_m": round(best[0], 1)})
    return moved


def explain_errors(rpc, res, radius=25.0, max_n=25):
    """For each error location, the nearest existing road edge and building lot: what the new road probably hit."""
    locs = (res or {}).get("error_locations") or []
    if not locs: return None
    locs = locs[:max_n]
    xs = [l["pos"][0] for l in locs]; zs = [l["pos"][1] for l in locs]
    box = {"xmin": min(xs) - radius, "zmin": min(zs) - radius, "xmax": max(xs) + radius, "zmax": max(zs) + radius}
    net = rpc("road_network", dict(box, limit=30000))
    blds = rpc("query_buildings", dict(box, limit=30000)).get("buildings", [])
    out = []
    for l in locs:
        p = l["pos"]; best_e = None
        for e in net["edges"]:
            for q in survey.polyline(e["curve"], 12):
                d = math.dist(p, q) - e.get("width", 0) / 2
                if best_e is None or d < best_e[0]: best_e = (d, e)
        best_b = None
        for b in blds:
            d = math.dist(p, (b["position"][0], b["position"][2]))
            if best_b is None or d < best_b[0]: best_b = (d, b)
        hit = {"reason": l["reason"], "pos": p}
        if best_e and best_e[0] <= radius:
            e = best_e[1]
            hit["nearest_edge"] = {"id": e["id"], "type": e["type"], "gap_m": round(max(0.0, best_e[0]), 1),
                                   "elevation": e.get("elevation"), "utility": "flow" not in e}
        if best_b and best_b[0] <= radius * 2:
            b = best_b[1]
            hit["nearest_building"] = {"id": b.get("id"), "type": b.get("type") or b.get("prefab"), "dist_m": round(best_b[0], 1), "lot": b.get("lot")}
        out.append(hit)
    return out


# ----------------------------------------------------------------------------- perception
def network_filtered(rpc, a):
    p = {k: a[k] for k in ("xmin", "zmin", "xmax", "zmax") if k in a}
    net = rpc("road_network", dict(p, limit=a.get("limit", 30000) if not a.get("summary") else 30000))
    E = net["edges"]
    if a.get("exclude_utilities", True): E = [e for e in E if "flow" in e]
    if a.get("types"):
        want = [t.lower() for t in a["types"]]
        E = [e for e in E if any(w in e["type"].lower() for w in want)]
    if a.get("min_elevation") is not None:
        E = [e for e in E if max(e.get("elevation") or [0]) >= a["min_elevation"]]
    if a.get("summary"):
        by = {}
        for e in E:
            d = by.setdefault(e["type"], {"edges": 0, "length_m": 0.0, "fl": 0.0, "fw": 0.0})
            d["edges"] += 1; d["length_m"] += e["length"]
            if "flow" in e: d["fl"] += e["flow"] * e["length"]; d["fw"] += e["length"]
        rows = []
        for t, d in sorted(by.items(), key=lambda kv: -kv[1]["length_m"]):
            r = {"type": t, "edges": d["edges"], "length_m": int(d["length_m"])}
            if d["fw"]: r["mean_flow"] = round(d["fl"] / d["fw"], 2)
            rows.append(r)
        return {"edges": len(E), "by_type": rows, "note": UTIL_NOTE}
    keep = {e["start_node"] for e in E} | {e["end_node"] for e in E}
    lim = a.get("limit")
    if lim: E = E[:lim]
    nodes = [n for n in net["nodes"] if n["id"] in keep]
    if a.get("compact"):
        E = [{"id": e["id"], "type": e["type"], "a": [round(e["curve"][0][0], 1), round(e["curve"][0][2], 1)],
              "b": [round(e["curve"][3][0], 1), round(e["curve"][3][2], 1)], "n": [e["start_node"], e["end_node"]],
              **({"elev": e["elevation"]} if e.get("elevation") and max(e["elevation"]) > 0.5 else {}),
              **({"flow": e["flow"]} if "flow" in e else {})} for e in E]
        nodes = [{"id": n["id"], "pos": [round(n["pos"][0], 1), round(n["pos"][2], 1)], "deg": n["degree"]} for n in nodes]
    return {"edges": E, "nodes": nodes}


INSPECT_SECTIONS = ("roads", "zoning", "unused_land")


def inspect_trimmed(res, a):
    secs = a.get("sections")
    if secs: res = {k: v for k, v in res.items() if k == "area" or k in secs or any(k2.startswith(k + ".") for k2 in secs)}
    lim = a.get("limit", 8)
    def trim(x):
        if isinstance(x, dict): return {k: trim(v) for k, v in x.items() if not (k == "legend" and not a.get("legend"))}
        if isinstance(x, list): return [trim(v) for v in x[:lim]]
        return x
    res = trim(res)
    if secs and any("." in s for s in secs):                   # e.g. roads.dead_ends: keep just those keys
        for s in secs:
            if "." not in s: continue
            top, sub = s.split(".", 1)
            if top in res and isinstance(res[top], dict):
                wanted = [x.split(".", 1)[1] for x in secs if x.startswith(top + ".")]
                res[top] = {k: v for k, v in res[top].items() if k in wanted}
    return res


def traffic(rpc, a):
    p = {k: a[k] for k in ("xmin", "zmin", "xmax", "zmax") if k in a}
    net = rpc("road_network", dict(p, limit=30000))
    E = [e for e in net["edges"] if "flow" in e]
    area = survey.Area(p["xmin"], p["zmin"], p["xmax"], p["zmax"])
    E = [e for e in E if area.inside(*survey.bez(e["curve"], 0.5))]
    by = {}
    for e in E:
        d = by.setdefault(e["type"], [0.0, 0.0, 0])
        d[0] += e["flow"] * e["length"]; d[1] += e["length"]; d[2] += 1
    types = [{"type": t, "edges": d[2], "length_m": int(d[1]), "mean_flow": round(d[0] / d[1], 2) if d[1] else None}
             for t, d in sorted(by.items(), key=lambda kv: -kv[1][1])]
    thr = a.get("threshold", 0.5); join = a.get("cluster_m", 80.0)
    bad = [e for e in E if e["flow"] < thr]
    mids = [survey.bez(e["curve"], 0.5) for e in bad]
    parent = list(range(len(bad)))
    def find(i):
        while parent[i] != i: parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for i in range(len(bad)):
        for j in range(i + 1, len(bad)):
            if math.dist(mids[i], mids[j]) < join: parent[find(i)] = find(j)
    groups = {}
    for i in range(len(bad)): groups.setdefault(find(i), []).append(i)
    clusters = []
    for g in groups.values():
        es = [bad[i] for i in g]; L = sum(e["length"] for e in es) or 1
        cx = sum(mids[i][0] * bad[i]["length"] for i in g) / L; cz = sum(mids[i][1] * bad[i]["length"] for i in g) / L
        worst = min(es, key=lambda e: e["flow"])
        clusters.append({"at": [round(cx), round(cz)], "edges": len(es), "length_m": int(L), "min_flow": worst["flow"],
                         "mean_flow": round(sum(e["flow"] * e["length"] for e in es) / L, 2),
                         "types": sorted({e["type"] for e in es}), "worst_edge": worst["id"],
                         "severity": round(sum((thr - e["flow"]) * e["length"] for e in es))})
    clusters.sort(key=lambda c: -c["severity"])
    tot = sum(d[1] for d in by.values()) or 1
    return {"area": [area.x0, area.z0, area.x1, area.z1], "edges": len(E),
            "mean_flow": round(sum(d[0] for d in by.values()) / tot, 2), "by_type": types[:a.get("types_limit", 15)],
            "below_threshold": {"threshold": thr, "edges": len(bad), "length_m": int(sum(e["length"] for e in bad))},
            "worst_clusters": clusters[:a.get("limit", 10)],
            "note": "flow = speed vs free flow (1 = no congestion); severity = length x shortfall below threshold"}


FAMILIES = [("lowrent", "residential lowrent"), ("office", "office"), ("industr", "industrial"), ("warehouse", "industrial"),
            ("agricult", "industrial"), ("forestry", "industrial"), ("oil", "industrial"), ("ore", "industrial"),
            ("commercial high", "commercial high"), ("commercial", "commercial low"), ("mixed", "mixed"),
            ("residential high", "residential high"), ("medium", "residential medium"), ("row", "residential medium"),
            ("residential low", "residential low"), ("low", "residential low"), ("residential", "residential")]


def family_of(name):
    n = name.lower()
    for key, fam in FAMILIES:
        if key in n: return fam
    return "other"


def _in_poly(p, poly):
    x, z = p; inside = False
    for (x1, z1), (x2, z2) in zip(poly, poly[1:] + poly[:1]):
        if (z1 > z) != (z2 > z) and x < x1 + (z - z1) * (x2 - x1) / (z2 - z1): inside = not inside
    return inside


def zone_summary(rpc, a):
    poly = a.get("polygon")
    if poly:
        xs = [p[0] for p in poly]; zs = [p[1] for p in poly]
        box = {"xmin": min(xs), "zmin": min(zs), "xmax": max(xs), "zmax": max(zs)}
    else: box = {k: a[k] for k in ("xmin", "zmin", "xmax", "zmax")}
    zb = rpc("zone_blocks", dict(box, limit=30000))
    legend = zb.get("legend", {})
    fam = {}; per_zone = {}; unzoned = [0, 0]
    area = survey.Area(box["xmin"], box["zmin"], box["xmax"], box["zmax"])
    for b in zb["blocks"]:
        survey.normalise_cells(b)
        for _x, _y, ch, built, c, _ in survey.cell_geometry(b):
            if ch == "#": continue
            if not area.inside(*c) or (poly and not _in_poly(c, poly)): continue
            if ch in ".?":
                unzoned[1 if built else 0] += 1; continue
            name = legend.get(ch, ch)
            f = fam.setdefault(family_of(name), [0, 0]); f[1 if built else 0] += 1
            if a.get("by_zone"): z = per_zone.setdefault(name, [0, 0]); z[1 if built else 0] += 1
    def row(v): return {"cells": v[0] + v[1], "built": v[1], "empty": v[0], "hectares": round((v[0] + v[1]) * 64 / 1e4, 2)}
    out = {"families": {k: row(v) for k, v in sorted(fam.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))},
           "unzoned": {"cells": sum(unzoned), "built_unzoned": unzoned[1]}, "cell_m2": 64}
    tot = sum(v[0] + v[1] for v in fam.values())
    if tot: out["share"] = {k: round((v[0] + v[1]) / tot, 3) for k, v in fam.items()}
    if a.get("by_zone"): out["zones"] = {k: row(v) for k, v in sorted(per_zone.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))}
    return out


def probe(rpc, a):
    """Dry-run each candidate on its own. Candidates: segment objects, or lists of segments (a candidate = all of them)."""
    cands = load_list(a, "candidates") or []
    fr = framed(a); out = []
    for i, c in enumerate(cands):
        segs = c if isinstance(c, list) else [c]
        segs = [frame_seg(s, fr) for s in segs]
        for s in segs: s.setdefault("type", a.get("type"))
        try:
            r = rpc("place_roads", {"segments": segs, "snap_tolerance": a.get("snap_tolerance", 2.0), "dry_run": True, "timeout_s": 60})
            row = {"i": i, "ok": r.get("errors", 0) == 0, "errors": r.get("errors", 0)}
            if r.get("error_reasons"): row["reasons"] = r["error_reasons"]
            if a.get("explain") and r.get("errors"): row["hits"] = explain_errors(rpc, r, max_n=5)
            if a.get("details"): row["snapped"] = r.get("snapped_ends"); row["intersections"] = r.get("intersections")
        except Exception as e:
            row = {"i": i, "ok": False, "error": str(e)}
        out.append(row)
    return {"candidates": len(cands), "ok": sum(1 for r in out if r["ok"]), "results": out}


# ----------------------------------------------------------------------------- builders
def frame_seg(s, fr):
    import tools3
    s = tools3.resolve_seg(s, tools3.RPC)
    if not fr: return s
    s["start"] = fr.to_world(s["start"]); s["end"] = fr.to_world(s["end"])
    if s.get("controls"): s["controls"] = [fr.to_world(c) for c in s["controls"]]
    if s.get("control"): s["control"] = fr.to_world(s["control"])
    return s


def path_line(a, fr):
    knots = pts_world(a["knots"], fr)
    line = geom.catmull_rom(knots, a.get("step", 8.0)) if a.get("smooth", True) else _poly_dense(knots, a.get("step", 8.0))
    return line, knots


def _poly_dense(knots, step):
    out = [tuple(knots[0])]
    for p, q in zip(knots, knots[1:]):
        n = max(1, int(math.dist(p, q) // step))
        out += [(p[0] + (q[0] - p[0]) * k / n, p[1] + (q[1] - p[1]) * k / n) for k in range(1, n + 1)]
    return out


def _force_pts(a, fr, knots):
    f = pts_world(a.get("joints") or [], fr)
    if a.get("smooth") is False or a.get("joints_at_knots"): f += knots[1:-1]
    return f


def build_path(a, fr=None):
    line, knots = path_line(a, fr)
    if a.get("offset"): line = geom.offset_line(line, a["offset"], a.get("taper_start", 0), a.get("taper_end", 0))
    L = geom.cumlen(line)[-1]
    cuts = geom.cut_indices(line, a.get("piece_m", 110.0), pts_world(a.get("avoid") or [], fr), a.get("avoid_m", 25.0), _force_pts(a, fr, knots))
    segs = geom.to_segments(line, cuts, a["type"], geom.elevation_fn(a.get("elevation"), L), reverse=a.get("reverse", False))
    return segs, {"length_m": round(L), "min_radius_m": round(geom.min_radius(line)), "segments": len(segs)}


def build_pair(a, fr=None):
    line, knots = path_line(a, fr)
    sep = a.get("separation", 24.0) / 2
    L = geom.cumlen(line)[-1]
    rht = a.get("traffic", "right") == "right"
    types = a.get("types") or [a["type"], a["type"]]
    avoid = pts_world(a.get("avoid") or [], fr); force = _force_pts(a, fr, knots)
    ts, te = a.get("taper_start", 0.0), a.get("taper_end", 0.0)
    elev = geom.elevation_fn(a.get("elevation"), L)
    out, info = [], {"length_m": round(L)}
    # with the knots' direction: right-hand traffic drives on the right (offset -sep, forward) and returns on the left
    for name, d, rev, typ in (("forward", -sep if rht else sep, False, types[0]), ("return", sep if rht else -sep, True, types[1])):
        off = geom.offset_line(line, d, ts, te)
        plain = geom.offset_line(line, d)
        cuts = geom.cut_indices(off, a.get("piece_m", 110.0), avoid, a.get("avoid_m", 25.0), [plain[_nearest(line, f)] for f in force])
        # elevation by distance along the centreline: map offset-line index -> centreline distance
        Lc = geom.cumlen(line)
        segs = geom.to_segments(off, cuts, typ, None, reverse=rev)
        for s in segs:                                     # elevation from the centreline position nearest each end
            e0 = elev(Lc[_nearest(off, s["start"])]); e1 = elev(Lc[_nearest(off, s["end"])])
            if abs(e0) > 0.05 or abs(e1) > 0.05: s["elevation"] = [round(e0, 2), round(e1, 2)]
        out += segs
        info[name] = {"segments": len(segs), "min_radius_m": round(geom.min_radius(off)), "start": segs[0]["start"], "end": segs[-1]["end"]}
    return out, info


def _nearest(line, p): return min(range(len(line)), key=lambda k: math.dist(line[k], p))


def build_lines(a, fr=None):
    lines = load_list(a, "lines") or []
    L = [{"points": pts_world(l["points"], fr), "type": l.get("type") or a.get("type")} for l in lines]
    segs = geom.network_segments(L, touch_m=a.get("touch_m", 1.5), merge_m=a.get("merge_m", 0.6))
    return segs, {"lines": len(L), "segments": len(segs), "length_m": round(sum(math.dist(s["start"], s["end"]) for s in segs))}


def place_built(rpc, a, segs, info, compact):
    import tools3
    if a.get("deck_height") is not None and not a.get("_levelled"):
        import tools4
        tools4.level_segments(rpc, segs, float(a["deck_height"]), a.get("min_elevation", 0.0))
        a = dict(a, _levelled=True)
    if a.get("before_after") and not (a.get("dry_run") or a.get("plan_only") or a.get("hold")):
        return tools3.with_before_after(rpc, a, seg_points(segs), lambda: place_built(rpc, dict(a, before_after=False, follow_camera=False), segs, info, compact))
    if a.get("plan_only"):
        info["segments_list"] = segs
        return info
    if a.get("save_to"):
        with open(a["save_to"], "w") as fh: json.dump(segs, fh)
        info["saved_to"] = a["save_to"]
    follow(rpc, a, seg_points(segs))
    if a.get("skip_failing") and not (a.get("dry_run") or a.get("hold")):
        import pave
        res, bad = pave.place_skip_failing(rpc, segs, a.get("snap_tolerance", 2.0))
        info["result"] = compact(res or {"applied": False}, a); info["refused"] = len(bad)
        if bad: info["refused_segments"] = bad[:30]
        after_build(rpc, a, segs, info)
        return info
    res = rpc("place_roads", {"segments": segs, "snap_tolerance": a.get("snap_tolerance", 2.0), "dry_run": a.get("dry_run", False),
                              "allow_errors": a.get("allow_errors", False), "hold": a.get("hold", False), "timeout_s": 90})
    if res.get("errors") and a.get("explain_errors", True): res["error_hits"] = explain_errors(rpc, res)
    if res.get("error_locations"): res["error_locations"] = res["error_locations"][:30]
    info["result"] = compact(res, a)
    if res.get("applied"): after_build(rpc, a, segs, info)
    return info


def after_build(rpc, a, segs, info):
    import tools3
    pts = seg_points(segs)
    tools3.note_edit(pts)
    if a.get("align", True) and not a.get("dry_run") and not a.get("hold"):   # v0.36: zero zone cracks on new roads
        import align
        al = align.after_build(rpc, segs)
        if al: info["zone_alignment"] = al
    if a.get("check", not fast()):
        try: info["check"] = tools3.local_check(rpc, pts)
        except Exception as e: info["check"] = f"check failed: {e}"
