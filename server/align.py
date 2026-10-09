"""v0.36 (2026-09-27): zone-cell alignment - zero cracks inside street blocks.

Game rule (decompiled Game.Zones.BlockSystem.CreateBlocks, Game.Tools.GenerateEdgesSystem): a road's cells are laid on a
line from node to node, extended at each end by
    continuous end (another zonable edge of the same cell width carries straight on, same half flag there):
        s = 4 * ((cellWidth odd) XOR half)
    other end:  s = cellWidth*4 - (8 if cellWidth > 1) + (half ? (odd ? -4 : +4) : 0)
then n = floor((len+0.1)/8) cells CENTRED on that line. A crossing road's rows start cellWidth*4 m from its centreline.
Cells whose edges do not coincide with the crossing road's rows straddle them, get hidden, and leave a crack (4 m for
a Medium Road, 3 cells wide). The game's road tool sets Road.Start/EndHalfAligned to line them up; MCP-built roads
never had them. plan() picks the flags per node exactly (chain DP along each straight road line); apply() sets them via
the mod's road_align (>= 0.36), then re-zones every regenerated cell with the zone its old neighbour had.
cracks() measures the result from the live zone blocks.
Generic: nothing city-specific - it only reads roads and zone blocks in the given box."""
import math, collections
import survey, tools2

SKIP = ("Highway", "Pipe", "voltage", "Track", "Train", "Cable", "Seaway", "Invisible", "Ramp", "Bridge", "Pathway", "Path")


def cell_width(w): return int(math.ceil(w / 8.0 - 0.01))


def _unit(v):
    l = math.hypot(*v); return (v[0] / l, v[1] / l) if l > 1e-9 else (0.0, 0.0)


def _ext(cw, half, continuous):
    odd = cw & 1
    if continuous: return 4.0 if (bool(odd) ^ bool(half)) else 0.0
    return cw * 4.0 - (8.0 if cw > 1 else 0.0) + ((-4.0 if odd else 4.0) if half else 0.0)


def _phase_err(x, c):
    e = (x - c) % 8.0
    return min(e, 8.0 - e)


def edge_cost(L, cw, h0, h1, cont0, cont1, targets0, targets1):
    """misalignment (m) of this edge's cell edges against the crossing roads' rows at both ends"""
    s0, s1 = _ext(cw, h0, cont0), _ext(cw, h1, cont1)
    lam = L + s0 + s1
    n = math.floor((lam + 0.1) / 8.0)
    if n < 2: return 0.0
    first = -s0 + (lam - 8 * n) / 2.0                 # first cell edge, measured from the start node along the edge
    last = -s1 + (lam - 8 * n) / 2.0                  # same, from the end node
    return sum(_phase_err(first, c) for c in targets0) + sum(_phase_err(last, c) for c in targets1)


def _network(rpc, box):
    net = rpc("road_network", dict(box, limit=40000))
    edges = [e for e in net["edges"] if "flow" in e and not any(k in e["type"] for k in SKIP)
             and max(e.get("elevation") or [0]) < 4]
    return edges


