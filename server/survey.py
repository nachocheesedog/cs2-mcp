"""Perception helpers for planning: a top-down map image and a facts-only check-up of roads and zoning.

They show what is there and what looks off (dead ends, near-miss connections, sharp junctions, congestion, unused
zonable land). They make no decisions: the caller looks at the map, reads the facts and decides what to build.
World axes: x to the right, z up the image. Zoning cells are 8 m; a road zones up to 6 cells (48 m) deep per side.
"""
import math, os, tempfile
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage
from scipy.spatial import cKDTree

OUT_DIR = os.path.join(tempfile.gettempdir(), "CS2MCP")
MAP_EDGE = 7100          # playable map is +-7168 m; roads ending out here are usually outside connections
ZONE_REACH = 48.0        # 6 cells


# ----------------------------------------------------------------------------- data
class Area:
    def __init__(self, x0, z0, x1, z1):
        self.x0, self.z0, self.x1, self.z1 = float(x0), float(z0), float(x1), float(z1)
    @property
    def params(self): return {"xmin": self.x0, "zmin": self.z0, "xmax": self.x1, "zmax": self.z1}
    @property
    def size(self): return max(self.x1 - self.x0, self.z1 - self.z0)
    def inside(self, x, z, margin=0.0):
        return self.x0 + margin <= x <= self.x1 - margin and self.z0 + margin <= z <= self.z1 - margin


def area_from(a, camera_ground):
    if all(k in a for k in ("xmin", "zmin", "xmax", "zmax")):
        return Area(a["xmin"], a["zmin"], a["xmax"], a["zmax"])
    c = a.get("center") or camera_ground()
    w = float(a.get("size", 1200)); h = float(a.get("size_z", w))
    return Area(c[0] - w / 2, c[1] - h / 2, c[0] + w / 2, c[1] + h / 2)


class World:
    """Everything the map and check-up need for one area (one round of bridge calls)."""
    def __init__(self, rpc, area, grid_step=None):
        self.area = area
        p = area.params
        self.net = rpc("road_network", {**p, "limit": 30000})
        self.zones = rpc("zone_blocks", {**p, "limit": 30000})
        self.buildings = rpc("query_buildings", {**p, "limit": 30000})["buildings"]
        step = grid_step or max(8, int(round(area.size / 180 / 4)) * 4)
        self.grid = rpc("terrain_grid", {**p, "step": step})
        self.edges = [e for e in self.net["edges"] if "flow" in e]   # roads only (pipes, cables, tracks have no Road component)
        self.nodes = {n["id"]: n for n in self.net["nodes"]}
        self.blocks = self.zones["blocks"]
        for b in self.blocks: normalise_cells(b)
        self.zonable_roads = {b["road"] for b in self.blocks if "road" in b}


# ----------------------------------------------------------------------------- geometry
def bez(c, t):
    u = 1 - t
    return (u * u * u * c[0][0] + 3 * u * u * t * c[1][0] + 3 * u * t * t * c[2][0] + t * t * t * c[3][0],
            u * u * u * c[0][2] + 3 * u * u * t * c[1][2] + 3 * u * t * t * c[2][2] + t * t * t * c[3][2])


def polyline(c, n=None):
    if n is None:
        chord = math.dist((c[0][0], c[0][2]), (c[3][0], c[3][2]))
        n = 1 if is_straight(c) else max(4, min(24, int(chord / 12)))
    return [bez(c, k / n) for k in range(n + 1)]


def is_straight(c, tol=0.5):
    ax, az, dx, dz = c[0][0], c[0][2], c[3][0], c[3][2]
    L = math.hypot(dx - ax, dz - az) or 1e-6
    for p in (c[1], c[2]):
        if abs((dx - ax) * (p[2] - az) - (dz - az) * (p[0] - ax)) / L > tol: return False
    return True


def arm_dir(c, at_start):
    """Unit direction leaving the node along the edge."""
    pts = [c[0], c[1], c[2], c[3]] if at_start else [c[3], c[2], c[1], c[0]]
    for q in pts[1:]:
        dx, dz = q[0] - pts[0][0], q[2] - pts[0][2]
        L = math.hypot(dx, dz)
        if L > 0.05: return dx / L, dz / L
    return 1.0, 0.0


