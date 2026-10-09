"""v0.13 tools (2026-09-23 evening): zoning & city health, distilled from zoning a large new district.

- zone_report       zoning mix per district (families, styles, built / empty)
- building_warnings warning icons per district x building category, share of each category affected
- rezone            cell-exact: zone type A -> B only where A is (optionally only empty cells), from zone_blocks
- rezone_buildings  lots under selected buildings (icon / type / zone) -> residential etc., then bulldoze them
- zone_palette      zone streets in an outline by rules (road type, direction, near a point / road type), rotating styles
- zone_snapshot     save zoning + warnings per district to disk and diff with the previous snapshot (growth watch)
Generic: every city decision (which district, which style) comes from the caller.
"""
import json, math, os, re, time, collections
import survey, tools2, tools3

SNAPDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "snapshots")


# ----------------------------------------------------------------------------- helpers
def districts(rpc):
    A = rpc("areas", {"xmin": -1e5, "zmin": -1e5, "xmax": 1e5, "zmax": 1e5, "kinds": ["district"], "limit": 500})["areas"]
    names = {d["id"]: d["name"] for d in rpc("list_districts", {})["districts"]}
    return {names.get(a["id"], a["id"]): a["points"] for a in A}


def pick_areas(rpc, a):
    """[(label, polygon)] from a: districts (names), polygon, or box; default every district."""
    if a.get("polygon"): return [("polygon", a["polygon"])]
    if a.get("xmin") is not None:
        return [("box", [[a["xmin"], a["zmin"]], [a["xmax"], a["zmin"]], [a["xmax"], a["zmax"]], [a["xmin"], a["zmax"]]])]
    D = districts(rpc)
    want = a.get("districts")
    if want:
        missing = [n for n in want if n not in D]
        if missing: raise ValueError(f"unknown districts {missing}; have {sorted(D)}")
        return [(n, D[n]) for n in want]
    return sorted(D.items())


def bbox(polys, pad=0.0):
    xs = [p[0] for poly in polys for p in poly]; zs = [p[1] for poly in polys for p in poly]
    return {"xmin": min(xs) - pad, "zmin": min(zs) - pad, "xmax": max(xs) + pad, "zmax": max(zs) + pad}


def which(pt, areas):
    for label, poly in areas:
        if tools2._in_poly(pt, poly): return label
    return None


def category(t):
    t = t.lower()
    if "mixed" in t: return "mixed"
    if "commercial" in t or "hotel" in t: return "commercial"
    if "office" in t: return "office"
    if "industrial" in t or "warehouse" in t or "storage" in t: return "industrial"
    if "residential" in t or "row" in t: return "residential"
    return "service/other"


def lot_quad(b, shrink=2.0):
    c = (b["position"][0], b["position"][2]); f = b["forward"]; r = (f[1], -f[0])
    hw, hd = b["lot"][0] * 4 - shrink, b["lot"][1] * 4 - shrink
    return [[round(c[0] + r[0] * sw * hw + f[0] * sd * hd, 2), round(c[1] + r[1] * sw * hw + f[1] * sd * hd, 2)]
            for sw, sd in ((-1, -1), (1, -1), (1, 1), (-1, 1))]


# ----------------------------------------------------------------------------- reports
def zone_report(rpc, a):
    out = {}
    for label, poly in pick_areas(rpc, a):
        z = tools2.zone_summary(rpc, {"polygon": poly, "by_zone": True})
        top = a.get("styles_top", 8)
        out[label] = {"families": {k: f"{v['cells']} cells, {round(100 * v['built'] / max(1, v['cells']))}% built" for k, v in z["families"].items()},
                      "styles": {k: [v["cells"], v["empty"]] for k, v in list(z.get("zones", {}).items())[:top]},
                      "empty_zoned_cells": sum(v["empty"] for v in z["families"].values()), "unzoned_cells": z["unzoned"]["cells"]}
    return {"districts": out, "styles_legend": "[cells, empty cells]", "cell_m2": 64}


