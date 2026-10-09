"""v0.27 tools (2026-09-26): where to put the next service building, from the game's own numbers.

- service_sites   free lots for a service building `type` (along at-grade roads, flat, dry, free - tools8.lot_candidates),
                  ranked by the demand that service has within radius_m: residents under-served for a coverage service
                  (healthcare / police / fire / education / park / post / welfare, tools6 thresholds), residents over the
                  garbage baseline, or bodies waiting for a hearse (deathcare, mod 0.27; older mods fall back to residents
                  farther than radius_m from any deathcare building). Picks are greedy: each takes the most demand, then the
                  demand it covers is discounted so the next pick goes to a different gap. Keeps min_gap_m from existing
                  facilities of the same kind, and every pick is dry-run through place_building (nothing built) so the
                  game has already accepted each returned site.
Generic: which service, which type, how many, radius and area all come from the caller.
"""
import math, collections
import tools5, tools6, tools8

# facility kinds the mod's coverage `services` list reports, per service
KIND = {"healthcare": "hospital", "education": "school", "police": "police", "garbage": "garbage", "deathcare": "deathcare"}
RADIUS = {"deathcare": 1200, "healthcare": 1500, "police": 1000, "fire": 1000, "education": 1200, "garbage": 1500,
          "park": 400, "post": 1000, "welfare": 1200}
CELL = 100.0
# existing facilities by type name, for services the mod's coverage list does not report (or reported only from 0.27 on)
NAMES = {"deathcare": ("crematorium", "cemetery"), "healthcare": ("hospital", "clinic", "medical"), "police": ("police", "prison"),
         "fire": ("fire",), "education": ("school", "college", "university"), "garbage": ("landfill", "incinerat", "recycl", "garbage"),
         "post": ("post",), "welfare": ("welfare", "shelter")}


def _thresholds(r, a):
    t = r["thresholds"]; ne = t.get("neutral_education") or 5
    thr = {"healthcare": 2.0, "fire": 2.0, "park": 2.0, "post": 1.0, "welfare": 1.0, "police": 2.0,
           "education": ne / 2, "crime": t.get("negligible_crime") or 5000, "garbage": t.get("garbage_baseline") or 500}
    thr["crime_watch"] = thr["crime"] / 5
    thr.update(a.get("thresholds") or {})
    return thr


