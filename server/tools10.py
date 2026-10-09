"""v0.27 tools (2026-09-26): measure and check junctions / interchanges so each one can be designed for its own site.
Claude designs the layout; these give the facts that design needs and catch what a drawing hides.

- site_movements  turning demand at a site: cars approaching a circle (x, z, radius) sampled over time, deduped, as an
                  entry -> exit matrix per approach (bearing, road type, lanes in / out). Needs mod 0.27 (site_movements).
- network_check   facts about the roads in an area, optionally with a draft laid over them (segments / plan_id, elevation
                  relative to ground): every movement between the site's approaches - reachable, path length, detour ratio;
                  dead ends and one-way traps; per road the tightest radius vs its design speed and the steepest grade;
                  merge / diverge angles; weaving length between an on-ramp merge and the next off-ramp split; lane balance
                  (auxiliary lane before an off-ramp / after an on-ramp - the user's standing rule); vertical clearance where
                  roads cross without a junction. Lane directions come from mod 0.27 (lanes=true); older mods: inferred
                  from the type name ('Oneway' = one way, start -> end) and flagged as inferred.
Generic: thresholds are parameters; nothing is built.
"""
import math, heapq, collections, re, time
import survey, zoning

R_EF = 0.25                       # superelevation + side friction used for the radius check: R = v^2 / (127 * R_EF)
SPEED_BY_KIND = {"highway": 100, "ramp": 60, "arterial": 60, "local": 40}
GRADE_BY_KIND = {"highway": 0.06, "ramp": 0.08, "arterial": 0.08, "local": 0.10}
SKIP = ("Pipe", "Power", "voltage", "Cable", "Track", "Train", "Subway", "Tram", "Pedestrian", "Path", "Seaway", "Invisible", "Quay")


def _kind(t, lanes_total):
    tl = t.lower()
    if "highway" in tl:
        return "ramp" if (("1 lane" in tl or "ramp" in tl) and lanes_total <= 1) or "ramp" in tl else "highway"
    if any(k in tl for k in ("large", "xl", "divided", "avenue", "boulevard")): return "arterial"
    return "local"


def _name_lanes(t):
    m = re.search(r"(\d+)\s*lane", t.lower())
    n = int(m.group(1)) if m else 1
    if "oneway" in t.lower().replace(" ", "").replace("-", ""): return (n, 0)
    return (n, n)


def _bez(c, t):
    u = 1 - t
    return [u ** 3 * c[0][k] + 3 * u * u * t * c[1][k] + 3 * u * t * t * c[2][k] + t ** 3 * c[3][k] for k in range(3)]


