"""v0.16 tools (2026-09-24): parks and shoreline detailing (mod `attractiveness`, `list_object_types`, `place_objects`).

- attractiveness   the game's park / landmark attractiveness with its factors (efficiency, maintenance, forest, beach,
                   height); or the best spots in an area by the terrain bonus a park would get there
- park_sites       where a park lot of a given type / size fits along roads in an area (flat, dry, free, facing the road),
                   ranked by attractiveness bonus + residents lacking park coverage nearby; ready for place_building
- place_objects    many parks / trees / props in one previewed, undoable operation: explicit items, or generated along a
                   shoreline / road / path (spacing, offset, facing water / inland / along) or scattered in a polygon
Generic: every city decision (which shore, which types, spacing) comes from the caller.
"""
import math, random, collections
import geom, survey, tools2, tools3, tools4, tools5

WHERE = ("districts", "polygon", "xmin", "zmin", "xmax", "zmax")


def _dseg(p, a, b):
    dx, dz = b[0] - a[0], b[1] - a[1]; L = dx * dx + dz * dz
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dz) / L))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dz)


def _area(rpc, a):
    areas = tools5.pick_areas(rpc, {k: a[k] for k in WHERE if k in a})
    return areas, tools5.bbox([p for _, p in areas], 0)


def _inside(pt, areas):
    return any(tools2._in_poly(pt, poly) for _, poly in areas)


def _lot(rpc, type_):
    r = rpc("list_building_types", {"filter": type_, "limit": 50, "growables": True})
    for b in r["buildings"]:
        if b["name"] == type_: return b["lot_m"]
    raise ValueError(f"unknown building type '{type_}'")


# ----------------------------------------------------------------------------- attractiveness
def attractiveness(rpc, a):
    """mode 'buildings' (default): every park / landmark in the area with the game's attractiveness and factors, lowest
    maintenance / efficiency flagged. mode 'spots': the terrain bonus on a step grid inside the area (optionally only on
    dry land within shore_m of water), best first; with type, the attractiveness that park would have there."""
    areas, box = _area(rpc, a)
    if a.get("mode", "buildings") == "spots":
        step = float(a.get("step", 50))
        r = rpc("attractiveness", dict(box, step=step, **({"type": a["type"]} if a.get("type") else {})))
        pts = [p for p in r["points"] if _inside(tuple(p["at"]), areas)]
        if a.get("shore_m") is not None or a.get("dry_only", True):
            wg = tools4.WaterGrid(rpc, tools4.box_around([tuple(p["at"]) for p in pts] or [(box["xmin"], box["zmin"])], 60))
            keep = []
            for p in pts:
                q = tuple(p["at"])
                if wg.depth(q) > 0.3: continue
                if a.get("shore_m") is not None:
                    near = any(wg.march(q, (math.cos(t), math.sin(t)), float(a["shore_m"]), step_m=5) for t in [k * math.pi / 4 for k in range(8)])
                    if not near: continue
                keep.append(p)
            pts = keep
        pts.sort(key=lambda p: -p["terrain_bonus_pct"])
        out = {"spots": pts[: int(a.get("top", 15))], "evaluated": len(pts), "note": r["note"]}
        if "type_base_attractiveness" in r: out["type_base_attractiveness"] = r["type_base_attractiveness"]
        return out
    r = rpc("attractiveness", box)
    B = [b for b in r["buildings"] if _inside(tuple(b["position"]), areas)]
    for b in B: b["district"] = tools5.which(tuple(b["position"]), areas)
    weak = [b for b in B if b.get("maintenance_pct", 100) < 60 or b["factors"].get("Efficiency", 0) <= -25]
    top = int(a.get("top", 25))
    return {"count": len(B), "total_attractiveness": sum(b["attractiveness"] for b in B),
            "buildings": B[:top], "held_back": [{k: b[k] for k in ("id", "type", "position", "attractiveness", "base", "factors") if k in b} | {"maintenance_pct": b.get("maintenance_pct")} for b in weak][:top],
            "note": r["note"]}


