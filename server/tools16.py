"""v0.46 tools (2026-10-16): a whole new district in one call, many calls in one, fast mode.

- build_district    outline -> street grid (cs2_district_grid's planner, snapped onto the zone lattice, joined to the
                    streets around it) -> zero-crack flags -> service buildings spread along the new streets -> zoning
                    of the new frontage by role (arterial / local street, only empty cells) -> game district + policies.
                    The simulation is paused for the whole build (nothing grows into the way of the next step), the
                    camera stays put, and one compact report comes back instead of dozens of tool round trips.
                    preview=true only drafts (map + zoning forecast + plan_id); build_district(plan_id=...) then builds
                    exactly that draft with the settings it was drafted with.
- remove_district   takes such a district back out: an exact undo of its own history entries while they are still
                    the newest, otherwise by shape (its roads - their zoning goes with them -, its service buildings,
                    its district outline).
- batch             many tool calls in one MCP call: the loop runs in the server, not one model round trip per step.
Presets only pick road types, block length and zone families; the caller can override every one of them.
"""
import json, math, os, re, time, collections
import survey, tools2, tools3, tools5, tools8

PRESETS = {
    "suburb": {"local_type": "Small Road", "arterial_type": "Medium Road", "arterial_every": 4, "block_length_cells": 22,
               "zones": {"arterial": ["commercial low"], "local": ["residential low"]}},
    "residential": {"local_type": "Small Road", "arterial_type": "Medium Road", "arterial_every": 4, "block_length_cells": 22,
                    "zones": {"arterial": ["residential medium", "mixed"], "local": ["residential row"]}},
    "downtown": {"local_type": "Medium Road", "arterial_type": "Large Road", "arterial_every": 3, "block_length_cells": 16,
                 "zones": {"arterial": ["commercial high", "mixed"], "local": ["residential high", "office high"]}},
    "office": {"local_type": "Medium Road", "arterial_type": "Large Road", "arterial_every": 3, "block_length_cells": 16,
               "zones": {"arterial": ["commercial high"], "local": ["office high", "office low"]}},
    "industrial": {"local_type": "Medium Road", "arterial_type": "Large Road", "arterial_every": 3, "block_length_cells": 28,
                   "zones": {"arterial": ["industrial"], "local": ["industrial"]}},
}
ROAD_FALLBACK = {"Large Road": ["Medium Road"], "Medium Road": ["Small Road"], "Small Road": ["Medium Road"]}

# zone family -> (words a zone type name must contain, words it must not contain, preferred word)
FAMILY = {
    "residential low": (("resid", "low"), ("rent", "mixed", "row", "medium", "high"), None),
    "residential medium": (("resid", "medium"), ("row", "rent", "mixed"), None),
    "residential row": (("resid", "row"), ("rent",), None),
    "residential high": (("resid", "high"), ("rent", "mixed"), None),
    "residential lowrent": (("rent",), (), None),
    "mixed": (("mixed",), (), None),
    "commercial low": (("commercial", "low"), (), None),
    "commercial high": (("commercial", "high"), (), None),
    "office low": (("office", "low"), (), None),
    "office high": (("office", "high"), (), None),
    "office": (("office",), (), None),
    "industrial": (("industr",), (), "manufact"),
}
FALLBACK = {"residential row": "residential medium", "residential medium": "residential low",
            "residential high": "residential medium", "residential lowrent": "residential low", "mixed": "commercial low",
            "commercial high": "commercial low", "office high": "office", "office low": "office"}
OPT_KEYS = ("name", "policies", "services", "theme", "district", "align", "check", "pause", "show", "map")


# ----------------------------------------------------------------------------- zone types
def theme_of(name):
    """Region tag of a zone type name ('NA Residential Low' -> 'NA'; a trailing tag works too), None for plain names."""
    w = name.split()
    for t in (w[:1] + w[-1:]) if len(w) > 1 else []:
        if t.isupper() and t.isalpha() and 2 <= len(t) <= 5: return t
    return None


def _has(name, w):
    """word w starts or ends one of the name's words ('resid' in 'NA Residential Low', 'rent' in 'Residential LowRent')"""
    return any(t.startswith(w) or t.endswith(w) for t in re.split(r"[^a-z0-9]+", name.lower()) if t)


