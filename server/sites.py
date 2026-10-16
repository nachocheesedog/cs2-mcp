"""v0.46 (2026-10-17): where a new district can go - free land next to the streets, and the street that reaches it.

find_sites() surveys the city once (streets, building lots, terrain, water, districts) and returns ranked sites. Each
site is reached the way a player extends a town: straight on from a dead end, or a square T off a street whose side is
still empty. The site carries the lattice (angle, origin, phases) that makes that join a straight continuation / a
square T, so cs2_build_district drafts a grid that is joined to the city by construction. (In a lived-in city the
streets at the edge of town are lined with buildings; a grid laid out on its own only rarely finds a gap between them,
and a lattice line running straight into a dead end never "hits" that street - the first live test found no buildable
grid for exactly these reasons.) A dry run of the first new street says whether the game lets you build there at all:
unbought map tiles, money.

Frame of a site: s along the anchor's direction (out of the city), l across it. The spine (lattice line u = 0) runs
from the anchor along s; the first cross street (v = v_phase) sits a zoning depth past the old street's cells, so the
old and the new blocks meet without a crack.
"""
import collections, math, time
import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree
from PIL import Image, ImageDraw
import survey

CELL = 8.0
DEPTH = 6 * CELL                       # zone cells reach this far from a road's kerb
MAP = 7168.0                           # the map is 14.336 km square around 0
NOT_JOINABLE = ("highway", "ramp", "pipe", "cable", "power", "voltage", "subway", "track", "train", "tram", "quay",
                "pedestrian", "path", "fence", "seaway", "invisible", "parking")
UTILITY = ("Pipe", "Cable", "Power", "Subway", "voltage")


def joinable(e):
    """A street at ground level a new street may join: a road (it has traffic flow), not a highway, ramp, path..."""
    if "flow" not in e: return False
    t = (e.get("type") or "").lower()
    if any(k in t for k in NOT_JOINABLE): return False
    el = e.get("elevation") or 0
    el = el if isinstance(el, list) else [el, el]
    return -3 <= min(el) and max(el) <= 3


def _unit(v):
    L = math.hypot(v[0], v[1])
    return (v[0] / L, v[1] / L) if L > 1e-6 else None


def end_dir(e, at_start):
    """Unit direction pointing OUT of the edge at that end: the way a street continued from there runs."""
    c = e["curve"]
    pts = [c[0], c[1], c[2], c[3]] if at_start else [c[3], c[2], c[1], c[0]]
    for q in pts[1:]:
        d = _unit((pts[0][0] - q[0], pts[0][2] - q[2]))
        if d: return d
    return None


def city_box(edges, pad):
    """The box of the city's joinable streets (0.5-99.5 %, so one stray road far out does not count), padded."""
    xs, zs = [], []
    for e in edges:
        if joinable(e):
            for p in (e["curve"][0], e["curve"][3]): xs.append(p[0]); zs.append(p[2])
    if len(xs) < 4: return None
    xs.sort(); zs.sort()
    q = lambda v, f: v[min(len(v) - 1, max(0, int(f * (len(v) - 1))))]
    return [max(-MAP, q(xs, .005) - pad), max(-MAP, q(zs, .005) - pad), min(MAP, q(xs, .995) + pad), min(MAP, q(zs, .995) + pad)]