def building_warnings(rpc, a):
    areas = pick_areas(rpc, a)
    box = bbox([p for _, p in areas])
    ignore = set(a.get("ignore_icons", ["Leveling Building"]))
    st = rpc("building_status", dict(box, limit=100000))["buildings"]
    per = collections.defaultdict(lambda: {"buildings": collections.Counter(), "icons": collections.defaultdict(collections.Counter),
                                           "examples": collections.defaultdict(list)})
    no_road = collections.Counter()
    for b in st:
        d = which((b["position"][0], b["position"][1]) if len(b["position"]) == 2 else (b["position"][0], b["position"][2]), areas)
        if not d: continue
        cat = category(b["type"]); per[d]["buildings"][cat] += 1
        if not b.get("has_road_access", True): no_road[d] += 1
        for ic in b.get("icons", []):
            if ic in ignore: continue
            per[d]["icons"][ic][cat] += 1
            if len(per[d]["examples"][ic]) < a.get("examples", 3) and b["id"] not in per[d]["examples"][ic]: per[d]["examples"][ic].append(b["id"])
    out = {}
    for d, v in per.items():
        icons = {}
        for ic, cats in v["icons"].items():
            icons[ic] = {c: f"{n} of {v['buildings'][c]} ({round(100 * n / max(1, v['buildings'][c]))}%)" for c, n in cats.most_common()}
        if icons or no_road[d]:
            out[d] = {"icons": icons, "without_road_access": no_road[d], "examples": dict(v["examples"]), "buildings": dict(v["buildings"])}
    tot = collections.Counter()
    for v in per.values():
        for ic, cats in v["icons"].items(): tot[ic] += sum(cats.values())
    return {"totals": dict(tot.most_common()), "districts": out, "ignored_icons": sorted(ignore)}


# ----------------------------------------------------------------------------- rezoning
def _runs(block, cells_ok):
    """contiguous runs of accepted cells along x within each row y -> [(y, x0, x1)]"""
    sx, sy = block["size"]; out = []
    for y in range(sy):
        x = 0
        while x < sx:
            if cells_ok(x, y):
                x0 = x
                while x + 1 < sx and cells_ok(x + 1, y): x += 1
                out.append((y, x0, x))
            x += 1
    return out


def _run_quad(block, y, x0, x1, inset=0.8):
    p = block["pos"]; d = block["dir"]; r = (d[1], -d[0]); sx, sy = block["size"]
    def centre(x): return (p[0] + d[0] * 4 * (sy - 2 * y - 1) + r[0] * 4 * (sx - 2 * x - 1),
                           p[2] + d[1] * 4 * (sy - 2 * y - 1) + r[1] * 4 * (sx - 2 * x - 1))
    a, b = centre(x0), centre(x1)
    ux = ((b[0] - a[0]) / (math.dist(a, b) or 1), (b[1] - a[1]) / (math.dist(a, b) or 1)) if x1 > x0 else (-r[0], -r[1])
    h = 4 - inset; e0 = (a[0] - ux[0] * h, a[1] - ux[1] * h); e1 = (b[0] + ux[0] * h, b[1] + ux[1] * h)
    return [[round(e0[0] - d[0] * h, 2), round(e0[1] - d[1] * h, 2)], [round(e1[0] - d[0] * h, 2), round(e1[1] - d[1] * h, 2)],
            [round(e1[0] + d[0] * h, 2), round(e1[1] + d[1] * h, 2)], [round(e0[0] + d[0] * h, 2), round(e0[1] + d[1] * h, 2)]]


def rezone(rpc, a):
    """Change zone style `from` (name or list) to `to` (name, or 'none') only on cells that currently have it; only_empty
    (default true) leaves cells with buildings alone, so nothing standing is disturbed. Where: districts / polygon / box."""
    areas = pick_areas(rpc, a)
    frm = a["from"] if isinstance(a["from"], list) else [a["from"]]
    zb = rpc("zone_blocks", dict(bbox([p for _, p in areas]), limit=100000))
    inv = {v: k for k, v in zb.get("legend", {}).items()}
    codes = {inv[f] for f in frm if f in inv}
    if not codes: return {"cells": 0, "note": f"none of {frm} is zoned in this area"}
    only_empty = a.get("only_empty", True)
    quads, cells = [], 0
    for b in zb["blocks"]:
        sx = b["size"][0]
        def ok(x, y, b=b):
            i = y * sx + x
            if i >= len(b["cells"]) or b["cells"][i] not in codes: return False
            if only_empty and b["built"][i] == "1": return False
            p = b["pos"]; d = b["dir"]; r = (d[1], -d[0]); sy = b["size"][1]
            c = (p[0] + d[0] * 4 * (sy - 2 * y - 1) + r[0] * 4 * (sx - 2 * x - 1), p[2] + d[1] * 4 * (sy - 2 * y - 1) + r[1] * 4 * (sx - 2 * x - 1))
            return which(c, areas) is not None
        for y, x0, x1 in _runs(b, ok):
            quads.append(_run_quad(b, y, x0, x1)); cells += x1 - x0 + 1
    out = {"cells": cells, "hectares": round(cells * 64 / 1e4, 2), "strips": len(quads), "from": frm, "to": a["to"], "only_empty": only_empty}
    if a.get("dry_run"): return out
    tools2.zone_many(rpc, [(q, a["to"]) for q in quads])
    out["applied"] = True
    return out