def resolve_zone(spec, names, theme=None):
    """A zone type name, or a family ('residential low', 'commercial high', 'office', 'industrial'...) or a fragment of a
    name ('CHI Row'), -> one of this game's zone types: the theme's variant first, then a plain one, then any.
    A family the game lacks falls back along FALLBACK (row -> medium -> low...). None if nothing fits."""
    if spec in names: return spec
    def rank(n):
        t = theme_of(n)
        return (0 if theme and t and t.lower() == theme.lower() else 1 if t is None else 2, n.lower().count(" "), n)
    key, seen = spec.lower().strip(), set()
    while key and key not in seen:
        seen.add(key)
        if key in FAMILY:
            need, avoid, pref = FAMILY[key]
            c = [n for n in names if all(_has(n, w) for w in need) and not any(_has(n, w) for w in avoid)]
            if pref and any(pref in n.lower() for n in c): c = [n for n in c if pref in n.lower()]
        else:
            c = [n for n in names if key in n.lower()]
        if c: return sorted(c, key=rank)[0]
        key = FALLBACK.get(key)
    return None


def city_theme(rpc, poly, pad=800.0):
    """The region prefix most of the zoned cells around the outline use (None if nothing nearby is zoned)."""
    try:
        zb = rpc("zone_blocks", dict(tools5.bbox([poly], pad), limit=6000))
    except Exception:
        return None
    legend, count = zb.get("legend", {}), collections.Counter()
    for b in zb.get("blocks", []):
        survey.normalise_cells(b)
        for ch in b["zone"]:
            if ch not in "#.?" and ch in legend:
                t = theme_of(legend[ch])
                if t: count[t] += 1
    return count.most_common(1)[0][0] if count else None


def zone_plan(rpc, zones, poly, theme=None):
    """{role: [families / names]} -> ({role: [zone type names]}, theme used, specs nothing matched)."""
    names = rpc("list_zone_types", {})["zone_types"]
    theme = theme or city_theme(rpc, poly)
    out, missing = {}, []
    for role, specs in zones.items():
        got = []
        for sp in specs:
            z = resolve_zone(sp, names, theme)
            if z is None: missing.append(sp)
            elif z not in got: got.append(z)
        if got: out[role] = got
    return out, theme, missing


# ----------------------------------------------------------------------------- settings
def _aslist(v): return [v] if isinstance(v, str) else list(v or [])


def zones_arg(z, preset):
    """zoning arg -> {role: [specs]}: None / true / 'preset' = the preset's, false / 'none' = no zoning, a name or family
    or list = every street, {arterial, local} (each a name / family / list) = per role over the preset."""
    if z is None or z is True or z == "preset": return {k: list(v) for k, v in preset.items()}
    if z is False or z == "none": return {}
    if isinstance(z, (str, list)): z = {"all": z}
    if not isinstance(z, dict): raise ValueError("zoning: false, 'preset', a zone name / family, a list, or {arterial, local}")
    out = {k: list(v) for k, v in preset.items()}
    if "all" in z: out = {"arterial": _aslist(z["all"]), "local": _aslist(z["all"])}
    for role in ("arterial", "local"):
        if role in z: out[role] = _aslist(z[role])
    return {k: v for k, v in out.items() if v}


def spec_of(a):
    """The preset (default 'residential') with the caller's overrides."""
    name = a.get("preset") or "residential"
    if name not in PRESETS: raise ValueError(f"unknown preset '{name}' - one of {sorted(PRESETS)}")
    s = {k: v for k, v in PRESETS[name].items() if k != "zones"}
    s["preset"] = name; s["zoning_depth_cells"] = 6
    for k in ("local_type", "arterial_type", "arterial_every", "block_length_cells", "zoning_depth_cells"):
        if a.get(k) is not None: s[k] = a[k]
    if s.get("arterial_type") in ("none", "", False): s["arterial_type"] = None
    s["zones"] = zones_arg(a.get("zoning"), PRESETS[name]["zones"])
    return s


def check_road_types(s, widths, explicit):
    """Preset road types this game lacks fall back (Large -> Medium -> Small); a type the caller named must exist."""
    notes = []
    for key in ("local_type", "arterial_type"):
        t = s.get(key)
        if not t or t in widths: continue
        if key in explicit: raise ValueError(f"unknown road type '{t}' (see cs2_list_road_types)")
        alt = next((x for x in ROAD_FALLBACK.get(t, []) if x in widths), None)
        if alt is None and key == "arterial_type":
            s[key] = None; notes.append(f"no '{t}' in this game: no arterials"); continue
        if alt is None: raise ValueError(f"no '{t}' in this game: give local_type (see cs2_list_road_types)")
        s[key] = alt; notes.append(f"no '{t}' in this game: using '{alt}'")
    if s.get("arterial_type") == s.get("local_type"): s["arterial_type"] = None
    return notes