# ----------------------------------------------------------------------------- free lots along roads
def lot_candidates(rpc, a, areas, box, W, D):
    """Every W x D lot along the at-grade roads in the area whose samples (4 m) are all free, dry, not-steep ground:
    centre set back so the front touches the road, facing it. Returns (candidates, padded box)."""
    pad = max(W, D) + 40
    gbox = {"xmin": box["xmin"] - pad, "zmin": box["zmin"] - pad, "xmax": box["xmax"] + pad, "zmax": box["zmax"] + pad}
    cell = float(a.get("grid_m", 4))
    while (gbox["xmax"] - gbox["xmin"]) / cell * (gbox["zmax"] - gbox["zmin"]) / cell > 40000: cell += 2
    occ = tools3.obstacles(rpc, dict(gbox, grid_m=cell, categories=["building", "pillar", "prop"]))
    rows, (ox, oz) = occ["rows"], occ["origin_top_left"]
    ok_chars = set(a.get("allow", ".T"))
    def free(p):
        i = int((p[0] - ox) / cell); j = int((oz - p[1]) / cell)
        return 0 <= j < len(rows) and 0 <= i < len(rows[0]) and rows[j][i] in ok_chars
    skip = ("Highway", "Ramp", "Pipe", "voltage", "Track", "Train", "Cable", "Seaway", "Invisible", "Quay", "Pedestrian", "Path")
    net = [e for e in rpc("road_network", dict(box, limit=30000))["edges"]
           if "flow" in e and max(e.get("elevation") or [0]) < 2 and not any(k.lower() in e["type"].lower() for k in skip)]
    if a.get("road_types"): net = [e for e in net if any(t.lower() in e["type"].lower() for t in a["road_types"])]
    step = float(a.get("step_m", 8)); cands = []
    for e in net:
        pl = survey.polyline(e["curve"], max(4, int(e["length"] / 4)))
        for s, P, t in tools4.stations(pl, step, W / 2, W / 2):
            if not _inside(P, areas): continue
            for sgn in (1, -1):
                n = (-t[1] * sgn, t[0] * sgn)                      # outward from the road
                back = e.get("width", 12) / 2 + D / 2 + 0.5
                C = (P[0] + n[0] * back, P[1] + n[1] * back)
                ok = True
                for u in range(-int(W / 2 / 4), int(W / 2 / 4) + 1):          # along the front
                    for v in range(0, int(D / 4) + 1):                          # into the lot, skipping the front 3 m
                        dv = min(D - 1.0, 3.0 + v * 4.0) - D / 2
                        q = (C[0] + t[0] * min(W / 2 - 1, u * 4) - n[0] * -dv, C[1] + t[1] * min(W / 2 - 1, u * 4) - n[1] * -dv)
                        if not free(q): ok = False; break
                    if not ok: break
                if ok: cands.append({"x": round(C[0], 1), "z": round(C[1], 1), "facing": [round(-n[0], 3), round(-n[1], 3)], "road": e["id"], "road_type": e["type"]})
    return cands, gbox


def verify_picks(rpc, type_, cands, sep, top, max_tries=None, allow_subnets=False):
    """Greedy non-overlapping picks from cands (best first) that the game accepts: each is dry-run through place_building
    (a preview, nothing is built) and dropped on any error, the next best taking its place. Returns (picks, rejected)."""
    picks, rejected, tries = [], [], 0
    max_tries = max_tries or top * 6
    for c in cands:
        if len(picks) >= top or tries >= max_tries: break
        if not all(math.dist((c["x"], c["z"]), (p["x"], p["z"])) >= sep for p in picks): continue
        tries += 1
        r = rpc("place_building", {"type": type_, "x": c["x"], "z": c["z"], "facing": c["facing"], "dry_run": True, "timeout_s": 90,
                                   "allow_subnets": bool(allow_subnets)})
        if r.get("errors"): rejected.append({"x": c["x"], "z": c["z"], "errors": r["errors"]}); continue
        picks.append(c)
    return picks, rejected