def _zone_for(b, a):
    """target zone for one building: `to` (fixed) or `map` {regex on building type or zone: zone}"""
    if a.get("to"): return a["to"]
    for pat, z in (a.get("map") or {}).items():
        if re.search(pat, b["type"]) or re.search(pat, b.get("zone", "")): return z
    return None


def rezone_buildings(rpc, a):
    """Select buildings (districts / polygon / box; icon, type_regex, zone_regex, category) -> rezone each lot to `to`
    or per `map`, then bulldoze them (default) so the new zone actually grows. Signature buildings are skipped."""
    areas = pick_areas(rpc, a)
    box = bbox([p for _, p in areas])
    st = rpc("building_status", dict(box, limit=100000))["buildings"]
    geo = {b["id"]: b for b in rpc("query_buildings", dict(box, limit=100000))["buildings"]}
    sel, skipped = [], collections.Counter()
    for s in st:
        g = geo.get(s["id"])
        if not g or not which((g["position"][0], g["position"][2]), areas): continue
        if a.get("icon") and a["icon"] not in s.get("icons", []): continue
        if a.get("type_regex") and not re.search(a["type_regex"], s["type"]): continue
        if a.get("zone_regex") and not re.search(a["zone_regex"], s.get("zone", "")): continue
        if a.get("category") and category(s["type"]) != a["category"]: continue
        if "Signature" in s["type"] and not a.get("include_signature"): skipped["signature"] += 1; continue
        z = _zone_for(dict(g, zone=s.get("zone", "")), a)
        if not z: skipped["no target zone"] += 1; continue
        sel.append((g, z))
    out = {"buildings": len(sel), "by_target": dict(collections.Counter(z for _, z in sel)), "skipped": dict(skipped),
           "bulldoze": a.get("bulldoze", True)}
    if a.get("dry_run"): out["examples"] = [[g["id"], g["type"], z] for g, z in sel[:10]]; return out
    tools2.zone_many(rpc, [(lot_quad(g), z) for g, z in sel])
    if a.get("bulldoze", True) and sel:
        r = rpc("bulldoze", {"ids": [g["id"] for g, _ in sel], "buildings": True, "roads": False, "timeout_s": 120})
        out["bulldozed"] = r.get("buildings")
    out["applied"] = True
    return out


# ----------------------------------------------------------------------------- palette zoning
def _rule_match(rule, e, ctx):
    m = rule.get("match", {})
    t = e["type"]
    if m.get("types") and not any(k.lower() in t.lower() for k in m["types"]): return False
    if m.get("direction") and m["direction"] != ctx["direction"]: return False
    if m.get("near_point") and math.dist(ctx["mid"], tuple(m["near_point"])) > m.get("within_m", 150): return False
    if m.get("near_types") and ctx["dist_to"](m["near_types"]) > m.get("within_m", 100): return False
    return True