def outline(a, api):
    """polygon, a box, or center + size (+ size_z, angle_deg); default an 800 m square around the camera's ground point."""
    if a.get("polygon"):
        poly = [[float(p[0]), float(p[1])] for p in a["polygon"]]
        if len(poly) < 3: raise ValueError("polygon needs 3+ points")
        return poly
    if all(a.get(k) is not None for k in ("xmin", "zmin", "xmax", "zmax")):
        return [[a["xmin"], a["zmin"]], [a["xmax"], a["zmin"]], [a["xmax"], a["zmax"]], [a["xmin"], a["zmax"]]]
    c = a.get("center") or api["camera_ground"]()
    w = float(a.get("size", 800)); h = float(a.get("size_z", w))
    return api["rect_corners"]([float(c[0]), float(c[1])], w, h, float(a.get("angle_deg", 0.0)))


def poly_angle(poly):
    """Direction of the outline's longest edge (mod 90): the grid angle when there are no streets to line up with."""
    p, q = max(zip(poly, poly[1:] + poly[:1]), key=lambda pq: math.dist(*pq))
    return round(math.degrees(math.atan2(q[1] - p[1], q[0] - p[0])) % 90, 2)


def centroid(poly): return [sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly)]


# ----------------------------------------------------------------------------- plan files
def _path(api, pid): return os.path.join(api["plan_dir"], pid + ".json")


def save_plan(api, pid, plan):
    os.makedirs(api["plan_dir"], exist_ok=True)
    with open(_path(api, pid), "w") as fh: json.dump(plan, fh)


def new_pid(api):
    """district_<date>_<time>, with a suffix when two builds land in the same second (a plan file is never overwritten)."""
    base, k = time.strftime("district_%Y%m%d_%H%M%S"), 1
    pid = base
    while os.path.exists(_path(api, pid)): k += 1; pid = f"{base}_{k}"
    return pid


def load_plan(api, pid):
    if pid in (None, "", "last"):
        files = sorted((f for f in os.listdir(api["plan_dir"]) if f.startswith("district_") and f.endswith(".json")), reverse=True) \
            if os.path.isdir(api["plan_dir"]) else []
        for f in files:
            with open(os.path.join(api["plan_dir"], f)) as fh: p = json.load(fh)
            if p.get("district_build") and not p["district_build"].get("removed"): return f[:-5], p
        raise ValueError("no district built by cs2_build_district to take back (give plan_id)")
    if not os.path.exists(_path(api, pid)): raise ValueError(f"no saved plan '{pid}' in {api['plan_dir']}")
    with open(_path(api, pid)) as fh: return pid, json.load(fh)


# ----------------------------------------------------------------------------- the new streets
def edges_on(edges, segs, tol=2.5):
    """Existing edges lying on the planned segments (by shape, so pieces the game split are found), bucketed."""
    import pave
    B, buckets = 400.0, {}
    for s in segs:
        xs = [s["start"][0], s["end"][0]] + [c[0] for c in s.get("controls", [])]
        zs = [s["start"][1], s["end"][1]] + [c[1] for c in s.get("controls", [])]
        for bx in range(math.floor(min(xs) / B), math.floor(max(xs) / B) + 1):
            for bz in range(math.floor(min(zs) / B), math.floor(max(zs) / B) + 1):
                buckets.setdefault((bx, bz), []).append(s)
    out = []
    for e in edges:
        if "flow" not in e: continue
        mx, mz = survey.bez(e["curve"], 0.5)
        cand = buckets.get((math.floor(mx / B), math.floor(mz / B)), [])
        if cand and pave.on_plan(e, cand, tol): out.append(e)
    return out


def street_key(e, lat, pitch):
    """Which street of the lattice an edge belongs to (consecutive parallel streets get consecutive numbers), so a list
    of zone styles rotates street by street."""
    an = math.radians(lat.get("angle_deg", 0.0)); U = (math.cos(an), math.sin(an)); V = (-U[1], U[0])
    o = lat.get("origin") or [0.0, 0.0]
    c = e["curve"]; d = (c[3][0] - c[0][0], c[3][2] - c[0][2]); m = survey.bez(c, 0.5)
    rel = (m[0] - o[0], m[1] - o[1])
    if abs(d[0] * U[0] + d[1] * U[1]) >= abs(d[0] * V[0] + d[1] * V[1]):       # runs along u: a line of constant v
        return round((rel[0] * V[0] + rel[1] * V[1] - lat.get("v_phase", 0.0)) / (pitch[1] or 1))
    return round((rel[0] * U[0] + rel[1] * U[1] - lat.get("u_phase", 0.0)) / (pitch[0] or 1))


