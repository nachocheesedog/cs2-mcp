"""v0.10 tools (2026-09-23): waterfronts and other helpers that replaced long scratch scripts.

- level_segments / deck_height: keep any new road at an absolute height (piers, causeways, level viaducts)
- shore_points: where the water is relative to a road or an area (waterline, seaward direction, depth)
- piers: perpendicular piers off a road out over the water, with level decks and optional T heads
- offset_road: a parallel street at a fixed distance from a road or path, with connectors back to it
- building_access: a building's front edge and where a road must run to serve it (optionally builds it)
- arcs: concentric circular arcs and radial streets that meet in exact shared junctions (fans, crescents, rings)
All generic: every city decision (which road, spacing, lengths) comes from the caller.
"""
import math
import geom, survey, tools2


# ----------------------------------------------------------------------------- terrain / water sampling
def heights(rpc, pts):
    out = []
    for i in range(0, len(pts), 400):
        r = rpc("terrain_height", {"points": [[float(p[0]), float(p[1])] for p in pts[i:i + 400]]})
        out += [q[1] for q in r["points"]]
    return out


def level_segments(rpc, segs, deck, min_elev=0.0):
    """Set each segment's end elevations so both ends sit at absolute height `deck` (never below min_elev). Ends that
    snap onto existing roads keep those roads' height anyway (the bridge ignores elevation there)."""
    if not segs: return segs
    hs = heights(rpc, [s["start"] for s in segs] + [s["end"] for s in segs]); n = len(segs)
    for i, s in enumerate(segs):
        e = [max(min_elev, deck - hs[i]), max(min_elev, deck - hs[n + i])]
        s["elevation"] = [round(v, 2) if v > 0.2 else 0.0 for v in e]
    return segs


class WaterGrid:
    """terrain_grid over a box (auto step under the bridge's 90k-sample limit): height + water depth lookups."""
    def __init__(self, rpc, box, step=None):
        area = (box["xmax"] - box["xmin"]) * (box["zmax"] - box["zmin"])
        step = step or max(5, int(math.ceil(math.sqrt(area / 85000))))
        g = rpc("terrain_grid", dict(box, step=step))
        self.H, self.W = g["heights"], g.get("water_depth") or [[0] * g["nx"] for _ in range(g["nz"])]
        self.o, self.st, self.nx, self.nz = g["origin"], g["step"], g["nx"], g["nz"]

    def _ij(self, p):
        i = int(round((p[0] - self.o[0]) / self.st)); j = int(round((p[1] - self.o[1]) / self.st))
        return (i, j) if 0 <= i < self.nx and 0 <= j < self.nz else None

    def depth(self, p):
        ij = self._ij(p); return self.W[ij[1]][ij[0]] if ij else 0.0

    def height(self, p):
        ij = self._ij(p); return self.H[ij[1]][ij[0]] if ij else None

    def march(self, p, d, max_m, step_m=4.0, wet=0.5):
        """First point along direction d (unit) from p where the water is deeper than `wet`; None if not within max_m."""
        s = step_m
        while s <= max_m:
            q = (p[0] + d[0] * s, p[1] + d[1] * s)
            if self.depth(q) > wet: return s, q
            s += step_m
        return None


def box_around(pts, pad):
    xs = [p[0] for p in pts]; zs = [p[1] for p in pts]
    return {"xmin": min(xs) - pad, "zmin": min(zs) - pad, "xmax": max(xs) + pad, "zmax": max(zs) + pad}


# ----------------------------------------------------------------------------- the line to work along
def road_chain(rpc, ids):
    """Existing road edges (ids) chained end to end -> dense polyline [(x,z)], its heights, and the edge ids in order."""
    want = set(ids)
    net = rpc("road_network", {"xmin": -9000, "zmin": -9000, "xmax": 9000, "zmax": 9000, "limit": 60000})
    E = {e["id"]: e for e in net["edges"] if e["id"] in want}
    missing = want - set(E)
    if missing: raise ValueError(f"road ids not found: {sorted(missing)[:10]}")
    by_node = {}
    for e in E.values():
        for n in (e["start_node"], e["end_node"]): by_node.setdefault(n, []).append(e["id"])
    ends = [n for n, es in by_node.items() if len(es) == 1]
    cur = ends[0] if ends else next(iter(by_node))
    order, used, pts = [], set(), []
    while True:
        nxt = [i for i in by_node.get(cur, []) if i not in used]
        if not nxt: break
        e = E[nxt[0]]; used.add(e["id"]); order.append(e["id"])
        poly = survey.polyline(e["curve"], max(4, int(e.get("length", 50) / 5)))
        if e["start_node"] != cur: poly = poly[::-1]
        pts += poly if not pts else poly[1:]
        cur = e["end_node"] if e["start_node"] == cur else e["start_node"]
    if len(used) < len(E): raise ValueError("the road ids do not form one continuous chain")
    return [(p[0], p[1]) for p in pts], order


