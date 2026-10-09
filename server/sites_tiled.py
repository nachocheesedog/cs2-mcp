"""Tiled service-site search (system Python, talks to the CS2MCP bridge): tools9.service_sites run over a big area
coarsens the occupancy grid (> 40,000 cells -> ~27 m cells) and finds almost no free lot. This collects the free lots
tile by tile (default 1,400 m tiles, 4 m grid), then runs the same greedy demand pick over the whole area once.
  python sites_tiled.py '<json args>'      args = service_sites args (type, service, districts | box, top, min_gap_m,
                                           radius_m, served_share, verify, allow_subnets) + tile_m, place (true = build them)
Prints one JSON object: picks (x, z, facing, demand_covered, district) and, with place=true, the placed building ids."""
import collections, json, math, sys
sys.path.insert(0, __file__.rsplit("\\", 1)[0] if "\\" in __file__ else ".")
import cs2_mcp, tools5, tools6, tools8, tools9

rpc = cs2_mcp.rpc


def run(a):
    svc = a.get("service") or "healthcare"; type_ = a["type"]
    top = int(a.get("top", 5)); radius = float(a.get("radius_m", tools9.RADIUS.get(svc, 1000)))
    min_gap = float(a.get("min_gap_m", 300)); share = float(a.get("served_share", 1.0))
    areas, box = tools8._area(rpc, a)
    W, D = a.get("lot_m") or tools8._lot(rpc, type_)
    tile = float(a.get("tile_m", 560))                      # <= ~560 m keeps lot_candidates on its 4 m grid
    cache = a.get("cands_file")
    import os
    if cache and os.path.exists(cache):
        cands = json.load(open(cache)); x = box["xmax"]       # cached free lots for this lot size: skip the scan
    else:
        cands = []; x = box["xmin"]
    while x < box["xmax"]:
        z = box["zmin"]
        while z < box["zmax"]:
            tb = {"xmin": x, "zmin": z, "xmax": min(x + tile, box["xmax"]), "zmax": min(z + tile, box["zmax"])}
            if any(tools8._inside((cx, cz), areas) for cx in (tb["xmin"], (tb["xmin"] + tb["xmax"]) / 2, tb["xmax"])
                   for cz in (tb["zmin"], (tb["zmin"] + tb["zmax"]) / 2, tb["zmax"])):
                c, _ = tools8.lot_candidates(rpc, dict(a, grid_m=4), areas, tb, W, D)
                cands += c
            z += tile
        x += tile
    if cache and not os.path.exists(cache): json.dump(cands, open(cache, "w"))
    seen = set(); uniq = []
    for k in cands:
        key = (round(k["x"]), round(k["z"]))
        if key not in seen: seen.add(key); uniq.append(k)
    cands = uniq
    pad = radius + 50
    r = rpc("coverage", {"xmin": box["xmin"] - pad, "zmin": box["zmin"] - pad, "xmax": box["xmax"] + pad, "zmax": box["zmax"] + pad}, timeout=240)
    c = {k: i for i, k in enumerate(r["columns"])}
    thr = tools9._thresholds(r, a)
    kind = tools9.KIND.get(svc)
    existing = [tuple(f["position"]) for f in r["services"] if kind and f["kind"] == kind]
    keys = tools9.NAMES.get(svc, ("park", "plaza", "garden", "square") if svc == "park" else ())
    for b in rpc("query_buildings", dict(box, limit=40000))["buildings"]:
        t = b["type"].lower()
        if b["type"] == type_ or any(k in t for k in keys):
            p = (b["position"][0], b["position"][2])
            if all(math.hypot(p[0] - ex, p[1] - ez) > 5 for ex, ez in existing): existing.append(p)
    dem = collections.defaultdict(float); CELL = tools9.CELL
    for row in r["rows"]:
        res = row[c["residents"]]
        if svc == "deathcare":
            w = row[c["dead_waiting"]] if "dead_waiting" in c else 0
        else:
            if row[c["kind"]] != 1 or res <= 0: continue
            if svc == "education":
                st = a.get("students", "both")
                w = ((row[c["children"]] - row[c["children_in_school"]]) if st in ("both", "children") else 0) +                     ((row[c["teens"]] - row[c["teens_in_school"]]) if st in ("both", "teens") else 0)
            else:
                bad, _ = tools6._bad(row, c, svc, thr); w = res if bad else 0
        if w > 0 and tools8._inside((row[c["x"]], row[c["z"]]), areas): dem[(int(row[c["x"]] // CELL), int(row[c["z"]] // CELL))] += w
    total = sum(dem.values())
    cands = [k for k in cands if all(math.hypot(k["x"] - ex, k["z"] - ez) >= min_gap for ex, ez in existing)]
    rc = int(math.ceil(radius / CELL))
    for k in cands:
        ci, cj = int(k["x"] // CELL), int(k["z"] // CELL)
        k["_cells"] = [(i, j) for i in range(ci - rc, ci + rc + 1) for j in range(cj - rc, cj + rc + 1)
                       if (i, j) in dem and math.hypot((i + 0.5) * CELL - k["x"], (j + 0.5) * CELL - k["z"]) <= radius]
    sep = max(W, D) + max(min_gap, float(a.get("spacing_m", 10)))
    picks, rejected, tried = [], [], set()
    if a.get("spread_m"):                                   # no residents yet (new areas): space facilities evenly
        spread = float(a["spread_m"]); ex = list(existing)
        if a.get("within"): cands = [k for k in cands if tools8._inside((k["x"], k["z"]), [("w", a["within"])])]
        while len(picks) < top:
            best, bd = None, 0.0
            for n, k in enumerate(cands):
                if n in tried: continue
                d = min([math.hypot(k["x"] - p[0], k["z"] - p[1]) for p in ex] + [1e9])
                if d > bd: best, bd = n, d
            if best is None or bd < spread: break
            tried.add(best); k = cands[best]
            if a.get("place_now"):
                res = rpc("place_building", {"type": type_, "x": k["x"], "z": k["z"], "facing": k["facing"], "timeout_s": 90,
                                             "follow_camera": False, "allow_subnets": bool(a.get("allow_subnets"))})
                if res.get("errors") or not res.get("building_id"):
                    rejected.append({"x": k["x"], "z": k["z"], "why": res.get("error_reasons")}); continue
                k["id"] = res.get("building_id")
            elif a.get("verify", True):
                pre = rpc("place_building", {"type": type_, "x": k["x"], "z": k["z"], "facing": k["facing"], "dry_run": True, "timeout_s": 90,
                                             "allow_subnets": bool(a.get("allow_subnets"))})
                if pre.get("errors"): rejected.append({"x": k["x"], "z": k["z"]}); continue
            ex.append((k["x"], k["z"]))
            picks.append({"x": k["x"], "z": k["z"], "facing": k["facing"], "demand_covered": round(bd), "district": tools5.which((k["x"], k["z"]), areas), "id": k.get("id")})
        top = 0
    while len(picks) < top and len(tried) < top * 8:
        best, bw = None, 0.0
        for n, k in enumerate(cands):
            if n in tried or not all(math.hypot(k["x"] - p["x"], k["z"] - p["z"]) >= sep for p in picks): continue
            w = sum(dem[q] for q in k["_cells"])
            if w > bw: best, bw = n, w
        if best is None or bw <= 0: break
        tried.add(best); k = cands[best]
        if a.get("place_now"):                              # build it now; a refused lot just falls through to the next best
            res = rpc("place_building", {"type": type_, "x": k["x"], "z": k["z"], "facing": k["facing"], "timeout_s": 90,
                                         "follow_camera": False, "allow_subnets": bool(a.get("allow_subnets"))})
            if res.get("errors") or not res.get("building_id"):
                rejected.append({"x": k["x"], "z": k["z"], "why": res.get("error_reasons")}); continue
            k["id"] = res.get("building_id")
        elif a.get("verify", True):
            pre = rpc("place_building", {"type": type_, "x": k["x"], "z": k["z"], "facing": k["facing"], "dry_run": True, "timeout_s": 90,
                                         "allow_subnets": bool(a.get("allow_subnets"))})
            if pre.get("errors"): rejected.append({"x": k["x"], "z": k["z"]}); continue
        if a.get("capacity"):                                # a school seats `capacity`: take it from the nearest cells first
            left = float(a["capacity"]); took = 0.0
            for q in sorted(k["_cells"], key=lambda q: math.hypot((q[0] + 0.5) * CELL - k["x"], (q[1] + 0.5) * CELL - k["z"])):
                t = min(dem[q], left); dem[q] -= t; left -= t; took += t
                if left <= 0: break
            bw = took
        else:
            for q in k["_cells"]: dem[q] *= (1 - share)
        picks.append({"x": k["x"], "z": k["z"], "facing": k["facing"], "demand_covered": round(bw, 1),
                      "district": tools5.which((k["x"], k["z"]), areas), "id": k.get("id")})
    out = {"service": svc, "type": type_, "lot_m": [W, D], "candidates": len(cands), "existing": len(existing),
           "demand_total": round(total, 1), "covered": round(sum(p["demand_covered"] for p in picks), 1), "picks": picks,
           "rejected": len(rejected), "rejected_why": [r.get("why") for r in rejected][:10]}
    if a.get("place"):
        placed = []
        for p in picks:
            res = rpc("place_building", {"type": type_, "x": p["x"], "z": p["z"], "facing": p["facing"], "timeout_s": 90,
                                         "follow_camera": False, "allow_subnets": bool(a.get("allow_subnets"))})
            placed.append({"x": p["x"], "z": p["z"], "id": res.get("building_id"), "errors": res.get("errors")})
        out["placed"] = placed
    return out


if __name__ == "__main__":
    print(json.dumps(run(json.loads(sys.argv[1]))))