def zone_new_streets(rpc, segs, s, zones, area, lat, pitch):
    """Zone the frontage of the streets this build made: arterials with zones['arterial'], the rest with zones['local'];
    a list rotates street by street. Arterial styles zone first (they claim the corners). Only empty cells."""
    net = rpc("road_network", dict(area.params, limit=40000))["edges"]
    mine = edges_on(net, segs)
    groups, order = collections.OrderedDict(), []
    for role in ("arterial", "local"):
        for z in zones.get(role, []):
            if z not in order: order.append(z)
    for e in mine:
        role = "arterial" if s.get("arterial_type") and e["type"] == s["arterial_type"] else "local"
        zs = zones.get(role) or (zones.get("local") if role == "arterial" else None)    # arterials without a style: the local one
        if not zs: continue
        groups.setdefault(zs[street_key(e, lat, pitch) % len(zs)], []).append(e["id"])
    out = {"new_edges": len(mine)}
    for z in order:
        if not groups.get(z): continue
        r = tools3.zone_along(rpc, dict(area.params, ids=groups[z], zone_type=z, overwrite=False,
                                        depth_m=float(s.get("zoning_depth_cells", 6)) * 8, follow_camera=False))
        out[z] = {"roads": r["roads"], "strips": r["quads"], "refused": r["refused"]}
    return out


# ----------------------------------------------------------------------------- services
def place_services(rpc, specs, poly):
    """Each spec {type, count (1), min_gap_m (300), lot_m?, allow_subnets?}: free lots of that size along the new streets,
    the first near the outline's centre (or far from existing ones of that type), the rest spread out; each is built
    for real and a refused lot falls through to the next best. Before zoning, so growables can't take the lots."""
    out, box, areas = [], tools5.bbox([poly], 0), [("district", poly)]
    cx, cz = centroid(poly)
    for sp in specs:
        if isinstance(sp, str): sp = {"type": sp}
        typ, n, gap = sp["type"], max(1, int(sp.get("count", 1))), float(sp.get("min_gap_m", 300))
        row = {"type": typ, "wanted": n, "placed": []}
        try:
            W, D = sp.get("lot_m") or tools8._lot(rpc, typ)
            cands, _ = tools8.lot_candidates(rpc, {"grid_m": 4, "step_m": 16}, areas, box, W, D)
            big = tools5.bbox([poly], 1500)
            have = [(b["position"][0], b["position"][2]) for b in rpc("query_buildings", dict(big, limit=20000))["buildings"] if b["type"] == typ]
        except Exception as e:
            row["error"] = str(e)[:200]; out.append(row); continue
        tried, why = set(), []
        while len(row["placed"]) < n and len(tried) < n * 8:
            others = have + [(p["x"], p["z"]) for p in row["placed"]]
            best, score = None, None
            for i, k in enumerate(cands):
                if i in tried: continue
                p = (k["x"], k["z"])
                if any(math.dist(p, q) < gap for q in others): continue
                sc = min(math.dist(p, q) for q in others) if others else -math.dist(p, (cx, cz))
                if best is None or sc > score: best, score = i, sc
            if best is None: break
            tried.add(best); k = cands[best]
            try:
                r = rpc("place_building", {"type": typ, "x": k["x"], "z": k["z"], "facing": k["facing"], "timeout_s": 90,
                                           "allow_subnets": bool(sp.get("allow_subnets"))})
            except Exception as e:
                if "built-in nets" in str(e):
                    row["error"] = f"{typ} has built-in nets: pass allow_subnets=true for it (save first)"; break
                why.append(str(e)[:120]); continue
            if r.get("errors") or not r.get("building_id"):
                why.append(r.get("error_reasons") or r.get("reason")); continue
            row["placed"].append({"id": r["building_id"], "x": k["x"], "z": k["z"]})
        if not cands and "error" not in row: row["note"] = "no free lot of that size along the new streets"
        if why: row["refused_lots"] = len(why); row["why"] = why[:3]
        out.append(row)
    return out


# ----------------------------------------------------------------------------- district, check
def make_district(rpc, poly, name=None, policies=None):
    q = {"polygon": poly, "timeout_s": 90}
    if name: q["name"] = name
    if policies: q["policies"] = list(policies)
    try:
        r = rpc("create_district", q)
    except Exception as e:
        return {"created": False, "error": str(e)[:200]}
    if not r.get("applied"):
        return {"created": False, "why": r.get("error_reasons") or r.get("reason"), "hint": "the outline probably overlaps another district"}
    out = {"created": True, "id": r.get("district_id"), "name": r.get("name")}
    if r.get("policies"): out["policies"] = r["policies"]
    return out