# ----------------------------------------------------------------------------- where a park fits
def park_sites(rpc, a):
    """Lots of type (or lot_m = [width along the road, depth]) along the roads in an area: centre set back so the lot's
    front touches the road, facing it; every lot sample (4 m) must be on free, dry, not-steep ground (trees may be
    cleared). Ranked by terrain attractiveness bonus (+ shore) and, with coverage, by nearby residents with little park
    coverage; non-overlapping picks. Output feeds place_building / place_objects (x, z, facing)."""
    areas, box = _area(rpc, a)
    W, D = a.get("lot_m") or _lot(rpc, a["type"])
    cands, gbox = lot_candidates(rpc, a, areas, box, W, D)
    if not cands: return {"sites": [], "lot_m": [W, D], "note": "no free lot of that size along the roads here"}
    bonus = rpc("attractiveness", {"points": [[c["x"], c["z"]] for c in cands]})["points"]
    for c, b in zip(cands, bonus): c["terrain_bonus_pct"] = b["terrain_bonus_pct"]; c["factors"] = b["factors"]
    if a.get("coverage", True):
        cov = rpc("coverage", gbox | {"xmin": gbox["xmin"] - 400, "zmin": gbox["zmin"] - 400, "xmax": gbox["xmax"] + 400, "zmax": gbox["zmax"] + 400}, timeout=180)
        col = {k: i for i, k in enumerate(cov["columns"])}
        R = [(r[col["x"]], r[col["z"]], r[col["residents"]], r[col["park"]]) for r in cov["rows"] if r[col["kind"]] == 1 and r[col["residents"]] > 0]
        lowp = float(a.get("low_park_coverage", 2.0))
        for c in cands:
            c["residents_lacking_parks_400m"] = int(sum(res for x, z, res, pk in R if 0 <= pk < lowp and (x - c["x"]) ** 2 + (z - c["z"]) ** 2 <= 160000))
    wr = float(a.get("residents_weight", 0.02))
    for c in cands: c["score"] = round(c["terrain_bonus_pct"] + wr * c.get("residents_lacking_parks_400m", 0), 1)
    cands.sort(key=lambda c: -c["score"])
    picks, sep, rejected = [], max(W, D) + float(a.get("spacing_m", 10)), []
    if a.get("type") and a.get("verify", True):                 # only sites the game accepts (dry-run each)
        picks, rejected = verify_picks(rpc, a["type"], cands, sep, int(a.get("top", 10)), allow_subnets=a.get("allow_subnets", False))
    else:
        for c in cands:
            if all(math.dist((c["x"], c["z"]), (p["x"], p["z"])) >= sep for p in picks): picks.append(c)
            if len(picks) >= int(a.get("top", 10)): break
    return {"lot_m": [W, D], "candidates": len(cands), "sites": picks, "verified": bool(a.get("type") and a.get("verify", True)),
            "rejected_by_game": len(rejected),
            "legend": "x, z = lot centre, facing = direction the front (road side) points - pass straight to place_building; "
                      "score = terrain bonus % + residents_weight x residents within 400 m with park coverage < low_park_coverage"}


# ----------------------------------------------------------------------------- placing many objects
def _blockers(rpc, pts, clearance):
    """Callable p -> blocked? : on water, a road (half width + clearance), a building / lot footprint."""
    box = tools4.box_around(pts, 40)
    wg = tools4.WaterGrid(rpc, box)
    net = [e for e in rpc("road_network", dict(box, limit=30000))["edges"] if max(e.get("elevation") or [0]) < 4]
    segs = []
    for e in net:
        pl = survey.polyline(e["curve"], max(4, int(e["length"] / 6)))
        segs += [(p, q, e.get("width", 8) / 2 + clearance) for p, q in zip(pl, pl[1:])]
    obs = rpc("obstacles", dict(box, categories=["building", "pillar"], limit=5000))["objects"]
    polys = [o.get("lot") or o["corners"] for o in obs]
    def blocked(p):
        if wg.depth(p) > 0.2: return "water"
        for u, v, w in segs:
            if _dseg(p, u, v) < w: return "road"
        for poly in polys:
            if tools2._in_poly(p, poly): return "building"
        return None
    return blocked