def normalise_cells(b):
    """-> b["zone"] (per cell: '#' hidden, '.' unzoned, else a legend key / '?') and b["occ"] (per cell: built on).
    Accepts the v0.4.1 format (separate 'built' string) and the v0.4.0 one (upper case = built, 'O' = built + ambiguous)."""
    cells = b["cells"]
    if "built" in b:
        b["zone"], b["occ"] = cells, [c == "1" for c in b["built"]]
        return
    zone, occ = [], []
    for ch in cells:
        if ch in "#.?": zone.append(ch); occ.append(False)
        elif ch == "O": zone.append("?"); occ.append(True)
        elif ch.isupper(): zone.append(ch.lower()); occ.append(True)
        else: zone.append(ch); occ.append(False)
    b["zone"], b["occ"] = "".join(zone), occ


def cell_geometry(b):
    """Yield (x, y, zone code, built, centre, corners) for each cell of a zone block."""
    px, _, pz = b["pos"]; dx, dz = b["dir"]; rx, rz = dz, -dx
    sx, sy = b["size"]
    for i, ch in enumerate(b["zone"]):
        y, x = divmod(i, sx)
        oy, ox = 4 * (sy - 2 * y - 1), 4 * (sx - 2 * x - 1)
        cx, cz = px + dx * oy + rx * ox, pz + dz * oy + rz * ox
        corners = [(cx + dx * sy_ + rx * sx_, cz + dz * sy_ + rz * sx_) for sy_, sx_ in ((4, 4), (4, -4), (-4, -4), (-4, 4))]
        yield x, y, ch, b["occ"][i], (cx, cz), corners


def lot_corners(bld):
    if not bld.get("lot"): return None
    fx, fz = bld.get("forward") or (0.0, 1.0)
    L = math.hypot(fx, fz) or 1.0; fx, fz = fx / L, fz / L; rx, rz = fz, -fx
    w, d = bld["lot"][0] * 4.0, bld["lot"][1] * 4.0
    x, _, z = bld["position"]
    return [(x + fx * a + rx * b, z + fz * a + rz * b) for a, b in ((d, w), (d, -w), (-d, -w), (-d, w))]


def r1(v): return round(v, 1)


# ----------------------------------------------------------------------------- rasters
class Raster:
    def __init__(self, area, res):
        self.a, self.res = area, res
        self.W = max(2, int(math.ceil((area.x1 - area.x0) / res)))
        self.H = max(2, int(math.ceil((area.z1 - area.z0) / res)))
    def px(self, x, z): return ((x - self.a.x0) / self.res, (self.a.z1 - z) / self.res)
    def world(self, i, j): return (self.a.x0 + (i + 0.5) * self.res, self.a.z1 - (j + 0.5) * self.res)
    def new(self): return Image.new("L", (self.W, self.H), 0)
    def terrain(self, grid, key):
        """Resample a terrain_grid layer (rows = z ascending) onto this raster (rows = z descending)."""
        arr = np.array(grid[key], dtype=np.float32)[::-1]
        img = Image.fromarray(arr, mode="F")
        # grid covers [xmin, xmin+(nx-1)*step] x [...]; stretch to the raster (the tail is < one step)
        return np.asarray(img.resize((self.W, self.H), Image.BILINEAR))


def slope_of(grid):
    h = np.array(grid["heights"], dtype=np.float32); s = grid["step"]
    gz, gx = np.gradient(h, s)
    out = dict(grid); out["slope"] = np.hypot(gx, gz).tolist()
    return out


def waste_analysis(w, res=None):
    """Land within zoning reach of a zonable road that is not road, zone cell, building lot, water or steep ground."""
    a = w.area
    res = res or max(2.0, a.size / 700)
    R = Raster(a, res)
    road_img, zroad_img, cell_img, lot_img = R.new(), R.new(), R.new(), R.new()
    dr, dz_, dc, dl = (ImageDraw.Draw(i) for i in (road_img, zroad_img, cell_img, lot_img))
    for e in w.edges:
        pts = [R.px(x, z) for x, z in polyline(e["curve"])]
        width = max(1, int(round((e["width"] + 1) / res)))
        dr.line(pts, fill=255, width=width, joint="curve")
        if e["id"] in w.zonable_roads: dz_.line(pts, fill=255, width=width, joint="curve")
    for b in w.blocks:
        for _, _, ch, _, _, corners in cell_geometry(b):
            if ch != "#": dc.polygon([R.px(*p) for p in corners], fill=255)
    for bld in w.buildings:
        lc = lot_corners(bld)
        if lc: dl.polygon([R.px(*p) for p in lc], fill=255)
    road = np.asarray(road_img) > 0; zroad = np.asarray(zroad_img) > 0
    cells = np.asarray(cell_img) > 0; lots = np.asarray(lot_img) > 0
    g = slope_of(w.grid)
    water = R.terrain(w.grid, "water_depth") > 0.5 if w.grid.get("water_depth") else np.zeros_like(road)
    steep = R.terrain(g, "slope") > 0.25
    near = ndimage.distance_transform_edt(~zroad) * res <= ZONE_REACH if zroad.any() else np.zeros_like(road)
    free = near & ~road & ~cells & ~lots & ~water
    waste = free & ~steep
    return R, {"waste": waste, "steep_unused": free & steep, "cells": cells, "road": road, "water": water, "near": near}