def check_outline(rpc, area, poly, refused=()):
    """Dead ends and islands among the roads inside the outline, and how much of it is zoned. Dead ends where a refused
    street would have joined are the ones worth fixing; the others are stubs at the outline (room to grow)."""
    w = survey.World(rpc, area)
    inside = lambda p: tools2._in_poly(p, poly)
    dead = [[round(n["pos"][0], 1), round(n["pos"][2], 1)] for n in w.nodes.values()
            if n.get("degree") == 1 and not n.get("owned_arms") and not n.get("outside_connection") and inside((n["pos"][0], n["pos"][2]))]
    parent = {}
    def find(x):
        while parent.setdefault(x, x) != x: parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for e in w.edges: parent[find(e["start_node"])] = find(e["end_node"])
    comps = collections.defaultdict(list)
    for e in w.edges: comps[find(e["start_node"])].append(e)
    islands = []
    for es in comps.values():
        ns = {e["start_node"] for e in es} | {e["end_node"] for e in es}
        pts = [(w.nodes[x]["pos"][0], w.nodes[x]["pos"][2]) for x in ns if x in w.nodes]
        if pts and all(inside(p) for p in pts) and not any(w.nodes.get(x, {}).get("outside_connection") for x in ns):
            islands.append({"edges": len(es), "at": [round(pts[0][0], 1), round(pts[0][1], 1)]})
    zoned = empty = 0
    for b in w.blocks:
        for _x, _y, ch, _built, c, _ in survey.cell_geometry(b):
            if ch == "#" or not inside(c): continue
            if ch == ".": empty += 1
            else: zoned += 1
    left = [d for d in dead if any(math.dist(d, s["start"]) < 4 or math.dist(d, s["end"]) < 4 for s in refused)]
    out = {"dead_ends": len(dead), "dead_ends_from_refused": len(left), "islands": len(islands), "zoned_cells": zoned,
           "unzoned_cells": empty}
    if left: out["fix_these"] = left[:5]
    if islands: out["island_examples"] = islands[:3]
    return out