def work_line(rpc, a, fr=None):
    """The line a tool works along: road_ids (existing roads, exact) or knots (smoothed through, frame accepted)."""
    if a.get("road_ids"):
        line, order = road_chain(rpc, a["road_ids"]); return line, True
    if a.get("knots"):
        line, _ = tools2.path_line(dict(a, step=5.0), fr); return [tuple(p) for p in line], False
    raise ValueError("give road_ids (existing roads to work along) or knots")


def stations(line, spacing, start_m=0.0, end_m=0.0):
    """Points every `spacing` m along the polyline (skipping start_m / end_m at the ends) with unit tangents."""
    L = geom.cumlen(line); out = []; s = start_m + spacing / 2 if spacing > 0 else start_m
    while s <= L[-1] - end_m:
        i = max(1, min(len(line) - 1, next(k for k in range(len(L)) if L[k] >= s)))
        p0, p1 = line[i - 1], line[i]; t = (s - L[i - 1]) / ((L[i] - L[i - 1]) or 1)
        P = (p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t)
        d = math.dist(p0, p1) or 1; out.append((s, P, ((p1[0] - p0[0]) / d, (p1[1] - p0[1]) / d)))
        s += spacing
    return out


# ----------------------------------------------------------------------------- shore facts
def shore_points(rpc, a, fr=None):
    """Along a road/path: every spacing_m, how far the water is on each side, the waterline point, the seaward direction,
    the depth probe_m beyond the waterline and the land height at the waterline."""
    line, _ = work_line(rpc, a, fr)
    reach, probe = a.get("max_reach_m", 250.0), a.get("probe_m", 60.0)
    wg = WaterGrid(rpc, box_around(line, reach + probe + 40))
    out = []
    for s, P, t in stations(line, a.get("spacing_m", 50.0), a.get("start_m", 0.0), a.get("end_m", 0.0)):
        rec = {"at_m": round(s), "point": [round(P[0], 1), round(P[1], 1)]}
        for side, n in (("left", (-t[1], t[0])), ("right", (t[1], -t[0]))):
            hit = wg.march(P, n, reach)
            if not hit: continue
            dist, W = hit
            q = (W[0] + n[0] * probe, W[1] + n[1] * probe)
            h = wg.height((W[0] - n[0] * 6, W[1] - n[1] * 6))
            rec[side] = {"water_m": round(dist), "waterline": [round(W[0], 1), round(W[1], 1)], "seaward": [round(n[0], 3), round(n[1], 3)],
                         "depth_beyond": round(wg.depth(q), 1), "land_height": round(h, 1) if h is not None else None}
        out.append(rec)
    return {"points": out, "count": len(out)}


# ----------------------------------------------------------------------------- piers
def _crosses(p, q, polys, skip=()):
    for eid, pl in polys:
        if eid in skip: continue
        for u, v in zip(pl, pl[1:]):
            if geom._inter(p, q, u, v) is not None: return eid
    return None