def regions(mask, R, min_m2=250, top=15):
    lab, n = ndimage.label(mask, structure=np.ones((3, 3)))
    if n == 0: return []
    idx = np.arange(1, n + 1)
    counts = ndimage.sum(np.ones_like(lab), lab, idx)
    coms = ndimage.center_of_mass(mask, lab, idx)
    boxes = ndimage.find_objects(lab)
    out = []
    for k in np.argsort(-counts)[:top * 3]:
        m2 = float(counts[k]) * R.res * R.res
        if m2 < min_m2: break
        cj, ci = coms[k]; sl = boxes[k]
        cx, cz = R.world(ci, cj)
        x0, z1 = R.world(sl[1].start, sl[0].start); x1, z0 = R.world(sl[1].stop - 1, sl[0].stop - 1)
        out.append({"area_m2": int(m2), "centre": [r1(cx), r1(cz)], "bbox": [r1(x0), r1(z0), r1(x1), r1(z1)]})
        if len(out) >= top: break
    return out


# ----------------------------------------------------------------------------- facts
def check(w, short_link_m=40.0, sharp_deg=40.0, kink_deg=8.0, near_miss_m=12.0):
    a = w.area
    E, N = w.edges, w.nodes
    arms = {}                                         # node id -> [(dir, edge)]
    for e in E:
        arms.setdefault(e["start_node"], []).append((arm_dir(e["curve"], True), e))
        arms.setdefault(e["end_node"], []).append((arm_dir(e["curve"], False), e))
    def pos(nid): p = N[nid]["pos"]; return (p[0], p[2])
    def in_area(nid): return nid in N and a.inside(*pos(nid))

    # dead ends + near misses (a dead end that stops just short of another road usually means a failed connection)
    seg_pts, seg_edge = [], []
    for e in E:
        for p in polyline(e["curve"], 16): seg_pts.append(p); seg_edge.append(e)
    tree = cKDTree(seg_pts) if seg_pts else None
    dead, near_miss = [], []
    for nid, n in N.items():
        if not in_area(nid) or n["degree"] != 1 or n.get("owned_arms") or n.get("outside_connection"): continue
        x, z = pos(nid)
        if abs(x) > MAP_EDGE or abs(z) > MAP_EDGE: continue
        own = arms[nid][0][1] if arms.get(nid) else None
        dead.append({"node": nid, "pos": [r1(x), r1(z)], "road": own["type"] if own else None})
        if tree is not None:
            best = None
            for k in tree.query_ball_point((x, z), near_miss_m + 20):
                e = seg_edge[k]
                if own is not None and e["id"] == own["id"]: continue
                if nid in (e["start_node"], e["end_node"]): continue
                d = math.dist((x, z), seg_pts[k]) - e["width"] / 2
                if d <= near_miss_m and (best is None or d < best[0]): best = (d, e)
            if best: near_miss.append({"node": nid, "pos": [r1(x), r1(z)], "gap_m": r1(max(0, best[0])), "to_road": best[1]["id"], "to_type": best[1]["type"]})

    # separate nodes sitting on top of each other (looks connected, is not)
    ids = [nid for nid in N if in_area(nid)]
    dup = []
    if len(ids) > 1:
        t2 = cKDTree([pos(i) for i in ids])
        for i, j in sorted(t2.query_pairs(4.0)):
            a_, b_ = ids[i], ids[j]
            if not any(b_ in (e["start_node"], e["end_node"]) for _, e in arms.get(a_, [])):
                dup.append({"nodes": [a_, b_], "pos": [r1(v) for v in pos(a_)]})

    # junction geometry
    degree_hist, sharp, kinks = {}, [], []
    for nid, lst in arms.items():
        if not in_area(nid): continue
        deg = N[nid]["degree"] if nid in N else len(lst)
        degree_hist[deg] = degree_hist.get(deg, 0) + 1
        angs = sorted(math.degrees(math.atan2(d[1], d[0])) % 360 for d, _ in lst)
        if len(angs) >= 3:
            gaps = [(angs[(k + 1) % len(angs)] - angs[k]) % 360 for k in range(len(angs))]
            if min(gaps) < sharp_deg:
                sharp.append({"node": nid, "pos": [r1(v) for v in pos(nid)], "min_angle_deg": r1(min(gaps)), "arms": len(angs),
                              "types": sorted({e["type"] for _, e in lst})})
        elif len(lst) == 2 and all(is_straight(e["curve"]) for _, e in lst):
            (d1, e1), (d2, e2) = lst
            turn = 180 - math.degrees(math.acos(max(-1, min(1, d1[0] * d2[0] + d1[1] * d2[1]))))
            if kink_deg < turn < 60:                   # slight bends on straight roads; real corners are deliberate
                kinks.append({"node": nid, "pos": [r1(v) for v in pos(nid)], "turn_deg": r1(turn)})

    # short links between junctions (weaving / queue spill-back risk)
    short = []
    for e in E:
        s, t = e["start_node"], e["end_node"]
        if s in N and t in N and N[s]["degree"] >= 3 and N[t]["degree"] >= 3 and e["length"] < short_link_m:
            mid = bez(e["curve"], 0.5)
            if a.inside(*mid): short.append({"road": e["id"], "type": e["type"], "length_m": e["length"], "mid": [r1(mid[0]), r1(mid[1])]})

    # connectivity: islands = components fully inside the area with no outside connection
    parent = {}
    def find(x):
        while parent.setdefault(x, x) != x: parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for e in E: parent[find(e["start_node"])] = find(e["end_node"])
    comps = {}
    for e in E: comps.setdefault(find(e["start_node"]), []).append(e)
    islands = []
    for root, es in comps.items():
        nodes = {e["start_node"] for e in es} | {e["end_node"] for e in es}
        if any(N.get(n, {}).get("outside_connection") for n in nodes): continue
        if not all(in_area(n) and a.inside(*pos(n), margin=5) for n in nodes): continue   # continues beyond the area
        if any(N.get(n, {}).get("owned_arms") for n in nodes): continue                     # attached to a building's own roads
        c = bez(es[0]["curve"], 0.5)
        islands.append({"edges": len(es), "length_m": int(sum(e["length"] for e in es)), "at": [r1(c[0]), r1(c[1])]})

    # traffic
    flows = [e for e in E if "flow" in e and a.inside(*bez(e["curve"], 0.5))]
    congested = sorted((e for e in flows if e["flow"] < 0.75), key=lambda e: e["flow"])[:15]
    length_by_type = {}
    for e in E: length_by_type[e["type"]] = length_by_type.get(e["type"], 0) + e["length"]

    # zoning
    vis = hidden = zoned = built = empty = 0
    depth_hist = [0] * 7
    zoned_empty = 0
    for b in w.blocks:
        sx, sy = b["size"]; cells = b["zone"]
        for ch, occ in zip(cells, b["occ"]):
            if ch == "#": hidden += 1; continue
            vis += 1
            if occ: built += 1
            if ch != ".": zoned += 1; zoned_empty += not occ
            elif not occ: empty += 1
        for x in range(sx):
            d = 0
            while d < sy and x + d * sx < len(cells) and cells[x + d * sx] != "#": d += 1
            depth_hist[min(d, 6)] += 1

    R, masks = waste_analysis(w)
    px_m2 = R.res * R.res
    waste_m2 = float(masks["waste"].sum()) * px_m2
    cell_m2 = vis * 64.0
    return {
        "area": [a.x0, a.z0, a.x1, a.z1],
        "roads": {
            "edges": len(E), "length_m_by_type": {k: int(v) for k, v in sorted(length_by_type.items(), key=lambda kv: -kv[1])},
            "junctions_by_arms": {str(k): v for k, v in sorted(degree_hist.items())},
            "dead_ends": {"count": len(dead), "list": dead[:20]},
            "near_miss_connections": {"count": len(near_miss), "list": sorted(near_miss, key=lambda d: d["gap_m"])[:20],
                                      "note": "dead ends stopping within a few metres of another road - usually a missed connection"},
            "overlapping_unconnected_nodes": {"count": len(dup), "list": dup[:20]},
            "sharp_junctions": {"count": len(sharp), "list": sorted(sharp, key=lambda d: d["min_angle_deg"])[:20], "threshold_deg": sharp_deg},
            "kinks": {"count": len(kinks), "list": sorted(kinks, key=lambda d: -d["turn_deg"])[:20], "threshold_deg": kink_deg},
            "short_links_between_junctions": {"count": len(short), "list": sorted(short, key=lambda d: d["length_m"])[:20], "threshold_m": short_link_m},
            "islands": {"count": len(islands), "list": islands[:20], "note": "road groups inside the area not connected to anything leaving it"},
            "congested": {"count_below_0_75": len([e for e in flows if e["flow"] < 0.75]), "measured": len(flows),
                          "worst": [{"road": e["id"], "type": e["type"], "flow": e["flow"],
                                     "mid": [r1(v) for v in bez(e["curve"], 0.5)]} for e in congested],
                          "note": "flow = speed vs free flow (1 = no congestion)"},
        },
        "zoning": {
            "blocks": len(w.blocks), "visible_cells": vis, "hidden_cells": hidden, "zoned_cells": zoned, "built_cells": built,
            "empty_unzoned_cells": empty, "zoned_not_built": zoned_empty,
            "frontage_depth_histogram": {str(k): v for k, v in enumerate(depth_hist)},
            "depth_note": "8 m road-frontage columns by usable depth in cells (6 = full). Many 1-3 columns = roads too close together or at awkward angles",
            "legend": w.zones.get("legend", {}),
        },
        "unused_land": {
            "flat_unused_m2_within_zoning_reach": int(waste_m2),
            "steep_unused_m2_within_zoning_reach": int(float(masks["steep_unused"].sum()) * px_m2),
            "zoning_efficiency": round(cell_m2 / (cell_m2 + waste_m2), 3) if cell_m2 + waste_m2 > 0 else None,
            "largest_pockets": regions(masks["waste"], R),
            "note": "land within 48 m of a zonable road that is not road, zone cell, building lot, water or steep (>25% grade)",
        },
    }


