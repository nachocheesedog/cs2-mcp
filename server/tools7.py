"""v0.15 tools (2026-09-24): transit, movement and where to put stations (mod `transit`, `movement`, `commute`).

- transit_report  lines ranked by riders / load (sampled over time), overcrowded / near-empty lines, busiest stops,
                  stops without lines, stops per district
- foot_traffic    pedestrians / cars / transit vehicles on a grid (sampled), busiest walking + driving cells, busy walking
                  cells far from any stop, walk share per district
- commute_flows   home -> work / school: district pairs, biggest long desire lines, which of them no line serves
- station_sites   uncovered demand (homes + jobs + walking) beyond a mode's catchment, clustered, with suggested sites
Generic: modes, radii and weights are parameters; nothing is city-specific.
"""
import math, time, collections
import tools5

MODE_RADIUS = {"Bus": 350, "Tram": 450, "Subway": 700, "Train": 900, "Ferry": 600, "Ship": 600}
WHERE = ("districts", "polygon", "xmin", "zmin", "xmax", "zmax")


def _areas(rpc, a):
    return tools5.pick_areas(rpc, {k: a[k] for k in WHERE if k in a})


def _nearest(pt, pts):
    best = 1e18
    for q in pts:
        d = (pt[0] - q[0]) ** 2 + (pt[1] - q[1]) ** 2
        if d < best: best = d
    return math.sqrt(best)


def _sample(rpc, method, params, samples, interval):
    out = []
    for i in range(max(1, samples)):
        if i: time.sleep(interval)
        out.append(rpc(method, params, timeout=180))
    return out


def _pct(s):
    return int(s[:-1])


def transit_report(rpc, a):
    snaps = _sample(rpc, "transit", {"include_cargo": a.get("include_cargo", False)}, int(a.get("samples", 3)), float(a.get("interval_s", 5)))
    areas = _areas(rpc, a)
    agg, stopw = {}, collections.defaultdict(list)
    for s in snaps:
        for l in s["lines"]:
            g = agg.setdefault(l["id"], {"l": l, "usage": [], "riders": [], "waiting": []})
            g["l"] = l; g["usage"].append(l["usage"]); g["riders"].append(l["riders"]); g["waiting"].append(l["waiting"])
        for st in s["stops"]: stopw[st["id"]].append(st["waiting"])
    lines = []
    for g in agg.values():
        l = g["l"]; n = len(g["usage"])
        busiest = sorted(l["stops"], key=lambda x: -x[2])[:3]
        lines.append({"line": f"{l['transport']} {l['number']}" + (f" ({l['name']})" if l.get("name") else ""), "id": l["id"],
                      "active": l["active"], "stops": len(l["stops"]), "length_km": round(l["length_m"] / 1000, 1),
                      "vehicles": l["vehicles"], "avg_load": f"{100 * sum(g['usage']) / n:.0f}%",
                      "avg_riders": round(sum(g["riders"]) / n), "avg_waiting": round(sum(g["waiting"]) / n),
                      "ticket_price": l["ticket_price"], "busiest_stops": [[b[1], b[2]] for b in busiest if b[2] > 0]})
    lines.sort(key=lambda x: -x["avg_riders"])
    stops = []
    for st in snaps[-1]["stops"]:
        w = stopw[st["id"]]
        stops.append({"id": st["id"], "mode": st["mode"], "station": st["station"], "position": st["position"],
                      "lines": len(st["lines"]), "avg_waiting": round(sum(w) / len(w), 1),
                      "district": tools5.which(tuple(st["position"]), areas)})
    top = int(a.get("top", 12))
    byd = collections.defaultdict(collections.Counter)
    for s in stops:
        c = byd[s["district"] or "(no district)"]; c[s["mode"]] += 1; c["waiting"] += s["avg_waiting"]
    by_mode = collections.defaultdict(lambda: [0, 0, 0])
    for x in lines:
        m = x["line"].split()[0]; by_mode[m][0] += 1; by_mode[m][1] += x["avg_riders"]; by_mode[m][2] += x["vehicles"]
    return {"samples": len(snaps),
            "by_mode": {m: {"lines": v[0], "riders_now": v[1], "vehicles": v[2]} for m, v in by_mode.items()},
            "lines": lines[: int(a.get("lines", 40))],
            "overcrowded_lines": [x["line"] for x in lines if x["active"] and _pct(x["avg_load"]) >= 90],
            "near_empty_lines": [x["line"] for x in lines if x["active"] and _pct(x["avg_load"]) <= 10],
            "busiest_stops": sorted(stops, key=lambda s: -s["avg_waiting"])[:top],
            "stops_without_lines": [(s["mode"], s["station"], s["position"]) for s in stops if s["lines"] == 0][:20],
            "stops_by_district": {d: {k: round(v) for k, v in c.items()} for d, c in sorted(byd.items())},
            "note": "live game values averaged over the samples; load = riders / capacity as in the line panel"}


