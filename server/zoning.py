"""v0.27 (2026-09-26): zoning forecast - how well a road layout (existing roads + an optional draft) will zone, before
anything is built.

The game gives every zonable road a zone block on each side, 6 cells (48 m) deep, in 8 m frontage columns. At a right-
angle junction the corner square goes to one of the two blocks - nothing is lost. Land IS lost when
  - two roads run (nearly) parallel closer than both their depths (blocks get shallow: more road per zoned cell),
  - roads meet at a skew (triangular wedges no rectangular block covers, and hidden overlaps),
  - two parallel roads are too far apart for their blocks to meet: a dead gap between the zoning edges that no zoning
    will ever reach.
Straight (grid) roads should score ~100% full depth with no dead gaps; curved / waterfront roads may lose some.

- forecast(w, draft)   w = survey.World of the area, draft = place_roads-shaped segments.
"""
import math, collections
import numpy as np
from scipy import ndimage
import survey

CELL, DEPTH = 8.0, 6
PARALLEL_COS = math.cos(math.radians(35))     # another road within 35 deg of this one's direction = "parallel"
SKEW_DEG = 80.0                                # grid junctions should be 90 deg; flag anything under this
SKIP = ("Highway", "Ramp", "Pipe", "voltage", "Power", "Track", "Train", "Subway", "Cable", "Seaway",
        "Invisible", "Quay", "Pedestrian", "Path", "Pillar")