# ----------------------------------------------------------------------------- the land
class Land:
    """Rasters of one box: streets, the band their zone cells may take, highways/tracks, lots, water, slope,
    districts; `free` = where a new street and its blocks could go."""

    def __init__(self, rpc, box, net=None, res=None, max_slope=0.25):
        x0, z0, x1, z1 = box
        self.area = A = survey.Area(x0, z0, x1, z1)
        span = max(x1 - x0, z1 - z0)
        self.res = res = float(res or (8.0 if span <= 5000 else 12.0 if span <= 8000 else 16.0))
        self.R = R = survey.Raster(A, res)
        H, W = R.H, R.W
        net = net or rpc("road_network", dict(A.params, limit=80000))
        self.truncated = bool(net.get("edges_in_area") and net.get("returned") and net["edges_in_area"] > net["returned"])
        self.edges = [e for e in net["edges"] if not any(k in (e.get("type") or "") for k in UTILITY)]
        self.nodes = {n["id"]: n for n in net["nodes"]}
        self.buildings = rpc("query_buildings", dict(A.params, limit=80000)).get("buildings", [])
        step = 16
        while ((x1 - x0) / step + 1) * ((z1 - z0) / step + 1) > 85000: step += 8
        self.grid = rpc("terrain_grid", dict(A.params, step=step))
        imgs = {k: R.new() for k in ("road", "major", "band", "lots")}
        dr = {k: ImageDraw.Draw(v) for k, v in imgs.items()}
        for e in self.edges:
            el = e.get("elevation") or 0
            el = el if isinstance(el, list) else [el, el]
            if min(el) < -3 or min(el) > 8: continue              # tunnels, and bridges high enough to pass under
            pts = [R.px(x, z) for x, z in survey.polyline(e["curve"])]
            major = ("flow" not in e) or ("Highway" in (e.get("type") or ""))
            dr["major" if major else "road"].line(pts, fill=255, width=max(1, int(round((e["width"] + 1) / res))), joint="curve")
            if not major:                                          # where its zone cells may be (both sides, 6 deep)
                dr["band"].line(pts, fill=255, width=max(1, int(round((e["width"] + 2 * DEPTH) / res))), joint="curve")
        for b in self.buildings:
            lc = survey.lot_corners(b)
            if lc: dr["lots"].polygon([R.px(*p) for p in lc], fill=255)
            else:
                x, _, z = b["position"]; i, j = R.px(x, z); dr["lots"].ellipse([i - 2, j - 2, i + 2, j + 2], fill=255)
        self.road, self.major, self.band, self.lots = (np.asarray(imgs[k]) > 0 for k in ("road", "major", "band", "lots"))
        self.water = R.terrain(self.grid, "water_depth") > 0.3 if self.grid.get("water_depth") else np.zeros((H, W), bool)
        self.hgt = R.terrain(self.grid, "heights")
        self.slope = R.terrain(survey.slope_of(self.grid), "slope")
        edt = lambda m: (ndimage.distance_transform_edt(~m) * res).astype(np.float32) if m.any() else np.full((H, W), 1e6, np.float32)
        self.d_road, self.d_major, self.d_lot, self.d_water = edt(self.road | self.major), edt(self.major), edt(self.lots), edt(self.water)
        self.free = (~self.band) & (self.d_road >= 14) & (self.d_lot >= 12) & (self.d_major >= 36) & (self.d_water >= 24) \
            & (self.slope <= max_slope)
        # stretches of free land: highways, tracks and water split them (a site counts only the stretch its spine reaches)
        self.free_lab, _ = ndimage.label(self.free, structure=np.ones((3, 3)))
        self.districts = []
        try:
            self.districts = [d for d in rpc("list_districts", {"outlines": True}).get("districts", []) if d.get("outline")][:250]
        except Exception:
            pass
        dimg = R.new(); dd = ImageDraw.Draw(dimg)
        for k, d in enumerate(self.districts): dd.polygon([R.px(*p) for p in d["outline"]], fill=k + 1)
        self.district_ix = np.asarray(dimg)
        npos = [(n["pos"][0], n["pos"][2]) for n in self.nodes.values()]
        self.node_tree = cKDTree(npos) if npos else None

    def sample(self, arr, X, Z, default):
        X = np.asarray(X, dtype=np.float64); Z = np.asarray(Z, dtype=np.float64)
        I = np.floor((X - self.area.x0) / self.res).astype(int); J = np.floor((self.area.z1 - Z) / self.res).astype(int)
        ok = (I >= 0) & (I < self.R.W) & (J >= 0) & (J < self.R.H)
        out = np.full(X.shape, default, dtype=arr.dtype)
        out[ok] = arr[J[ok], I[ok]]
        return out

    # ------------------------------------------------------------------------- anchors
    def dead_ends(self):
        arms = collections.defaultdict(list)
        for e in self.edges:
            for k in ("start_node", "end_node"): arms[e[k]].append(e)
        out = []
        for nid, n in self.nodes.items():
            if n.get("degree") != 1 or n.get("outside_connection") or n.get("owned_arms"): continue
            es = arms.get(nid) or []
            if len(es) != 1 or not joinable(es[0]): continue
            e = es[0]; d = end_dir(e, e["start_node"] == nid)
            p = (n["pos"][0], n["pos"][2])
            if d and self.area.inside(*p, margin=60): out.append(anchor("dead_end", p, d, e, nid))
        return out

    def sides(self, step=32.0, gap=40.0):
        """Points along straight joinable streets (>= gap from their ends), both sides."""
        out = []
        for e in self.edges:
            if not joinable(e) or e.get("length", 0) < 2 * gap + 8: continue
            c = e["curve"]
            if not survey.is_straight(c, tol=1.5): continue
            a, b = (c[0][0], c[0][2]), (c[3][0], c[3][2]); L = math.dist(a, b)
            t_ = ((b[0] - a[0]) / L, (b[1] - a[1]) / L); nrm = (-t_[1], t_[0])
            s = gap
            while s <= L - gap + 1e-6:
                p = (a[0] + t_[0] * s, a[1] + t_[1] * s)
                if self.area.inside(*p, margin=60):
                    for sg in (1, -1): out.append(anchor("side", p, (nrm[0] * sg, nrm[1] * sg), e, None))
                s += step
        return out

    def behind(self, an, reach=260.0, half_width=150.0):
        """The old street parallel to the new cross streets right behind a dead end (the one its stub comes from):
        (s, width) in the anchor frame (s < 0), or None."""
        if not hasattr(self, "_straight"):
            st = [e for e in self.edges if joinable(e) and survey.is_straight(e["curve"], tol=1.5)]
            self._straight = (np.array([(e["curve"][0][0], e["curve"][0][2]) for e in st]).reshape(-1, 2),
                              np.array([(e["curve"][3][0], e["curve"][3][2]) for e in st]).reshape(-1, 2),
                              np.array([float(e["width"]) for e in st]), [e["id"] for e in st])
        A, B, Wd, ids = self._straight
        if not len(A): return None
        P = np.array(an["pos"]); D = np.array(an["dir"]); N = np.array([-D[1], D[0]])
        AB = B - A; L = np.hypot(AB[:, 0], AB[:, 1]); L[L < 1e-6] = 1e-6
        across = np.abs(AB @ D) / L <= 0.05                       # runs across the dead end's direction
        sm = (((A + B) / 2) - P) @ D; la, lb = (A - P) @ N, (B - P) @ N
        m = across & (sm >= -reach) & (sm < -4) & (np.maximum(la, lb) >= -half_width) & (np.minimum(la, lb) <= half_width)
        m &= np.array([i != an.get("edge") for i in ids])
        if not m.any(): return None
        i = int(np.where(m)[0][np.argmax(sm[m])])
        return float(sm[i]), float(Wd[i])

    # ------------------------------------------------------------------------- checks
    def spine(self, an, half, v1, max_grade, max_slope=0.40):
        """None if a street can run from the anchor straight out to the first cross street (+40 m), else why not -
        the same rules pave.plan_fill applies to that connector (lots, highways/tracks, water, other streets, grade)."""
        P = np.array(an["pos"]); D = np.array(an["dir"]); w_old = an["street_w"]; dead = an["kind"] == "dead_end"
        back = 0.0 if dead else w_old / 2                     # where the old street's carriageway ends
        skip = w_old / 2 + half + 6 + (4 if dead else 48)       # plan_fill lets the connector's last metres approach roads
        S = np.arange(back + 2 * self.res, v1 + 40.0, 4.0)
        X, Z = P[0] + D[0] * S, P[1] + D[1] * S
        if not self.area.inside(X[-1], Z[-1], margin=40): return "outside_the_survey"
        lot = self.sample(self.d_lot, X, Z, np.float32(0)); maj = self.sample(self.d_major, X, Z, np.float32(0))
        rd = self.sample(self.d_road, X, Z, np.float32(0)); wat = self.sample(self.water, X, Z, True)
        slope = self.sample(self.slope, X, Z, np.float32(9))
        need_lot = np.where(dead & (S < skip), max(1.0, half - 1.0), half + 7.0)
        if (lot < need_lot).any(): return "buildings"
        if (maj < half + 21).any(): return "highway_or_rail"
        if wat.any(): return "water"
        if (slope > max_slope).any(): return "steep"
        req = np.minimum(np.maximum(0.0, S - back - 1.5 * self.res), half + 7.0)
        if (rd < req - 0.5 * self.res).any(): return "another_street_in_the_way"
        Sg = np.arange(0.0, v1 + 0.1, 10.0)
        h = self.sample(self.hgt, P[0] + D[0] * Sg, P[1] + D[1] * Sg, np.float32(np.nan))
        if len(h) > 2 and np.nanmax(np.abs(h[2:] - h[:-2]) / 20.0) > max_grade: return "grade"
        return None

    def rect(self, an, L, Wd, comp, ns=16, nl=16):
        """Free land of stretch `comp` in the L x Wd rectangle ahead of the anchor, trimmed where its sides / far end
        are mostly not free. -> dict(s0, s1, l0, l1, share, free_ha) or None."""
        P = np.array(an["pos"]); D = np.array(an["dir"]); N = np.array([-D[1], D[0]])
        s0 = 2.0 if an["kind"] == "dead_end" else an["street_w"] / 2 + DEPTH + 2
        sk = s0 + (np.arange(ns) + 0.5) / ns * L; lm = -Wd / 2 + (np.arange(nl) + 0.5) / nl * Wd
        SS, LL = np.meshgrid(sk, lm, indexing="ij")
        F = self.sample(self.free_lab, P[0] + D[0] * SS + N[0] * LL, P[1] + D[1] * SS + N[1] * LL, np.int32(0)) == comp
        col = F.mean(0); mid = nl // 2
        i0 = 0
        while i0 < mid - 1 and col[i0] < 0.4: i0 += 1
        i1 = nl
        while i1 > mid + 1 and col[i1 - 1] < 0.4: i1 -= 1
        row = F[:, i0:i1].mean(1); k1 = ns
        while k1 > 3 and row[k1 - 1] < 0.4: k1 -= 1
        sub = F[:k1, i0:i1]
        if sub.size == 0: return None
        share = float(sub.mean())
        l0, l1 = -Wd / 2 + i0 * Wd / nl, -Wd / 2 + i1 * Wd / nl; s1 = s0 + k1 * L / ns
        di = self.sample(self.district_ix, P[0] + D[0] * SS[:k1, i0:i1] + N[0] * LL[:k1, i0:i1],
                         P[1] + D[1] * SS[:k1, i0:i1] + N[1] * LL[:k1, i0:i1], np.uint8(0))
        over = None
        if (di > 0).mean() > 0.02:
            k = collections.Counter(int(v) for v in di.ravel() if v).most_common(1)[0][0]
            over = self.districts[k - 1].get("name")
        return {"s0": s0, "s1": s1, "l0": l0, "l1": l1, "share": round(share, 2),
                "free_ha": round(share * (s1 - s0) * (l1 - l0) / 1e4, 1), "district_overlap": over}