def place_objects(rpc, a):
    """items = [{type, x, z, angle_deg | facing}] as given; or generate: along = {shore: {from, to} | road_ids | knots,
    spacing_m, offset_m (from the line; for a shore + = inland), side 'left'|'right'|'both' (road/knots), facing 'water' |
    'inland' | 'along' | 'road' | angle}, or scatter = {polygon, spacing_m}; types = one or a list (cycled, or random with
    seed), jitter_m. Points on water, roads (clearance_m) or buildings are skipped. dry_run shows the plan; undoable."""
    rng = random.Random(a.get("seed", 1))
    types = a.get("types") or ([a["type"]] if a.get("type") else None)
    items, skipped = list(a.get("items") or []), collections.Counter()
    def pick(k): return rng.choice(types) if a.get("random_types") else types[k % len(types)]
    jit = float(a.get("jitter_m", 0))
    def jitter(p): return (p[0] + rng.uniform(-jit, jit), p[1] + rng.uniform(-jit, jit)) if jit else p
    gen = []                                              # (point, facing vector)
    if a.get("along"):
        g = a["along"]
        if g.get("shore"):
            line, left_is_water, _ = tools4.shore_line(rpc, dict(g["shore"], offset_m=g.get("offset_m", 0.0)))
            sea = 1 if left_is_water else -1
            for s, P, t in tools4.stations(line, float(g.get("spacing_m", 20)), g.get("start_m", 0.0), g.get("end_m", 0.0)):
                nw = (-t[1] * sea, t[0] * sea)             # toward the water
                f = {"water": nw, "inland": (-nw[0], -nw[1]), "along": t}.get(g.get("facing", "water"))
                gen.append((P, f if f else (math.sin(math.radians(float(g["facing"]))), math.cos(math.radians(float(g["facing"]))))))
        else:
            line, _ = tools4.work_line(rpc, g)
            off = float(g.get("offset_m", 0)); sides = {"left": [1], "right": [-1], "both": [1, -1]}[g.get("side", "both")]
            for s, P, t in tools4.stations(line, float(g.get("spacing_m", 20)), g.get("start_m", 0.0), g.get("end_m", 0.0)):
                for sg in sides:
                    n = (-t[1] * sg, t[0] * sg); Q = (P[0] + n[0] * off, P[1] + n[1] * off)
                    fac = g.get("facing", "road")
                    f = {"road": (-n[0], -n[1]), "away": n, "along": t}.get(fac) if isinstance(fac, str) else (math.sin(math.radians(fac)), math.cos(math.radians(fac)))
                    gen.append((Q, f))
    if a.get("scatter"):
        g = a["scatter"]; poly = g["polygon"]; sp = float(g.get("spacing_m", 15))
        xs = [p[0] for p in poly]; zs = [p[1] for p in poly]
        z = min(zs) + sp / 2
        while z < max(zs):
            x = min(xs) + sp / 2 + (sp / 2 if int((z - min(zs)) / sp) % 2 else 0)
            while x < max(xs):
                if tools2._in_poly((x, z), poly):
                    ang = rng.uniform(0, 2 * math.pi); gen.append(((x, z), (math.sin(ang), math.cos(ang))))
                x += sp
            z += sp * 0.866
    if gen:
        if not types: raise ValueError("types (or type) required to generate objects")
        blocked = _blockers(rpc, [p for p, _ in gen], float(a.get("clearance_m", 2.0))) if a.get("avoid", True) else (lambda p: None)
        for k, (p, f) in enumerate(gen):
            q = jitter(p); why = blocked(q)
            if why: skipped[why] += 1; continue
            items.append({"type": pick(k), "x": round(q[0], 2), "z": round(q[1], 2), "facing": [round(f[0], 4), round(f[1], 4)]})
    if not items: return {"placed": 0, "skipped": dict(skipped), "note": "nothing to place"}
    out = {"planned": len(items), "skipped": dict(skipped)}
    if a.get("plan_only"):
        out["items"] = items[: int(a.get("show", 30))]; return out
    placed, undo = 0, []
    for i in range(0, len(items), int(a.get("batch", 200))):
        r = rpc("place_objects", {"items": items[i:i + int(a.get("batch", 200))], "dry_run": a.get("dry_run", False),
                                   "allow_errors": a.get("allow_errors", False)}, timeout=180)
        placed += r.get("placed", 0) if not a.get("dry_run") else 0
        if r.get("undo_id") is not None: undo.append(r["undo_id"])
        out.setdefault("batches", []).append({k: r.get(k) for k in ("objects", "errors", "warnings", "applied", "placed", "undo_id") if k in r})
    out["placed"] = placed; out["undo_ids"] = undo
    return out