def _cubic(p0, c1, c2, p3, n):
    out = []
    for k in range(n + 1):
        t = k / n; u = 1 - t
        out.append((u ** 3 * p0[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t ** 3 * p3[0],
                    u ** 3 * p0[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t ** 3 * p3[1]))
    return out


def seg_polyline(s):
    """A place_roads segment (start, end, optional controls [c1, c2] or control) as a polyline every ~4 m."""
    p0, p3 = tuple(s["start"]), tuple(s["end"])
    L = math.dist(p0, p3); n = max(2, int(L / 4))
    if s.get("controls"):
        c1, c2 = s["controls"]; return _cubic(p0, tuple(c1), tuple(c2), p3, n)
    if s.get("control"):
        c = tuple(s["control"])
        c1 = (p0[0] + 2 / 3 * (c[0] - p0[0]), p0[1] + 2 / 3 * (c[1] - p0[1])); c2 = (p3[0] + 2 / 3 * (c[0] - p3[0]), p3[1] + 2 / 3 * (c[1] - p3[1]))
        return _cubic(p0, c1, c2, p3, n)
    return [(p0[0] + (p3[0] - p0[0]) * k / n, p0[1] + (p3[1] - p0[1]) * k / n) for k in range(n + 1)]


def _unit(dx, dz):
    L = math.hypot(dx, dz) or 1.0
    return (dx / L, dz / L)


def _end_tan(p, c, c2):
    """Unit direction leaving end point p into the curve (toward its control c; c2 if c coincides with p)."""
    d = (c[0] - p[0], c[1] - p[1])
    if math.hypot(*d) < 1e-3: d = (c2[0] - p[0], c2[1] - p[1])
    return _unit(*d)


def _junctions(roads, ends, nodes, tol=12.0, tans=None):
    """Junctions -> list of (pos, [arm directions away from it], all_straight). ends[k] = (start key, end key) of road k:
    existing roads use the game's node ids; a draft end takes an existing node within tol m, else its rounded position
    (and a draft end on the middle of a road makes a T with that road's two directions)."""
    arms = collections.defaultdict(list); pos = {}; straight = collections.defaultdict(lambda: True)
    npos = {nid: (p[0], p[2]) for nid, p in nodes.items()}
    for k, (pl, _, is_draft, st) in enumerate(roads):
        m = min(3, len(pl) - 1)
        t0, t1 = (tans[k] if tans else (_unit(pl[m][0] - pl[0][0], pl[m][1] - pl[0][1]), _unit(pl[-1 - m][0] - pl[-1][0], pl[-1 - m][1] - pl[-1][1])))
        for key, p, d in ((ends[k][0], pl[0], t0), (ends[k][1], pl[-1], t1)):
            if key is None:                                  # draft end: an existing node nearby, or its own point
                near = min(npos.items(), key=lambda kv: math.dist(kv[1], p), default=None)
                key = near[0] if near and math.dist(near[1], p) <= tol else ("p", round(p[0] / 4), round(p[1] / 4))
                if key[0] == "p":
                    for pl2, _, _, st2 in roads:
                        if pl2 is pl: continue
                        P = np.array(pl2); dd = np.hypot(*(P - p).T); i = int(np.argmin(dd))
                        if dd[i] <= 3 and 2 < i < len(pl2) - 3:
                            t = _unit(pl2[i + 1][0] - pl2[i - 1][0], pl2[i + 1][1] - pl2[i - 1][1])
                            arms[key] += [t, (-t[0], -t[1])]; straight[key] = straight[key] and st2; break
            arms[key].append(d); pos.setdefault(key, npos.get(key, p)); straight[key] = straight[key] and st
    out = []
    for key, A in arms.items():
        uniq = []
        for d in A:
            if all(d[0] * u[0] + d[1] * u[1] < math.cos(math.radians(10)) for u in uniq): uniq.append(d)
        if len(uniq) >= 3: out.append((pos[key], uniq, straight[key]))
    return out


def _min_angle(arms):
    angs = sorted(math.degrees(math.atan2(d[1], d[0])) % 360 for d in arms)
    gaps = [(angs[(i + 1) % len(angs)] - angs[i]) % 360 for i in range(len(angs))]
    return min(gaps)


def forecast(w, draft=None, widths=None, res=4.0, max_slope=0.40, hotspots=8, max_gap_m=120.0, min_hole_m2=1000.0, square_tol_deg=2.0):
    """Zoning forecast for the zonable roads in w (existing, at grade) plus the draft segments."""
    area = w.area
    R = survey.Raster(area, res); H, W = R.H, R.W
    roads, ends, tans = [], [], []                       # (polyline, half width, is_draft, straight), (start key, end key), exact end tangents
    for e in w.net["edges"]:
        if "flow" not in e or any(k.lower() in e["type"].lower() for k in SKIP): continue
        el = e.get("elevation") or 0; el = el if isinstance(el, list) else [el, el]
        if max(abs(v) for v in el) > 3: continue          # bridges / tunnels get no zoning
        roads.append((survey.polyline(e["curve"], max(2, int(e["length"] / 4))), e["width"] / 2, False, survey.is_straight(e["curve"])))
        ends.append((e.get("start_node"), e.get("end_node")))
        c = e["curve"]; tans.append((_end_tan((c[0][0], c[0][2]), (c[1][0], c[1][2]), (c[2][0], c[2][2])),
                                     _end_tan((c[3][0], c[3][2]), (c[2][0], c[2][2]), (c[1][0], c[1][2]))))
    for s in draft or []:
        wd = (widths or {}).get(s.get("type"), 12.0)
        roads.append((seg_polyline(s), wd / 2, True, not (s.get("controls") or s.get("control"))))
        ends.append((None, None))
        p0, p3 = tuple(s["start"]), tuple(s["end"])
        if s.get("controls"): c1, c2 = tuple(s["controls"][0]), tuple(s["controls"][1])
        elif s.get("control"): c1 = c2 = tuple(s["control"])
        else: c1, c2 = p3, p0
        tans.append((_end_tan(p0, c1, c2), _end_tan(p3, c2, c1)))
    if not roads: return {"columns": 0, "note": "no zonable roads here"}
    from PIL import Image, ImageDraw
    bimg = Image.new("L", (W, H), 0); bd = ImageDraw.Draw(bimg)       # at-grade highways / tracks: a gap stops there
    for e in w.net["edges"]:
        if "flow" in e and not any(k in e["type"] for k in ("Highway", "Ramp")): continue
        if not any(k.lower() in e["type"].lower() for k in ("Highway", "Ramp", "Track", "Train")): continue
        el = e.get("elevation") or 0; el = el if isinstance(el, list) else [el, el]
        if max(abs(v) for v in el) > 3: continue
        bd.line([R.px(x, z) for x, z in survey.polyline(e["curve"])], fill=255, width=max(1, int(round(e["width"] / res))))
    barrier = np.asarray(bimg) > 0

    # nearest road per pixel (Voronoi of the centrelines), its direction there, and the distance to it
    lab = np.zeros((H, W), np.int32); tx = np.zeros((H, W), np.float32); tz = np.zeros((H, W), np.float32)
    for idx, (pl, half, _, _) in enumerate(roads, start=1):
        for (x0, z0), (x1, z1) in zip(pl, pl[1:]):
            ux, uz = _unit(x1 - x0, z1 - z0)
            i0, j0 = R.px(x0, z0); i1, j1 = R.px(x1, z1)
            n = max(1, int(max(abs(i1 - i0), abs(j1 - j0)) * 2))
            for k in range(n + 1):
                i = int(i0 + (i1 - i0) * k / n); j = int(j0 + (j1 - j0) * k / n)
                if 0 <= i < W and 0 <= j < H: lab[j, i] = idx; tx[j, i] = ux; tz[j, i] = uz
    dist, (ji, ii) = ndimage.distance_transform_edt(lab == 0, return_indices=True)
    nearest = lab[ji, ii]; ntx = tx[ji, ii]; ntz = tz[ji, ii]; dist = dist * res
    water = R.terrain(w.grid, "water_depth") > 0.3 if w.grid.get("water_depth") else np.zeros((H, W), bool)
    steep = R.terrain(survey.slope_of(w.grid), "slope") > max_slope
    halfs = np.array([0.0] + [h for _, h, _, _ in roads])
    reach = dist <= halfs[nearest] + DEPTH * CELL + 2    # inside some road's zoning reach
    in_corridor = dist <= halfs[nearest] + 0.5          # on some road's surface

    def at(x, z):
        i, j = R.px(x, z); i, j = int(i), int(j)
        return (j, i) if 0 <= i < W and 0 <= j < H else None

    hist = collections.Counter(); stop = collections.Counter(); hist_d = collections.Counter()
    kind = {"straight": collections.Counter(), "curved": collections.Counter()}
    lost = lost_d = 0; shallow_pts = []
    gap_cols, gap_m2, gap_pts, gap_widths = 0, 0.0, [], []
    for idx, (pl, half, is_draft, straight) in enumerate(roads, start=1):
        kh = kind["straight" if straight else "curved"]
        s = np.concatenate([[0], np.cumsum([math.dist(a, b) for a, b in zip(pl, pl[1:])])])
        if s[-1] < CELL: continue
        P = np.array(pl)
        for m in np.arange(CELL / 2, s[-1] - CELL / 2 + 1e-6, CELL):   # column centres along the road
            k = int(np.searchsorted(s, m) - 1); k = max(0, min(k, len(pl) - 2))
            f = (m - s[k]) / ((s[k + 1] - s[k]) or 1)
            c = P[k] + (P[k + 1] - P[k]) * f
            t = (P[k + 1] - P[k]); t = t / (np.linalg.norm(t) or 1); nrm = np.array([-t[1], t[0]])
            cij = at(c[0], c[1])
            if cij is None: continue
            if nearest[cij] != idx and in_corridor[cij]:                      # inside a junction (not a piece of this street)
                sx, sz = R.world(ii[cij], ji[cij])
                if abs((sx - c[0]) * nrm[0] + (sz - c[1]) * nrm[1]) > 1.0: continue
            for side in (1, -1):
                depth, why, parallel_hit = 0, "full", False
                for d in range(DEPTH):
                    q = c + nrm * side * (half + CELL * d + CELL / 2)
                    ij = at(q[0], q[1])
                    if ij is None: why = "edge"; break
                    if water[ij] or steep[ij]: why = "terrain"; break
                    o = nearest[ij]
                    if o != idx:
                        sx, sz = R.world(ii[ij], ji[ij])                      # the other road's nearest centreline point
                        lateral = abs((sx - c[0]) * nrm[0] + (sz - c[1]) * nrm[1])
                        par = abs(t[0] * ntx[ij] + t[1] * ntz[ij]) >= PARALLEL_COS
                        if not (par and lateral <= half + CELL / 2):          # not just this street's next piece
                            if in_corridor[ij]: why = "road"; parallel_hit = par; break    # another road's surface within 48 m
                            if par and dist[ij] < halfs[o] + DEPTH * CELL - 0.5:
                                why = "road"; parallel_hit = True; break      # inside a parallel road's block: too close
                        # a crossing road's block takes this corner cell: the land is still zoned, not a loss
                    depth += 1
                if why == "edge": continue
                if why == "road" and depth == 0 and not parallel_hit: continue    # beside a crossing street: the street itself
                if why == "terrain" and depth == 0: continue      # shore / cliff frontage: not a layout question
                hist[depth] += 1; stop[why] += 1
                if why != "terrain": kh[depth] += 1
                if is_draft: hist_d[depth] += 1
                if why == "road":
                    lost += DEPTH - depth
                    if is_draft: lost_d += DEPTH - depth
                    if depth <= 4: shallow_pts.append((float(c[0] + nrm[0] * side * (half + 12)), float(c[1] + nrm[1] * side * (half + 12))))
                if why == "full":                               # past the zoning edge: land no road will ever zone?
                    g, closed = 0.0, False
                    for step in range(1, int(max_gap_m / 4) + 1):
                        q = c + nrm * side * (half + DEPTH * CELL + 4.0 * step - 2)
                        ij = at(q[0], q[1])
                        if ij is None or water[ij] or steep[ij] or barrier[ij]: break
                        if in_corridor[ij] or (reach[ij] and nearest[ij] != idx): closed = True; break
                        if reach[ij]: continue
                        g += 4.0
                    if closed and g >= 8.0:
                        gap_cols += 1; gap_m2 += g * CELL / 2           # each gap is seen from both sides
                        gap_widths.append(g)
                        gp = c + nrm * side * (half + DEPTH * CELL + g / 2)
                        gap_pts.append((float(gp[0]), float(gp[1])))

    def share(h):
        n = sum(h.values()); return round(h[DEPTH] / n, 3) if n else None
    def hot(pts, key):
        cl = collections.Counter((int(x // 60), int(z // 60)) for x, z in pts)
        return [{"pos": [round((i + 0.5) * 60), round((j + 0.5) * 60)], key: n} for (i, j), n in cl.most_common(hotspots)]

    # holes (v0.27b): land inside a block that no road's 6-cell zoning reaches - enclosed by roads, not open to water or
    # the edge of the area. The user's rule: none of these inside a grid.
    free = ~reach & ~in_corridor & ~water & ~steep & ~barrier
    labl, nlab = ndimage.label(free)
    holes = []
    if nlab:
        sizes = ndimage.sum(free, labl, index=range(1, nlab + 1)) * res * res
        for k, a_ in enumerate(sizes, start=1):
            if a_ < float(min_hole_m2): continue
            ys, xs = np.nonzero(labl == k)
            if ys.min() == 0 or xs.min() == 0 or ys.max() == H - 1 or xs.max() == W - 1: continue     # open to the area edge
            y0, y1, x0, x1 = max(0, ys.min() - 3), min(H, ys.max() + 4), max(0, xs.min() - 3), min(W, xs.max() + 4)
            if water[y0:y1, x0:x1].any() or steep[y0:y1, x0:x1].any(): continue                   # open to water / cliffs
            cx, cz = R.world(xs.mean(), ys.mean())
            holes.append({"pos": [round(cx), round(cz)], "m2": int(a_), "size_m": [int((xs.max() - xs.min() + 1) * res), int((ys.max() - ys.min() + 1) * res)]})
    holes.sort(key=lambda h: -h["m2"])

    skew = []
    nodes = {n["id"]: n["pos"] for n in (w.net.get("nodes") or [])} if isinstance(w.net.get("nodes"), list) else             {k: v["pos"] for k, v in (w.net.get("nodes") or {}).items()}
    not_square = []
    for p, arms, st in _junctions(roads, ends, nodes, tans=tans):
        a = _min_angle(arms)
        if not (area.x0 <= p[0] <= area.x1 and area.z0 <= p[1] <= area.z1): continue
        if a < SKEW_DEG:
            skew.append({"pos": [round(p[0]), round(p[1])], "min_angle_deg": round(a, 1), "grid": st})
        elif a < 90 - square_tol_deg:                   # 80..88: looks square on the map, still breaks the blocks
            not_square.append({"pos": [round(p[0]), round(p[1])], "min_angle_deg": round(a, 1), "grid": st})
    skew.sort(key=lambda j: j["min_angle_deg"])
    cols = sum(hist.values())
    out = {"columns": cols, "depth_histogram": {str(d): hist[d] for d in range(DEPTH + 1)},
           "full_depth_share_straight": share(kind["straight"]), "full_depth_share_curved": share(kind["curved"]),
           "stopped_by": dict(stop), "cells_lost_to_layout": lost, "m2_lost_to_layout": int(lost * CELL * CELL),
           "shallow_hotspots": hot(shallow_pts, "shallow_columns"),
           "dead_gaps": {"columns": gap_cols, "m2": int(gap_m2), "median_width_m": float(np.median(gap_widths)) if gap_widths else 0.0,
                         "hotspots": hot(gap_pts, "gap_columns"),
                         "note": f"land between two roads' zoning edges (< {max_gap_m:.0f} m wide) that no zoning will ever reach"},
           "holes": {"count": len(holes), "m2": sum(h["m2"] for h in holes), "list": holes[:hotspots],
                     "note": f"land inside a block (>= {min_hole_m2:.0f} m2) that no road's zoning reaches - a street is missing or a block is too big"},
           "not_square_junctions": {"count": len(not_square), "list": sorted(not_square, key=lambda j: j["min_angle_deg"])[:hotspots],
                                    "note": f"junctions between {SKEW_DEG:.0f} and {90 - square_tol_deg:.0f} deg - still break the block's zoning; grid corners must be 90"},
           "skewed_junctions": {"count": len(skew), "grid": sum(1 for j in skew if j["grid"]), "worst": skew[:hotspots],
                                "note": f"junctions whose smallest angle is under {SKEW_DEG:.0f} deg (wedges + hidden cells); grid = all arms straight"},
           "target": "grid: full_depth_share_straight ~1.0, no holes, no dead gaps, every corner 90 deg (within 2); "
                     "curved / waterfront roads may lose some",
           "note": "8 m frontage columns on both sides of every zonable road, usable depth in cells (6 = full, 48 m); a crossing "
                   "road taking a corner cell is not a loss, a parallel road closer than both depths or a road surface within "
                   "48 m is; columns facing water / steep ground are left out of the shares"}
    if draft:
        dc = sum(hist_d.values())
        out["draft"] = {"columns": dc, "depth_histogram": {str(d): hist_d[d] for d in range(DEPTH + 1)},
                        "full_depth_share": round(hist_d[DEPTH] / dc, 3) if dc else None, "cells_lost_to_layout": lost_d}
    return out