# ----------------------------------------------------------------------------- map
ZONE_COLOURS = [("low", "resid", (163, 214, 128)), ("row", "resid", (120, 190, 95)), ("medium", "resid", (120, 190, 95)),
                ("high", "resid", (62, 140, 60)), ("mixed", "", (80, 185, 170)), ("", "resid", (120, 190, 95)),
                ("", "commercial", (96, 150, 225)), ("", "office", (160, 115, 210)), ("", "industr", (230, 180, 70)),
                ("", "agri", (190, 150, 90)), ("", "forest", (120, 150, 80)), ("", "ore", (150, 130, 120)), ("", "oil", (110, 100, 100))]


def zone_colour(name):
    n = name.lower()
    for a, b, c in ZONE_COLOURS:
        if a in n and b in n: return c
    return (200, 200, 200)


def road_colour(e, by):
    if by == "traffic" and "flow" in e:
        f = e["flow"]
        return (70, 170, 70) if f >= 0.9 else (215, 200, 60) if f >= 0.75 else (235, 140, 50) if f >= 0.55 else (215, 50, 40)
    t = e["type"].lower()
    if "highway" in t or "ramp" in t: return (200, 70, 70)
    if "large" in t: return (235, 150, 60)
    if "medium" in t: return (240, 205, 90)
    if "gravel" in t or "dirt" in t: return (180, 150, 110)
    if "invisible" in t or "path" in t: return (200, 120, 200)
    return (252, 252, 252)


