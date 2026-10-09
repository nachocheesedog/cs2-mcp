"""Waterfront back rows + bulk service upgrades (2026-10-08).

waterfront_backs: zone blocks whose BACK edge runs into water with no road behind (lots that turn their back on the
    water) -> dezone those columns (front to back) and bulldoze the growables standing in them.
upgrade_all: install every fitting service upgrade on many buildings at once (schools, hospitals, ...), extension
    upgrades placed on a free spot beside the parent."""
import collections, math, time
import survey, tools3, tools5


def _road_hash(net, cell=20.0):
    h = collections.defaultdict(list)
    for e in net:
        if any(k in e["type"] for k in ("Pipe", "voltage", "Cable", "Invisible")): continue
        for p in survey.polyline(e["curve"], 6):
            h[(int(p[0] // cell), int(p[1] // cell))].append(p)
    return h, cell


def _near_road(h, cell, p, r):
    i, j = int(p[0] // cell), int(p[1] // cell)
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            for q in h.get((i + di, j + dj), ()):
                if math.dist(p, q) <= r: return True
    return False


class _Water:
    """terrain_grid water depth, fetched lazily in tiles"""
    def __init__(self, rpc, tile=1000.0, step=8.0, water_m=0.5):
        self.rpc, self.tile, self.step, self.water_m, self.t = rpc, tile, step, water_m, {}
    def __call__(self, x, z):
        k = (int(x // self.tile), int(z // self.tile))
        if k not in self.t:
            b = {"xmin": k[0] * self.tile - 16, "zmin": k[1] * self.tile - 16, "xmax": (k[0] + 1) * self.tile + 16, "zmax": (k[1] + 1) * self.tile + 16}
            self.t[k] = tools3.Mask(self.rpc, b, self.step, 0, self.water_m)
        return self.t[k].water(x, z)


def _in_quad(p, q):
    s = None
    for i in range(4):
        a, b = q[i], q[(i + 1) % 4]
        c = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
        if abs(c) < 1e-9: continue
        if s is None: s = c > 0
        elif s != (c > 0): return False
    return True


def waterfront_backs(rpc, a):
    """a: districts / polygon / box; probe_m (40) = how far behind the back edge water counts; road_m (12) = a road this
    close to the probe line means the lots have a street behind them (kept); bulldoze (true); dry_run."""
    areas = tools5.pick_areas(rpc, a)
    box = tools5.bbox([p for _, p in areas], 60)
    zb = rpc("zone_blocks", dict(box, limit=100000))
    net = rpc("road_network", dict(box, limit=40000))["edges"]
    rh, rc = _road_hash(net)
    water = _Water(rpc, water_m=float(a.get("water_m", 0.5)))
    probe = float(a.get("probe_m", 40)); road_m = float(a.get("road_m", 12))
    flagged_blocks = []                                                     # (block, centre fn, {(x, y)} zoned cells)
    for b in zb["blocks"]:
        p = b["pos"]; d = b["dir"]; r = (d[1], -d[0]); sx, sy = b["size"]; cs = b["cells"]
        def centre(x, y, p=p, d=d, r=r, sx=sx, sy=sy): return (p[0] + d[0] * 4 * (sy - 2 * y - 1) + r[0] * 4 * (sx - 2 * x - 1),
                                                               p[2] + d[1] * 4 * (sy - 2 * y - 1) + r[1] * 4 * (sx - 2 * x - 1))
        if tools5.which(centre(sx // 2, 0), areas) is None: continue
        fc = set()
        for x in range(sx):
            ys = [y for y in range(sy) if y * sx + x < len(cs) and cs[y * sx + x] != "#"]
            if not ys or not any(cs[y * sx + x] not in "#." for y in ys): continue
            c = centre(x, max(ys)); edge = (c[0] - d[0] * 4, c[1] - d[1] * 4)
            hit = False
            for t in range(0, int(probe) + 1, 6):
                q = (edge[0] - d[0] * t, edge[1] - d[1] * t)
                if t >= 6 and _near_road(rh, rc, q, road_m): break          # a street behind the lots: keep
                if water(*q): hit = True; break
            if hit: fc |= {(x, y) for y in ys if cs[y * sx + x] not in "#."}
        if fc: flagged_blocks.append((b, centre, fc))
    grid = collections.defaultdict(list)                                    # flagged cell centres -> (block idx, x, y)
    for k, (b, centre, fc) in enumerate(flagged_blocks):
        for x, y in fc:
            c = centre(x, y); grid[(int(c[0] // 16), int(c[1] // 16))].append((c, k, x, y))
    # growables standing on those cells: new ones (level < keep_level) go, established ones keep their cells zoned
    keep_level = int(a.get("keep_level", 2))
    st = {s["id"]: s for s in rpc("building_status", dict(box, limit=100000))["buildings"]}
    victims, kept, by_district, by_level = [], 0, collections.Counter(), collections.Counter()
    for g in rpc("query_buildings", dict(box, limit=100000))["buildings"]:
        s = st.get(g["id"])
        if not s or not s.get("zone"): continue                                # growables only
        lq = tools5.lot_quad(g, shrink=1.0); cx, cz = g["position"][0], g["position"][2]
        rad = int(max(g["lot"]) * 4 // 16) + 2
        under = [(k, x, y) for di in range(-rad, rad + 1) for dj in range(-rad, rad + 1)
                 for c, k, x, y in grid.get((int(cx // 16) + di, int(cz // 16) + dj), ()) if _in_quad(c, lq)]
        if not under: continue
        lvl = s.get("level") or 1
        if lvl >= keep_level or "Signature" in g["type"]:
            kept += 1
            for k, x, y in under: flagged_blocks[k][2].discard((x, y))
        else:
            victims.append(g["id"]); by_district[tools5.which((cx, cz), areas) or "?"] += 1; by_level[lvl] += 1
    quads, cells, blocks = [], 0, 0
    for b, centre, fc in flagged_blocks:
        if not fc: continue
        blocks += 1
        for y, x0, x1 in tools5._runs(b, lambda x, y, fc=fc: (x, y) in fc):
            quads.append(tools5._run_quad(b, y, x0, x1, inset=0.5)); cells += x1 - x0 + 1
    out = {"blocks": blocks, "cells": cells, "strips": len(quads), "bulldoze_buildings": len(victims), "kept_established": kept,
           "by_district": dict(by_district), "by_level": dict(by_level)}
    if a.get("dry_run"): return out
    import tools2
    out["dezoned"] = tools2.zone_many(rpc, [(q, "none") for q in quads])          # dezone first: nothing regrows meanwhile
    if a.get("bulldoze", True) and victims:
        done = 0
        for i in range(0, len(victims), 40):
            rpc("bulldoze", {"ids": victims[i:i + 40], "buildings": True, "roads": False, "timeout_s": 120}); done += len(victims[i:i + 40]); time.sleep(3); rpc("ping", {})
        out["bulldozed"] = done
    return out


# ----------------------------------------------------------------------------- bulk upgrades
def upgrade_all(rpc, a, place_building):
    """a: types (parent building types, e.g. ["ElementarySchool03","FR_HighSchool"]) or ids; upgrades (names; default
    every upgrade the build menu lists for that type: '<type> ...' / '<type>_Ext..'); districts / box to limit;
    dry_run. place_building = the server's t_place_building (finds a spot beside the parent for lot upgrades)."""
    box = None
    if a.get("districts") or a.get("polygon") or a.get("xmin") is not None:
        areas = tools5.pick_areas(rpc, a); box = tools5.bbox([p for _, p in areas], 10)
    q = dict(box or {"xmin": -1e5, "zmin": -1e5, "xmax": 1e5, "zmax": 1e5}, limit=100000)
    bs = rpc("query_buildings", q)["buildings"]
    if box: bs = [b for b in bs if tools5.which((b["position"][0], b["position"][2]), areas)]
    if a.get("ids"): bs = [b for b in bs if b["id"] in set(a["ids"])]
    elif a.get("types"): bs = [b for b in bs if b["type"] in set(a["types"])]
    menu = []
    if not a.get("upgrades"):
        for t in sorted({b["type"] for b in bs}): menu += [m["name"] for m in rpc("menu_assets", {"filter": t}).get("assets", [])]
    cap_cache = {}
    def adds_capacity(u):                                   # capacity_only: keep upgrades that add seats / beds / cells / vehicles
        if u not in cap_cache:
            cap = 0
            for comp, fields in (("SchoolData", ("m_StudentCapacity",)), ("HospitalData", ("m_PatientCapacity", "m_AmbulanceCapacity")),
                                 ("DeathcareFacilityData", ("m_HearseCapacity", "m_StorageCapacity")), ("PoliceStationData", ("m_PatrolCarCapacity", "m_JailCapacity")),
                                 ("PrisonData", ("m_PrisonerCapacity",)), ("GarbageFacilityData", ("m_VehicleCapacity", "m_GarbageCapacity"))):
                try: f = rpc("prefab_entity", {"name": u, "component": comp})["fields"]
                except Exception: continue
                cap += sum(float(f.get(k) or 0) for k in fields)
            cap_cache[u] = cap > 0
        return cap_cache[u]
    res, counts = [], collections.Counter()
    for b in bs:
        ups = a.get("upgrades") or [m for m in menu if m != b["type"] and (m.startswith(b["type"] + " ") or m.startswith(b["type"] + "_Ext")
                                                                            or m.startswith(b["type"] + "Ext"))]
        if a.get("capacity_only"): ups = [u for u in ups if adds_capacity(u)]
        for u in ups:
            try:
                r = place_building({"type": u, "parent": b["id"], "allow_subnets": True, "follow_camera": False, "dry_run": bool(a.get("dry_run"))})
                ok = not r.get("errors") and r.get("ok", True) is not False
                counts[(u, "ok" if ok else "refused")] += 1
                if not ok: res.append({"parent": b["id"], "upgrade": u, "why": r.get("error_reasons") or r.get("error")})
            except Exception as e:
                m = str(e)
                counts[(u, "already" if "already has" in m else "error")] += 1
                if "already has" not in m: res.append({"parent": b["id"], "upgrade": u, "why": m[:160]})
    return {"buildings": len(bs), "results": {f"{u} {k}": v for (u, k), v in sorted(counts.items())}, "problems": res[:30]}


# ----------------------------------------------------------------------------- access fixes
def fix_access(rpc, a):
    """Growables with access warnings (default No Pedestrian Access; icons=[...] for No Road Access / No Car Access)
    -> bulldozed so the lot regrows (zoning kept); service buildings with the warning are only listed (their usual cause
    is a loose extension: rebuild in place, then cs2_upgrade_all capacity_only). Where: districts / polygon / box (default
    whole city); dry_run lists."""
    icons = set(a.get("icons") or ["No Pedestrian Access"])
    box = {"xmin": -1e5, "zmin": -1e5, "xmax": 1e5, "zmax": 1e5}
    areas = None
    if a.get("districts") or a.get("polygon") or a.get("xmin") is not None:
        areas = tools5.pick_areas(rpc, a); box = tools5.bbox([p for _, p in areas], 10)
    st = rpc("building_status", dict(box, problems_only=True, limit=100000))["buildings"]
    hit = [b for b in st if icons & set(b["icons"]) and (areas is None or tools5.which(tuple(b["position"]), areas))]
    grow = [b for b in hit if b.get("zone") and "Signature" not in b["type"]]
    other = [{"id": b["id"], "type": b["type"], "icons": sorted(icons & set(b["icons"]))} for b in hit if b not in grow]
    out = {"growables": len(grow), "by_zone": dict(collections.Counter(b.get("zone") for b in grow)), "services_or_signature": other}
    if a.get("dry_run") or not grow: return out
    ids = [b["id"] for b in grow]
    for i in range(0, len(ids), 40):
        rpc("bulldoze", {"ids": ids[i:i + 40], "buildings": True, "roads": False, "timeout_s": 120}); time.sleep(3); rpc("ping", {})
    out["bulldozed"] = len(ids)
    return out
