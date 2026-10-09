"""v0.8 tools: ramps, line extension with node reuse and clipping, zoning along roads, obstacles/pillars, trimming roads,
shore/edge queries, post-build junction checks, named reference points, camera tours and before/after shots.

Mechanics and facts only: where things go, their types and sizes are always the caller's decision.
"""
import base64, json, math, os, time
import geom, survey

RPC = None            # set by cs2_mcp so @name node refs can be re-read from the game

# ----------------------------------------------------------------------------- small geometry helpers
def bez3(c, t):
    u = 1 - t
    return [u * u * u * c[0][k] + 3 * u * u * t * c[1][k] + 3 * u * t * t * c[2][k] + t * t * t * c[3][k] for k in range(3)]


def bez3_d(c, t):
    u = 1 - t
    return [3 * u * u * (c[1][k] - c[0][k]) + 6 * u * t * (c[2][k] - c[1][k]) + 3 * t * t * (c[3][k] - c[2][k]) for k in range(3)]


def bez_split(c, t):
    """de Casteljau: -> (first part, second part), each 4 [x,y,z] points."""
    def lerp(p, q): return [p[k] + (q[k] - p[k]) * t for k in range(3)]
    a, b, cc, d = c
    ab, bc, cd = lerp(a, b), lerp(b, cc), lerp(cc, d)
    abc, bcd = lerp(ab, bc), lerp(bc, cd)
    m = lerp(abc, bcd)
    return [a, ab, abc, m], [m, bcd, cd, d]


def nearest_t(c, p, n=200):
    best = (1e18, 0.0)
    for i in range(n + 1):
        t = i / n; q = bez3(c, t); d = (q[0] - p[0]) ** 2 + (q[2] - p[1]) ** 2
        if d < best[0]: best = (d, t)
    lo, hi = max(0.0, best[1] - 1 / n), min(1.0, best[1] + 1 / n)
    for _ in range(30):                                   # refine
        m1, m2 = lo + (hi - lo) / 3, hi - (hi - lo) / 3
        q1, q2 = bez3(c, m1), bez3(c, m2)
        if (q1[0] - p[0]) ** 2 + (q1[2] - p[1]) ** 2 < (q2[0] - p[0]) ** 2 + (q2[2] - p[1]) ** 2: hi = m2
        else: lo = m1
    t = (lo + hi) / 2; q = bez3(c, t)
    return t, math.dist((q[0], q[2]), p)


def arc_t(c, dist, n=400):
    """t at arc length `dist` from the start (negative = from the end)."""
    pts = [bez3(c, i / n) for i in range(n + 1)]
    L = [0.0]
    for i in range(1, n + 1): L.append(L[-1] + math.dist(pts[i - 1], pts[i]))
    if dist < 0: dist = L[-1] + dist
    dist = max(0.0, min(L[-1], dist))
    for i in range(1, n + 1):
        if L[i] >= dist:
            f = (dist - L[i - 1]) / ((L[i] - L[i - 1]) or 1)
            return (i - 1 + f) / n, L[-1]
    return 1.0, L[-1]


def unit(v):
    n = math.hypot(v[0], v[1]) or 1.0
    return (v[0] / n, v[1] / n)


def rot(v, deg):
    a = math.radians(deg); c, s = math.cos(a), math.sin(a)
    return (v[0] * c - v[1] * s, v[0] * s + v[1] * c)


def r(v, n=2): return round(v, n)


def box_of(pts, pad):
    xs = [p[0] for p in pts]; zs = [p[1] for p in pts]
    return {"xmin": min(xs) - pad, "zmin": min(zs) - pad, "xmax": max(xs) + pad, "zmax": max(zs) + pad}


def ground(rpc, pts):
    if not pts: return []
    res = rpc("terrain_height", {"points": [[p[0], p[1]] for p in pts]})
    hs = res.get("heights") or res.get("points") or res
    out = []
    for h in hs: out.append(h if isinstance(h, (int, float)) else (h.get("height") if isinstance(h, dict) else h[1]))
    return out


def edges_near(rpc, p, radius=150.0, types=None, roads_only=True):
    # road_network selects edges by their end points, so search wide (long highway pieces) and filter by real distance
    q = max(radius, 3200.0)
    net = rpc("road_network", {"xmin": p[0] - q, "zmin": p[1] - q, "xmax": p[0] + q, "zmax": p[1] + q, "limit": 30000})
    E = [e for e in net["edges"] if ("flow" in e or not roads_only)]
    if types: E = [e for e in E if any(t.lower() in e["type"].lower() for t in types)]
    E = [e for e in E if min(math.dist(p, x) for x in survey.polyline(e["curve"], 24)) <= radius + e.get("length", 0) / 24]
    return E, {n["id"]: n for n in net["nodes"]}


def pick_edge(rpc, a, p, radius=150.0):
    E, N = edges_near(rpc, p, radius, a.get("road_types"))
    if a.get("edge"): E = [e for e in E if e["id"] == a["edge"]]
    if not E: raise ValueError(f"no matching road within {radius} m of {p}")
    best = min(((nearest_t(e["curve"], p, 60), e) for e in E), key=lambda x: x[0][1])
    (t, d), e = best
    t, d = nearest_t(e["curve"], p)
    return e, t, d, N


# ----------------------------------------------------------------------------- named reference points
REF_PATH = os.path.join(survey.OUT_DIR, "refs.json")


def _refs():
    try:
        with open(REF_PATH) as fh: return json.load(fh)
    except Exception: return {}


def _save_refs(d):
    os.makedirs(os.path.dirname(REF_PATH), exist_ok=True)
    with open(REF_PATH, "w") as fh: json.dump(d, fh, indent=1)