def anchor(kind, p, d, e, nid):
    return {"kind": kind, "pos": (float(p[0]), float(p[1])), "dir": (float(d[0]), float(d[1])), "edge": e["id"], "node": nid,
            "street": e.get("type"), "street_w": float(e.get("width") or 12.0)}


def first_cross(an, first_w, behind=None):
    """Distance from the anchor to the first cross street, so the old blocks and the new ones meet without a gap:
    side T - kerb to kerb 96 m from the street it joins; dead end - kerb to kerb 96 m from the old street behind it
    (`behind` = (s, width) from Land.behind), but at least far enough to clear the dead end; none behind: one zoning
    depth + 8 m past the dead end."""
    if an["kind"] != "dead_end": return an["street_w"] / 2 + 2 * DEPTH + first_w / 2
    if behind is None: return first_w / 2 + DEPTH + 8.0
    s_b, w_b = behind
    return max(first_w / 2 + 14.0, s_b + w_b / 2 + 2 * DEPTH + first_w / 2)


def lattice_of(an, v1):
    """Lattice with V along the anchor's direction, the spine (u = 0) through the anchor, a cross street at v1."""
    dx, dz = an["dir"]
    return {"angle_deg": round(math.degrees(math.atan2(-dx, dz)), 4), "origin": [round(an["pos"][0], 3), round(an["pos"][1], 3)],
            "u_phase": 0.0, "v_phase": round(v1, 3)}