def service_sites(rpc, a):
    svc = a.get("service") or "healthcare"
    type_ = a["type"]
    top = int(a.get("top", 5)); radius = float(a.get("radius_m", RADIUS.get(svc, 1000)))
    min_gap = float(a.get("min_gap_m", 300)); share = float(a.get("served_share", 1.0))
    areas, box = tools8._area(rpc, a)
    W, D = a.get("lot_m") or tools8._lot(rpc, type_)
    cands, gbox = tools8.lot_candidates(rpc, a, areas, box, W, D)
    if not cands: return {"sites": [], "lot_m": [W, D], "note": "no free lot of that size along the roads here"}

    pad = radius + 50
    r = rpc("coverage", {"xmin": gbox["xmin"] - pad, "zmin": gbox["zmin"] - pad, "xmax": gbox["xmax"] + pad, "zmax": gbox["zmax"] + pad}, timeout=180)
    c = {k: i for i, k in enumerate(r["columns"])}
    thr = _thresholds(r, a)
    kind = KIND.get(svc)
    existing = [tuple(f["position"]) for f in r["services"] if kind and f["kind"] == kind]
    if a.get("avoid_type", True):                               # same type + same service by name (no upgrades / props)
        keys = NAMES.get(svc, ())
        bs = rpc("query_buildings", dict(gbox, limit=20000))["buildings"]
        for b in bs:
            t = b["type"].lower()
            if b["type"] == type_ or any(k in t for k in keys):
                p = (b["position"][0], b["position"][2])
                if all(math.hypot(p[0] - x, p[1] - z) > 5 for x, z in existing): existing.append(p)

    # demand per 100 m cell
    basis = None
    dem = collections.defaultdict(float)
    for row in r["rows"]:
        res = row[c["residents"]]
        if svc == "deathcare":
            if "dead_waiting" in c:
                w = row[c["dead_waiting"]]; basis = "bodies waiting for a hearse"
            else:
                if row[c["kind"]] != 1 or res <= 0: continue
                near = min((math.hypot(row[c["x"]] - x, row[c["z"]] - z) for x, z in existing), default=1e9)
                w = res if near > radius else 0
                basis = f"residents farther than {radius:.0f} m from a deathcare building (mod < 0.27 has no body count - restart for it)"
        else:
            if row[c["kind"]] != 1 or res <= 0: continue
            bad, _ = tools6._bad(row, c, svc, thr)
            if svc == "education":
                w = (row[c["children"]] - row[c["children_in_school"]]) + (row[c["teens"]] - row[c["teens_in_school"]])
                basis = "children + teens not in school"
            else:
                w = res if bad else 0
                basis = f"residents under-served for {svc}"
        if w > 0: dem[(int(row[c["x"]] // CELL), int(row[c["z"]] // CELL))] += w
    total = sum(dem.values())

    # candidates that keep min_gap_m from existing facilities; the demand cells each one reaches
    cands = [k for k in cands if all(math.hypot(k["x"] - x, k["z"] - z) >= min_gap for x, z in existing)]
    rc = int(math.ceil(radius / CELL))
    for k in cands:
        ci, cj = int(k["x"] // CELL), int(k["z"] // CELL)
        k["_cells"] = [(i, j) for i in range(ci - rc, ci + rc + 1) for j in range(cj - rc, cj + rc + 1)
                       if (i, j) in dem and math.hypot((i + 0.5) * CELL - k["x"], (j + 0.5) * CELL - k["z"]) <= radius]

    sep = max(W, D) + max(min_gap, float(a.get("spacing_m", 10)))
    picks, rejected, tried = [], [], set()
    max_tries = int(a.get("max_tries", top * 8))
    while len(picks) < top and len(tried) < max_tries:
        best, bw = None, 0.0
        for n, k in enumerate(cands):
            if n in tried or not all(math.hypot(k["x"] - p["x"], k["z"] - p["z"]) >= sep for p in picks): continue
            w = sum(dem[q] for q in k["_cells"])
            if w > bw: best, bw = n, w
        if best is None or bw <= 0: break
        tried.add(best); k = cands[best]
        if a.get("verify", True):
            pre = rpc("place_building", {"type": type_, "x": k["x"], "z": k["z"], "facing": k["facing"], "dry_run": True, "timeout_s": 90,
                                         "allow_subnets": bool(a.get("allow_subnets"))})
            if pre.get("errors"): rejected.append({"x": k["x"], "z": k["z"], "errors": pre["errors"]}); continue
        for q in k["_cells"]: dem[q] *= (1 - share)
        picks.append({"x": k["x"], "z": k["z"], "facing": k["facing"], "road": k["road"], "road_type": k["road_type"],
                      "demand_covered": round(bw, 1), "district": tools5.which((k["x"], k["z"]), areas)})
    covered = sum(p["demand_covered"] for p in picks)
    return {"service": svc, "type": type_, "lot_m": [W, D], "basis": basis, "radius_m": radius,
            "demand_total": round(total, 1), "covered_by_sites": round(covered, 1),
            "covered_pct": round(100 * covered / total, 1) if total else None,
            "existing_facilities": len(existing), "candidates": len(cands), "sites": picks,
            "verified": a.get("verify", True), "rejected_by_game": len(rejected), "rejected": rejected[:5],
            "legend": "x, z, facing go straight into cs2_place_building; demand_covered = demand within radius_m not already "
                      "covered by an earlier pick; covered_pct = share of the area's demand these sites reach"}