def foot_traffic(rpc, a):
    g = float(a.get("grid_m", 100))
    areas = _areas(rpc, a)
    box = tools5.bbox([p for _, p in areas], 50)
    snaps = _sample(rpc, "movement", dict(box, grid_m=g), int(a.get("samples", 3)), float(a.get("interval_s", 4)))
    acc = collections.defaultdict(lambda: [0.0, 0.0, 0.0])
    for s in snaps:
        for x, z, pd, cr, tr in s["cells"]:
            c = acc[(x, z)]; c[0] += pd / len(snaps); c[1] += cr / len(snaps); c[2] += tr / len(snaps)
    stops = [tuple(s["position"]) for s in rpc("transit", {})["stops"]]
    cells = []
    for (x, z), (pd, cr, tr) in acc.items():
        d = tools5.which((x, z), areas)
        if d is None and any(k in a for k in WHERE): continue
        cells.append({"at": [round(x), round(z)], "pedestrians": round(pd, 1), "cars": round(cr, 1), "transit_vehicles": round(tr, 1), "district": d})
    top = int(a.get("top", 12)); far = int(a.get("far_from_stop_m", 400))
    walk = sorted(cells, key=lambda c: -c["pedestrians"])
    busiest_walk = [dict(c, nearest_stop_m=round(_nearest(c["at"], stops)) if stops else None) for c in walk[:top]]
    gaps = []
    for c in walk[: top * 6]:
        dn = _nearest(c["at"], stops) if stops else 1e9
        if dn > far: gaps.append(dict(c, nearest_stop_m=round(dn) if stops else None))
    byd = collections.defaultdict(lambda: [0.0, 0.0])
    for c in cells: b = byd[c["district"] or "(no district)"]; b[0] += c["pedestrians"]; b[1] += c["cars"]
    tot = snaps[-1]
    return {"samples": len(snaps), "grid_m": g,
            "now": {"pedestrians": tot["pedestrians"], "cars": tot["cars"], "transit_vehicles": tot["transit_vehicles"]},
            "by_district": {d: {"pedestrians": round(v[0]), "cars": round(v[1]), "walk_share": f"{100 * v[0] / max(1, v[0] + v[1]):.0f}%"}
                            for d, v in sorted(byd.items(), key=lambda kv: -kv[1][0])},
            "busiest_walking": busiest_walk, "busiest_driving": sorted(cells, key=lambda c: -c["cars"])[:top],
            f"busy_walking_over_{far}m_from_a_stop": gaps[:top],
            "note": "snapshot counts of agents on the map averaged over samples (people inside buildings / vehicles not counted)"}