def nice_step(span, target=8):
    for s in (25, 50, 100, 200, 250, 500, 1000, 2000, 5000):
        if span / s <= target: return s
    return 10000


def font(size):
    try: return ImageFont.truetype("arial.ttf", size)
    except OSError: return ImageFont.load_default()


def render_map(w, px=1100, colour_by="type", layers=None, plan=None, marks=None, facts=None, districts=None, outlines=None):
    layers = set(layers or ["terrain", "waste", "zones", "buildings", "roads", "issues", "grid"])
    a = w.area
    res = a.size / px
    R = Raster(a, res)
    # terrain: hillshade + contours + water
    h = R.terrain(w.grid, "heights")
    gy, gx = np.gradient(h, res)
    shade = np.clip(0.82 + 0.9 * (-gx * 0.6 + gy * 0.6) / np.sqrt(1 + gx * gx + gy * gy), 0.55, 1.15)
    base = np.stack([np.full_like(h, 226), np.full_like(h, 222), np.full_like(h, 205)], -1) * shade[..., None]
    if "terrain" in layers:
        hr = float(np.ptp(h)) if h.size else 0
        ci = next((c for c in (2, 5, 10, 20, 25, 50, 100) if hr / c <= 18), 200)
        band = np.floor(h / ci)
        edge = (np.diff(band, axis=0, prepend=band[:1]) != 0) | (np.diff(band, axis=1, prepend=band[:, :1]) != 0)
        base[edge] *= 0.8
        slope = np.hypot(gx, gy)
        base[slope > 0.25] = base[slope > 0.25] * 0.85 + np.array([40, 10, 0])
    if w.grid.get("water_depth"):
        d = R.terrain(w.grid, "water_depth")
        wet = d > 0.3
        k = np.clip(d / 20, 0, 1)[..., None]
        base[wet] = (np.array([150, 195, 235]) * (1 - k) + np.array([60, 110, 175]) * k)[wet]
    img = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8), "RGB")
    over = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)

    if "waste" in layers:
        WR, masks = waste_analysis(w, res=max(res, 2.0))
        m = Image.fromarray((masks["waste"] * 255).astype(np.uint8)).resize(img.size, Image.NEAREST)
        over.paste((230, 40, 200, 120), mask=m)

    legend = w.zones.get("legend", {})
    if "zones" in layers:
        outline = res < 1.4
        for b in w.blocks:
            for _, _, ch, occ, _, corners in cell_geometry(b):
                if ch == "#": continue
                pts = [R.px(*p) for p in corners]
                if ch == ".": col = (150, 150, 150, 200) if occ else (245, 245, 240, 170)
                else:
                    c = zone_colour(legend.get(ch, ""))
                    col = tuple(int(v * 0.72) for v in c) + (235,) if occ else c + (215,)
                d.polygon(pts, fill=col, outline=(90, 90, 90, 90) if outline else None)
    if "buildings" in layers:
        for bld in w.buildings:
            lc = lot_corners(bld)
            if lc: d.polygon([R.px(*p) for p in lc], outline=(60, 60, 60, 160))
    img = Image.alpha_composite(img.convert("RGBA"), over)
    d = ImageDraw.Draw(img)

    if "roads" in layers:
        order = sorted(w.edges, key=lambda e: e["width"])
        for e in order:
            pts = [R.px(x, z) for x, z in polyline(e["curve"])]
            wpx = max(2, int(round(e["width"] / res)))
            elevated = e.get("elevation") and max(e["elevation"]) > 3
            d.line(pts, fill=(40, 40, 40) if elevated else (110, 110, 110), width=wpx + (4 if elevated else 2), joint="curve")
        for e in order:
            pts = [R.px(x, z) for x, z in polyline(e["curve"])]
            d.line(pts, fill=road_colour(e, colour_by), width=max(1, int(round(e["width"] / res))), joint="curve")

    if plan:
        for s in plan:
            c0, c1 = s["start"], s["end"]
            if s.get("controls"): ctl = s["controls"]
            elif s.get("control"):
                q = s["control"]; ctl = [[c0[0] + (q[0] - c0[0]) * 2 / 3, c0[1] + (q[1] - c0[1]) * 2 / 3], [c1[0] + (q[0] - c1[0]) * 2 / 3, c1[1] + (q[1] - c1[1]) * 2 / 3]]
            else: ctl = [[c0[0] + (c1[0] - c0[0]) / 3, c0[1] + (c1[1] - c0[1]) / 3], [c0[0] + (c1[0] - c0[0]) * 2 / 3, c0[1] + (c1[1] - c0[1]) * 2 / 3]]
            cv = [[c0[0], 0, c0[1]], [ctl[0][0], 0, ctl[0][1]], [ctl[1][0], 0, ctl[1][1]], [c1[0], 0, c1[1]]]
            pts = [R.px(x, z) for x, z in polyline(cv, 24)]
            for k in range(0, len(pts) - 1):
                if k % 2 == 0: d.line(pts[k:k + 2], fill=(0, 190, 230), width=3)
            for p in (pts[0], pts[-1]): d.ellipse([p[0] - 3, p[1] - 3, p[0] + 3, p[1] + 3], outline=(0, 150, 200), width=2)

    f_small, f_mid = font(11), font(13)
    if "issues" in layers and facts:
        rd = facts["roads"]
        def ring(pt, col, r=6):
            x, y = R.px(*pt); d.ellipse([x - r, y - r, x + r, y + r], outline=col, width=2)
        for it in rd["dead_ends"]["list"]: ring(it["pos"], (220, 30, 30))
        for it in rd["near_miss_connections"]["list"]: ring(it["pos"], (255, 0, 200), 9)
        for it in rd["sharp_junctions"]["list"]: ring(it["pos"], (255, 120, 0), 8)
        for it in rd["short_links_between_junctions"]["list"]: ring(it["mid"], (255, 200, 0), 5)
        for it in rd["overlapping_unconnected_nodes"]["list"]: ring(it["pos"], (0, 0, 0), 10)

    # districts: existing ones solid purple with their name, drafts (outlines) dashed purple
    for dist, dashed in [(x, False) for x in (districts or [])] + [(x, True) for x in (outlines or [])]:
        pts = [R.px(*q) for q in (dist.get("outline") or dist.get("points") or [])]
        if len(pts) < 3: continue
        ring_ = pts + [pts[0]]
        for k in range(len(ring_) - 1):
            a0, a1 = ring_[k], ring_[k + 1]
            if not dashed: d.line([a0, a1], fill=(120, 40, 170), width=3); continue
            n = max(1, int(math.dist(a0, a1) / 8))
            for j in range(0, n, 2):
                d.line([(a0[0] + (a1[0] - a0[0]) * j / n, a0[1] + (a1[1] - a0[1]) * j / n),
                        (a0[0] + (a1[0] - a0[0]) * min(n, j + 1) / n, a0[1] + (a1[1] - a0[1]) * min(n, j + 1) / n)], fill=(150, 60, 210), width=3)
        label = dist.get("name") or dist.get("label")
        if label:
            cx = sum(q[0] for q in pts) / len(pts); cy = sum(q[1] for q in pts) / len(pts)
            d.text((cx, cy), str(label), fill=(90, 20, 130), font=f_mid, anchor="mm", stroke_width=3, stroke_fill=(255, 255, 255))

    for m in marks or []:
        x, y = R.px(*m["pos"])
        d.ellipse([x - 5, y - 5, x + 5, y + 5], fill=(0, 120, 255), outline=(255, 255, 255))
        if m.get("label"): d.text((x + 7, y - 7), str(m["label"]), fill=(0, 40, 120), font=f_mid, stroke_width=2, stroke_fill=(255, 255, 255))

    if "grid" in layers:
        st = nice_step(a.size)
        gx0 = math.ceil(a.x0 / st) * st
        while gx0 <= a.x1:
            x, _ = R.px(gx0, 0); d.line([(x, 0), (x, R.H)], fill=(0, 0, 0, 60), width=1)
            d.text((x + 2, 2), f"x {int(gx0)}", fill=(20, 20, 20), font=f_small, stroke_width=2, stroke_fill=(255, 255, 255)); gx0 += st
        gz0 = math.ceil(a.z0 / st) * st
        while gz0 <= a.z1:
            _, y = R.px(0, gz0); d.line([(0, y), (R.W, y)], fill=(0, 0, 0, 60), width=1)
            d.text((2, y + 2), f"z {int(gz0)}", fill=(20, 20, 20), font=f_small, stroke_width=2, stroke_fill=(255, 255, 255)); gz0 += st
        # scale bar
        sb = nice_step(a.size, 5) if a.size > 300 else 50
        L = sb / res; y = R.H - 16
        d.rectangle([10, y - 4, 10 + L, y], fill=(20, 20, 20)); d.text((14 + L, y - 10), f"{sb} m  (+z up)", fill=(20, 20, 20), font=f_small, stroke_width=2, stroke_fill=(255, 255, 255))
    return img.convert("RGB")


def save(img, name):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name)
    img.save(path)
    return path