def build_piers(rpc, a, fr=None):
    """Piers off a road (road_ids, exact T junctions) or a path (knots), every spacing_m, straight out over the water on
    `side` (auto = the side with water nearer). Each pier: road point -> waterline -> length_m beyond, deck level at
    deck_height (absolute) or the waterline land height + deck_above_m. Optional head = {length_m, type?} T crosspiece.
    Candidates that miss water, hit shallow water, cross other roads or come closer than min_gap_m are skipped."""
    line, exact = work_line(rpc, a, fr)
    typ = a.get("type", "QuayLarge01"); length = a.get("length_m", 120.0); reach = a.get("max_reach_m", 200.0)
    min_depth = a.get("min_depth_m", 2.0); gap = a.get("min_gap_m", a.get("spacing_m", 150.0) * 0.6)
    head = a.get("head"); piece = a.get("piece_m", 60.0)
    wg = WaterGrid(rpc, box_around(line, reach + length + 60))
    near = box_around(line, reach + length + 60)
    net = rpc("road_network", dict(near, limit=40000))["edges"]
    polys = [(e["id"], [tuple(p) for p in survey.polyline(e["curve"], 12)]) for e in net
             if not any(k in e["type"] for k in ("Pipe", "voltage", "Cable", "Seaway"))]
    own = set(a.get("road_ids") or [])
    accepted, skipped, segs = [], [], []
    if a.get("at"):   # piers exactly at the line points nearest to these (e.g. opposite cross streets -> 4-way junctions)
        dense = stations(line, 2.0)
        cand = [min(dense, key=lambda st: math.dist(st[1], tuple(q))) for q in tools2.pts_world(a["at"], fr)]
    else:
        cand = stations(line, a.get("spacing_m", 150.0), a.get("start_m", 0.0), a.get("end_m", 0.0))
    for s, P, t in cand:
        sides = {"left": (-t[1], t[0]), "right": (t[1], -t[0])}
        hits = {k: wg.march(P, n, reach) for k, n in sides.items() if a.get("side", "auto") in ("auto", k)}
        hits = {k: v for k, v in hits.items() if v}
        if not hits: skipped.append({"at_m": round(s), "why": f"no water within {reach:.0f} m"}); continue
        side = min(hits, key=lambda k: hits[k][0]); n = sides[side]; dist, Wp = hits[side]
        tip = (Wp[0] + n[0] * length, Wp[1] + n[1] * length)
        depths = [wg.depth((Wp[0] + n[0] * length * f, Wp[1] + n[1] * length * f)) for f in (0.5, 0.75, 1.0)]
        if min(depths) < min_depth: skipped.append({"at_m": round(s), "why": f"shallow ({min(depths):.1f} m) out there"}); continue
        if any(math.dist(P, q["from"]) < gap for q in accepted): skipped.append({"at_m": round(s), "why": "too close to the previous pier"}); continue
        start = (P[0] + n[0] * 0.5, P[1] + n[1] * 0.5) if not exact else P
        hit = _crosses((P[0] + n[0] * 8, P[1] + n[1] * 8), tip, polys, own)
        if hit: skipped.append({"at_m": round(s), "why": f"would cross road {hit}"}); continue
        land_h = wg.height((Wp[0] - n[0] * 6, Wp[1] - n[1] * 6)) or 0.0
        deck = a.get("deck_height", land_h + a.get("deck_above_m", 1.0))
        pts = [P] + ([Wp] if dist > 12 else [])
        k = max(1, math.ceil(length / piece))
        pts += [(Wp[0] + n[0] * length * i / k, Wp[1] + n[1] * length * i / k) for i in range(1, k + 1)]
        pier = [{"start": [round(p[0], 3), round(p[1], 3)], "end": [round(q[0], 3), round(q[1], 3)], "type": typ} for p, q in zip(pts, pts[1:])]
        if head:
            hl = head.get("length_m", 60.0) / 2
            for sgn in (1, -1):
                e = (tip[0] + t[0] * hl * sgn, tip[1] + t[1] * hl * sgn)
                pier.append({"start": [round(tip[0], 3), round(tip[1], 3)], "end": [round(e[0], 3), round(e[1], 3)], "type": head.get("type", typ)})
        level_segments(rpc, pier[1:] if exact else pier, deck)
        if exact:   # first piece starts on the road: keep the road's height there, reach the deck at the waterline
            h1 = heights(rpc, [pier[0]["end"]])[0]; pier[0]["elevation"] = [0.0, round(max(0.0, deck - h1), 2)]
        accepted.append({"at_m": round(s), "from": P, "side": side, "shore_m": round(dist), "deck_height": round(deck, 1), "segments": len(pier)})
        segs += pier
    info = {"piers": len(accepted), "skipped": skipped[:40], "pier_list": [{k: (v if k != "from" else [round(v[0], 1), round(v[1], 1)]) for k, v in q.items()} for q in accepted]}
    return segs, info