def resolve(v, rpc=None):
    """'@name' -> stored point (a node ref is re-read from the game so moved nodes are followed). Lists recurse."""
    if isinstance(v, str) and v.startswith("@"):
        refs = _refs(); name = v[1:]
        if name not in refs: raise ValueError(f"unknown reference '{v}' (cs2_refs list)")
        ref = refs[name]
        if ref.get("node") and rpc:
            p = ref["pos"]
            net = rpc("road_network", {"xmin": p[0] - 40, "zmin": p[1] - 40, "xmax": p[0] + 40, "zmax": p[1] + 40, "limit": 5000})
            for n in net["nodes"]:
                if n["id"] == ref["node"]:
                    np_ = [r(n["pos"][0], 3), r(n["pos"][2], 3)]
                    if np_ != p: ref["pos"] = np_; refs[name] = ref; _save_refs(refs)
                    break
        return list(ref["pos"])
    if isinstance(v, list) and v and isinstance(v[0], (list, str)):
        return [resolve(x, rpc) for x in v]
    return v


def resolve_seg(s, rpc):
    s = dict(s)
    for k in ("start", "end", "control"):
        if k in s: s[k] = resolve(s[k], rpc)
    if s.get("controls"): s["controls"] = [resolve(c, rpc) for c in s["controls"]]
    return s


def t_refs(rpc, a):
    refs = _refs(); act = a.get("action", "list")
    if act == "list": return {"refs": refs, "use": "pass '@name' wherever a point [x,z] is accepted"}
    if act == "delete":
        for n in a.get("names", []): refs.pop(n, None)
        _save_refs(refs); return {"deleted": a.get("names", []), "left": len(refs)}
    if act == "set":
        out = {}
        for item in a.get("items", []):
            name = item["name"]; p = resolve(item.get("pos") or item.get("near"), rpc)
            ref = {"pos": [r(p[0], 3), r(p[1], 3)]}
            if item.get("note"): ref["note"] = item["note"]
            if item.get("near"):                         # store the nearest road node (followed if the game moves it)
                net = rpc("road_network", {"xmin": p[0] - 60, "zmin": p[1] - 60, "xmax": p[0] + 60, "zmax": p[1] + 60, "limit": 5000})
                road = {e["start_node"] for e in net["edges"] if "flow" in e} | {e["end_node"] for e in net["edges"] if "flow" in e}
                ns = [n for n in net["nodes"] if n["id"] in road]
                if not ns: raise ValueError(f"no road node within 60 m of {p}")
                n = min(ns, key=lambda n: math.dist(p, (n["pos"][0], n["pos"][2])))
                ref = {"pos": [r(n["pos"][0], 3), r(n["pos"][2], 3)], "node": n["id"], "degree": n["degree"], **({"note": item["note"]} if item.get("note") else {})}
            refs[name] = ref; out[name] = ref
        _save_refs(refs); return {"set": out}
    raise ValueError("action: list | set | delete")


# ----------------------------------------------------------------------------- post-build junction check
def local_check(rpc, pts, pad=60.0, limit=10):
    b = box_of(pts, pad)
    w = survey.World(rpc, survey.Area(b["xmin"], b["zmin"], b["xmax"], b["zmax"]), grid_step=16)
    f = survey.check(w)["roads"]
    out = {}
    for k in ("dead_ends", "near_miss_connections", "overlapping_unconnected_nodes", "sharp_junctions", "short_links_between_junctions"):
        v = f.get(k) or {}
        if v.get("count"): out[k] = {"count": v["count"], "list": v["list"][:limit]}
    return out or "clean: no dead ends, near misses, overlapping nodes, sharp junctions or short links around the change"


# ----------------------------------------------------------------------------- camera: tours, before/after
_recent = []          # [x,z] centres of recent edits (this server session)


def note_edit(pts):
    if not pts: return
    xs = [p[0] for p in pts]; zs = [p[1] for p in pts]
    _recent.append([r((min(xs) + max(xs)) / 2, 1), r((min(zs) + max(zs)) / 2, 1)])
    del _recent[:-30]


def shot(rpc, x, z, zoom=None, settle=2.0, width=900):
    p = {"x": x, "z": z}
    if zoom: p["zoom"] = zoom
    rpc("camera_goto", p); time.sleep(settle)
    s = rpc("screenshot", {"max_width": width, "timeout_s": 60})
    with open(s["path"], "rb") as fh: return base64.b64encode(fh.read()).decode(), s["path"]


def t_tour(rpc, a):
    pts = [resolve(p, rpc) for p in (a.get("points") or [])] or list(reversed(_recent))[:a.get("recent", 4)]
    if not pts: raise ValueError("no points given and no recent edits in this session")
    content, paths = [], []
    for p in pts[:8]:
        data, path = shot(rpc, p[0], p[1], a.get("zoom"), a.get("settle_s", 2.0), a.get("max_width", 900))
        content.append({"type": "image", "data": data, "mimeType": "image/jpeg"}); paths.append(path)
    content.append({"type": "text", "text": json.dumps({"points": pts[:8], "paths": paths})})
    return {"__mcp_content__": content}