# ----------------------------------------------------------------------------- build
def build_district(rpc, a, api):
    import pave
    t0 = time.time()
    if a.get("plan_id"):                                          # build a draft made with preview=true
        pid, plan = load_plan(api, a["plan_id"])
        if plan.get("district_build"): raise ValueError(f"{pid} is already built - cs2_remove_district it first, or draft again")
        ds = plan.get("district_spec") or {}
        if not ds: raise ValueError(f"{pid} is not a cs2_build_district draft (use cs2_build_plan for district_grid plans)")
        s, poly, lat, pitch, notes = ds["spec"], ds["polygon"], ds["lattice"], ds["pitch_m"], list(ds.get("notes", []))
        if "zoning" in a: s["zones"] = zones_arg(a["zoning"], s["zones"])
        opts = dict(ds.get("opts", {}), **{k: a[k] for k in OPT_KEYS if k in a})
        area = survey.Area(*plan["area"])
    else:
        s = spec_of(a)
        notes = check_road_types(s, api["widths"](), [k for k in ("local_type", "arterial_type") if a.get(k)])
        poly = outline(a, api)
        g = {"polygon": poly, "local_type": s["local_type"], "arterial_type": s.get("arterial_type"), "arterial_every": s["arterial_every"],
             "block_length_cells": s["block_length_cells"], "zoning_depth_cells": s["zoning_depth_cells"],
             "max_grade": a.get("max_grade", 0.15), "connected_only": a.get("connected_only", True),
             "connect_beyond_m": a.get("connect_beyond_m", 250.0)}
        for k in ("angle_deg", "origin", "u_phase", "v_phase", "match_radius", "seed", "pitch_u", "pitch_v"):
            if a.get(k) is not None: g[k] = a[k]
        try:
            plan, area, extra = api["grid_plan"](g)
        except ValueError as e:
            if "lattice" not in str(e) or "angle_deg" in g: raise
            g["angle_deg"] = poly_angle(poly)                         # no streets nearby to line up with
            notes.append(f"no straight streets nearby to line up with: grid angle {g['angle_deg']} deg from the outline")
            plan, area, extra = api["grid_plan"](g)
        lat, pitch = extra["lattice"], extra["pitch_m"]
        opts = {k: a[k] for k in OPT_KEYS if k in a}
        pid = new_pid(api)
        plan["area"] = [area.x0, area.z0, area.x1, area.z1]
        plan["district_spec"] = {"spec": s, "polygon": poly, "lattice": lat, "pitch_m": pitch, "opts": opts, "notes": notes}
    grid = [x for x in plan["segments"] if x.get("phase") == "grid"]
    head = {"plan_id": pid, "preset": s.get("preset"), "roads": {"local": s["local_type"], "arterial": s.get("arterial_type")},
            "stats": plan["stats"]}
    if notes: head["notes"] = notes
    if not grid:
        st = plan["stats"]
        why = ("the outline reaches no existing street within a block or two - move it next to a street, or pass "
               "connected_only=false and join it up yourself") if st.get("dropped_unconnected") else \
              "no buildable land in the outline (water, steep ground, existing zoning or buildings)"
        return dict(head, built=False, reason=why)
    zones, theme, missing = zone_plan(rpc, s["zones"], poly, opts.get("theme")) if s["zones"] else ({}, None, [])
    head["zones"] = zones
    if theme: head["theme"] = theme
    if missing: head["no_zone_type_for"] = missing
    if a.get("preview"): return preview(rpc, a, api, pid, plan, area, poly, head, opts)

    ping = rpc("ping", {})
    hist = rpc("history", {}).get("entries_newest_first") or []
    h0 = hist[0]["id"] if hist else 0
    try: ci = rpc("city_info", {})
    except Exception: ci = {}
    speed0 = None
    if opts.get("pause", True) and ci.get("speed") is not None:
        try:
            rpc("set_speed", {"speed": 0}); speed0 = ci["speed"]
        except Exception:
            speed0 = None
    rep, journal = dict(head), {"services": [], "district": None}
    try:
        if not plan.get("snapped"):                               # snap once and keep the snapped shapes: what is built
            snapped, rep_lat = api["lattice"](grid, {"lattice": a.get("lattice", "snap")})
            it = iter(snapped)
            plan["segments"] = [next(it) if x.get("phase") == "grid" else x for x in plan["segments"]]
            plan["snapped"] = True
            if rep_lat: rep["lattice"] = {k: rep_lat.get(k) for k in ("lines_moved", "max_move_m", "mode")}
        save_plan(api, pid, plan)
        # one preview of the whole plan first: no money = stop here, instead of bisecting every refused street
        pre = rpc("place_roads", {"segments": [pave.clean(x) for x in plan["segments"]][:400], "snap_tolerance": 1.5,
                                  "dry_run": True, "timeout_s": 120})
        if pre.get("errors"):
            reasons = pre.get("error_reasons") or {}
            rep["precheck"] = {"errors": pre["errors"], "reasons": reasons}
            if any("money" in str(k).lower() for k in reasons):
                rep["built"] = False; rep["reason"] = "not enough money for these streets (the game's preview says so)"
                return rep
        res = api["build_plan"](pid, plan, {"lattice": "off", "follow_camera": False, "chunk_m": a.get("chunk_m", 1000.0)})
        bad = res.get("refused_segments") or []
        rep["roads"].update({k: res.get(k) for k in ("segments_built", "refused", "edges_created")})
        if bad: rep["roads"]["refused_examples"] = bad[:3]
        built = [x for x in plan["segments"] if pave.clean(x) not in bad]
        if not res.get("segments_built"):
            rep["built"] = False; rep["reason"] = "the game refused every street (see refused_examples)"
            return rep
        if opts.get("align", True):
            import align
            al = align.after_build(rpc, [pave.clean(x) for x in built])
            if al: rep["alignment"] = {k: al.get(k) for k in ("new_edges", "flags_set", "misalignment_m_after", "error") if al.get(k) is not None}
        if opts.get("services"):
            rep["services"] = place_services(rpc, opts["services"], poly)
            journal["services"] = [dict(p, type=r["type"]) for r in rep["services"] for p in r["placed"]]
        if zones:
            rep["zoning"] = zone_new_streets(rpc, built, s, zones, area, lat, pitch)
        if opts.get("district", True):
            rep["district"] = make_district(rpc, poly, opts.get("name"), opts.get("policies"))
            if rep["district"].get("created"): journal["district"] = {"id": rep["district"]["id"], "name": rep["district"]["name"]}
        if opts.get("check", True):
            try: rep["check"] = check_outline(rpc, area, poly, bad)
            except Exception as e: rep["check"] = {"error": str(e)[:200]}
        rep["built"] = True
    finally:
        if speed0 is not None:
            try: rpc("set_speed", {"speed": speed0})
            except Exception: pass
        try: hist = rpc("history", {}).get("entries_newest_first") or []
        except Exception: hist = []
        mine = [h["id"] for h in hist if h["id"] > h0]
        if mine or rep.get("built"):                              # something is in the game: journal it for remove_district
            plan["district_build"] = dict(journal, undo_ids=mine, load_generation=ping.get("load_generation"), save=ping.get("save_name"),
                                          city=ping.get("city_name"), built_at=time.strftime("%Y-%m-%d %H:%M:%S"))
            save_plan(api, pid, plan)
    rep["undo_entries"] = len(mine)
    if len(mine) > 45: rep["warning"] = "more history entries than the 50-deep undo stack keeps: cs2_remove_district will remove by shape"
    try:
        m1 = rpc("city_info", {}).get("money")
        if not ci.get("unlimited_money") and isinstance(ci.get("money"), (int, float)) and isinstance(m1, (int, float)):
            rep["money_spent"] = round(ci["money"] - m1)
    except Exception:
        pass
    rep["remove_with"] = f"cs2_remove_district plan_id={pid}"
    if opts.get("show", True):
        c = centroid(poly)
        try: rpc("camera_goto", {"x": c[0], "z": c[1], "zoom": max(600.0, min(4000.0, area.size * 1.2))})
        except Exception: pass
    rep["seconds"] = round(time.time() - t0, 1)
    if opts.get("map"):
        w = survey.World(rpc, area)
        img = survey.render_map(w, px=int(a.get("px", 900)), layers=["terrain", "zones", "buildings", "roads", "issues"],
                                outlines=[{"points": poly, "label": opts.get("name") or pid}])
        return api["image_result"](survey.save(img, pid + "_built.png"), "image/png", json.dumps(rep))
    return rep


