"""Area filling: pave a whole region with a zoning-optimised street lattice that stops at the water, steep ground and
existing development, joins existing streets, and is closed off along the shore by smooth waterfront roads.

plan_fill() only plans (reads the world through `rpc`, returns segments + stats). build() places a plan in spatial
chunks with place_skip_failing(), which bisects refused batches with dry runs so one bad segment no longer sinks a
whole batch. remove_built() takes a built plan back out by shape (robust to edges the game split or re-created).
Frame: u along angle_deg, v along angle_deg + 90. Lines of constant u are spaced pitch_u (set it to road width + 96 m
for full 6-cell zoning on both sides); lines of constant v are spaced pitch_v (block length).
"""
import math, time
import numpy as np
from scipy import ndimage
import contourpy
import survey

CELL = 8.0


# ----------------------------------------------------------------------------- small geometry helpers
def _cubic(p0, p1, p2, p3, t):
    u = 1 - t
    return (u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
            u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1])


def _seg_hits(p, d, max_t, A, B):
    """First hit of ray p + d*t (0 < t <= max_t) against segments A[i]->B[i]. -> (t, index) or (None, None)."""
    if len(A) == 0: return None, None
    e = B - A; w = A - p
    den = d[0] * e[:, 1] - d[1] * e[:, 0]
    ok = np.abs(den) > 1e-9
    den = np.where(ok, den, 1.0)
    t = (w[:, 0] * e[:, 1] - w[:, 1] * e[:, 0]) / den
    s = (w[:, 0] * d[1] - w[:, 1] * d[0]) / den
    m = ok & (t > 0.5) & (t <= max_t) & (s >= 0) & (s <= 1)
    if not m.any(): return None, None
    i = np.where(m)[0][np.argmin(t[m])]
    return float(t[i]), int(i)


def _angle_between(d, e):
    c = abs(d[0] * e[0] + d[1] * e[1]) / ((math.hypot(*d) * math.hypot(*e)) or 1)
    return math.degrees(math.acos(min(1.0, c)))


class _UF:
    def __init__(self): self.p = {}
    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x: self.p[x] = self.p[self.p[x]]; x = self.p[x]
        return x
    def union(self, a, b): self.p[self.find(a)] = self.find(b)