# ----------------------------------------------------------------------------- parallel street
def build_offset_road(rpc, a, fr=None):
    """A street parallel to a road (road_ids) or path (knots) at offset_m (+ = left of its direction), trimmed by start_m /
    end_m, with straight connectors back to it every connect_every_m (0 = none) and at both ends (connect_ends)."""
    line, exact = work_line(rpc, a, fr)
    L = geom.cumlen(line)
    # zoning spacing (v0.27): centre to centre = source half + 96 m (6 cells each side) + new half puts both streets'
    # blocks back to back - full depth, no overlap, no dead gap between the zoning edges. That is the default.
    widths = {r["name"]: r["width"] for r in rpc("list_road_types")["road_types"]}
    w_new = widths.get(a["type"], 12.0)
    w_src = a.get("source_width")
    if w_src is None and a.get("road_ids"):
        xs = [p_[0] for p_ in line]; zs = [p_[1] for p_ in line]
        net = rpc("road_network", {"xmin": min(xs) - 5, "xmax": max(xs) + 5, "zmin": min(zs) - 5, "zmax": max(zs) + 5, "limit": 5000})
        ws = [e["width"] for e in net["edges"] if e["id"] in a["road_ids"]]
        w_src = max(ws) if ws else None
    w_src = w_src or w_new
    # a source that gets no zoning (highway, ramp, track, pedestrian path) only needs ONE 48 m strip between them
    src_zones = a.get("source_zones")
    if src_zones is None:
        src_types = []
        if a.get("road_ids"):
            src_types = [e["type"] for e in net["edges"] if e["id"] in a["road_ids"]] if "net" in dir() else []
        src_zones = not any(k in t for t in src_types for k in ("Highway", "Ramp", "Track", "Train", "Pedestrian"))
    full = w_src / 2 + (2 if src_zones else 1) * 6 * 8.0 + w_new / 2
    off = a.get("offset_m")
    if off is None or off == "full":
        off = full * (-1 if a.get("side", "left") == "right" else 1)
    gap = abs(off) - full
    zinfo = {"offset_m": round(off, 1), "full_depth_offset_m": round(full, 1), "gap_between_zoning_m": round(gap, 1),
             "source_zones": src_zones}
    if gap < -2: zinfo["warning"] = f"blocks overlap by {-gap:.0f} m: both streets get shallow zoning (use {full:.0f} m)"
    elif 2 < gap < w_new + 96: zinfo["warning"] = f"a {gap:.0f} m dead gap between the zoning edges that nothing will fill (use {full:.0f} m, or >= {full + w_new + 96:.0f} m for another street between)"
    s0, s1 = a.get("start_m", 0.0), L[-1] - a.get("end_m", 0.0)
    sec = [p for p, d in zip(line, L) if s0 <= d <= s1]
    back = geom.offset_line(sec, off)
    every = a.get("connect_every_m", 200.0)
    conns = []
    if every:
        for s, P, t in stations(sec, every):
            n = (-t[1], t[0]); conns.append((P, (P[0] + n[0] * off, P[1] + n[1] * off)))
    if a.get("connect_ends", True):
        conns = [(sec[0], back[0])] + conns + [(sec[-1], back[-1])]
    knots = back[::6] + ([back[-1]] if (len(back) - 1) % 6 else [])
    segs, info = tools2.build_path({"knots": [list(k) for k in knots], "type": a["type"], "piece_m": a.get("piece_m", 100.0),
                                    "joints": [list(q) for _, q in conns]})
    nodes = [tuple(s["start"]) for s in segs] + [tuple(segs[-1]["end"])]
    ctype = a.get("connector_type", a["type"])
    for P, Q in conns:
        Qn = min(nodes, key=lambda v: math.dist(v, Q))
        if math.dist(P, Qn) > 3: segs.append({"start": [round(P[0], 3), round(P[1], 3)], "end": [round(Qn[0], 3), round(Qn[1], 3)], "type": ctype})
    info["connectors"] = len(conns); info["zoning"] = zinfo
    return segs, info