def preview(rpc, a, api, pid, plan, area, poly, head, opts):
    """Draft only: the plan with its zoning forecast on a map, saved under plan_id for build_district(plan_id=...)."""
    import pave, zoning
    save_plan(api, pid, plan)
    w = survey.World(rpc, area)
    fc = zoning.forecast(w, plan["segments"], api["widths"]())
    head["zoning_forecast"] = api["forecast_brief"](fc, zoning.forecast(w))
    if opts.get("services"): head["services"] = [sp if isinstance(sp, str) else sp.get("type") for sp in opts["services"]]
    head["next"] = f"cs2_build_district plan_id={pid} builds exactly this; or redraft with other settings"
    img = survey.render_map(w, px=int(a.get("px", 900)), layers=["terrain", "zones", "roads"],
                            plan=[pave.clean(x) for x in plan["segments"]], marks=api["forecast_marks"](fc),
                            outlines=[{"points": poly, "label": opts.get("name") or "new district"}])
    return api["image_result"](survey.save(img, pid + ".png"), "image/png", json.dumps(head))


# ----------------------------------------------------------------------------- remove
def remove_district(rpc, a, api):
    """Take a cs2_build_district district back out. Exact undo when its history entries are still the newest ones of
    this city load; otherwise (or by_shape=true) by shape: its roads (their zoning goes with them), its service
    buildings (by type + position) and its district outline (by name). clear_buildings=true first bulldozes the
    buildings that grew inside the outline since (batches of 40, sim paused)."""
    import pave
    pid, plan = load_plan(api, a.get("plan_id"))
    j = plan.get("district_build")
    if not j: raise ValueError(f"{pid} was drafted but never built - nothing to remove")
    if j.get("removed"): return {"plan_id": pid, "removed": False, "reason": f"already removed at {j['removed']}"}
    poly = plan["district_spec"]["polygon"]; area = survey.Area(*plan["area"])
    ping = rpc("ping", {})
    hist = rpc("history", {}).get("entries_newest_first") or []
    want, head = set(j.get("undo_ids") or []), []
    for h in hist:
        if h["id"] not in want: break
        head.append(h["id"])
    exact = bool(want) and set(head) == want and ping.get("load_generation") == j.get("load_generation")
    grown = []
    if a.get("clear_buildings"):
        mine = {(round(p["x"]), round(p["z"])) for p in j.get("services", [])}
        for b in rpc("query_buildings", dict(tools5.bbox([poly], 0), limit=20000))["buildings"]:
            p = (b["position"][0], b["position"][2])
            if tools2._in_poly(p, poly) and tools5.category(b["type"]) != "service/other" and (round(p[0]), round(p[1])) not in mine:
                grown.append(b["id"])
    out = {"plan_id": pid, "method": "undo" if exact and not grown and not a.get("by_shape") else "shape"}
    if a.get("dry_run"):
        return dict(out, undo_entries=len(head) if exact else 0, services=len(j.get("services", [])), buildings_to_clear=len(grown),
                    district=j.get("district"))
    speed0 = None
    try:
        speed0 = rpc("city_info", {}).get("speed"); rpc("set_speed", {"speed": 0})
    except Exception:
        speed0 = None
    try:
        if out["method"] == "undo":
            done = 0
            for uid in head:
                r = rpc("undo", {"expect_id": uid, "timeout_s": 120})
                if not (r.get("applied") or r.get("undone")): out["stopped_at"] = uid; break
                done += 1
            out["undone"] = done
        else:
            for k in range(0, len(grown), 40):
                rpc("bulldoze", {"ids": grown[k:k + 40], "buildings": True, "roads": False, "timeout_s": 120})
                if k + 40 < len(grown): time.sleep(3)
            if grown: out["buildings_cleared"] = len(grown)
            gone = 0
            for p in j.get("services", []):
                bs = rpc("query_buildings", {"xmin": p["x"] - 4, "zmin": p["z"] - 4, "xmax": p["x"] + 4, "zmax": p["z"] + 4, "limit": 20})["buildings"]
                ids = [b["id"] for b in bs if b["type"] == p["type"]]
                if ids:
                    rpc("bulldoze", {"ids": ids, "buildings": True, "roads": False, "timeout_s": 120}); gone += len(ids)
            out["services_removed"] = gone
            out["roads"] = pave.remove_built(rpc, plan, area)
            d = j.get("district")
            if d:
                q = {"name": d["name"]} if d.get("name") else {"id": d["id"]}
                try: out["district"] = bool(rpc("delete_district", dict(q, timeout_s=90)).get("applied"))
                except Exception as e: out["district"] = f"not deleted: {str(e)[:120]}"
    finally:
        if speed0 is not None:
            try: rpc("set_speed", {"speed": speed0})
            except Exception: pass
    j["removed"] = time.strftime("%Y-%m-%d %H:%M:%S"); save_plan(api, pid, plan)
    return out