def frame_poly(an, s0, s1, l0, l1):
    P, D = an["pos"], an["dir"]; N = (-D[1], D[0])
    return [[round(P[0] + D[0] * s + N[0] * l, 1), round(P[1] + D[1] * s + N[1] * l, 1)] for s, l in ((s0, l0), (s0, l1), (s1, l1), (s1, l0))]


def probe(rpc, an, v1, local_type):
    """Dry run of the first piece of the spine past the cross street: does the game let you build there at all?"""
    P, D = an["pos"], an["dir"]
    seg = {"start": [round(P[0] + D[0] * v1, 2), round(P[1] + D[1] * v1, 2)],
           "end": [round(P[0] + D[0] * (v1 + 40), 2), round(P[1] + D[1] * (v1 + 40), 2)], "type": local_type}
    try:
        r = rpc("place_roads", {"segments": [seg], "dry_run": True, "snap_tolerance": 1.5, "timeout_s": 30})
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}
    if r.get("errors"): return {"ok": False, "reasons": r.get("error_reasons") or {"errors": r["errors"]}}
    return {"ok": True}


# ----------------------------------------------------------------------------- search
def find_sites(rpc, *, local_type, widths, first_w, pitch_u, pitch_v, size=600.0, count=5, near=None, radius=None,
               polygon=None, connect_beyond=250.0, check=True, max_grade=0.15, land=None):
    """Ranked places for a new district. size = side of the square looked at ahead of each anchor (the site is
    trimmed to the free part); near [x, z] ranks closer sites higher (radius limits the search to around it);
    polygon = an outline already chosen: only anchors that reach into it (within connect_beyond), best first.
    -> {"sites": [...], "found": counts per stage, "box", "seconds"}"""
    t0 = time.time()
    half = widths[local_type] / 2
    found = collections.Counter()
    if land is None:
        if polygon:
            xs, zs = [p[0] for p in polygon], [p[1] for p in polygon]; pad = connect_beyond + 200
            box = [min(xs) - pad, min(zs) - pad, max(xs) + pad, max(zs) + pad]; net = None
        elif near is not None and radius:
            box = [near[0] - radius, near[1] - radius, near[0] + radius, near[1] + radius]; net = None
        else:
            net = rpc("road_network", {"xmin": -MAP, "zmin": -MAP, "xmax": MAP, "zmax": MAP, "limit": 80000})
            box = city_box([e for e in net["edges"] if not any(k in (e.get("type") or "") for k in UTILITY)], size + 300)
            if box is None: return {"sites": [], "found": {"streets": 0}, "reason": "no streets in this city yet", "seconds": round(time.time() - t0, 1)}
        box = [max(-MAP, box[0]), max(-MAP, box[1]), min(MAP, box[2]), min(MAP, box[3])]
        land = Land(rpc, box, net=net)
    ancs = land.dead_ends() + land.sides()
    found["dead_ends"] = sum(1 for x in ancs if x["kind"] == "dead_end"); found["street_sides"] = len(ancs) - found["dead_ends"]
    poly_in = None
    if polygon:
        import tools2
        poly_in = lambda p: tools2._in_poly(p, polygon)
    cands = []
    for an in ancs:
        v1 = first_cross(an, first_w)
        P, D = an["pos"], an["dir"]
        if polygon:                                    # must reach into the outline within connect_beyond
            s_in = next((s for s in np.arange(0.0, connect_beyond + 60, 8.0) if poly_in((P[0] + D[0] * s, P[1] + D[1] * s))), None)
            if s_in is None: continue
            v1 = max(v1, s_in + first_w / 2 + 24)
        # quick test: is there free land just past the first cross street? (its stretch is what the site may count)
        b = (P[0] + D[0] * (v1 + 24), P[1] + D[1] * (v1 + 24))
        comp = int(land.sample(land.free_lab, [b[0]], [b[1]], np.int32(0))[0])
        if not comp: continue
        found["free_land_beyond"] += 1
        if an["kind"] == "side" and land.node_tree is not None and land.node_tree.query(P)[0] < 38: found["near_a_junction"] += 1; continue
        if an["kind"] == "dead_end" and not polygon:   # line the first cross street up with the old street behind
            v1 = first_cross(an, first_w, land.behind(an))
        why = land.spine(an, half, v1, max_grade)
        if why: found["spine_" + why] += 1; continue
        found["spine_ok"] += 1
        if polygon:
            cands.append(dict(an, v1=v1, score=1.0 / (1.0 + v1 / 100.0) * (1.15 if an["kind"] == "dead_end" else 1.0)))
            continue
        r = land.rect(an, size, size, comp)
        if not r or r["share"] < 0.45 or r["free_ha"] < 6 or r["s1"] < v1 + pitch_v * 0.5: found["too_little_free_land"] += 1; continue
        sc = r["free_ha"] * (1.15 if an["kind"] == "dead_end" else 1.0) * (0.5 if r["district_overlap"] else 1.0)
        if near is not None: sc /= 1.0 + math.dist(P, near) / 2500.0
        cands.append(dict(an, v1=v1, rect=r, comp=comp, score=sc))
    cands.sort(key=lambda c: -c["score"])
    chosen = []
    for c in cands:                                    # one site per stretch of land
        if polygon:
            if any(math.dist(c["pos"], o["pos"]) < 60 for o in chosen): continue
        elif any(math.dist(_centre(c), _centre(o)) < 0.6 * size for o in chosen): continue
        chosen.append(c)
        if len(chosen) >= count + 4: break
    sites = []
    for c in chosen:
        lat = lattice_of(c, c["v1"])
        if polygon:
            poly = polygon; seed = None
        else:
            r = c["rect"]; poly = frame_poly(c, r["s0"], r["s1"], r["l0"], r["l1"])
            seed = _seed(land, c, r, pitch_v)
        st = {"kind": c["kind"], "anchor": [round(c["pos"][0], 1), round(c["pos"][1], 1)], "dir": [round(c["dir"][0], 4), round(c["dir"][1], 4)],
              "street": c["street"], "edge": c["edge"], "node": c["node"], "polygon": poly, "lattice": lat, "v1": round(c["v1"], 1)}
        if seed: st["seed"] = seed
        if not polygon:
            r = c["rect"]
            st.update(center=[round(v, 1) for v in _centre(c)], size_m=[round(r["l1"] - r["l0"]), round(r["s1"] - r["s0"])],
                      free_share=r["share"], free_ha=r["free_ha"])
            if r["district_overlap"]: st["overlaps_district"] = r["district_overlap"]
            if near is not None: st["distance_m"] = round(math.dist(c["pos"], near))
        sites.append(st)
    if check and not polygon:
        ok = 0
        for st in sites:
            if ok >= count: break
            an = {"pos": tuple(st["anchor"]), "dir": tuple(st["dir"])}
            st["check"] = probe(rpc, an, st["v1"], local_type)
            if st["check"]["ok"]: ok += 1
            else: found["refused_by_game"] += 1
        probed = [s for s in sites if "check" in s]
        sites = [s for s in probed if s["check"]["ok"]][:count] + [s for s in probed if not s["check"]["ok"]][:3]
    else:
        sites = sites[:count]
    for i, st in enumerate(sites): st["rank"] = i + 1
    out = {"sites": sites, "found": dict(found), "box": [round(v) for v in (land.area.x0, land.area.z0, land.area.x1, land.area.z1)],
           "seconds": round(time.time() - t0, 1)}
    if land.truncated: out["warning"] = "the city has more streets than one survey returns - pass near + radius to look at one part"
    out["_land"] = land
    return out