def plan(rpc, a):
    """Flags that line every straight zonable road's cells up with its crossing roads. box = xmin..zmax.
    Returns changes [{id, start_half, end_half}] + predicted misalignment before/after (metres, summed over ends)."""
    box = {k: a[k] for k in ("xmin", "zmin", "xmax", "zmax")}
    edges = _network(rpc, box)
    info, at = {}, collections.defaultdict(list)
    for e in edges:
        c = e["curve"]; p0 = (c[0][0], c[0][2]); p3 = (c[3][0], c[3][2])
        chord = math.dist(p0, p3)
        t0 = _unit((c[1][0] - c[0][0], c[1][2] - c[0][2])) if math.dist(p0, (c[1][0], c[1][2])) > 0.1 else _unit((p3[0] - p0[0], p3[1] - p0[1]))
        t1 = _unit((c[3][0] - c[2][0], c[3][2] - c[2][2])) if math.dist(p3, (c[2][0], c[2][2])) > 0.1 else _unit((p3[0] - p0[0], p3[1] - p0[1]))
        info[e["id"]] = dict(e=e, cw=cell_width(e["width"]), L=e["length"], straight=chord > 0.995 * e["length"],
                             out={e["start_node"]: (-t0[0], -t0[1]), e["end_node"]: t1}, half=list(e.get("half") or [False, False]))
        at[e["start_node"]].append(e["id"]); at[e["end_node"]].append(e["id"])

    def partner(eid, node):
        """the edge carrying straight on through node with the same cell width (candidate for 'continuous')"""
        d = info[eid]["out"][node]
        for o in at[node]:
            if o == eid: continue
            do = info[o]["out"][node]
            if d[0] * do[0] + d[1] * do[1] < -0.99 and info[o]["cw"] == info[eid]["cw"]: return o
        return None

    def targets(eid, node):
        d = info[eid]["out"][node]; t = []
        for o in at[node]:
            if o == eid: continue
            do = info[o]["out"][node]
            if abs(d[0] * do[0] + d[1] * do[1]) < 0.2:           # a crossing road at ~90 deg
                t.append((info[o]["cw"] * 4.0) % 8.0)
        return t

    # straight road lines: chains of edges joined by partners
    seen, lines = set(), []
    for eid in info:
        if eid in seen or not info[eid]["straight"]: continue
        chain = [eid]; seen.add(eid)
        for direction in (0, 1):
            cur = eid
            node = info[cur]["e"]["end_node"] if direction == 0 else info[cur]["e"]["start_node"]
            while True:
                nxt = partner(cur, node)
                if not nxt or nxt in seen or not info[nxt]["straight"]: break
                seen.add(nxt)
                chain = chain + [nxt] if direction == 0 else [nxt] + chain
                e2 = info[nxt]["e"]; node = e2["end_node"] if e2["start_node"] == node else e2["start_node"]; cur = nxt
        lines.append(chain)

    changes, residual, before, after = [], [], 0.0, 0.0
    for chain in lines:
        # node sequence along the chain
        if len(chain) == 1:
            e = info[chain[0]]["e"]; nodes = [e["start_node"], e["end_node"]]
        else:
            e0, e1 = info[chain[0]]["e"], info[chain[1]]["e"]
            shared = {e0["start_node"], e0["end_node"]} & {e1["start_node"], e1["end_node"]}
            first = e0["start_node"] if e0["end_node"] in shared else e0["end_node"]
            nodes = [first]
            for eid in chain:
                e = info[eid]["e"]; nodes.append(e["end_node"] if e["start_node"] == nodes[-1] else e["start_node"])
        k = len(chain)
        def cost(i, hA, hB):
            eid = chain[i]; d = info[eid]; e = d["e"]; nA, nB = nodes[i], nodes[i + 1]
            contA, contB = (i > 0), (i < k - 1)            # interior nodes of the chain carry straight on (same flag both sides)
            if e["start_node"] == nA:
                return edge_cost(d["L"], d["cw"], hA, hB, contA, contB, targets(eid, nA), targets(eid, nB))
            return edge_cost(d["L"], d["cw"], hB, hA, contB, contA, targets(eid, nB), targets(eid, nA))
        # current cost
        def cur_h(i, node):
            e = info[chain[i]]["e"]; h = info[chain[i]]["half"]
            return h[0] if e["start_node"] == node else h[1]
        for i in range(k):
            before += cost(i, cur_h(i, nodes[i]), cur_h(i, nodes[i + 1]))
        # Viterbi over node flags h_0..h_k (prefer unchanged flags on ties)
        best = {h: (0.0, [h]) for h in (False, True)}
        for i in range(k):
            nb = {}
            for hB in (False, True):
                cands = []
                for hA, (cs, path) in best.items():
                    tie = 0.001 * ((hA != cur_h(i, nodes[i])) + (hB != cur_h(i, nodes[i + 1])))
                    cands.append((cs + cost(i, hA, hB) + tie, path + [hB]))
                nb[hB] = min(cands, key=lambda t: t[0])
            best = nb
        total, path = min(best.values(), key=lambda t: t[0])
        for i in range(k):
            eid = chain[i]; e = info[eid]["e"]
            hS = path[i] if e["start_node"] == nodes[i] else path[i + 1]
            hE = path[i + 1] if e["start_node"] == nodes[i] else path[i]
            ci = cost(i, path[i], path[i + 1]); after += ci
            if ci > 0.5:                                   # flags alone can't fit this edge: its length has to change
                d = info[eid]; L = d["L"]; fit = None
                for dl in sorted((x * 0.5 for x in range(-16, 17)), key=abs):
                    d["L"] = L + dl
                    if min(cost(i, hA, hB) for hA in (False, True) for hB in (False, True)) < 0.3: fit = dl; break
                d["L"] = L
                mid = survey.bez(e["curve"], 0.5)
                residual.append({"id": eid, "type": e["type"], "length": round(L, 1), "misalignment_m": round(ci, 1),
                                 "mid": [round(mid[0], 1), round(mid[1], 1)], "length_change_to_fit_m": fit})
            if [hS, hE] != info[eid]["half"] and (not a.get("only_ids") or eid in a["only_ids"]):
                changes.append({"id": eid, "start_half": hS, "end_half": hE})
    return {"lines": len(lines), "edges": len(info), "changes": changes,
            "misalignment_m_before": round(before, 1), "misalignment_m_after": round(after, 1),
            "residual": sorted(residual, key=lambda r: -r["misalignment_m"])}