# ----------------------------------------------------------------------------- building access
def building_access(rpc, a):
    """Front edge of a building's lot (from its position, facing and lot size) and the centreline a road of `type` needs
    to serve it; which existing roads run along that front. build = true places that road (extend_m past both corners)."""
    bid = a["id"]
    b = None
    for q in rpc("query_buildings", {"limit": 100000})["buildings"]:
        if q["id"] == bid: b = q; break
    if not b: raise ValueError(f"no building {bid}")
    f = b["forward"]; r = (f[1], -f[0]); c = (b["position"][0], b["position"][2])
    w, d = b["lot"][0] * 8.0, b["lot"][1] * 8.0
    mid = (c[0] + f[0] * d / 2, c[1] + f[1] * d / 2)
    left = (mid[0] - r[0] * w / 2, mid[1] - r[1] * w / 2); right = (mid[0] + r[0] * w / 2, mid[1] + r[1] * w / 2)
    widths = {t["name"]: t["width"] for t in rpc("list_road_types", {"filter": a.get("type", "")})["road_types"]} if a.get("type") else {}
    hw = widths.get(a.get("type"), 16.0) / 2
    off = hw + a.get("margin_m", 0.5)
    ext = a.get("extend_m", 0.0)
    cl = [(left[0] + f[0] * off - r[0] * ext, left[1] + f[1] * off - r[1] * ext),
          (right[0] + f[0] * off + r[0] * ext, right[1] + f[1] * off + r[1] * ext)]
    out = {"id": bid, "type": b["type"], "lot_m": [w, d], "front_edge": [[round(v, 1) for v in left], [round(v, 1) for v in right]],
           "facing": f, "road_centreline": [[round(v, 1) for v in p] for p in cl], "centre_offset_m": off}
    E = rpc("road_network", dict(box_around([left, right], 60), limit=5000))["edges"]
    along = []
    for e in E:
        if any(k in e["type"] for k in ("Pipe", "voltage", "Cable", "Seaway", "Invisible")): continue
        pl = survey.polyline(e["curve"], 16)
        dmin = min(abs((p[0] - mid[0]) * f[0] + (p[1] - mid[1]) * f[1]) for p in pl)
        lat = min(abs((p[0] - mid[0]) * r[0] + (p[1] - mid[1]) * r[1]) for p in pl)
        if lat <= w / 2 + 20 and dmin <= hw + 25: along.append({"id": e["id"], "type": e["type"], "front_distance_m": round(dmin, 1)})
    out["roads_along_front"] = along
    try: out["status"] = rpc("building_status", {"id": bid})["buildings"][0]
    except Exception: out["status"] = "needs mod 0.10 (building_status)"
    if a.get("build"):
        seg = [{"start": [round(cl[0][0], 3), round(cl[0][1], 3)], "end": [round(cl[1][0], 3), round(cl[1][1], 3)], "type": a["type"]}]
        out["build"] = rpc("place_roads", {"segments": seg, "snap_tolerance": a.get("snap_tolerance", 3.0), "dry_run": a.get("dry_run", False), "timeout_s": 60})
    return out


# ----------------------------------------------------------------------------- along the shore (quays, promenades)
def shore_line(rpc, a, fr=None):
    """The waterline between `from` and `to` (points near the shore; frame accepted) offset_m from the water's edge
    (+ = inland, - = out over the water), densified every 5 m: (points, left_is_water, WaterGrid). Shared by quays / paths
    (build_shore_path) and objects placed along a shore (tools8)."""
    import tools3
    A, B = [tuple(p) for p in tools2.pts_world([a["from"], a["to"]], fr)]
    box = box_around([A, B], a.get("pad_m", 250.0))
    o = tools3.outline(rpc, dict(box, kind="water", tolerance_m=a.get("tolerance_m", 3.0), min_length_m=50.0, limit=30,
                                 **({"step": a["step"]} if a.get("step") else {})))
    best = None
    for ln in o["lines"]:
        P = [tuple(p) for p in ln["points"]]
        ia = min(range(len(P)), key=lambda i: math.dist(P[i], A)); ib = min(range(len(P)), key=lambda i: math.dist(P[i], B))
        score = math.dist(P[ia], A) + math.dist(P[ib], B)
        if best is None or score < best[0]: best = (score, P, ia, ib)
    if best is None: raise ValueError("no waterline found near from/to")
    _, P, ia, ib = best
    sec = P[ia:ib + 1] if ia <= ib else P[ib:ia + 1][::-1]
    if len(sec) < 2: raise ValueError("from and to map to the same waterline point")
    dense = geom.catmull_rom(sec, 5.0) if a.get("smooth_raw", False) else _densify(sec, 5.0)
    wg = WaterGrid(rpc, box_around(dense, 60))
    i = len(dense) // 2; p0, p1 = dense[max(0, i - 1)], dense[min(len(dense) - 1, i + 1)]
    d = math.dist(p0, p1) or 1; n = (-(p1[1] - p0[1]) / d, (p1[0] - p0[0]) / d)
    left_is_water = wg.depth((dense[i][0] + n[0] * 12, dense[i][1] + n[1] * 12)) > wg.depth((dense[i][0] - n[0] * 12, dense[i][1] - n[1] * 12))
    off = a.get("offset_m", 0.0) * (-1 if left_is_water else 1)
    return (geom.offset_line(dense, off) if off else dense), left_is_water, wg