# ----------------------------------------------------------------------------- planning
def plan_fill(rpc, area, *, angle_deg=58.5, origin=(0.0, 0.0), pitch_u=120.0, pitch_v=200.0, u_phase=0.0, v_phase=0.0,
              local_type="Medium Road", arterial_type=None, arterial_every=4, shore_type=None, shore_setback=60.0,
              max_slope=0.40, max_grade=0.10, seed=None, polygon=None, res=8.0, clear_road=6.0, clear_major=20.0, clear_lot=6.0,
              min_run_m=150.0, junction_gap_m=35.0, min_angle_deg=None, connect_reach=1.6, keep_islands=False, debug_png=None, grid=True, rim=True,
              perp_deg=80.0, min_angle_curved=60.0, approach_m=52.0, connect_beyond=0.0):
    """Plan a street lattice over the free land of `area` (survey.Area). Returns a dict with 'segments' (place_roads
    format, each with a 'phase': 'grid' | 'shore') and 'stats'. seed [x,z] picks the land mass (default: area centre);
    polygon [[x,z],...] further limits the fill. connect_beyond (m, v0.46) lets the connectors - not the lattice - leave
    the polygon by up to that much, so an outline drawn a little short of the street it should join still joins it.

    Zoning rules built in (v0.27, learned from an early waterfront district):
    - a street joins another at >= perp_deg (80): when the lattice line would hit at a skew it bends so its last
      approach_m (52 m = one zoning depth + a margin) arrives at 90 deg; if that bend does not fit it is dropped (a
      straight target) or kept down to min_angle_curved (60, a curved / waterfront road - some loss is accepted there)
    - with a rim road the grid stays a full double zoning depth away from it (rim half + 96 m + local half), so the
      grid's blocks never overlap the rim road's"""
    if min_angle_deg is not None: perp_deg = min_angle_curved = min_angle_deg      # old callers: one threshold
    t0 = time.time()
    widths = {r["name"]: r["width"] for r in rpc("list_road_types")["road_types"]}
    for t in (local_type, arterial_type, shore_type):
        if t and t not in widths: raise ValueError(f"unknown road type '{t}'")
    shore_type = shore_type or local_type
    step = 8
    while ((area.x1 - area.x0) / step + 1) * ((area.z1 - area.z0) / step + 1) > 88000: step += 4
    w = survey.World(rpc, area, grid_step=step)
    R = survey.Raster(area, res)
    H, W = R.H, R.W

    # --- rasters
    from PIL import Image, ImageDraw
    img_road, img_major, img_cell, img_lot = R.new(), R.new(), R.new(), R.new()
    dr, dm, dc, dl = (ImageDraw.Draw(i) for i in (img_road, img_major, img_cell, img_lot))
    obstacles = []                      # (curve, type, width) of ground-level nets we must keep clear of / may join
    for e in w.net["edges"]:
        typ = e["type"]
        if any(k in typ for k in ("Pipe", "Cable", "Power", "Subway")): continue
        el = e.get("elevation") or 0
        el = el if isinstance(el, list) else [el, el]
        if min(el) < -3 or min(el) > 8: continue          # tunnels, and bridges high enough to pass under
        pts = [R.px(x, z) for x, z in survey.polyline(e["curve"])]
        wd = max(1, int(round((e["width"] + 1) / res)))
        major = ("flow" not in e) or ("Highway" in typ)   # tracks and highways: never join, keep well clear
        (dm if major else dr).line(pts, fill=255, width=wd, joint="curve")
        obstacles.append((e, major))
    for b in w.blocks:
        for _, _, ch, _, _, corners in survey.cell_geometry(b):
            if ch != "#": dc.polygon([R.px(*p) for p in corners], fill=255)
    for bld in w.buildings:
        lc = survey.lot_corners(bld)
        if lc: dl.polygon([R.px(*p) for p in lc], fill=255)
        else:
            x, _, z = bld["position"]; i, j = R.px(x, z); dl.ellipse([i - 3, j - 3, i + 3, j + 3], fill=255)
    road = np.asarray(img_road) > 0; major = np.asarray(img_major) > 0
    cells = np.asarray(img_cell) > 0; lots = np.asarray(img_lot) > 0
    water = R.terrain(w.grid, "water_depth") > 0.3 if w.grid.get("water_depth") else np.zeros((H, W), bool)
    slope = R.terrain(survey.slope_of(w.grid), "slope")
    edt = lambda m: ndimage.distance_transform_edt(~m) * res if m.any() else np.full((H, W), 1e6)
    cliff = ndimage.binary_opening(slope > max_slope, iterations=1)   # drop single-pixel slope noise
    d_road, d_major, d_cell, d_lot = edt(road), edt(major), edt(cells), edt(lots)
    # the rim road keeps a constant setback from every barrier: water, cliffs and highways/tracks
    d_water = edt(water | cliff | major)

    # land mass containing the seed, limited to the area (and polygon)
    lab, _ = ndimage.label(~water, structure=np.ones((3, 3)))
    sx, sz = seed or ((area.x0 + area.x1) / 2, (area.z0 + area.z1) / 2)
    si, sj = (int(v) for v in R.px(sx, sz))
    if not (0 <= si < W and 0 <= sj < H) or lab[sj, si] == 0: raise ValueError("seed is not on land inside the area")
    region = lab == lab[sj, si]
    conn_zone = region                   # where connectors may run: the region, or the land a little beyond the polygon
    if polygon:
        pimg = R.new(); ImageDraw.Draw(pimg).polygon([R.px(*p) for p in polygon], fill=255)
        pmask = np.asarray(pimg) > 0
        if connect_beyond > 0: conn_zone = region & (ndimage.distance_transform_edt(~pmask) * res <= connect_beyond)
        region = region & pmask
    edge_pad = int(math.ceil(40 / res))
    for m_ in ((region,) if conn_zone is region else (region, conn_zone)):
        m_[:edge_pad] = m_[-edge_pad:] = False; m_[:, :edge_pad] = m_[:, -edge_pad:] = False
    flat = slope <= max_slope            # ground a road may sit on at all (cliffs are not); the grade ALONG a road is max_grade
    hgt = R.terrain(w.grid, "heights")

    def grade_ok(p, q):
        """Longitudinal grade over any 20 m of p->q stays within max_grade (roads may run along steep banks, not up them)."""
        L = math.dist(p, q); n = max(2, int(L / 10))
        hs = []
        for k in range(n + 1):
            ij = at(p[0] + (q[0] - p[0]) * k / n, p[1] + (q[1] - p[1]) * k / n)
            if ij is None: return False
            hs.append(hgt[ij])
        stepm = L / n; lag = max(1, int(round(20 / stepm)))
        for k in range(len(hs) - lag):
            if abs(hs[k + lag] - hs[k]) / (lag * stepm) > max_grade: return False
        return abs(hs[-1] - hs[0]) / (L or 1) <= max_grade

    if debug_png:                        # why each pixel is closed to a local road: colour per first failing rule
        half = widths[local_type] / 2
        rules = [(~region, (40, 40, 40)), (~flat, (150, 90, 40)), (d_water < shore_setback + 30, (60, 110, 200)),
                 (d_major < half + clear_major, (200, 40, 40)), (d_road < half + clear_road, (240, 200, 60)),
                 (d_cell < half + 1, (80, 180, 80)), (d_lot < half + clear_lot, (160, 60, 160))]
        rgb = np.full((H, W, 3), 235, np.uint8); done = np.zeros((H, W), bool)
        for m_, col in rules: rgb[m_ & ~done] = col; done |= m_
        Image.fromarray(rgb).save(debug_png)

    def at(x, z):
        i, j = R.px(x, z); i, j = int(i), int(j)
        return (j, i) if 0 <= i < W and 0 <= j < H else None

    def free(x, z, half, need_water):
        ij = at(x, z)
        if ij is None or not region[ij] or not flat[ij]: return False
        return (d_water[ij] >= need_water and d_road[ij] >= half + clear_road and d_major[ij] >= half + clear_major
                and d_cell[ij] >= half + 1 and d_lot[ij] >= half + clear_lot)

    def path_free(p, q, half, need_water, skip_end=0.0, cells_ok=False, zone=None):
        """Every 4 m along p->q is buildable. The last skip_end metres may approach water/roads (a connector's target).
        zone = the mask the path must stay in (default the region; connectors pass `conn_zone`)."""
        zone = region if zone is None else zone
        L = math.dist(p, q); n = max(2, int(L / 4))
        for k in range(n + 1):
            f = k / n
            x, z = p[0] + (q[0] - p[0]) * f, p[1] + (q[1] - p[1]) * f
            ij = at(x, z)
            if ij is None or not zone[ij] or not flat[ij] or water[ij]: return False
            if d_lot[ij] < half + clear_lot or d_major[ij] < half + clear_major: return False
            if f * L <= L - skip_end:
                if d_water[ij] < need_water or d_road[ij] < half + clear_road: return False
                if not cells_ok and d_cell[ij] < half + 1: return False
        return grade_ok(p, q)

    # --- lattice
    a = math.radians(angle_deg); U = (math.cos(a), math.sin(a)); V = (-math.sin(a), math.cos(a))
    ox, oz = origin
    def world(u, v): return (ox + U[0] * u + V[0] * v, oz + U[1] * u + V[1] * v)
    cu = [((x - ox) * U[0] + (z - oz) * U[1], (x - ox) * V[0] + (z - oz) * V[1])
          for x, z in ((area.x0, area.z0), (area.x1, area.z0), (area.x1, area.z1), (area.x0, area.z1))]
    umin, umax = min(c[0] for c in cu), max(c[0] for c in cu); vmin, vmax = min(c[1] for c in cu), max(c[1] for c in cu)
    ks = range(int(math.floor((umin - u_phase) / pitch_u)), int(math.ceil((umax - u_phase) / pitch_u)) + 1)
    js = range(int(math.floor((vmin - v_phase) / pitch_v)), int(math.ceil((vmax - v_phase) / pitch_v)) + 1)
    us = {k: u_phase + k * pitch_u for k in ks}; vs = {j: v_phase + j * pitch_v for j in js}
    art = lambda j: bool(arterial_type and arterial_every and j % arterial_every == 0)
    # with a rim road the grid stops short of it; without one it may run up to the barrier itself
    grid_need = (shore_setback + widths[shore_type or local_type] / 2 + 2 * 6 * CELL + widths[local_type] / 2) if rim \
        else widths[local_type] / 2 + 12.0
    kept = {}                            # ((k,j),(k2,j2)) -> segment dict
    for k in (ks if grid else ()):                         # lines of constant u, pieces j..j+1
        for j in js:
            if j + 1 not in vs: continue
            p, q = world(us[k], vs[j]), world(us[k], vs[j + 1])
            if path_free(p, q, widths[local_type] / 2, grid_need): kept[((k, j), (k, j + 1))] = {"start": p, "end": q, "type": local_type}
    for j in (js if grid else ()):       # lines of constant v, pieces k..k+1
        typ = arterial_type if art(j) else local_type
        for k in ks:
            if k + 1 not in us: continue
            p, q = world(us[k], vs[j]), world(us[k + 1], vs[j])
            if path_free(p, q, widths[typ] / 2, grid_need): kept[((k, j), (k + 1, j))] = {"start": p, "end": q, "type": typ}
    deg = {}
    for (n1, n2) in kept: deg[n1] = deg.get(n1, 0) + 1; deg[n2] = deg.get(n2, 0) + 1

    # --- shoreline: contour of distance-to-water at the setback, kept where it is free, smoothed
    gen = contourpy.contour_generator(z=d_water.astype(np.float64))
    shore_runs = []
    half_s = widths[shore_type] / 2
    for line in (gen.lines(shore_setback) if rim else ()):
        pts = [R.world(i, j) for i, j in line]
        ok = [free(x, z, half_s, 0) for x, z in pts]
        run = []
        for p, good in zip(pts + [None], ok + [False]):
            if good: run.append(p); continue
            if len(run) > 2 and sum(math.dist(run[i], run[i + 1]) for i in range(len(run) - 1)) >= min_run_m:
                shore_runs.append(run)
            run = []
    dense_runs = []                      # smoothed, resampled every 2 m
    for run in shore_runs:
        P = np.array(run)
        seg = np.hypot(*np.diff(P, axis=0).T); s = np.concatenate([[0], np.cumsum(seg)])
        n = max(3, int(s[-1] / 2)); ss = np.linspace(0, s[-1], n)
        X = np.interp(ss, s, P[:, 0]); Z = np.interp(ss, s, P[:, 1])
        k = min(len(X) // 2 * 2 - 1, 31)  # moving-average smoothing over ~60 m
        if k >= 3:
            ker = np.ones(k) / k
            X = np.concatenate([X[:k // 2], np.convolve(X, ker, "valid"), X[-(k // 2):]])
            Z = np.concatenate([Z[:k // 2], np.convolve(Z, ker, "valid"), Z[-(k // 2):]])
        D = np.stack([X, Z], 1)
        # split where the rim itself climbs too steeply (20 m window)
        hh = np.array([hgt[at(x, z)] if at(x, z) else np.nan for x, z in D])
        steep = np.zeros(len(D), bool)
        for i in range(len(D) - 10):
            if not abs(hh[i + 10] - hh[i]) / 20.0 <= max_grade: steep[i:i + 11] = True
        start = None
        for i in range(len(D) + 1):
            if i < len(D) and not steep[i]:
                if start is None: start = i
                continue
            if start is not None and (i - start) * 2 >= min_run_m: dense_runs.append(D[start:i])
            start = None

    # --- connectors: from each lattice frontier node, straight on along its line to the shore or an existing street
    A_s, B_s, owner_s = [], [], []       # shore polyline pieces
    for ri, D in enumerate(dense_runs):
        A_s.append(D[:-1]); B_s.append(D[1:]); owner_s += [(ri, i) for i in range(len(D) - 1)]
    A_s = np.concatenate(A_s) if A_s else np.zeros((0, 2)); B_s = np.concatenate(B_s) if B_s else np.zeros((0, 2))
    A_e, B_e, owner_e = [], [], []       # existing streets (joinable) and majors (blocking)
    for e, is_major in obstacles:
        P = np.array(survey.polyline(e["curve"], max(1, int(e["length"] / 6))))
        A_e.append(P[:-1]); B_e.append(P[1:]); owner_e += [(e, is_major)] * (len(P) - 1)
    A_e = np.concatenate(A_e) if A_e else np.zeros((0, 2)); B_e = np.concatenate(B_e) if B_e else np.zeros((0, 2))
    node_pos = np.array([n["pos"][::2] for n in w.nodes.values()]) if w.nodes else np.zeros((0, 2))
    node_ids = list(w.nodes.keys())

    def curve_free(pts, half, skip_end):
        L = sum(math.dist(a, b) for a, b in zip(pts, pts[1:])); acc = 0.0
        for a_, b_ in zip(pts, pts[1:]):
            acc += math.dist(a_, b_)
            if not path_free(a_, b_, half, 0, skip_end=max(0.0, skip_end - (L - acc)), cells_ok=skip_end > 0.1, zone=conn_zone): return False
        return True

    def bend(p, d, hit, T, A, B, owners, owner_key, half, skip_end):
        """Cubic from p (leaving along d, so the lattice junction stays square) to a point E on the target whose last
        approach_m arrive perpendicular. E = halfway between the straight hit and the foot of the perpendicular from p."""
        T = T / (np.linalg.norm(T) or 1)
        foot = hit + T * ((p - hit) @ T)
        E0 = (hit + foot) / 2
        same = [i for i, o in enumerate(owners) if owner_key(o)]
        if not same: return None
        AA, BB = A[same], B[same]; ab = BB - AA
        tt = np.clip(((E0 - AA) * ab).sum(1) / np.maximum((ab * ab).sum(1), 1e-9), 0, 1)
        P = AA + ab * tt[:, None]; i = int(np.argmin(np.hypot(*(P - E0).T)))
        E = P[i]; T2 = ab[i] / (np.linalg.norm(ab[i]) or 1); N2 = np.array([-T2[1], T2[0]])
        if N2 @ (p - E) < 0: N2 = -N2                 # normal pointing back toward p
        L = float(np.linalg.norm(E - p))
        if L < approach_m + 20 or N2 @ (p - E) < approach_m: return None
        c1 = p + d * L * 0.35; c2 = E + N2 * max(L * 0.4, approach_m)
        pts = [tuple(_cubic(tuple(p), tuple(c1), tuple(c2), tuple(E), k / 16)) for k in range(17)]
        if not curve_free(pts, half, skip_end): return None
        return {"end": tuple(map(float, E)), "controls": [tuple(map(float, c1)), tuple(map(float, c2))], "idx": same[i]}

    connectors, shore_hits = [], {}      # shore_hits[run] = [(arc index, point)]
    ends = []
    for (n1, n2) in kept:                # frontier = node whose continuation along the same line is not kept
        for n, other in ((n1, n2), (n2, n1)):
            nxt = (2 * n[0] - other[0], 2 * n[1] - other[1])
            if (tuple(sorted((n, nxt))) in kept) or ((n, nxt) in kept) or ((nxt, n) in kept): continue
            ends.append((n, other))
    for n, other in ends:
        p = np.array(world(us[n[0]], vs[n[1]])); q = np.array(world(us[other[0]], vs[other[1]]))
        d = (p - q) / np.linalg.norm(p - q)
        const_v = n[1] == other[1]
        typ = (arterial_type if art(n[1]) else local_type) if const_v else local_type
        reach = (pitch_u if const_v else pitch_v) * connect_reach
        ts, si_ = _seg_hits(p, d, reach, A_s, B_s)
        te, ei_ = _seg_hits(p, d, reach, A_e, B_e)
        if ts is not None and (te is None or ts < te):
            hit = p + d * ts; ri, pi = owner_s[si_]
            ang = _angle_between(d, B_s[si_] - A_s[si_]); seg = None
            if ang < perp_deg:                   # skewed onto the rim road: bend to arrive square
                b = bend(p, d, hit, B_s[si_] - A_s[si_], A_s, B_s, owner_s, lambda o, ri=ri: o[0] == ri, widths[typ] / 2, 0.1)
                if b: hit = np.array(b["end"]); pi = owner_s[b["idx"]][1]; seg = b
                elif ang < min_angle_curved: continue
            if seg is None and not path_free(tuple(p), tuple(hit), widths[typ] / 2, 0, skip_end=0.1, zone=conn_zone): continue
            if any(math.dist(hit, h) < junction_gap_m for _, h in shore_hits.get(ri, [])): continue
            shore_hits.setdefault(ri, []).append((pi, tuple(hit)))
            c = {"start": tuple(p), "end": tuple(map(float, hit)), "type": typ, "to": ("shore", ri), "from": n}
            if seg: c["controls"] = seg["controls"]; c["bent"] = True
            connectors.append(c)
        elif te is not None:
            e, is_major = owner_e[ei_]
            if is_major: continue
            hit = p + d * te
            ang = _angle_between(d, B_e[ei_] - A_e[ei_]); seg = None
            skip = e["width"] / 2 + widths[typ] / 2 + clear_road + 48
            if ang < perp_deg:                   # skewed onto an existing street: bend to arrive square
                b = bend(p, d, hit, B_e[ei_] - A_e[ei_], A_e, B_e, owner_e, lambda o, e=e: o[0] is e, widths[typ] / 2, skip)
                if b: hit = np.array(b["end"]); seg = b
                elif survey.is_straight(e["curve"]) or ang < min_angle_curved: continue
            if seg is None and not path_free(tuple(p), tuple(hit), widths[typ] / 2, 0, skip_end=skip, cells_ok=True, zone=conn_zone): continue
            if len(node_pos):
                dn = np.hypot(*(node_pos - hit).T); i = int(np.argmin(dn))
                if dn[i] < 3: hit = node_pos[i]
                elif dn[i] < junction_gap_m: continue
            c = {"start": tuple(p), "end": tuple(map(float, hit)), "type": typ, "to": ("existing", e["id"]), "from": n}
            if seg: c["controls"] = seg["controls"]; c["bent"] = True
            connectors.append(c)

    # --- shore segments: cubic pieces between junctions (connector hits) and regular breaks every ~80 m
    shore_segs = []
    for ri, D in enumerate(dense_runs):
        hits = sorted(shore_hits.get(ri, []))
        s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(D, axis=0).T))])
        breaks = {0: tuple(D[0]), len(D) - 1: tuple(D[-1])}
        for pi, h in hits: breaks[pi] = h
        hs = sorted(s[i] for i in breaks)
        for i in range(0, len(D), 5):            # extra breaks every ~80 m, kept away from junctions
            if all(abs(s[i] - x) > 40 for x in hs) and all(abs(s[i] - s[b]) > 70 for b in breaks): breaks[i] = tuple(D[i])
        idx = sorted(breaks)
        def tangent(i):
            a_, b_ = max(0, i - 4), min(len(D) - 1, i + 4)
            v = D[b_] - D[a_]; return v / (np.linalg.norm(v) or 1)
        for i0, i1 in zip(idx, idx[1:]):
            p0, p3 = breaks[i0], breaks[i1]; L = s[i1] - s[i0]
            if L < 4: continue
            t0_, t1_ = tangent(i0), tangent(i1)
            c1 = (p0[0] + t0_[0] * L / 3, p0[1] + t0_[1] * L / 3); c2 = (p3[0] - t1_[0] * L / 3, p3[1] - t1_[1] * L / 3)
            shore_segs.append({"start": p0, "end": p3, "controls": [c1, c2], "type": shore_type, "run": ri})

    # --- keep only what connects to the existing network
    uf = _UF()
    for (n1, n2) in kept: uf.union(("n",) + n1, ("n",) + n2)
    for c in connectors: uf.union(("n",) + c["from"], c["to"] if c["to"][0] == "shore" else ("EXISTING",))
    for sgm in shore_segs: uf.find(("shore", sgm["run"]))
    root = uf.find(("EXISTING",))
    live = (lambda key: True) if (keep_islands or not grid) else (lambda key: uf.find(key) == root)
    grid_segs = [dict(v, key=k) for k, v in kept.items() if live(("n",) + k[0])]
    conn_segs = [c for c in connectors if live(("n",) + c["from"])]
    shore_segs = [s_ for s_ in shore_segs if live(("shore", s_["run"]))]
    dropped = len(kept) + len(connectors) - len(grid_segs) - len(conn_segs)

    def out(sg, phase):
        o = {"start": [round(sg["start"][0], 2), round(sg["start"][1], 2)], "end": [round(sg["end"][0], 2), round(sg["end"][1], 2)],
             "type": sg["type"], "phase": phase}
        if "controls" in sg: o["controls"] = [[round(c[0], 2), round(c[1], 2)] for c in sg["controls"]]
        return o
    segs = [out(s_, "grid") for s_ in grid_segs] + [out(s_, "grid") for s_ in conn_segs] + [out(s_, "shore") for s_ in shore_segs]
    length = lambda s_: math.dist(s_["start"], s_["end"])
    return {"segments": segs, "seed": [sx, sz],
            "stats": {"grid_segments": len(grid_segs), "connectors": len(conn_segs),
                      "to_existing": sum(1 for c in conn_segs if c["to"][0] == "existing"),
                      "bent_to_square": sum(1 for c in conn_segs if c.get("bent")),
                      "shore_segments": len(shore_segs), "shore_runs": len({s_["run"] for s_ in shore_segs}),
                      "dropped_unconnected": dropped, "road_km": round(sum(length(s_) for s_ in segs) / 1000, 1),
                      "plan_s": round(time.time() - t0, 1)}}


def connect_options(rpc, area, max_m=250.0, min_gap=35.0, min_angle=35.0, max_grade=0.18):
    """For every dead end in `area`: (a) carry straight on until the first road, (b) run to the nearest junction.
    Each option says whether it is clean (clear of water / building lots / steep grade, lands >= min_gap from other
    junctions or on one, meets the road at >= min_angle) and why not. Facts only: the caller decides what to build."""
    from PIL import ImageDraw
    w = survey.World(rpc, area)
    facts = survey.check(w)
    res = 4.0; R = survey.Raster(area, res)
    lot_img = R.new(); dl = ImageDraw.Draw(lot_img)
    for bld in w.buildings:
        lc = survey.lot_corners(bld)
        if lc: dl.polygon([R.px(*p) for p in lc], fill=255)
    lots = np.asarray(lot_img) > 0
    water = R.terrain(w.grid, "water_depth") > 0.3 if w.grid.get("water_depth") else np.zeros_like(lots)
    hgt = R.terrain(w.grid, "heights")
    def at(x, z):
        i, j = R.px(x, z); i, j = int(i), int(j)
        return (j, i) if 0 <= i < R.W and 0 <= j < R.H else None
    def problem(p, q):
        L = math.dist(p, q); n = max(2, int(L / 4)); hs = []
        for k in range(n + 1):
            ij = at(p[0] + (q[0] - p[0]) * k / n, p[1] + (q[1] - p[1]) * k / n)
            if ij is None: return "leaves the area"
            if water[ij]: return "crosses water"
            if lots[ij] and 8 < k * L / n < L - 8: return "crosses a building lot"
            hs.append(float(hgt[ij]))
        lag = max(1, int(20 / (L / n)))
        if any(abs(hs[k + lag] - hs[k]) / (lag * L / n) > max_grade for k in range(len(hs) - lag)): return "too steep"
        return None
    polys = [(e, np.array(survey.polyline(e["curve"], max(1, int(e["length"] / 6))))) for e in w.edges]
    owner = [e for e, P in polys for _ in range(len(P) - 1)]
    A = np.concatenate([P[:-1] for _, P in polys]) if polys else np.zeros((0, 2))
    B = np.concatenate([P[1:] for _, P in polys]) if polys else np.zeros((0, 2))
    nodes = {nid: (n["pos"][0], n["pos"][2]) for nid, n in w.nodes.items()}
    npos = np.array(list(nodes.values())) if nodes else np.zeros((0, 2)); nids = list(nodes)
    out = []
    for de in facts["roads"]["dead_ends"]["list"]:
        nid, p = de["node"], np.array(de["pos"], dtype=float)
        own = [e for e in w.edges if nid in (e.get("start_node"), e.get("end_node"))]
        if not own: continue
        e0 = own[0]; ax, az = survey.arm_dir(e0["curve"], e0.get("start_node") == nid)
        d = np.array([-ax, -az]); opts = []
        mask = np.array([o["id"] != e0["id"] for o in owner], bool)
        Am, Bm, om = A[mask], B[mask], [o for o, m_ in zip(owner, mask) if m_]
        t, i = _seg_hits(p, d, max_m, Am, Bm)
        if t is not None:
            hit = p + d * t; tgt = om[i]
            dn = np.hypot(*(npos - hit).T); k = int(np.argmin(dn))
            end = npos[k] if dn[k] < 3 else hit
            why = problem(tuple(p), tuple(end))
            if not why and 3 <= dn[k] < min_gap: why = f"lands {dn[k]:.0f} m from a junction"
            ang = _angle_between(d, Bm[i] - Am[i])
            if not why and ang < min_angle: why = f"meets the road at {ang:.0f} deg"
            if not why and "Highway" in tgt["type"]: why = "target is a highway"
            opts.append({"kind": "straight on", "segment": {"start": [round(p[0], 1), round(p[1], 1)], "end": [round(float(end[0]), 1), round(float(end[1]), 1)]},
                         "length_m": round(float(np.linalg.norm(end - p))), "target_type": tgt["type"], "ok": not why, "problem": why})
        if len(npos):
            dn = np.hypot(*(npos - p).T)
            for k in np.argsort(dn):
                if nids[k] == nid or nids[k] in (e0.get("start_node"), e0.get("end_node")) or dn[k] < 1: continue
                if dn[k] > max_m: break
                q = npos[k]; why = problem(tuple(p), tuple(q))
                opts.append({"kind": "nearest junction", "segment": {"start": [round(p[0], 1), round(p[1], 1)], "end": [round(float(q[0]), 1), round(float(q[1]), 1)]},
                             "length_m": round(float(dn[k])), "ok": not why, "problem": why})
                break
        out.append({"dead_end": [round(p[0], 1), round(p[1], 1)], "road": de.get("road"), "options": opts})
    return {"dead_ends": len(out), "with_a_clean_option": sum(1 for o in out if any(x["ok"] for x in o["options"])), "list": out}


def detect_lattice(rpc, area, pitch_u=None, pitch_v=None, angle_deg=None):
    """Street lattice of the existing roads in `area`: dominant angle, and for each axis the line pitch and phase, in a
    frame with origin (0,0). -> {angle_deg, origin, pitch_u, u_phase, u_fit, pitch_v, v_phase, v_fit}. Give pitch_u /
    pitch_v to only fit the phase (the angle is reliable; pitches of hand-built cities often are not: check *_fit).
    angle_deg fixes the angle (the caller's choice) and only fits the phases to the parallel streets - so a new grid's
    lines sit an exact number of pitches from them: no overlapping blocks, no dead gap between the zoning edges."""
    pitches = {"u": pitch_u, "v": pitch_v}
    net = rpc("road_network", {**area.params, "limit": 30000})
    segs = []
    for e in net["edges"]:
        if "flow" not in e or not survey.is_straight(e["curve"]): continue
        a_, d_ = e["curve"][0], e["curve"][3]
        L = math.hypot(d_[0] - a_[0], d_[2] - a_[2])
        if L > 20: segs.append((a_[0], a_[2], d_[0], d_[2], L))
    if len(segs) < (2 if angle_deg is not None else 4): raise ValueError("too few straight roads in the area to read a lattice")
    # peak of a length-weighted histogram of directions mod 90 (so diagonal streets don't drag it), then refine
    # with the weighted mean of the streets within 5 deg of the peak
    dirs = [(math.degrees(math.atan2(z1 - z0, x1 - x0)) % 90, L) for x0, z0, x1, z1, L in segs]
    hist = [0.0] * 90
    for d_, L in dirs:
        for k in (-1, 0, 1): hist[(int(round(d_)) + k) % 90] += L * (1.0 if k == 0 else 0.5)
    peak = max(range(90), key=hist.__getitem__)
    near = [(((d_ - peak + 45) % 90) - 45, L) for d_, L in dirs if abs(((d_ - peak + 45) % 90) - 45) <= 5]
    ang = (peak + sum(o * L for o, L in near) / (sum(L for _, L in near) or 1)) % 90
    if angle_deg is not None: ang = angle_deg
    a = math.radians(ang); U = (math.cos(a), math.sin(a)); V = (-math.sin(a), math.cos(a))
    out = {"angle_deg": round(ang, 2), "origin": [0.0, 0.0]}
    for axis, (P, Q) in (("u", (U, V)), ("v", (V, U))):
        pos = []                           # lines of constant `axis` run along the other axis
        for x0, z0, x1, z1, L in segs:
            dx, dz = (x1 - x0) / L, (z1 - z0) / L
            if abs(dx * Q[0] + dz * Q[1]) > 0.995: pos.append(((x0 + x1) / 2 * P[0] + (z0 + z1) / 2 * P[1], L))
        if len(pos) < 2: continue
        tot = sum(L for _, L in pos)
        def fit(pitch):                    # best phase and the share of road length lying on that lattice (+-6 m)
            return max(((sum(L for p, L in pos if min((p - ph) % pitch, pitch - (p - ph) % pitch) < 6) / tot, ph)
                        for ph in range(0, int(pitch), 2)))
        fixed = pitches.get(axis)
        scores = {pt: fit(pt) for pt in ([fixed] if fixed else range(96, 324, 4))}
        top = max(sc for sc, _ in scores.values())
        pitch = max(pt for pt, (sc, _) in scores.items() if sc >= 0.9 * top)   # largest pitch that explains ~all of it
        out["pitch_" + axis], out[axis + "_phase"], out[axis + "_fit"] = float(pitch), float(scores[pitch][1]), round(scores[pitch][0], 2)
    return out


# ----------------------------------------------------------------------------- building
def place_skip_failing(rpc, segs, snap_tolerance=1.5, min_split=1):
    """Place segs; if the game refuses, find the refused segments by bisecting with dry runs and build the rest.
    -> (result of the applied batch or None, [refused segments])"""
    def dry(ss):
        return rpc("place_roads", {"segments": ss, "snap_tolerance": snap_tolerance, "dry_run": True, "timeout_s": 120})
    def bad_of(ss, known=None):
        r = known or dry(ss)
        if r.get("errors", 0) == 0: return []
        if len(ss) <= min_split: return list(ss)
        h = len(ss) // 2
        return bad_of(ss[:h]) + bad_of(ss[h:])
    first = rpc("place_roads", {"segments": segs, "snap_tolerance": snap_tolerance, "timeout_s": 120})
    if first.get("applied"): return first, []
    bad = bad_of(segs, first)
    good = [s for s in segs if s not in bad]
    if not good: return None, bad
    res = rpc("place_roads", {"segments": good, "snap_tolerance": snap_tolerance, "timeout_s": 120})
    if res.get("applied"): return res, bad
    # errors that only appear when pieces are together: place in halves, each seeing the previous as existing
    h = len(good) // 2
    r1, b1 = place_skip_failing(rpc, good[:h], snap_tolerance) if h else (None, [])
    r2, b2 = place_skip_failing(rpc, good[h:], snap_tolerance)
    merged = {"applied": bool(r1 or r2), "created_ids": (r1 or {}).get("created_ids", []) + (r2 or {}).get("created_ids", []),
              "undo_ids": [r.get("undo_id") for r in (r1, r2) if r and r.get("undo_id")]}
    return merged, bad + b1 + b2


def chunks(plan, tile=700.0, max_segs=180):
    """Grid segments in tiles ordered outward from the plan seed (so each chunk joins what is already built), then shore."""
    sx, sz = plan["seed"]
    tiles = {}
    for s in plan["segments"]:
        if s["phase"] != "grid": continue
        mx, mz = (s["start"][0] + s["end"][0]) / 2, (s["start"][1] + s["end"][1]) / 2
        tiles.setdefault((math.floor(mx / tile), math.floor(mz / tile)), []).append(s)
    order = sorted(tiles, key=lambda t: math.hypot((t[0] + .5) * tile - sx, (t[1] + .5) * tile - sz))
    out = []
    for t in order:
        ss = tiles[t]
        for k in range(0, len(ss), max_segs): out.append(("grid", ss[k:k + max_segs]))
    shore = [s for s in plan["segments"] if s["phase"] == "shore"]
    for k in range(0, len(shore), max_segs): out.append(("shore", shore[k:k + max_segs]))
    return out


def clean(s): return {k: v for k, v in s.items() if k in ("start", "end", "type", "controls", "control", "elevation")}


def on_plan(e, segs, tol=2.5):
    """Does existing edge e lie on one of the planned segments (both ends + middle within tol)?"""
    c = e["curve"]
    probes = [(c[0][0], c[0][2]), survey.bez(c, 0.5), (c[3][0], c[3][2])]
    for s in segs:
        if "controls" in s:
            P = [s["start"], s["controls"][0], s["controls"][1], s["end"]]
            poly = [_cubic(*P, k / 24) for k in range(25)]
        else:
            poly = [tuple(s["start"]), tuple(s["end"])]
        def dist(pt):
            best = 1e9
            for a_, b_ in zip(poly, poly[1:]):
                ax, az = a_; bx, bz = b_; dx, dz = bx - ax, bz - az; L2 = dx * dx + dz * dz or 1e-9
                t = max(0, min(1, ((pt[0] - ax) * dx + (pt[1] - az) * dz) / L2))
                best = min(best, math.hypot(ax + dx * t - pt[0], az + dz * t - pt[1]))
            return best
        if all(dist(p) < tol for p in probes): return True
    return False


def remove_built(rpc, plan, area, batch=200):
    """Bulldoze every existing edge that lies on the plan's segments (by shape, so split/re-created edges are found)."""
    segs = plan["segments"]
    net = rpc("road_network", {**area.params, "limit": 60000})
    # bucket planned segments spatially so the shape test stays fast
    B = 400.0; buckets = {}
    for s in segs:
        xs = [s["start"][0], s["end"][0]] + [c[0] for c in s.get("controls", [])]
        zs = [s["start"][1], s["end"][1]] + [c[1] for c in s.get("controls", [])]
        for bx in range(math.floor(min(xs) / B), math.floor(max(xs) / B) + 1):
            for bz in range(math.floor(min(zs) / B), math.floor(max(zs) / B) + 1):
                buckets.setdefault((bx, bz), []).append(s)
    ids = []
    for e in net["edges"]:
        if "flow" not in e: continue
        mx, mz = survey.bez(e["curve"], 0.5)
        cand = buckets.get((math.floor(mx / B), math.floor(mz / B)), [])
        if cand and on_plan(e, cand): ids.append(e["id"])
    results = []
    for k in range(0, len(ids), batch):
        results.append(rpc("bulldoze", {"ids": ids[k:k + batch], "timeout_s": 120}))
    return {"edges_found": len(ids), "batches": len(results), "applied": sum(1 for r in results if r.get("applied"))}