# ----------------------------------------------------------------------------- measure
def _cells(rpc, box):
    zb = rpc("zone_blocks", dict(box, limit=100000))
    out = []
    for bi, b in enumerate(zb["blocks"]):
        p = b["pos"]; d = b["dir"]; r = (d[1], -d[0]); sx, sy = b["size"]
        for i, c in enumerate(b["cells"]):
            x, y = i % sx, i // sx
            ctr = (p[0] + d[0] * 4 * (sy - 2 * y - 1) + r[0] * 4 * (sx - 2 * x - 1), p[2] + d[1] * 4 * (sy - 2 * y - 1) + r[1] * 4 * (sx - 2 * x - 1))
            out.append((bi, ctr, c, (d[0], d[1])))
    return zb, out


def crack_area(rpc, a, step=2.0):
    """What the eye sees: m2 of land inside the box that is not under a visible cell or a road but has visible cells on
    BOTH sides within 9 m (along the local grid axes) - the slivers between blocks. 0 = zero cracks."""
    box = {k: a[k] for k in ("xmin", "zmin", "xmax", "zmax")}
    zb, cells = _cells(rpc, box)
    vis = [(c, d) for _, c, ch, d in cells if ch != "#"]
    grid = collections.defaultdict(list)
    for c, d in vis: grid[(int(c[0] // 12), int(c[1] // 12))].append((c, d))
    segs = []
    for e in rpc("road_network", dict(box, limit=40000))["edges"]:
        pl = survey.polyline(e["curve"], max(4, int(e["length"] / 6)))
        segs += [(p, q, e.get("width", 8) / 2) for p, q in zip(pl, pl[1:])]
    rgrid = collections.defaultdict(list)
    for s in segs:
        for p in (s[0], s[1]): rgrid[(int(p[0] // 40), int(p[1] // 40))].append(s)
    def on_road(p):
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for u, v, w in rgrid.get((int(p[0] // 40) + dx, int(p[1] // 40) + dz), ()):
                    L2 = (v[0] - u[0]) ** 2 + (v[1] - u[1]) ** 2 or 1e-9
                    t = max(0.0, min(1.0, ((p[0] - u[0]) * (v[0] - u[0]) + (p[1] - u[1]) * (v[1] - u[1])) / L2))
                    if math.dist(p, (u[0] + t * (v[0] - u[0]), u[1] + t * (v[1] - u[1]))) < w + 0.5: return True
        return False
    def near(p):
        out = []
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                out += grid.get((int(p[0] // 12) + dx, int(p[1] // 12) + dz), [])
        return out
    area, spots = 0.0, collections.Counter()
    x = box["xmin"]
    while x < box["xmax"]:
        z = box["zmin"]
        while z < box["zmax"]:
            p = (x, z); nb = near(p)
            if nb:
                inside = any(abs((p[0] - c[0]) * d[0] + (p[1] - c[1]) * d[1]) <= 4 and abs((p[0] - c[0]) * -d[1] + (p[1] - c[1]) * d[0]) <= 4 for c, d in nb)
                if not inside and not on_road(p):
                    d = nb[0][1]; sides = set()
                    for c, _ in nb:
                        u = (c[0] - p[0]) * d[0] + (c[1] - p[1]) * d[1]; v = (c[0] - p[0]) * -d[1] + (c[1] - p[1]) * d[0]
                        if abs(v) < 4 and 0 < abs(u) < 9: sides.add(("u", u > 0))
                        if abs(u) < 4 and 0 < abs(v) < 9: sides.add(("v", v > 0))
                    if {("u", True), ("u", False)} <= sides or {("v", True), ("v", False)} <= sides:
                        area += step * step; spots[(round(x / 50) * 50, round(z / 50) * 50)] += 1
            z += step
        x += step
    return {"crack_m2": round(area), "hotspots": [{"pos": list(k), "m2": n * step * step} for k, n in spots.most_common(8)]}


def cracks(rpc, a):
    """Misaligned neighbours: visible cells of DIFFERENT blocks, parallel grids, centres < 12.5 m apart but not on the same
    8 m lattice (off by > 1 m on either axis). 0 = every cell lines up. Also counts the land between them."""
    box = {k: a[k] for k in ("xmin", "zmin", "xmax", "zmax")}
    zb, cells = _cells(rpc, box)
    vis = [(bi, c, d) for bi, c, ch, d in cells if ch != "#"]
    grid = collections.defaultdict(list)
    for v in vis: grid[(int(v[1][0] // 16), int(v[1][1] // 16))].append(v)
    bad, spots = 0, collections.Counter()
    for bi, c, d in vis:
        kx, kz = int(c[0] // 16), int(c[1] // 16)
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for bj, c2, d2 in grid.get((kx + dx, kz + dz), ()):
                    if bj <= bi: continue
                    if abs(d[0] * d2[1] - d[1] * d2[0]) > 0.02 and abs(d[0] * d2[0] + d[1] * d2[1]) > 0.02: continue   # not parallel/perpendicular
                    dist = math.dist(c, c2)
                    if dist > 12.5: continue
                    u = (c2[0] - c[0]) * d[0] + (c2[1] - c[1]) * d[1]; v = (c2[0] - c[0]) * -d[1] + (c2[1] - c[1]) * d[0]
                    off = max(_phase_err(u, 0), _phase_err(v, 0))
                    if off > 1.0:
                        bad += 1; spots[(round(c[0] / 50) * 50, round(c[1] / 50) * 50)] += 1
    return {"misaligned_neighbours": bad, "hotspots": [{"pos": list(k), "count": n} for k, n in spots.most_common(8)],
            "visible_cells": len(vis), "note": "0 = zero cracks: every visible cell sits on its neighbours' 8 m lattice"}


# ----------------------------------------------------------------------------- apply
def after_build(rpc, segs):
    """Called by the road builders after a build (mod >= 0.36): line the NEW edges' cells up with the roads they meet.
    New edges = edges whose midpoint lies on a placed segment; neighbours are left alone (a crossing road's rows do not
    depend on its flags). Never raises - alignment is a bonus, not the build."""
    try:
        ver = tuple(int(x) for x in str(rpc("ping", {}).get("mod_version", "0")).split(".")[:2])
        if ver < (0, 36) or not segs: return None
        lines = []
        for sg in segs:
            pts = [sg["start"]] + ([sg["control"]] if sg.get("control") else []) + [sg["end"]]
            lines.append([(q[0], q[1]) for q in pts])
        allp = [q for l in lines for q in l]
        xs = [q[0] for q in allp]; zs = [q[1] for q in allp]
        box = {"xmin": min(xs) - 60, "zmin": min(zs) - 60, "xmax": max(xs) + 60, "zmax": max(zs) + 60}
        def on_new(e):
            m = survey.bez(e["curve"], 0.5)
            for l in lines:
                for u, v in zip(l, l[1:]):
                    L2 = (v[0] - u[0]) ** 2 + (v[1] - u[1]) ** 2 or 1e-9
                    t = max(0.0, min(1.0, ((m[0] - u[0]) * (v[0] - u[0]) + (m[1] - u[1]) * (v[1] - u[1])) / L2))
                    if math.dist(m, (u[0] + t * (v[0] - u[0]), u[1] + t * (v[1] - u[1]))) < (6.0 if len(l) > 2 else 1.5): return True
            return False
        ids = {e["id"] for e in _network(rpc, box) if on_new(e)}
        if not ids: return None
        r = apply(rpc, dict(box, only_ids=ids, rezone=False, settle_s=1))
        return {"new_edges": len(ids), "flags_set": len(r.get("changes", [])), "misalignment_m_after": r.get("misalignment_m_after"),
                "residual": r.get("residual", [])[:10]}
    except Exception as ex:
        return {"error": str(ex)[:200]}


def apply(rpc, a):
    """plan() -> road_align (mod >= 0.36) -> wait for the blocks to rebuild -> re-zone each new empty cell with the zone
    of the nearest old cell (<= 6 m). dry_run returns the plan only."""
    import time
    p = plan(rpc, a)
    if a.get("dry_run") or not p["changes"]: return p
    box = {k: a[k] for k in ("xmin", "zmin", "xmax", "zmax")}
    _, old = _cells(rpc, box)
    old_z = [(c, ch) for _, c, ch, _ in old if ch not in ".#"]
    legend = rpc("zone_blocks", dict(box, limit=1))["legend"]
    res = rpc("road_align", {"edges": p["changes"]})
    p["road_align"] = {k: res.get(k) for k in ("edges_to_change", "applied", "undo_id", "skipped")}
    time.sleep(float(a.get("settle_s", 3)))
    zb, new = _cells(rpc, box)
    idx = collections.defaultdict(list)
    for c, ch in old_z: idx[(int(c[0] // 8), int(c[1] // 8))].append((c, ch))
    items = []
    for bi, c, ch, d in new:
        if ch != ".": continue
        best, bd = None, 6.0
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for c2, ch2 in idx.get((int(c[0] // 8) + dx, int(c[1] // 8) + dz), ()):
                    dd = math.dist(c, c2)
                    if dd < bd: best, bd = ch2, dd
        if best:
            dirv = d; r = (dirv[1], -dirv[0]); h = 3.2
            q = [[c[0] + (-r[0] - dirv[0]) * h, c[1] + (-r[1] - dirv[1]) * h], [c[0] + (r[0] - dirv[0]) * h, c[1] + (r[1] - dirv[1]) * h],
                 [c[0] + (r[0] + dirv[0]) * h, c[1] + (r[1] + dirv[1]) * h], [c[0] + (-r[0] + dirv[0]) * h, c[1] + (-r[1] + dirv[1]) * h]]
            items.append((q, legend[best]))
    ok, bad = tools2.zone_many(rpc, items, overwrite=False) if a.get("rezone", True) else (0, 0)
    p["rezoned_cells"] = ok; p["rezone_refused"] = bad
    p["cracks_after"] = cracks(rpc, a)["misaligned_neighbours"]
    return p


# ----------------------------------------------------------------------------- lattice: kerb-to-kerb = 8k
# Derived from the block rule above: with the half flags set (after_build does that), a straight road's cells line up
# with both crossing roads iff the KERB-TO-KERB distance between consecutive crossing roads is a whole multiple of 8 m
# (road widths counted as cellWidth*8: Small 16, Medium 24). Off by even 0.5 m = a whole hidden cell = a crack.
def _fam_key(d):
    return round(math.degrees(math.atan2(d[1], d[0])) % 180.0)


def _lines(items, tol=0.6):
    """items: [{p0, p1, w, movable, ...}] straight segments -> {family angle: (D, N, [line dicts])}"""
    fams = collections.defaultdict(list)
    for it in items:
        d = _unit((it["p1"][0] - it["p0"][0], it["p1"][1] - it["p0"][1]))
        if d == (0.0, 0.0): continue
        k = _fam_key(d); it["_d"] = d
        # merge 179/0
        fams[k % 180].append(it)
    out = {}
    for k, its in fams.items():
        a = math.radians(k); D = (math.cos(a), math.sin(a)); N = (-D[1], D[0])
        lines = []
        for it in sorted(its, key=lambda it: (it["p0"][0] + it["p1"][0]) / 2 * N[0] + (it["p0"][1] + it["p1"][1]) / 2 * N[1]):
            o = ((it["p0"][0] + it["p1"][0]) / 2) * N[0] + ((it["p0"][1] + it["p1"][1]) / 2) * N[1]
            s0 = it["p0"][0] * D[0] + it["p0"][1] * D[1]; s1 = it["p1"][0] * D[0] + it["p1"][1] * D[1]
            if lines and abs(lines[-1]["o"] - o) < tol:
                L = lines[-1]; L["items"].append(it); L["w"] = max(L["w"], it["w"]); L["movable"] = L["movable"] and it["movable"]
                L["s"] = (min(L["s"][0], s0, s1), max(L["s"][1], s0, s1))
            else:
                lines.append({"o": o, "w": it["w"], "movable": it["movable"], "items": [it], "s": (min(s0, s1), max(s0, s1))})
        out[k] = (D, N, lines)
    return out


def _crossed(la, lb, perp_lines, N):
    """do some perpendicular roads span from line a to line b (i.e. the two lines face each other across a block)?"""
    lo, hi = sorted((la["o"], lb["o"]))
    for pl in perp_lines:
        ext = [p[0] * N[0] + p[1] * N[1] for it in pl["items"] for p in (it["p0"], it["p1"])]
        if min(ext) <= lo + 1.0 and max(ext) >= hi - 1.0: return True
    return False


def lattice_solve(items):
    """Shift movable lines (<= 4 m) so every pair of facing lines is kerb-to-kerb 8k apart. Returns (moves, residual):
    moves = {(family, line index): delta_m}, residual = pairs that fixed lines leave off-lattice."""
    fams = _lines(items)
    moves, residual = {}, []
    for k, (D, N, lines) in fams.items():
        perp = fams.get((k + 90) % 180, (None, None, []))[2]
        if not lines or not perp: continue
        order = list(range(len(lines)))
        # anchors: fixed lines; if none, the first line
        anchors = [i for i in order if not lines[i]["movable"]] or [0]
        pos = {i: lines[i]["o"] for i in order}
        done = set(anchors)
        def fit(i, j):                                   # place j relative to already placed i
            g = abs(pos[j] - pos[i]) - lines[i]["w"] / 2 - lines[j]["w"] / 2
            want = lines[i]["w"] / 2 + lines[j]["w"] / 2 + 8 * round(g / 8)
            return pos[i] + (want if pos[j] > pos[i] else -want)
        for a in anchors:
            for step in (1, -1):
                i = a
                while 0 <= i + step < len(lines):
                    j = i + step
                    if not _crossed(lines[i], lines[j], perp, N): i = j; continue
                    if j in done and not lines[j]["movable"]:
                        g = abs(pos[j] - pos[i]) - lines[i]["w"] / 2 - lines[j]["w"] / 2
                        off = g - 8 * round(g / 8)
                        if abs(off) > 0.1: residual.append({"family_deg": k, "gap_m": round(g, 2), "off_m": round(off, 2),
                                                             "at": [round(c, 1) for c in lines[j]["items"][0]["p0"]]})
                        i = j; continue
                    if j in done and lines[j]["movable"] and abs(pos[j] - lines[j]["o"]) > 1e-6: break   # placed from the other side
                    t = fit(i, j)
                    if abs(t - lines[j]["o"]) <= 4.01:
                        pos[j] = t; done.add(j)
                        if abs(t - lines[j]["o"]) > 0.01: moves[(k, j)] = t - lines[j]["o"]
                    else:
                        residual.append({"family_deg": k, "gap_m": None, "off_m": round(t - lines[j]["o"], 2), "at": [round(c, 1) for c in lines[j]["items"][0]["p0"]]})
                    i = j
    return fams, moves, residual


def snap_segments(segs, widths, fixed_edges=()):
    """Move planned straight segments (dicts with start/end [x,z], type) onto the lattice. fixed_edges = existing
    straight road edges near the plan ({curve, width}) used as anchors. Returns (new segs, report)."""
    items = []
    for i, s in enumerate(segs):
        if s.get("control") or s.get("controls"): continue
        items.append({"p0": tuple(s["start"][:2]), "p1": tuple(s["end"][:2]), "w": widths.get(s.get("type"), 16),
                      "movable": True, "seg": i})
    for e in fixed_edges:
        c = e["curve"]
        items.append({"p0": (c[0][0], c[0][2]), "p1": (c[3][0], c[3][2]), "w": e.get("width", 16), "movable": False})
    fams, moves, residual = lattice_solve(items)
    out = [dict(s) for s in segs]
    moved = 0
    for (k, j), delta in moves.items():
        D, N, lines = fams[k]; L = lines[j]; o = L["o"]
        shift = (N[0] * delta, N[1] * delta)
        def on_line(p): return abs(p[0] * N[0] + p[1] * N[1] - o) < 0.6 and L["s"][0] - 1 <= p[0] * D[0] + p[1] * D[1] <= L["s"][1] + 1
        for s in out:
            for key in ("start", "end"):
                p = s[key]
                if on_line(p):
                    s[key] = [round(p[0] + shift[0], 3), round(p[1] + shift[1], 3)] + list(p[2:]); moved += 1
    return out, {"lines_moved": len(moves), "max_move_m": round(max([abs(v) for v in moves.values()] or [0]), 2),
                 "endpoints_moved": moved, "off_lattice_left": residual[:30]}


def lattice_check(rpc, a):
    """Existing roads in a box: facing pairs of parallel lines whose kerb-to-kerb gap is not 8k (each one a crack)."""
    box = {k: a[k] for k in ("xmin", "zmin", "xmax", "zmax")}
    items = []
    for e in _network(rpc, box):
        c = e["curve"]
        if math.dist((c[0][0], c[0][2]), (c[3][0], c[3][2])) < 0.995 * e["length"]: continue
        items.append({"p0": (c[0][0], c[0][2]), "p1": (c[3][0], c[3][2]), "w": cell_width(e["width"]) * 8, "movable": True})
    fams, moves, residual = lattice_solve(items)
    offs = [{"family_deg": k, "move_m": round(v, 2), "line_at": [round(x, 1) for x in fams[k][2][j]["items"][0]["p0"]]}
            for (k, j), v in moves.items()]
    return {"lines_off_lattice": len(offs), "moves_to_fit": sorted(offs, key=lambda r: -abs(r["move_m"]))[:40],
            "unfixable": residual[:20]}


# ----------------------------------------------------------------------------- repair: move an existing line onto the lattice
def move_line(rpc, a):
    """Shift one existing straight road line by `delta` m along its normal (lattice_check -> moves_to_fit gives
    family_deg / line_at / move_m). Rebuilds the line's edges shifted, the cross streets ending on it (their near end
    follows, they stay straight), heals the through road where the line ends in a T (the two pieces become one, so no
    split node / seam is left behind), after clearing the growable buildings within clear_m of the line. Zoning is
    snapshotted first and restored cell by cell. dry_run = counts only."""
    import time, pave
    k = float(a["family_deg"]); delta = float(a["delta"]); at = tuple(a["line_at"])
    D = (math.cos(math.radians(k)), math.sin(math.radians(k))); N = (-D[1], D[0])
    o = at[0] * N[0] + at[1] * N[1]
    clear_m = float(a.get("clear_m", 52.0))
    box0 = {"xmin": at[0] - 700, "zmin": at[1] - 700, "xmax": at[0] + 700, "zmax": at[1] + 700}
    net = rpc("road_network", dict(box0, limit=40000))
    edges = [e for e in net["edges"] if "flow" in e and not any(t in e["type"] for t in SKIP)]
    P = lambda c: (c[0], c[2])
    def off(p): return p[0] * N[0] + p[1] * N[1]
    def along(p): return p[0] * D[0] + p[1] * D[1]
    def straight(e): return math.dist(P(e["curve"][0]), P(e["curve"][3])) >= 0.995 * e["length"]
    def par(e):
        d = _unit((e["curve"][3][0] - e["curve"][0][0], e["curve"][3][2] - e["curve"][0][2])); return abs(d[0] * D[0] + d[1] * D[1]) > 0.999
    # the line: parallel straight edges on offset o, grown from the edge nearest `at` through collinear neighbours
    cand = [e for e in edges if straight(e) and par(e) and abs(off(P(e["curve"][0])) - o) < 0.6 and abs(off(P(e["curve"][3])) - o) < 0.6]
    if not cand: return {"error": "no straight road line there (ids/positions change on every load - re-run lattice_check)"}
    seed = min(cand, key=lambda e: abs(along(at) - (along(P(e["curve"][0])) + along(P(e["curve"][3]))) / 2))
    line, frontier = {seed["id"]}, [seed]
    nodes_of = collections.defaultdict(list)
    for e in edges: nodes_of[e["start_node"]].append(e); nodes_of[e["end_node"]].append(e)
    byid = {e["id"]: e for e in cand}
    while frontier:
        e = frontier.pop()
        for n in (e["start_node"], e["end_node"]):
            for f in nodes_of[n]:
                if f["id"] in byid and f["id"] not in line: line.add(f["id"]); frontier.append(f)
    L = [byid[i] for i in line]
    nodes = {n for e in L for n in (e["start_node"], e["end_node"])}
    npos = {nd["id"]: (nd["pos"][0], nd["pos"][2]) for nd in net["nodes"]}
    ext = sorted(along(npos[n]) for n in nodes if n in npos)
    s_lo, s_hi = ext[0], ext[-1]
    end_nodes = [n for n in nodes if n in npos and along(npos[n]) in (s_lo, s_hi)]
    # cross streets touching the line (not part of it)
    touch = [e for e in edges if e["id"] not in line and (e["start_node"] in nodes or e["end_node"] in nodes)]
    ramps = [e for e in net["edges"] if any(t in e["type"] for t in ("Highway", "Ramp")) and (e["start_node"] in nodes or e["end_node"] in nodes)]
    if ramps:                                           # 2026-09-27: a shifted avenue could not rejoin its ramp terminal (refused, undone)
        return {"error": "the line meets a highway ramp - ramp terminals can't move; leave this line or redesign the ramp"}
    heal, rebuild_touch = [], []
    for n in end_nodes:
        through = [e for e in touch if n in (e["start_node"], e["end_node"]) and not par(e) and straight(e)]
        if len(through) == 2:                               # the line ends in a T on a through road: rebuild it whole
            far = [P(e["curve"][3]) if e["start_node"] == n else P(e["curve"][0]) for e in through]
            heal.append({"ids": [e["id"] for e in through], "seg": {"start": list(far[0]), "end": list(far[1]), "type": through[0]["type"]}})
    healed = {i for h in heal for i in h["ids"]}
    for e in touch:
        if e["id"] in healed: continue
        if not straight(e): return {"error": f"a curved street ends on the line near {[round(c) for c in P(e['curve'][0])]} - move it by hand"}
        rebuild_touch.append(e)
    sh = (N[0] * delta, N[1] * delta)
    def mv(p): return [round(p[0] + sh[0], 3), round(p[1] + sh[1], 3)]
    new = [{"start": mv(P(e["curve"][0])), "end": mv(P(e["curve"][3])), "type": e["type"]} for e in L]
    for e in rebuild_touch:
        s0, s1 = P(e["curve"][0]), P(e["curve"][3])
        new.append({"start": mv(s0) if e["start_node"] in nodes else list(s0), "end": mv(s1) if e["end_node"] in nodes else list(s1), "type": e["type"]})
    new += [h["seg"] for h in heal]
    old_ids = [e["id"] for e in L] + [e["id"] for e in rebuild_touch] + sorted(healed)
    # buildings to clear: growables whose centre is within clear_m of the line (and along its extent) or of a rebuilt street end
    blds = rpc("building_status", dict(box0, limit=100000))["buildings"]
    import tools5
    grow = [b for b in blds if tools5.category(b["type"]) in ("residential", "mixed", "commercial", "office", "industrial")
            and "Signature" not in b["type"] and abs(off(tuple(b["position"])) - o) < clear_m and s_lo - 8 <= along(tuple(b["position"])) <= s_hi + 8]
    area = {"xmin": min(npos[n][0] for n in nodes) - 120, "zmin": min(npos[n][1] for n in nodes) - 120,
            "xmax": max(npos[n][0] for n in nodes) + 120, "zmax": max(npos[n][1] for n in nodes) + 120}
    out = {"line_edges": len(L), "cross_streets_rebuilt": len(rebuild_touch), "through_roads_healed": len(heal),
           "buildings_to_clear": len(grow), "delta_m": delta, "line_length_m": round(s_hi - s_lo, 1)}
    if a.get("dry_run"): return out
    before = crack_area(rpc, area)["crack_m2"]
    zb0, old = _cells(rpc, area); legend = zb0["legend"]
    old_z = [(c, ch) for _, c, ch, _ in old if ch not in ".#"]
    ids = [b["id"] for b in grow]
    for i in range(0, len(ids), 40):
        rpc("bulldoze", {"ids": ids[i:i + 40], "buildings": True, "roads": False, "timeout_s": 120}); time.sleep(3); rpc("ping", {})
    for i in range(0, len(old_ids), 12):
        rpc("bulldoze", {"ids": old_ids[i:i + 12], "roads": True, "buildings": False, "timeout_s": 120}); time.sleep(3); rpc("ping", {})
    res, bad = pave.place_skip_failing(rpc, new, 1.5)
    out["placed"] = len(new) - len(bad); out["refused"] = bad
    time.sleep(2)
    out["alignment"] = after_build(rpc, new)
    time.sleep(3)
    zb, cur = _cells(rpc, area)
    idx = collections.defaultdict(list)
    for c, ch in old_z: idx[(int(c[0] // 8), int(c[1] // 8))].append((c, ch))
    items = []
    for bi, c, ch, d in cur:
        if ch != ".": continue
        best, bd = None, 20.0          # moved streets shift cells up to ~12 m: look wide, nearest wins
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for c2, ch2 in idx.get((int(c[0] // 8) + dx, int(c[1] // 8) + dz), ()):
                    dd = math.dist(c, c2)
                    if dd < bd: best, bd = ch2, dd
        if best:
            r = (d[1], -d[0]); h = 3.2
            items.append(([[c[0] + (-r[0] - d[0]) * h, c[1] + (-r[1] - d[1]) * h], [c[0] + (r[0] - d[0]) * h, c[1] + (r[1] - d[1]) * h],
                           [c[0] + (r[0] + d[0]) * h, c[1] + (r[1] + d[1]) * h], [c[0] + (-r[0] + d[0]) * h, c[1] + (-r[1] + d[1]) * h]], legend[best]))
    out["rezoned"] = tools2.zone_many(rpc, items, overwrite=False)[0]
    out["crack_m2_before"] = before; out["crack_m2_after"] = crack_area(rpc, area)["crack_m2"]
    return out