def _curve_facts(c, n=24):
    """(length, min radius m, max grade) of a 3-D cubic Bezier [[x,y,z] x4]."""
    P = [_bez(c, i / n) for i in range(n + 1)]
    L = sum(math.dist((a[0], a[2]), (b[0], b[2])) for a, b in zip(P, P[1:]))
    rmin = float("inf")
    for a, b, d in zip(P, P[1:], P[2:]):
        ab = math.dist((a[0], a[2]), (b[0], b[2])); bd = math.dist((b[0], b[2]), (d[0], d[2])); ad = math.dist((a[0], a[2]), (d[0], d[2]))
        cross = abs((b[0] - a[0]) * (d[2] - a[2]) - (b[2] - a[2]) * (d[0] - a[0]))
        if cross > 1e-6: rmin = min(rmin, ab * bd * ad / (2 * cross))
    g = 0.0
    step = max(1, n // 6)                                  # grade over ~1/6 of the edge (smooths the sample noise)
    for i in range(0, n + 1 - step):
        a, b = P[i], P[i + step]
        h = math.dist((a[0], a[2]), (b[0], b[2]))
        if h > 5: g = max(g, abs(b[1] - a[1]) / h)
    return L, rmin, g


def _tangent(c, at_start):
    a, b = (c[0], c[1]) if at_start else (c[3], c[2])
    if math.dist((a[0], a[2]), (b[0], b[2])) < 1e-3: b = c[2] if at_start else c[1]
    dx, dz = b[0] - a[0], b[2] - a[2]; L = math.hypot(dx, dz) or 1
    return (dx / L, dz / L)                                # pointing away from that end into the edge


def _draft_edges(rpc, segs, nodes, widths):
    """Draft segments (place_roads shape, elevation relative to ground) -> edge dicts like road_network's, with nodes
    snapped to existing nodes within 3 m / to each other."""
    pts = [tuple(s["start"]) for s in segs] + [tuple(s["end"]) for s in segs]
    ground = rpc("terrain_height", {"points": [list(p) for p in pts]})["points"]      # [[x, y, z], ...]
    gh = {p: q[1] for p, q in zip(pts, ground)}
    npos = {nid: (p[0], p[2]) for nid, p in nodes.items()}
    made, out = {}, []
    def node_for(p, y):
        for nid, q in npos.items():
            if math.dist(q, p) <= 3: return nid
        for nid, q in made.items():
            if math.dist(q, p) <= 1.5: return nid
        nid = f"draft:{len(made)}"; made[nid] = p; nodes[nid] = [p[0], y, p[1]]; return nid
    for k, s in enumerate(segs):
        el = s.get("elevation", 0); el = el if isinstance(el, list) else [el, el]
        p0, p3 = tuple(s["start"]), tuple(s["end"])
        y0 = gh.get(p0, 0) + el[0]; y3 = gh.get(p3, 0) + el[1]
        if s.get("controls"): c1, c2 = s["controls"]
        elif s.get("control"):
            c = s["control"]; c1 = (p0[0] + 2 / 3 * (c[0] - p0[0]), p0[1] + 2 / 3 * (c[1] - p0[1])); c2 = (p3[0] + 2 / 3 * (c[0] - p3[0]), p3[1] + 2 / 3 * (c[1] - p3[1]))
        else: c1 = (p0[0] + (p3[0] - p0[0]) / 3, p0[1] + (p3[1] - p0[1]) / 3); c2 = (p0[0] + 2 * (p3[0] - p0[0]) / 3, p0[1] + 2 * (p3[1] - p0[1]) / 3)
        curve = [[p0[0], y0, p0[1]], [c1[0], y0 + (y3 - y0) / 3, c1[1]], [c2[0], y0 + 2 * (y3 - y0) / 3, c2[1]], [p3[0], y3, p3[1]]]
        t = s.get("type", "Medium Road")
        out.append({"id": f"draft:{k}", "type": t, "width": widths.get(t, 12), "curve": curve,
                    "start_node": node_for(p0, y0), "end_node": node_for(p3, y3), "elevation": el, "draft": True})
    return out


def _load(rpc, a, area):
    net = rpc("road_network", dict(area.params, limit=30000, lanes=True))
    edges = [e for e in net["edges"] if "flow" in e or "Highway" in e["type"]]
    edges = [e for e in edges if not any(k.lower() in e["type"].lower() for k in SKIP)]
    nodes = {n["id"]: n["pos"] for n in net["nodes"]}
    outside = {n["id"] for n in net["nodes"] if n.get("outside_connection")}
    lanes_known = any("lanes" in e for e in edges)
    draft = a.get("segments")
    if a.get("plan_id"):
        import json, os
        with open(os.path.join(survey.OUT_DIR, "plans", a["plan_id"] + ".json")) as fh: draft = json.load(fh)["segments"]
    if draft:
        widths = {r["name"]: r["width"] for r in rpc("list_road_types")["road_types"]}
        edges += _draft_edges(rpc, draft, nodes, widths)
    for e in edges:
        if "lanes" not in e: e["lanes"] = list(_name_lanes(e["type"])); e["lanes_inferred"] = True
        elif re.search(r"\d+\s*lane", e["type"].lower()):
            # mod 0.27 / 0.28 count split lane pieces twice on long edges: trust the lane number in the type name
            nf, nb = _name_lanes(e["type"])
            if list(e["lanes"]) != [nf, nb] and (e["lanes"][0] in (nf, 2 * nf) and e["lanes"][1] in (nb, 2 * nb)):
                e["lanes"] = [nf, nb]; e["lanes_from_name"] = True
        e["kind"] = _kind(e["type"], sum(e["lanes"]))
    return edges, nodes, outside, lanes_known


def network_check(rpc, a, camera_ground=None):
    t0 = time.time()
    site = a.get("site")                                    # [x, z]; radius = a['radius']
    if site and not any(k in a for k in ("xmin", "center")):
        R = float(a.get("radius", 250)); pad = R + 150
        area = survey.Area(site[0] - pad, site[1] - pad, site[0] + pad, site[1] + pad)
    else:
        area = survey.area_from(a, camera_ground)
    edges, nodes, outside, lanes_known = _load(rpc, a, area)
    speeds = dict(SPEED_BY_KIND, **(a.get("design_speed_kmh") or {}))
    grades = dict(GRADE_BY_KIND, **(a.get("max_grade") or {}))
    min_clear = float(a.get("min_clearance_m", 6.0)); weave_min = float(a.get("weave_min_m", 400.0))

    # ---- directed graph: arcs (u -> v, edge, length); per node the arcs in / out with their tangents
    arcs_out = collections.defaultdict(list); arcs_in = collections.defaultdict(list)
    facts = {}
    for e in edges:
        L, rmin, g = _curve_facts(e["curve"])
        facts[e["id"]] = (L, rmin, g)
        f, b = e["lanes"]
        s, t = e["start_node"], e["end_node"]
        if f: arcs_out[s].append((t, e, L, "f")); arcs_in[t].append((s, e, L, "f"))
        if b: arcs_out[t].append((s, e, L, "b")); arcs_in[s].append((t, e, L, "b"))

    def inside(nid):
        p = nodes.get(nid); return p and area.x0 <= p[0] <= area.x1 and area.z0 <= p[2] <= area.z1

    issues = collections.defaultdict(list)

    # ---- dead ends and one-way traps (nodes fully inside, not map edges)
    margin = 60
    for nid, p in nodes.items():
        if not (area.x0 + margin <= p[0] <= area.x1 - margin and area.z0 + margin <= p[2] <= area.z1 - margin): continue
        if nid in outside: continue
        ins, outs = len(arcs_in[nid]), len(arcs_out[nid])
        touching = {x[1]["id"] for x in arcs_in[nid] + arcs_out[nid]}
        if ins and not outs: issues["one_way_trap"].append({"pos": [round(p[0]), round(p[2])], "note": "traffic can enter but not leave"})
        elif outs and not ins: issues["one_way_source"].append({"pos": [round(p[0]), round(p[2])], "note": "traffic can leave but never arrive"})
        elif len(touching) == 1 and ins and outs: issues["dead_end"].append({"pos": [round(p[0]), round(p[2])], "road": next(iter(touching))})

    # ---- geometry per road
    for e in edges:
        L, rmin, g = facts[e["id"]]
        v = speeds[e["kind"]]; need = v * v / (127 * R_EF)
        mid = _bez(e["curve"], 0.5)
        if L > 15 and rmin < need * 0.97:                  # 3 % tolerance: arcs drawn exactly at the limit are fine
            issues["tight_curve"].append({"road": e["id"], "type": e["type"], "pos": [round(mid[0]), round(mid[2])],
                                          "radius_m": round(rmin), "needs_m": round(need), "design_kmh": v})
        if g > grades[e["kind"]]:
            issues["steep"].append({"road": e["id"], "type": e["type"], "pos": [round(mid[0]), round(mid[2])],
                                    "grade_pct": round(100 * g, 1), "max_pct": round(100 * grades[e["kind"]], 1)})

    # ---- merges / diverges on one-way carriageways: angle, lane balance, weaving
    def oneway(e): return (e["lanes"][0] == 0) != (e["lanes"][1] == 0)
    def lanes_dir(e, d): return e["lanes"][0] if d == "f" else e["lanes"][1]
    def tan_at(e, nid, d):                                  # travel direction at node nid for arc direction d
        at_start = e["start_node"] == nid
        tx, tz = _tangent(e["curve"], at_start)
        return (tx, tz) if (d == "f") == at_start else (-tx, -tz)
    merges, diverges = {}, {}
    for nid in nodes:
        if not inside(nid): continue
        ins = [x for x in arcs_in[nid] if oneway(x[1])]; outs = [x for x in arcs_out[nid] if oneway(x[1])]
        if len(ins) == 2 and len(outs) == 1 and len(arcs_in[nid]) == 2 and len(arcs_out[nid]) == 1:
            (_, e1, _, d1), (_, e2, _, d2) = ins; (_, eo, _, do) = outs[0]
            t1 = tan_at(e1, nid, d1); t2 = tan_at(e2, nid, d2)
            ang = math.degrees(math.acos(max(-1, min(1, t1[0] * t2[0] + t1[1] * t2[1]))))
            main, ramp = (e1, e2) if lanes_dir(e1, d1) >= lanes_dir(e2, d2) else (e2, e1)
            lm, lr, lo = lanes_dir(main, d1 if main is e1 else d2), lanes_dir(ramp, d2 if ramp is e2 else d1), lanes_dir(eo, do)
            p = nodes[nid]; merges[nid] = (eo, do)
            rec = {"pos": [round(p[0]), round(p[2])], "angle_deg": round(ang, 1), "lanes": f"{lm} + {lr} -> {lo}"}
            if ang > 25: issues["sharp_merge"].append(rec)
            if lo < lm + lr and main["kind"] == "highway":
                issues["lane_drop_at_merge"].append(dict(rec, note="on-ramp lanes vanish at the gore: add an auxiliary lane after the merge (standing rule)"))
        if len(ins) == 1 and len(outs) == 2 and len(arcs_in[nid]) == 1 and len(arcs_out[nid]) == 2:
            (_, ei, _, di) = ins[0]; (_, e1, _, d1), (_, e2, _, d2) = outs
            t1 = tan_at(e1, nid, d1); t2 = tan_at(e2, nid, d2)
            ang = math.degrees(math.acos(max(-1, min(1, t1[0] * t2[0] + t1[1] * t2[1]))))
            main, ramp = (e1, e2) if lanes_dir(e1, d1) >= lanes_dir(e2, d2) else (e2, e1)
            li, lm, lr = lanes_dir(ei, di), lanes_dir(main, d1 if main is e1 else d2), lanes_dir(ramp, d2 if ramp is e2 else d1)
            p = nodes[nid]; diverges[nid] = (ei, di)
            rec = {"pos": [round(p[0]), round(p[2])], "angle_deg": round(ang, 1), "lanes": f"{li} -> {lm} + {lr}"}
            if ang > 25: issues["sharp_diverge"].append(rec)
            if li < lm + lr and main["kind"] == "highway":
                issues["no_aux_lane_before_exit"].append(dict(rec, note="widen the carriageway +1 lane before the split so the ramp takes the extra lane (standing rule)"))
    # weaving: from each merge, follow the carriageway downstream to the first diverge
    for nid in merges:
        dist, cur, seen = 0.0, nid, set()
        while cur not in seen and dist < weave_min:
            seen.add(cur)
            outs = [x for x in arcs_out[cur] if oneway(x[1])]
            if len(outs) != 1: break
            nxt, e, L, d = outs[0]; dist += L; cur = nxt
            if cur in diverges:
                p0, p1 = nodes[nid], nodes[cur]
                issues["short_weave"].append({"from": [round(p0[0]), round(p0[2])], "to": [round(p1[0]), round(p1[2])],
                                              "length_m": round(dist), "min_m": weave_min}); break

    # ---- vertical clearance where roads cross without sharing a node
    polys = {e["id"]: [_bez(e["curve"], i / 16) for i in range(17)] for e in edges}
    ids = list(polys)
    def seg_x(p, q, r, s):
        d = (q[0] - p[0]) * (s[2] - r[2]) - (q[2] - p[2]) * (s[0] - r[0])
        if abs(d) < 1e-9: return None
        t = ((r[0] - p[0]) * (s[2] - r[2]) - (r[2] - p[2]) * (s[0] - r[0])) / d
        u = ((r[0] - p[0]) * (q[2] - p[2]) - (r[2] - p[2]) * (q[0] - p[0])) / d
        return (t, u) if 0 <= t <= 1 and 0 <= u <= 1 else None
    emap = {e["id"]: e for e in edges}
    def touches(d, e):                                     # a draft end sitting on road e (a T onto its middle)
        if not d.get("draft"): return False
        for end in (d["curve"][0], d["curve"][3]):
            if min(math.dist((end[0], end[2]), (q[0], q[2])) for q in polys[e["id"]]) <= 4: return True
        return False
    bbox = {k: (min(p[0] for p in v), max(p[0] for p in v), min(p[2] for p in v), max(p[2] for p in v)) for k, v in polys.items()}
    for i, a_ in enumerate(ids):
        ea = emap[a_]
        for b_ in ids[i + 1:]:
            eb = emap[b_]
            if {ea["start_node"], ea["end_node"]} & {eb["start_node"], eb["end_node"]}: continue
            if touches(ea, eb) or touches(eb, ea): continue
            A, B = bbox[a_], bbox[b_]
            if A[1] < B[0] or B[1] < A[0] or A[3] < B[2] or B[3] < A[2]: continue
            done = False
            for p, q in zip(polys[a_], polys[a_][1:]):
                for r, s in zip(polys[b_], polys[b_][1:]):
                    x = seg_x(p, q, r, s)
                    if not x: continue
                    ya = p[1] + (q[1] - p[1]) * x[0]; yb = r[1] + (s[1] - r[1]) * x[1]
                    sep = abs(ya - yb)
                    if sep < min_clear:
                        issues["low_clearance" if sep > 1.0 else "crossing_without_junction"].append(
                            {"roads": [a_, b_], "pos": [round(p[0] + (q[0] - p[0]) * x[0]), round(p[2] + (q[2] - p[2]) * x[0])],
                             "clearance_m": round(sep, 1), "min_m": min_clear})
                    done = True; break
                if done: break

    out = {"area": [round(area.x0), round(area.z0), round(area.x1), round(area.z1)], "roads": len(edges),
           "draft_roads": sum(1 for e in edges if e.get("draft")),
           "lane_directions": "from the game (mod 0.27)" if lanes_known else "INFERRED from type names ('Oneway' = start -> end) - restart with mod 0.27 for real lanes",
           "issue_counts": {k: len(v) for k, v in issues.items()}, "issues": {k: v[:12] for k, v in issues.items()}}

    # ---- movements between the site's approaches
    if site:
        R = float(a.get("radius", 250))
        def ins_(p): return math.dist((p[0], p[2]), site) <= R
        appr = []
        for e in edges:
            pa, pb = nodes.get(e["start_node"]), nodes.get(e["end_node"])
            if not pa or not pb or ins_(pa) == ins_(pb): continue
            inner, outer = (e["start_node"], e["end_node"]) if ins_(pa) else (e["end_node"], e["start_node"])
            f, b = e["lanes"]
            lanes_in = (b if ins_(pa) else f); lanes_out = (f if ins_(pa) else b)
            po = nodes[outer]; brg = (math.degrees(math.atan2(po[0] - site[0], po[2] - site[1])) + 360) % 360
            appr.append({"road": e["id"], "type": e["type"], "inner": inner, "outer": outer, "bearing_deg": round(brg),
                         "lanes_in": lanes_in, "lanes_out": lanes_out, "outer_pos": [round(po[0]), round(po[2])]})
        kinds = a.get("approach_kinds")
        if kinds is None and any(emap[x["road"]]["kind"] == "highway" for x in appr):
            kinds = ["highway", "ramp", "arterial"]               # an interchange: local streets are not its approaches
        if kinds: appr = [x for x in appr if emap[x["road"]]["kind"] in kinds]
        def label(x): return f"{'N NE E SE S SW W NW'.split()[int(((x['bearing_deg'] + 22.5) % 360) // 45)]} {x['type']} ({x['road']})"
        # Dijkstra from each entry, no immediate U-turn back along the same road
        def shortest(src_arc_node, src_edge, targets):
            best = {(src_arc_node, src_edge): 0.0}; pq = [(0.0, src_arc_node, src_edge)]; found = {}
            while pq:
                d, u, via = heapq.heappop(pq)
                if d > best.get((u, via), 1e18) or d > 20000: continue
                for (v, e, L, _) in arcs_out[u]:
                    if e["id"] == via: continue
                    if e["id"] in targets and v == targets[e["id"]]:
                        found.setdefault(e["id"], d + L)
                    nd = d + L
                    if nd < best.get((v, e["id"]), 1e18):
                        best[(v, e["id"])] = nd; heapq.heappush(pq, (nd, v, e["id"]))
            return found
        exits = {x["road"]: x["outer"] for x in appr if x["lanes_out"]}
        mv, missing, detours = [], [], []
        for en in appr:
            if not en["lanes_in"]: continue
            L_in = facts[en["road"]][0]
            found = shortest(en["inner"], en["road"], {k: v for k, v in exits.items() if k != en["road"]})
            for ex in appr:
                if not ex["lanes_out"] or ex["road"] == en["road"]: continue
                straight = math.dist(en["outer_pos"], ex["outer_pos"])
                if ex["road"] in found:
                    Lp = L_in + found[ex["road"]]
                    rec = {"from": label(en), "to": label(ex), "path_m": round(Lp),
                           "detour": round(Lp / straight, 2) if straight >= 100 else None}
                    mv.append(rec)
                    if straight >= 100 and Lp / straight > float(a.get("max_detour", 2.5)): detours.append(rec)
                else:
                    missing.append({"from": label(en), "to": label(ex)})
        out["site"] = {"centre": site, "radius": R, "approaches": [{k: x[k] for k in ("road", "type", "bearing_deg", "lanes_in", "lanes_out")} for x in appr],
                       "movements_possible": len(mv), "movements_missing": missing[:40], "long_detours": detours[:20],
                       "movements": mv[:80] if a.get("all_movements") else None,
                       "approach_kinds": kinds or "all",
                       "note": "every entry -> exit pair between the roads crossing the site circle; missing = no legal path; "
                               "detour = path length / straight distance between the approaches' outer ends"}
        if out["site"]["movements"] is None: del out["site"]["movements"]
    out["check_s"] = round(time.time() - t0, 1)
    if a.get("map", False):
        w = survey.World(rpc, area)
        marks = []
        for k, v in issues.items():
            tag = {"tight_curve": "R", "steep": "%", "sharp_merge": "M", "sharp_diverge": "M", "short_weave": "W",
                   "no_aux_lane_before_exit": "A", "lane_drop_at_merge": "A", "low_clearance": "C", "crossing_without_junction": "X",
                   "one_way_trap": "T", "one_way_source": "T", "dead_end": "E"}.get(k, "?")
            for it in v[:10]: marks.append({"pos": it.get("pos") or it.get("from"), "label": tag})
        img = survey.render_map(w, px=int(a.get("px", 900)), layers=["terrain", "roads", "issues"], marks=marks,
                                plan=[{k: s[k] for k in ("start", "end", "controls", "control", "type") if k in s} for s in (a.get("segments") or [])] or None)
        out["map_path"] = survey.save(img, time.strftime("netcheck_%H%M%S.png"))
        out["map_legend"] = "R tight curve, % steep, M sharp merge/diverge, W short weave, A lane balance, C low clearance, X crossing without junction, T one-way trap, E dead end"
    return out


def site_movements(rpc, a):
    """Sample the site's turning movements: samples x interval_s, cars deduped by id -> entry -> exit matrix."""
    site = a["site"]; R = float(a.get("radius", 150))
    n = int(a.get("samples", 12)); iv = float(a.get("interval_s", 2.0))
    seen, approaches = {}, {}
    for k in range(n):
        try:
            r = rpc("site_movements", {"x": site[0], "z": site[1], "radius": R, "ring": a.get("ring", 250)})
        except Exception as ex:
            if "unknown method" in str(ex).lower() or "site_movements" in str(ex):
                return {"error": "needs mod 0.27 (site_movements) - restart the game after deploying it"}
            raise
        for ap in r["approaches"]:
            if re.search(r"\d+\s*lane", ap["type"].lower()):     # mod < 0.29 double-counts split lane pieces
                nf, nb = _name_lanes(ap["type"]); n = nf or nb
                for k in ("lanes_in", "lanes_out"):
                    if ap.get(k) == 2 * n: ap[k] = n
            approaches[ap["edge"]] = ap
        old_mod = not any("passes_through" in ap for ap in r["approaches"])
        for vid, en, ex in r["cars"]:
            if old_mod and ex == "ends_inside": continue          # mod 0.27: these were cars heading AWAY from the site
            seen[vid] = (en, ex)
        if k < n - 1: time.sleep(iv)
    def label(eid):
        ap = approaches.get(eid)
        if not ap: return eid
        return f"{'N NE E SE S SW W NW'.split()[int(((ap['bearing_deg'] + 22.5) % 360) // 45)]} {ap['type']} ({eid})"
    mat = collections.Counter((en, ex) for en, ex in seen.values())
    tot = sum(mat.values()) or 1
    by_entry = collections.Counter(en for en, _ in seen.values()); by_exit = collections.Counter(ex for _, ex in seen.values())
    rows = [{"from": label(en), "to": ("straight through" if ex == en else label(ex)) if ex in approaches else ex,
             "cars": c, "share": round(c / tot, 3)} for (en, ex), c in mat.most_common()]
    return {"site": site, "radius": R, "cars_sampled": len(seen), "samples": n, "interval_s": iv,
            "approaches": [dict(ap, label=label(e)) for e, ap in approaches.items()],
            "movements": rows[: int(a.get("top", 40))],
            "entering_by_approach": {label(k): v for k, v in by_entry.most_common()},
            "leaving_by_approach": {(label(k) if k in approaches else k): v for k, v in by_exit.most_common()},
            "note": "distinct cars seen on or approaching the site over the sampling window, with the approach their planned path "
                    "leaves by; shares are of those cars. Run longer (samples / interval_s) at busy hours for a steadier picture"}