def commute_flows(rpc, a):
    g = float(a.get("grid_m", 400))
    r = rpc("commute", {"grid_m": g, "limit": int(a.get("limit", 6000)), "students": a.get("students", True)}, timeout=240)
    areas = _areas(rpc, a)
    D = lambda x, z: tools5.which((x, z), areas) or "(none)"
    mat, inside = collections.Counter(), collections.Counter()
    for hx, hz, wx, wz, n, purpose in r["flows"]:
        a1, b1 = D(hx, hz), D(wx, wz)
        mat[(a1, b1)] += n
        if a1 == b1: inside[a1] += n
    walk = float(a.get("walk_m", 450))
    stop_lines = [(tuple(s["position"]), set(s["lines"])) for s in rpc("transit", {})["stops"] if s["lines"]]
    def lines_near(x, z):
        out = set()
        for p, ls in stop_lines:
            if (p[0] - x) ** 2 + (p[1] - z) ** 2 <= walk * walk: out |= ls
        return out
    min_d = float(a.get("min_distance_m", 1500)); top = int(a.get("top", 20))
    long_ = sorted((f for f in r["flows"] if math.dist((f[0], f[1]), (f[2], f[3])) >= min_d), key=lambda f: -f[4])
    desire = []
    for hx, hz, wx, wz, n, purpose in long_[: top * 3]:
        desire.append({"from": [round(hx), round(hz)], "to": [round(wx), round(wz)], "people": n, "purpose": purpose,
                       "km": round(math.dist((hx, hz), (wx, wz)) / 1000, 1), "from_district": D(hx, hz), "to_district": D(wx, wz),
                       "served_by_line": bool(lines_near(hx, hz) & lines_near(wx, wz))})
    return {"trips": r["trips"], "unresolved": r["unresolved"], "grid_m": g,
            "district_pairs": [{"from": k[0], "to": k[1], "people": v} for k, v in mat.most_common(int(a.get("pairs", 25)) + 20) if k[0] != k[1]][: int(a.get("pairs", 25))],
            "stay_in_district": dict(inside.most_common()),
            "biggest_unserved_desire_lines": [d for d in desire if not d["served_by_line"]][:top],
            "biggest_desire_lines": desire[:top],
            "note": "every worker's home -> workplace (and student -> school) now; served = one line stops within walk_m of both ends"}


def station_sites(rpc, a):
    mode = a.get("mode", "Bus"); radius = float(a.get("radius_m", MODE_RADIUS.get(mode, 500)))
    g = float(a.get("grid_m", 200))
    areas = _areas(rpc, a)
    r = rpc("commute", {"grid_m": g, "limit": 200000}, timeout=240)
    demand = collections.Counter()
    for hx, hz, wx, wz, n, purpose in r["flows"]:
        demand[(hx, hz)] += n; demand[(wx, wz)] += n
    if a.get("walking", True):
        mv = rpc("movement", dict(tools5.bbox([p for _, p in areas], 50), grid_m=g))
        for x, z, pd, cr, tr in mv["cells"]: demand[(x, z)] += pd * float(a.get("walk_weight", 5))
    stops = [tuple(s["position"]) for s in rpc("transit", {})["stops"] if a.get("any_mode") or s["mode"] == mode]
    unc = {}
    for (x, z), n in demand.items():
        d = tools5.which((x, z), areas)
        if d is None or (stops and _nearest((x, z), stops) <= radius): continue
        unc[(int(x // g), int(z // g))] = (n, d)
    ctr = lambda m: ((m[0] + .5) * g, (m[1] + .5) * g)
    seen, clusters = set(), []
    for k in unc:
        if k in seen: continue
        stack, mem = [k], []; seen.add(k)
        while stack:
            q = stack.pop(); mem.append(q)
            for dx in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    nb = (q[0] + dx, q[1] + dz)
                    if nb in unc and nb not in seen: seen.add(nb); stack.append(nb)
        tot = sum(unc[m][0] for m in mem)
        picks, left = [], {m: unc[m][0] for m in mem}
        while left and len(picks) < int(a.get("sites_per_gap", 6)):     # greedy catchment-sized picks inside the cluster
            best = max(left, key=lambda m: sum(v for o, v in left.items() if math.dist(ctr(o), ctr(m)) <= radius))
            got = [o for o in left if math.dist(ctr(o), ctr(best)) <= radius]
            val = sum(left[o] for o in got)
            if val < float(a.get("min_demand", 150)): break
            picks.append({"site": [round(ctr(best)[0]), round(ctr(best)[1])], "demand_in_catchment": round(val)})
            for o in got: del left[o]
        if picks:
            cx = sum(ctr(m)[0] * unc[m][0] for m in mem) / tot; cz = sum(ctr(m)[1] * unc[m][0] for m in mem) / tot
            clusters.append({"centre": [round(cx), round(cz)], "uncovered_demand": round(tot),
                             "district": collections.Counter(unc[m][1] for m in mem).most_common(1)[0][0], "suggested_sites": picks})
    clusters.sort(key=lambda c: -c["uncovered_demand"])
    return {"mode": mode, "catchment_m": radius, "existing_stops": len(stops), "gaps": clusters[: int(a.get("top", 8))],
            "legend": "demand = homes + jobs/schools (each commute counts at both ends) + pedestrians x walk_weight; gaps = "
                      "clusters farther than catchment_m from a stop of this mode; sites = greedy catchment-sized picks, snap to a road"}