def _centre(c):
    r = c["rect"]; s, l = (r["s0"] + r["s1"]) / 2, (r["l0"] + r["l1"]) / 2
    P, D = c["pos"], c["dir"]; N = (-D[1], D[0])
    return (P[0] + D[0] * s + N[0] * l, P[1] + D[1] * s + N[1] * l)


def _seed(land, c, r, pitch_v):
    """A free point of the site's stretch past the first cross street (the land mass the planner fills)."""
    P, D = c["pos"], c["dir"]; N = (-D[1], D[0])
    for s in (c["v1"] + min(pitch_v / 2, (r["s1"] - c["v1"]) / 2), (r["s0"] + r["s1"]) / 2, c["v1"] + 30):
        for l in (0.0, 40.0, -40.0, 100.0, -100.0):
            x, z = P[0] + D[0] * s + N[0] * l, P[1] + D[1] * s + N[1] * l
            if int(land.sample(land.free_lab, [x], [z], np.int32(0))[0]) == c["comp"]: return [round(x, 1), round(z, 1)]
    s = c["v1"] + 30
    return [round(P[0] + D[0] * s, 1), round(P[1] + D[1] * s, 1)]


# ----------------------------------------------------------------------------- picture
def render(land, sites, px=900):
    """Top-down picture: free land green, streets grey, highways/tracks red, lots brown, water blue; numbered sites."""
    R = land.R
    rgb = np.full((R.H, R.W, 3), (222, 214, 192), np.uint8)
    rgb[land.free] = (184, 222, 160)
    rgb[land.lots] = (150, 118, 96)
    rgb[land.water] = (120, 160, 214)
    rgb[land.road] = (96, 96, 96)
    rgb[land.major] = (190, 70, 60)
    img = Image.fromarray(rgb)
    k = px / max(R.W, R.H)
    img = img.resize((max(1, int(R.W * k)), max(1, int(R.H * k))), Image.BILINEAR if k < 1 else Image.NEAREST)
    d = ImageDraw.Draw(img)
    col = [(220, 30, 30), (30, 90, 220), (200, 120, 0), (140, 40, 180), (0, 140, 140), (90, 90, 20), (220, 0, 140), (40, 40, 40)]
    for st in sites:
        c = col[(st["rank"] - 1) % len(col)]
        pts = [(R.px(*p)[0] * k, R.px(*p)[1] * k) for p in st["polygon"]]
        d.line(pts + pts[:1], fill=c, width=3)
        ax, az = R.px(*st["anchor"]); ax, az = ax * k, az * k
        d.ellipse([ax - 5, az - 5, ax + 5, az + 5], fill=c)
        cx = sum(p[0] for p in pts) / len(pts); cz = sum(p[1] for p in pts) / len(pts)
        d.rectangle([cx - 9, cz - 9, cx + 9, cz + 9], fill=(255, 255, 255), outline=c)
        d.text((cx - 4, cz - 6), str(st["rank"]), fill=c)
    return img