def build_shore_path(rpc, a, fr=None):
    """A net that follows the waterline from `from` to `to` (points near the shore; frame accepted), offset_m from the
    water's edge (+ = inland, - = out over the water), smoothed with knots every smooth_m (default 30). For quay walls,
    promenades, lakeshore drives. Uses the water outline at the terrain grid's resolution (step, default auto)."""
    line, left_is_water, _ = shore_line(rpc, a, fr)
    step = a.get("smooth_m", 30.0); L = geom.cumlen(line)
    knots, nxt = [line[0]], step
    for p, s in zip(line, L):
        if s >= nxt: knots.append(p); nxt += step
    if math.dist(knots[-1], line[-1]) > step / 3: knots.append(line[-1])
    else: knots[-1] = line[-1]
    if a.get("join_from"): knots.insert(0, tuple(tools2.pts_world([a["join_from"]], fr)[0]))   # exact existing node to start on
    if a.get("join_to"): knots.append(tuple(tools2.pts_world([a["join_to"]], fr)[0]))
    segs, info = tools2.build_path({"knots": [list(k) for k in knots], "type": a["type"], "piece_m": a.get("piece_m", 80.0),
                                    "elevation": a.get("elevation")})
    info["water_side"] = "left" if left_is_water else "right"
    return segs, info