def with_before_after(rpc, a, pts, do):
    """Screenshot the spot, run do(), screenshot again; returns MCP content with both images and do()'s result."""
    xs = [p[0] for p in pts]; zs = [p[1] for p in pts]
    cx, cz = (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2
    zoom = a.get("camera_zoom") or max(300.0, min(4000.0, max(max(xs) - min(xs), max(zs) - min(zs)) * 1.3))
    before, _ = shot(rpc, cx, cz, zoom, 1.5, a.get("max_width", 800))
    res = do()
    after, _ = shot(rpc, cx, cz, zoom, 2.5, a.get("max_width", 800))
    return {"__mcp_content__": [{"type": "text", "text": "before:"}, {"type": "image", "data": before, "mimeType": "image/jpeg"},
                                {"type": "text", "text": "after:"}, {"type": "image", "data": after, "mimeType": "image/jpeg"},
                                {"type": "text", "text": json.dumps(res, indent=1)}]}


# ----------------------------------------------------------------------------- ramp
def build_ramp(rpc, a, fr=None):
    """Branch a ramp off (or onto) an existing carriageway. Returns (segments, info, aux_edges)."""
    import tools2
    at = resolve(a["at"], rpc); at = tools2.pts_world([at], fr)[0]
    e, t, d, N = pick_edge(rpc, a, at)
    c = e["curve"]
    if a.get("distance_m") is not None:                       # shift along the edge from the picked point (+ = edge direction)
        _, total = arc_t(c, 0)
        t, _ = arc_t(c, total * t + a["distance_m"])
    P = bez3(c, t); T = unit((bez3_d(c, t)[0], bez3_d(c, t)[2]))
    if a.get("against_edge"): T = (-T[0], -T[1])              # two-way road: the lanes travelling against the edge's drawing direction
    side = a.get("side", "right"); sgn = -1 if side == "right" else 1          # + = left of travel
    ang = a.get("angle_deg", 30.0)
    mode = a.get("mode", "off")
    if mode == "off": D = rot(T, sgn * ang)                    # leaves the carriageway toward `side`
    else: D = rot(T, -sgn * ang)                               # arrives from `side`, heading into the carriageway
    lead = a.get("lead_m", 40.0)
    knots = tools2.pts_world([resolve(k, rpc) for k in a["knots"]], fr)
    Pxz = (P[0], P[2])
    if mode == "off": path = [Pxz, (P[0] + D[0] * lead, P[2] + D[1] * lead)] + [tuple(k) for k in knots]
    else: path = [tuple(k) for k in knots] + [(P[0] - D[0] * lead, P[2] - D[1] * lead), Pxz]
    line = geom.catmull_rom(path, a.get("step", 6.0))
    L = geom.cumlen(line)[-1]
    el = e.get("elevation") or [0, 0]
    e_split = el[0] + (el[1] - el[0]) * t
    prof = a.get("elevation")
    if prof is None:                                          # carriageway height at the split -> end_elevation at the far end
        far = a.get("end_elevation", 0.0)
        prof = [[0, e_split], [L, far]] if mode == "off" else [[0, far], [L, e_split]]
    avoid = tools2.pts_world([resolve(p, rpc) for p in (a.get("avoid") or [])], fr)
    cuts = geom.cut_indices(line, a.get("piece_m", 90.0), avoid, a.get("avoid_m", 25.0))
    segs = geom.to_segments(line, cuts, a["type"], geom.elevation_fn(prof, L))
    # the end at the carriageway must be the exact curve point so it splits that edge
    k = 0 if mode == "off" else -1
    key = "start" if mode == "off" else "end"
    segs[k][key] = [r(P[0], 3), r(P[2], 3)]
    info = {"carriageway": {"edge": e["id"], "type": e["type"], "t": r(t, 3), "split_at": [r(P[0], 1), r(P[2], 1)],
                            "elevation_there": r(e_split, 1)},
            "ramp": {"length_m": round(L), "min_radius_m": round(geom.min_radius(line)), "segments": len(segs), "mode": mode, "side": side,
                     "angle_deg": ang}}
    aux = None
    if a.get("aux_lane"):
        aux = aux_edges(rpc, e, t, a["aux_lane"], mode, N)
        info["aux_lane"] = aux["info"]
    return segs, info, aux


def aux_edges(rpc, e, t, spec, mode, N):
    """Edges of the carriageway covering `length_m` before (off-ramp) or after (on-ramp) the split, walking across nodes."""
    length = spec.get("length_m", 200.0)
    c = e["curve"]
    _, total = arc_t(c, 0)
    here = total * t
    cover = [(e["id"], e["type"])]
    need = length - (here if mode == "off" else total - here)
    node = e["start_node"] if mode == "off" else e["end_node"]
    E, _ = edges_near(rpc, (c[0][0], c[0][2]), length + 300, None)
    cur = e
    while need > 0:
        nxt = [x for x in E if x["id"] != cur["id"] and (x["end_node"] == node if mode == "off" else x["start_node"] == node)
               and x["type"] == e["type"]]
        if len(nxt) != 1: break                                # stop at junctions / type changes
        cur = nxt[0]; cover.append((cur["id"], cur["type"])); need -= cur["length"]
        node = cur["start_node"] if mode == "off" else cur["end_node"]
    return {"ids": [i for i, _ in cover], "info": {"type": spec["type"], "edges": len(cover), "wanted_m": length,
                                                   "covered_m": round(length - need) if need > 0 else round(length),
                                                   "note": "whole edges are upgraded; the part beyond the split on the far side is upgraded too"}}


# ----------------------------------------------------------------------------- extend lines (grid with node reuse + clipping)
def _seg_inter(p, q, r_, s):
    d = (q[0] - p[0]) * (s[1] - r_[1]) - (q[1] - p[1]) * (s[0] - r_[0])
    if abs(d) < 1e-9: return None
    u = ((r_[0] - p[0]) * (s[1] - r_[1]) - (r_[1] - p[1]) * (s[0] - r_[0])) / d
    v = ((r_[0] - p[0]) * (q[1] - p[1]) - (r_[1] - p[1]) * (q[0] - p[0])) / d
    if 0 <= u <= 1 and 0 <= v <= 1: return u
    return None


class Mask:
    """Water / steep lookup on a terrain grid."""
    def __init__(self, rpc, box, step, max_grade, water_m=0.5):
        g = rpc("terrain_grid", dict(box, step=step))
        self.o = g["origin"]; self.s = g["step"]; self.H = g["heights"]; self.W = g.get("water_depth"); self.nx = g["nx"]; self.nz = g["nz"]
        self.max_grade = max_grade; self.water_m = water_m
    def _ij(self, x, z):
        return (min(self.nx - 1, max(0, int(round((x - self.o[0]) / self.s)))), min(self.nz - 1, max(0, int(round((z - self.o[1]) / self.s)))))
    def water(self, x, z):
        if not self.W: return False
        i, j = self._ij(x, z); return self.W[j][i] > self.water_m
    def height(self, x, z):
        i, j = self._ij(x, z); return self.H[j][i]
    def bad(self, p, q):
        if self.water(*q): return "water"
        if self.max_grade and abs(self.height(*q) - self.height(*p)) / max(1e-6, math.dist(p, q)) > self.max_grade: return "steep"
        return None


def extend_lines(rpc, a, fr=None):
    import tools2
    lines = tools2.load_list(a, "lines") or []
    L = [{"points": tools2.pts_world([resolve(p, rpc) for p in l["points"]], fr), "type": l.get("type") or a.get("type"), "name": l.get("name", str(i)),
          "end_keep": l.get("end_keep")}
         for i, l in enumerate(lines)]
    allpts = [p for l in L for p in l["points"]]
    pad = max(60.0, a.get("snap_m", 30.0) * 2)
    box = box_of(allpts, pad)
    net = rpc("road_network", dict(box, limit=30000))
    E = [e for e in net["edges"] if "flow" in e]
    N = {n["id"]: (n["pos"][0], n["pos"][2]) for n in net["nodes"]}
    stop_types = [s.lower() for s in (a.get("stop_at_types") or [])]
    end_types = [s.lower() for s in (a.get("end_at_types") or [])]
    end_ids = set(a.get("end_at_ids") or [])
    snap_m = a.get("snap_m", 30.0); gap = a.get("stop_gap_m", 12.0)
    polys = [(e, survey.polyline(e["curve"], 16)) for e in E]
    mask = None
    if a.get("clip_to_land", True) or a.get("max_grade"):
        span = max(box["xmax"] - box["xmin"], box["zmax"] - box["zmin"])
        step = max(8, int(math.ceil(math.sqrt((box["xmax"] - box["xmin"]) * (box["zmax"] - box["zmin"]) / 85000) / 4) * 4))
        mask = Mask(rpc, box, step, a.get("max_grade", 0.0))
    report, out_lines = [], []
    for l in L:
        pts = [tuple(p) for p in l["points"]]
        rep = {"line": l["name"]}
        # 1) crossings with existing roads: stop at forbidden types, reuse an existing node within snap_m
        events = []                                           # (distance along line, kind, point)
        acc = 0.0
        for p, q in zip(pts, pts[1:]):
            seg_len = math.dist(p, q)
            for e, pl in polys:
                elev = max(e.get("elevation") or [0])
                for a_, b_ in zip(pl, pl[1:]):
                    u = _seg_inter(p, q, a_, b_)
                    if u is None: continue
                    x = (p[0] + (q[0] - p[0]) * u, p[1] + (q[1] - p[1]) * u)
                    dist = acc + seg_len * u
                    if elev >= 4: continue                    # passes under an elevated road
                    if (end_types and any(s in e["type"].lower() for s in end_types)) or e["id"] in end_ids: events.append((dist, "end", x, e["type"]))
                    elif any(s in e["type"].lower() for s in stop_types): events.append((dist, "stop", x, e["type"]))
                    else:
                        cand = [N[n] for n in (e["start_node"], e["end_node"]) if n in N and math.dist(N[n], x) <= snap_m]
                        if cand: events.append((dist, "node", min(cand, key=lambda c: math.dist(c, x)), None))
            acc += seg_len
        # 2) clip at stop roads: keep the piece(s) between stops, ending `gap` short of them
        cuts = sorted(ev for ev in events if ev[1] == "stop")
        total = acc
        keep = [(0.0, total)]
        ends = sorted(ev for ev in events if ev[1] == "end")
        if ends:                                              # end exactly on those roads; drop what lies beyond them
            mode = l.get("end_keep") or a.get("end_keep", "auto")
            if mode == "start": keep = [(0.0, ends[0][0])]                 # from the line's first point to the first end road
            elif mode == "end": keep = [(ends[-1][0], total)]               # from the last end road to the line's last point
            elif len(ends) >= 2: keep = [(ends[0][0], ends[-1][0])]
            else: keep = [(0.0, ends[0][0])] if ends[0][0] > total - ends[0][0] else [(ends[0][0], total)]
            rep["ends_on"] = sorted({ev[3] for ev in ends})
        for dist, _, _, typ in cuts:
            nk = []
            for s0, s1 in keep:
                if s0 < dist < s1: nk += [(s0, dist - gap), (dist + gap, s1)]
                else: nk.append((s0, s1))
            keep = [k for k in nk if k[1] - k[0] > a.get("min_piece_m", 40.0)]
        if cuts: rep["stopped_at"] = sorted({c[3] for c in cuts})
        dense = _dense(pts, 4.0)
        runs = []
        for s0, s1 in keep:
            piece = [(d_, p) for d_, p in dense if s0 <= d_ <= s1]
            if len(piece) < 2: continue
            # 3) clip to land / grade: keep every stretch between water / too-steep steps that is long enough
            if mask:
                cur = [piece[0]]
                for (dp, p), (dq, q) in zip(piece, piece[1:]):
                    why = mask.bad(p, q)
                    if why:
                        if len(cur) > 1: runs.append(cur)
                        rep.setdefault("clipped", []).append({"why": why, "at": [r(q[0], 1), r(q[1], 1)]}); cur = [(dq, q)]
                    else: cur.append((dq, q))
                if len(cur) > 1: runs.append(cur)
            else: runs.append(piece)
        if len(rep.get("clipped", [])) > 6: rep["clipped"] = rep["clipped"][:3] + [{"more": len(rep["clipped"]) - 3}]
        kept = 0
        for run in runs:
            s0, s1 = run[0][0], run[-1][0]
            piece = [p for _, p in run]
            if math.dist(piece[0], piece[-1]) < a.get("min_piece_m", 40.0): continue
            kept += 1
            for ev in ends:                                   # exact crossing points for ends on end_at roads
                if abs(ev[0] - s0) < 6: piece[0] = ev[2]
                if abs(ev[0] - s1) < 6: piece[-1] = ev[2]
            # 4) rebuild as the original vertices inside [s0, s1] plus snapped crossing nodes, in order
            verts = [(d_, p) for d_, p in _vertices(pts) if s0 <= d_ <= s1 and _within(p, piece)]
            verts.append((s0, piece[0])); verts.append((s1, piece[-1]))
            for dist, kind, x, _ in events:
                if kind == "node" and s0 < dist < s1: verts.append((dist, x)); rep.setdefault("reused_nodes", 0); rep["reused_nodes"] += 1
            verts.sort(key=lambda v: v[0])
            clean = []
            for _, p in verts:
                if not clean or math.dist(clean[-1], p) > 1.0: clean.append(p)
            # 5) ends near an existing node snap onto it
            for idx in (0, -1):
                near = [n for n in N.values() if math.dist(n, clean[idx]) <= a.get("end_snap_m", snap_m)]
                if near:
                    clean[idx] = min(near, key=lambda n: math.dist(n, clean[idx])); rep.setdefault("ends_snapped", 0); rep["ends_snapped"] += 1
            out_lines.append({"points": [list(p) for p in clean], "type": l["type"]})
        rep["pieces"] = kept
        report.append(rep)
    segs = geom.network_segments(out_lines, touch_m=a.get("touch_m", 1.5), merge_m=a.get("merge_m", 0.6))
    changed = [x for x in report if len(x) > 2 or x.get("pieces") != 1]
    return segs, {"lines": len(L), "segments": len(segs), "length_m": round(sum(math.dist(s["start"], s["end"]) for s in segs)),
                  "report": changed[:a.get("report_limit", 40)], "unchanged_lines": len(report) - len(changed)}


def _dense(pts, step):
    out = [(0.0, pts[0])]; acc = 0.0
    for p, q in zip(pts, pts[1:]):
        L = math.dist(p, q); n = max(1, int(L // step))
        for k in range(1, n + 1):
            out.append((acc + L * k / n, (p[0] + (q[0] - p[0]) * k / n, p[1] + (q[1] - p[1]) * k / n)))
        acc += L
    return out


def _vertices(pts):
    out = [(0.0, pts[0])]; acc = 0.0
    for p, q in zip(pts, pts[1:]): acc += math.dist(p, q); out.append((acc, q))
    return out


def _within(p, piece): return any(math.dist(p, q) < 3 for q in piece)


def _plen(piece): return sum(math.dist(p, q) for p, q in zip(piece, piece[1:]))


# ----------------------------------------------------------------------------- zoning along roads
def zone_along(rpc, a, fr=None):
    import tools2
    if a.get("ids"):
        pts = []
        box = None
        if a.get("xmin") is not None: box = {k: a[k] for k in ("xmin", "zmin", "xmax", "zmax")}
        else: raise ValueError("give the area box (xmin..zmax) that contains the road ids")
        E = [e for e in rpc("road_network", dict(box, limit=30000))["edges"] if e["id"] in set(a["ids"])]
    else:
        box = {k: a[k] for k in ("xmin", "zmin", "xmax", "zmax")}
        E = [e for e in rpc("road_network", dict(box, limit=30000))["edges"] if "flow" in e]
        if a.get("types"): E = [e for e in E if any(t.lower() in e["type"].lower() for t in a["types"])]
        ar = survey.Area(box["xmin"], box["zmin"], box["xmax"], box["zmax"])
        E = [e for e in E if ar.inside(*survey.bez(e["curve"], 0.5))]
    depth = a.get("depth_m", 48.0); start = a.get("start_m", 0.0); side = a.get("side", "both")
    quads = []
    for e in E:
        straight = survey.is_straight(e["curve"])
        pl = survey.polyline(e["curve"], 2 if straight else 6)
        w = e.get("width", 0) / 2
        for p, q in zip(pl, pl[1:]):
            tx, tz = unit((q[0] - p[0], q[1] - p[1])); nx, nz = -tz, tx
            for sg, nm in ((1, "left"), (-1, "right")):
                if side not in ("both", nm): continue
                d0, d1 = sg * (w + start), sg * (w + start + depth)
                quads.append([[r(p[0] + nx * d0), r(p[1] + nz * d0)], [r(q[0] + nx * d0), r(q[1] + nz * d0)],
                              [r(q[0] + nx * d1), r(q[1] + nz * d1)], [r(p[0] + nx * d1), r(p[1] + nz * d1)]])
    if a.get("plan_only"): return {"roads": len(E), "quads": quads}
    tools2.follow(rpc, a, [c for qd in quads for c in qd])
    ok, fail = tools2.zone_many(rpc, [(qd, a["zone_type"]) for qd in quads], overwrite=a.get("overwrite", False),
                                dry_run=a.get("dry_run", False))
    note_edit([c for qd in quads for c in qd])
    return {"roads": len(E), "quads": len(quads), "zoned": ok, "refused": fail, "zone_type": a["zone_type"], "depth_m": depth, "side": side}


# ----------------------------------------------------------------------------- obstacles, pillars, clear spots
CODES = {"building": "B", "building_part": "B", "lot": "L", "pillar": "P", "tree": "T", "prop": "o", "road_prop": "o",
         "building_prop": "o", "road": "R", "water": "W", "steep": "S"}


def _in_poly(p, poly):
    x, z = p; inside = False
    for (x1, z1), (x2, z2) in zip(poly, poly[1:] + poly[:1]):
        if (z1 > z) != (z2 > z) and x < x1 + (z - z1) * (x2 - x1) / (z2 - z1): inside = not inside
    return inside


def obstacles(rpc, a):
    box = {k: a[k] for k in ("xmin", "zmin", "xmax", "zmax")}
    p = dict(box, limit=a.get("limit", 3000), names=a.get("names", False), building_props=a.get("building_props", False))
    if a.get("categories"): p["categories"] = a["categories"]
    res = rpc("obstacles", p)
    if not a.get("grid_m"):
        if not a.get("full"):
            for o in res["objects"]:
                if o["cat"] in ("tree", "prop", "road_prop"): o.pop("top", None); o.pop("y", None)
        return res
    cell = a["grid_m"]
    nx = int(math.ceil((box["xmax"] - box["xmin"]) / cell)); nz = int(math.ceil((box["zmax"] - box["zmin"]) / cell))
    if nx * nz > 40000: raise ValueError(f"grid would be {nx}x{nz}; use a bigger grid_m or a smaller box")
    G = [["." for _ in range(nx)] for _ in range(nz)]
    def centre(i, j): return (box["xmin"] + (i + 0.5) * cell, box["zmax"] - (j + 0.5) * cell)   # row 0 = north (max z)
    prio = "WSRBPLTo."
    def put(i, j, ch):
        if 0 <= i < nx and 0 <= j < nz and prio.index(ch) < prio.index(G[j][i]): G[j][i] = ch
    def poly_fill(poly, ch):
        xs = [q[0] for q in poly]; zs = [q[1] for q in poly]
        i0, i1 = int((min(xs) - box["xmin"]) / cell), int((max(xs) - box["xmin"]) / cell)
        j0, j1 = int((box["zmax"] - max(zs)) / cell), int((box["zmax"] - min(zs)) / cell)
        hit = False
        for j in range(max(0, j0), min(nz, j1 + 1)):
            for i in range(max(0, i0), min(nx, i1 + 1)):
                if _in_poly(centre(i, j), poly): put(i, j, ch); hit = True
        if not hit:                                           # smaller than a cell: mark the cell it sits in
            cx, cz = sum(xs) / len(xs), sum(zs) / len(zs)
            put(int((cx - box["xmin"]) / cell), int((box["zmax"] - cz) / cell), ch)
    for o in res["objects"]:
        if o.get("lot"): poly_fill(o["lot"], "L")
        poly_fill(o["corners"], CODES.get(o["cat"], "o"))
    if a.get("roads", True):
        net = rpc("road_network", dict(box, limit=30000))
        for e in net["edges"]:
            if "flow" not in e: continue
            if max(e.get("elevation") or [0]) >= 4 and not a.get("elevated_roads"): continue
            w = e.get("width", 8) / 2
            for q in survey.polyline(e["curve"], max(4, int(e["length"] / cell) * 2)):
                for dj in range(-int(w // cell) - 1, int(w // cell) + 2):
                    for di in range(-int(w // cell) - 1, int(w // cell) + 2):
                        i = int((q[0] - box["xmin"]) / cell) + di; j = int((box["zmax"] - q[1]) / cell) + dj
                        if 0 <= i < nx and 0 <= j < nz and math.dist(centre(i, j), q) <= w + cell / 2: put(i, j, "R")
    if a.get("terrain", True):
        g = rpc("terrain_grid", dict(box, step=max(4, cell)))
        H, Wd, o, st = g["heights"], g.get("water_depth"), g["origin"], g["step"]
        for j in range(nz):
            for i in range(nx):
                x, z = centre(i, j)
                gi = min(g["nx"] - 1, max(0, int(round((x - o[0]) / st)))); gj = min(g["nz"] - 1, max(0, int(round((z - o[1]) / st))))
                if Wd and Wd[gj][gi] > 0.5: put(i, j, "W"); continue
                if 0 < gi < g["nx"] - 1 and 0 < gj < g["nz"] - 1:
                    sl = max(abs(H[gj][gi + 1] - H[gj][gi - 1]), abs(H[gj + 1][gi] - H[gj - 1][gi])) / (2 * st)
                    if sl > a.get("steep_grade", 0.25): put(i, j, "S")
    rows = ["".join(rw) for rw in G]
    free = sum(rw.count(".") for rw in rows)
    return {"grid_m": cell, "size": [nx, nz], "origin_top_left": [box["xmin"], box["zmax"]],
            "legend": "W water, S steep, R road (at grade), B building, P pillar, L building lot, T tree, o prop, . free",
            "rows": rows, "row_0": "north edge (max z); column 0 = min x", "free_cells": free, "by_category": res.get("by_category")}


def pillars(rpc, a):
    """Elevated roads in a box with their pillar positions; with `cross` = [[x,z],[x,z]] (a street you plan to pass under),
    where it crosses each elevated road, the nearest pillar there, and the nearest clear spots along the elevated road."""
    box = {k: a[k] for k in ("xmin", "zmin", "xmax", "zmax")}
    net = rpc("road_network", dict(box, limit=30000))
    E = [e for e in net["edges"] if "flow" in e and max(e.get("elevation") or [0]) >= a.get("min_elevation", 4)]
    obs = rpc("obstacles", dict(box, categories=["pillar"], limit=5000, pad=10))["objects"]
    P = [((sum(c[0] for c in o["corners"]) / 4, sum(c[1] for c in o["corners"]) / 4), o) for o in obs]
    clear = a.get("clearance_m", 12.0)
    out = {"elevated_edges": len(E), "pillars": len(P)}
    if not a.get("cross"):
        out["pillar_positions"] = [[r(p[0], 1), r(p[1], 1)] for p, _ in P][:a.get("limit", 200)]
        out["edges"] = [{"id": e["id"], "type": e["type"], "elev": e.get("elevation"),
                         "a": [r(e["curve"][0][0], 1), r(e["curve"][0][2], 1)], "b": [r(e["curve"][3][0], 1), r(e["curve"][3][2], 1)]} for e in E][:a.get("limit", 200)]
        return out
    A, B = resolve(a["cross"], rpc)
    rows = []
    for e in E:
        pl = survey.polyline(e["curve"], 32)
        for p, q in zip(pl, pl[1:]):
            u = _seg_inter(A, B, p, q)
            if u is None: continue
            x = (A[0] + (B[0] - A[0]) * u, A[1] + (B[1] - A[1]) * u)
            t, _ = nearest_t(e["curve"], x)
            _, Ltot = arc_t(e["curve"], 0)
            near = sorted((math.dist(x, pp), pp) for pp, _ in P)
            row = {"edge": e["id"], "type": e["type"], "cross_at": [r(x[0], 1), r(x[1], 1)],
                   "deck_elevation": r(bez3(e["curve"], t)[1] - 0, 1), "nearest_pillar_m": r(near[0][0], 1) if near else None,
                   "clear": (not near) or near[0][0] >= clear}
            if not row["clear"]:
                spots = []                                   # slide along the elevated road until pillars are >= clearance away
                for sgn in (1, -1):
                    for k in range(1, 60):
                        tt, _ = arc_t(e["curve"], Ltot * t + sgn * k * 2.0)
                        if tt <= 0 or tt >= 1: break
                        q3 = bez3(e["curve"], tt); qq = (q3[0], q3[2])
                        if all(math.dist(qq, pp) >= clear for pp, _ in P):
                            spots.append({"shift_m": sgn * k * 2.0, "at": [r(qq[0], 1), r(qq[1], 1)]}); break
                row["clear_spots"] = spots
            rows.append(row)
            break
    out["crossings"] = rows
    return out


# ----------------------------------------------------------------------------- trim
def trim(rpc, a):
    """Cut an existing road at a point and remove one side: keep='start'|'end' (edge direction) or keep_point=[x,z]."""
    at = resolve(a["at"], rpc)
    e, t, d, _ = pick_edge(rpc, a, at, a.get("radius", 60.0))
    if d > a.get("max_offset_m", 15.0): raise ValueError(f"nearest road is {d:.1f} m from the cut point")
    c = e["curve"]
    first, second = bez_split(c, t)
    if a.get("keep_point"):
        kp = resolve(a["keep_point"], rpc)
        keep = "start" if math.dist(kp, (c[0][0], c[0][2])) < math.dist(kp, (c[3][0], c[3][2])) else "end"
    else: keep = a.get("keep", "start")
    piece = first if keep == "start" else second
    gs = ground(rpc, [(piece[0][0], piece[0][2]), (piece[3][0], piece[3][2])])
    seg = {"start": [r(piece[0][0], 3), r(piece[0][2], 3)], "end": [r(piece[3][0], 3), r(piece[3][2], 3)],
           "controls": [[r(piece[1][0], 3), r(piece[1][2], 3)], [r(piece[2][0], 3), r(piece[2][2], 3)]], "type": e["type"]}
    if gs and len(gs) == 2:
        el = [r(piece[0][1] - gs[0]), r(piece[3][1] - gs[1])]
        if max(abs(v) for v in el) > 1.0: seg["elevation"] = el
    info = {"edge": e["id"], "type": e["type"], "cut_t": r(t, 3), "kept": keep, "kept_length_m": round(arc_t(piece, 0)[1]),
            "removed_length_m": round(arc_t(second if keep == "start" else first, 0)[1])}
    if a.get("dry_run"): info["segment"] = seg; return info
    import tools2
    tools2.follow(rpc, a, [seg["start"], seg["end"]])
    b = rpc("bulldoze", {"ids": [e["id"]], "timeout_s": 90})
    info["bulldoze"] = {k: b.get(k) for k in ("applied", "undo_id", "errors")}
    if seg["start"] != seg["end"] and info["kept_length_m"] >= 2:
        p = rpc("place_roads", {"segments": [seg], "snap_tolerance": 1.0, "timeout_s": 90, "allow_errors": a.get("allow_errors", False)})
        info["rebuild"] = {k: p.get(k) for k in ("applied", "undo_id", "errors", "error_reasons")}
    note_edit([seg["start"], seg["end"]])
    return info


# ----------------------------------------------------------------------------- shore / edges
def rays(rpc, a):
    import tools2
    fr = tools2.framed(a)
    origin = tools2.pts_world([resolve(a["from"], rpc)], fr)[0]
    dirs = a.get("directions_deg")
    if dirs is None: dirs = list(range(0, 360, a.get("every_deg", 45)))
    head = a["frame"].get("heading_deg", 0) if fr else 0
    step = a.get("step_m", 4.0); mx = a.get("max_m", 1500.0)
    grade = a.get("max_grade", 0.25)
    out = []
    for dd in dirs:
        ang = math.radians(dd + head); D = (math.cos(ang), math.sin(ang))
        pts = [(origin[0] + D[0] * k * step, origin[1] + D[1] * k * step) for k in range(int(mx / step) + 1)]
        bx = {"xmin": min(p[0] for p in pts) - 8, "zmin": min(p[1] for p in pts) - 8, "xmax": max(p[0] for p in pts) + 8, "zmax": max(p[1] for p in pts) + 8}
        gs = max(4, step, int(math.ceil(math.sqrt((bx["xmax"] - bx["xmin"]) * (bx["zmax"] - bx["zmin"]) / 85000) / 4) * 4))
        g = rpc("terrain_grid", dict(bx, step=gs))
        H, W, o, st = g["heights"], g.get("water_depth"), g["origin"], g["step"]
        def at(p):
            i = min(g["nx"] - 1, max(0, int(round((p[0] - o[0]) / st)))); j = min(g["nz"] - 1, max(0, int(round((p[1] - o[1]) / st))))
            return H[j][i], (W[j][i] if W else 0.0)
        hit = None; prev = at(pts[0])
        for k in range(1, len(pts)):
            h, w = at(pts[k])
            if w > 0.5: hit = ("water", k); break
            if grade and abs(h - prev[0]) / step > grade: hit = ("steep", k); break
            prev = (h, w)
        row = {"dir_deg": dd}
        if hit:
            p = pts[hit[1] - 1]
            row.update({"hit": hit[0], "dist_m": round((hit[1] - 1) * step), "last_ok": [r(p[0], 1), r(p[1], 1)]})
        else: row.update({"hit": None, "dist_m": mx})
        out.append(row)
    return {"from": origin, "rays": out, "note": "dir_deg is " + ("relative to the frame heading (0 = along, 90 = left)" if fr else "world: 0 = +x, 90 = +z")}


def outline(rpc, a):
    """Boundary polylines of water (or steep ground) in a box, simplified to `tolerance_m`."""
    box = {k: a[k] for k in ("xmin", "zmin", "xmax", "zmax")}
    span = max(box["xmax"] - box["xmin"], box["zmax"] - box["zmin"])
    area_m2 = (box["xmax"] - box["xmin"]) * (box["zmax"] - box["zmin"])
    step = a.get("step") or max(8, int(math.ceil(math.sqrt(area_m2 / 85000) / 4) * 4))   # bridge limit: 90k samples
    g = rpc("terrain_grid", dict(box, step=step))
    H, W, o, st, nx, nz = g["heights"], g.get("water_depth"), g["origin"], g["step"], g["nx"], g["nz"]
    kind = a.get("kind", "water")
    def inside(i, j):
        if kind == "water": return bool(W) and W[j][i] > a.get("water_m", 0.5)
        if 0 < i < nx - 1 and 0 < j < nz - 1:
            return max(abs(H[j][i + 1] - H[j][i - 1]), abs(H[j + 1][i] - H[j - 1][i])) / (2 * st) > a.get("max_grade", 0.25)
        return False
    M = [[inside(i, j) for i in range(nx)] for j in range(nz)]
    # marching squares -> edge segments between cell midpoints
    segs = []
    def P(i, j): return (o[0] + i * st, o[1] + j * st)
    def mid(p, q): return ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
    for j in range(nz - 1):
        for i in range(nx - 1):
            a_, b_, c_, d_ = M[j][i], M[j][i + 1], M[j + 1][i + 1], M[j + 1][i]
            idx = a_ * 1 + b_ * 2 + c_ * 4 + d_ * 8
            if idx in (0, 15): continue
            pa, pb, pc, pd = P(i, j), P(i + 1, j), P(i + 1, j + 1), P(i, j + 1)
            e = {"ab": mid(pa, pb), "bc": mid(pb, pc), "cd": mid(pc, pd), "da": mid(pd, pa)}
            table = {1: [("da", "ab")], 2: [("ab", "bc")], 3: [("da", "bc")], 4: [("bc", "cd")], 5: [("da", "ab"), ("bc", "cd")],
                     6: [("ab", "cd")], 7: [("da", "cd")], 8: [("cd", "da")], 9: [("cd", "ab")], 10: [("ab", "bc"), ("cd", "da")],
                     11: [("cd", "bc")], 12: [("bc", "da")], 13: [("bc", "ab")], 14: [("ab", "da")]}
            for u, v in table[idx]: segs.append((e[u], e[v]))
    # chain segments into polylines
    key = lambda p: (round(p[0], 2), round(p[1], 2))
    adj = {}
    for s in segs:
        adj.setdefault(key(s[0]), []).append(s[1]); adj.setdefault(key(s[1]), []).append(s[0])
    used = set(); lines = []
    for s in segs:
        k = (key(s[0]), key(s[1]))
        if k in used or k[::-1] in used: continue
        line = [s[0], s[1]]; used.add(k)
        for end in (1, 0):
            while True:
                cur = line[-1] if end else line[0]
                nxt = None
                for q in adj.get(key(cur), []):
                    kk = (key(cur), key(q))
                    if kk not in used and kk[::-1] not in used: nxt = q; used.add(kk); break
                if nxt is None: break
                if end: line.append(nxt)
                else: line.insert(0, nxt)
        lines.append(line)
    tol = a.get("tolerance_m", 6.0)
    lines = [_rdp(l, tol) for l in lines if _plen(l) >= a.get("min_length_m", 60.0)]
    lines.sort(key=lambda l: -_plen(l))
    return {"kind": kind, "step": st, "lines": [{"length_m": round(_plen(l)), "points": [[r(p[0], 1), r(p[1], 1)] for p in l]}
                                                for l in lines[:a.get("limit", 10)]]}


def _rdp(pts, eps):
    if len(pts) < 3: return pts
    a, b = pts[0], pts[-1]
    dx, dz = b[0] - a[0], b[1] - a[1]; L = math.hypot(dx, dz) or 1e-9
    dmax, idx = 0.0, 0
    for i in range(1, len(pts) - 1):
        d = abs(dz * pts[i][0] - dx * pts[i][1] + b[0] * a[1] - b[1] * a[0]) / L
        if d > dmax: dmax, idx = d, i
    if dmax > eps: return _rdp(pts[:idx + 1], eps)[:-1] + _rdp(pts[idx:], eps)
    return [a, b]