def zone_palette(rpc, a):
    """Zone the streets inside `polygon` (world, or frame) by the first matching rule each: rule = {match: {types,
    direction ('along'|'across' the frame heading), near_point + within_m, near_types + within_m}, zone | zones +
    rotate ('street' = per street across the frame, every street_m; 'along' = per alternate_m along it)}. Zoned in
    `order` (earlier styles claim corners). overwrite false by default: only empty cells. plan_only lists road counts + frontage."""
    fr = tools2.framed(a)
    poly = tools2.pts_world(a["polygon"], fr) if (fr and a.get("polygon_in_frame")) else a["polygon"]   # frame = direction only unless polygon_in_frame
    box = bbox([poly], 60)
    net = rpc("road_network", dict(box, limit=40000))["edges"]
    skip = ("Highway", "Pipe", "voltage", "Track", "Train", "Cable", "Seaway", "Invisible")
    lines = {e["id"]: survey.polyline(e["curve"], 6) for e in net}
    def dist_to_types(pl, types):
        best = 1e9
        for e in net:
            if not any(k.lower() in e["type"].lower() for k in types): continue
            for p in pl[::2]:
                for q in lines[e["id"]][::2]: best = min(best, math.dist(p, q))
        return best
    heading = math.radians((a.get("frame") or {}).get("heading_deg", 0.0))
    U = (math.cos(heading), math.sin(heading)); V = (-U[1], U[0])
    groups = collections.defaultdict(list)
    for e in net:
        if any(k in e["type"] for k in skip): continue
        pl = lines[e["id"]]; mid = pl[len(pl) // 2]
        if not tools2._in_poly(mid, poly): continue
        p0, p1 = pl[0], pl[-1]; da = (p1[0] - p0[0]) * U[0] + (p1[1] - p0[1]) * U[1]; db = (p1[0] - p0[0]) * V[0] + (p1[1] - p0[1]) * V[1]
        ctx = {"direction": "along" if abs(da) >= abs(db) else "across", "mid": mid, "dist_to": lambda types, pl=pl: dist_to_types(pl, types)}
        for rule in a["rules"]:
            if not _rule_match(rule, e, ctx): continue
            zs = rule.get("zones") or [rule["zone"]]
            if len(zs) > 1:
                if rule.get("rotate", "street") == "street":
                    k = round((mid[0] * (V if ctx["direction"] == "along" else U)[0] + mid[1] * (V if ctx["direction"] == "along" else U)[1]) / rule.get("street_m", 90))
                else:
                    k = round((mid[0] * U[0] + mid[1] * U[1]) / rule.get("alternate_m", 120))
                z = zs[k % len(zs)]
            else: z = zs[0]
            groups[z].append(e["id"]); break
    plen = lambda pl: sum(math.dist(pl[i], pl[i + 1]) for i in range(len(pl) - 1))
    out = {"roads_by_zone": {k: f"{len(v)} roads, {sum(plen(lines[i]) for i in v):.0f} m frontage" for k, v in groups.items()}}
    if a.get("plan_only") or a.get("dry_run"): return out
    order = a.get("order") or list(groups)
    for z in order + [z for z in groups if z not in order]:
        if not groups.get(z): continue
        r = tools3.zone_along(rpc, dict(box, ids=groups[z], zone_type=z, overwrite=a.get("overwrite", False), depth_m=a.get("depth_m", 48.0), follow_camera=False))
        out.setdefault("zoned", {})[z] = f"{r['zoned']}/{r['quads']} strips"
    return out


# ----------------------------------------------------------------------------- growth watch
def zone_snapshot(rpc, a):
    """Save zoning (built / empty per style) + warning totals per district under `label`, and diff with the previous
    snapshot: cells built since, zoned cells still empty, warnings up or down."""
    os.makedirs(SNAPDIR, exist_ok=True)
    rep = {}
    for label, poly in pick_areas(rpc, a):
        z = tools2.zone_summary(rpc, {"polygon": poly, "by_zone": True})
        rep[label] = {k: [v["built"], v["empty"]] for k, v in z.get("zones", {}).items()}
    warn = building_warnings(rpc, a)["totals"]
    snap = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "label": a.get("label", ""), "zones": rep, "warnings": warn}
    files = sorted(f for f in os.listdir(SNAPDIR) if f.endswith(".json"))
    prev = json.load(open(os.path.join(SNAPDIR, files[-1]))) if files else None
    with open(os.path.join(SNAPDIR, time.strftime("%Y%m%d-%H%M%S") + ".json"), "w") as fh: json.dump(snap, fh)
    out = {"saved": snap["time"], "warnings": warn}
    if prev:
        diff, stalled, new_d = {}, {}, []
        for d, zs in rep.items():
            if d not in prev["zones"]:
                new_d.append(d); continue               # no baseline for this district - nothing to diff against
            for z, (built, empty) in zs.items():
                pb, pe = (prev["zones"][d].get(z) or [0, 0])
                if built != pb or empty != pe:
                    diff.setdefault(d, {})[z] = {"built_since": built - pb, "empty_change": empty - pe, "still_empty": empty}
                elif empty:
                    stalled.setdefault(d, {})[z] = empty    # zoned but nothing grew since the last snapshot
        out["since"] = prev["time"]; out["changes"] = diff; out["stalled_empty_cells"] = stalled
        if new_d: out["not_in_previous"] = new_d
        out["warnings_change"] = {k: warn.get(k, 0) - prev["warnings"].get(k, 0) for k in set(warn) | set(prev["warnings"])}
    return out