def _densify(pts, step):
    out = [pts[0]]
    for p, q in zip(pts, pts[1:]):
        k = max(1, int(math.dist(p, q) // step))
        out += [(p[0] + (q[0] - p[0]) * i / k, p[1] + (q[1] - p[1]) * i / k) for i in range(1, k + 1)]
    return out


# ----------------------------------------------------------------------------- terraforming
def terraform(rpc, a, fr=None):
    """Brush the terrain like the in-game tool (mod 0.10): type level | shift | soften | slope, brush size_m, strength,
    frames. Where: points [[x,z] or [x,z,target_y]], or line = [[x,z], ...] with width_m (brush points every size/3 along it,
    and across it when wider than the brush). Target height for level: target_height (absolute), or target = 'sides' ->
    per point the median ground height side_m beyond the line's edges on both sides (to erase a berm / fill a trench so it
    matches its surroundings)."""
    size = a.get("size_m", 30.0)
    pts = []
    if a.get("line"):
        line = [tuple(p) for p in tools2.pts_world(a["line"], fr)]
        w = a.get("width_m", size); dense = _densify(line, max(4.0, size / 3))
        across = [0.0] if w <= size else [(-w / 2 + size / 2) + k * (size / 3) for k in range(int((w - size) / (size / 3)) + 1)]
        side = w / 2 + a.get("side_m", 20.0)
        probes = []
        for i, P in enumerate(dense):
            p0, p1 = dense[max(0, i - 1)], dense[min(len(dense) - 1, i + 1)]
            d = math.dist(p0, p1) or 1; n = (-(p1[1] - p0[1]) / d, (p1[0] - p0[0]) / d)
            for o in across: pts.append([P[0] + n[0] * o, P[1] + n[1] * o])
            probes.append((len(across), [(P[0] + n[0] * side, P[1] + n[1] * side), (P[0] - n[0] * side, P[1] - n[1] * side)]))
        if a.get("target") == "sides":
            hs = heights(rpc, [q for _, pr in probes for q in pr]); k = 0; tg = []
            for cnt, pr in probes:
                t = sorted(hs[k:k + len(pr)]); k += len(pr); tg += [t[len(t) // 2] if len(t) % 2 else (t[0] + t[1]) / 2] * cnt
            pts = [[p[0], p[1], round(t, 2)] for p, t in zip(pts, tg)]
    else:
        pts = [list(p) for p in tools2.pts_world([q[:2] for q in a["points"]], fr)]
        pts = [p + ([a["points"][i][2]] if len(a["points"][i]) > 2 else []) for i, p in enumerate(pts)]
    q = {"type": a.get("type", "level"), "size": size, "strength": a.get("strength", 1.0), "frames": a.get("frames", 90),
         "points": [[round(v, 2) for v in p] for p in pts], "dry_run": a.get("dry_run", False), "timeout_s": 120}
    if a.get("target_height") is not None: q["target_height"] = a["target_height"]
    if a.get("brush"): q["brush"] = a["brush"]
    tools2.follow(rpc, a, [p[:2] for p in pts])
    out = rpc("terraform", q)
    out["brush_points"] = len(pts)
    return out


# ----------------------------------------------------------------------------- arcs
def build_arcs(a, fr=None):
    """Concentric arcs around `center` (frame accepted) and radial streets between them, junctions shared exactly.
    arcs: [{radius, from_deg, to_deg, type?}] (angles: 0 = +a / +x, counter-clockwise), radials: [{deg, r0, r1, type?}].
    Each arc is split where a radial crosses it; radials are split at every arc they cross."""
    c = tools2.pts_world([a["center"]], fr)[0] if fr else a["center"]
    rot = math.radians((fr and a.get("frame", {}).get("heading_deg", 0.0)) or 0.0)
    typ = a.get("type", "Small Road"); maxlen = a.get("piece_m", 110.0)
    P = lambda R, t: (c[0] + R * math.cos(t + rot), c[1] + R * math.sin(t + rot))
    arcs = a.get("arcs", []); rads = a.get("radials", [])
    segs = []
    for arc in arcs:
        R = arc["radius"]; t0, t1 = math.radians(arc["from_deg"]), math.radians(arc["to_deg"])
        cuts = sorted({t0, t1} | {math.radians(r["deg"]) for r in rads if min(t0, t1) < math.radians(r["deg"]) < max(t0, t1)
                                  and r.get("r0", 0) <= R <= r.get("r1", 1e9)}, reverse=t0 > t1)
        for u, v in zip(cuts, cuts[1:]):
            n = max(1, math.ceil(abs(v - u) * R / maxlen))
            for i in range(n):
                a1, a2 = u + (v - u) * i / n, u + (v - u) * (i + 1) / n
                k = 4 / 3 * math.tan((a2 - a1) / 4) * R
                p1, p2 = P(R, a1), P(R, a2)
                d1 = (-math.sin(a1 + rot), math.cos(a1 + rot)); d2 = (-math.sin(a2 + rot), math.cos(a2 + rot))
                segs.append({"start": [round(p1[0], 3), round(p1[1], 3)], "end": [round(p2[0], 3), round(p2[1], 3)], "type": arc.get("type", typ),
                             "controls": [[round(p1[0] + k * d1[0], 3), round(p1[1] + k * d1[1], 3)], [round(p2[0] - k * d2[0], 3), round(p2[1] - k * d2[1], 3)]]})
    for r in rads:
        t = math.radians(r["deg"]); r0, r1 = r["r0"], r["r1"]
        stops = sorted({r0, r1} | {arc["radius"] for arc in arcs if r0 < arc["radius"] < r1
                                   and min(arc["from_deg"], arc["to_deg"]) <= r["deg"] <= max(arc["from_deg"], arc["to_deg"])})
        for u, v in zip(stops, stops[1:]):
            n = max(1, math.ceil((v - u) / maxlen))
            for i in range(n):
                p1, p2 = P(u + (v - u) * i / n, t), P(u + (v - u) * (i + 1) / n, t)
                segs.append({"start": [round(p1[0], 3), round(p1[1], 3)], "end": [round(p2[0], 3), round(p2[1], 3)], "type": r.get("type", typ)})
    return segs, {"arcs": len(arcs), "radials": len(rads), "segments": len(segs)}