# ----------------------------------------------------------------------------- batch
BATCH_DENY = ("cs2_batch", "cs2_restart_game", "cs2_main_menu", "cs2_load_save")


def brief(res, max_chars):
    """A tool result shortened for a batch report: images dropped (counted), long JSON cut."""
    if isinstance(res, dict) and "__mcp_content__" in res:
        parts = res["__mcp_content__"]
        texts = [p["text"] for p in parts if p.get("type") == "text"]
        res = {"images_omitted": sum(1 for p in parts if p.get("type") == "image")}
        for t in texts:
            try: res["result"] = json.loads(t)
            except ValueError: res["text"] = t
    txt = json.dumps(res)
    return res if len(txt) <= max_chars else {"truncated": True, "head": txt[:max_chars]}


def batch(rpc, a, api):
    """Run calls [{tool, args}] in order inside the server: one MCP call instead of one model round trip per step.
    fast (default true) turns the camera move and the post-build check off for builders that have them, unless a
    call's args say otherwise; pause = pause the simulation for the batch; stop_on_error (default true)."""
    calls = a.get("calls") or []
    if not calls: raise ValueError("calls: [{tool, args}] required")
    if len(calls) > 200: raise ValueError("at most 200 calls per batch")
    H = api["handlers"]; S = {n: sch for n, _, sch, _ in api["tools"]}
    stop, fast, mx = a.get("stop_on_error", True), a.get("fast", True), int(a.get("max_chars", 1500))
    t0, rows = time.time(), []
    speed0 = None
    if a.get("pause"):
        try:
            speed0 = rpc("city_info", {}).get("speed"); rpc("set_speed", {"speed": 0})
        except Exception:
            speed0 = None
    try:
        for i, c in enumerate(calls):
            name = c.get("tool") or c.get("name")
            args = dict(c.get("args") or c.get("arguments") or {})
            if name in BATCH_DENY or name not in H:
                rows.append({"i": i, "tool": name, "ok": False, "error": "not allowed in a batch" if name in BATCH_DENY else "unknown tool"})
                if stop: break
                continue
            props = (S.get(name) or {}).get("properties") or {}
            if fast:
                for k in ("follow_camera", "check"):
                    if k in props: args.setdefault(k, False)
            t1 = time.time()
            try:
                res = brief(H[name](args), mx)
                rows.append({"i": i, "tool": name, "ok": True, "s": round(time.time() - t1, 1), "result": res})
            except Exception as e:
                rows.append({"i": i, "tool": name, "ok": False, "error": f"{type(e).__name__}: {e}"[:400]})
                if stop: break
    finally:
        if speed0 is not None:
            try: rpc("set_speed", {"speed": speed0})
            except Exception: pass
    ok = sum(1 for r in rows if r["ok"])
    return {"calls": len(calls), "ran": len(rows), "ok": ok, "failed": len(rows) - ok, "seconds": round(time.time() - t0, 1), "results": rows}
