"""Design queue: stage several road designs (grids, links...) that already preview clean, review them together, build them with ONE call.

cs2_queue_add       dry-runs a design with auto_fix and stores the cleaned segments (+ an optional zoning spec) in a JSON file; nothing is built.
cs2_queue_list      what is staged: name, segments, bounding box, zoning, conflicts that were resolved when it was added.
cs2_queue_remove    drops one design (name) or all of them.
cs2_queue_commit    builds the staged designs in order (chunks of <= `chunk` segments: hold with auto_fix, then commit), checks the
                    network for islands around each one and applies its zoning. One tool call = one approval.

A design is cleaned against the game as it is when it is added. Segments that cross or overlap an earlier staged design are dropped at add time
(first come, first served); at commit time every chunk is previewed again with auto_fix, so whatever changed in between (houses that were built, ...)
is dropped instead of failing. Queue file: $CS2MCP_QUEUE_DIR/queue.json, default ~/cs2mcp-queue.
"""
import json, math, os, time

_ENDPOINT = 3.0          # m: ends closer than this are the same node (a T or a shared corner is not a conflict)
_OVERLAP_MIN = 10.0      # m: collinear overlap longer than this is a conflict


def _file():
    d = os.environ.get("CS2MCP_QUEUE_DIR") or os.path.join(os.path.expanduser("~"), "cs2mcp-queue")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "queue.json")


def load():
    try:
        with open(_file()) as fh: return json.load(fh)
    except (OSError, ValueError):
        return []


def save(q):
    tmp = _file() + ".tmp"
    with open(tmp, "w") as fh: json.dump(q, fh)
    os.replace(tmp, _file())


def _bbox(segs):
    xs = [p[0] for s in segs for p in (s["start"], s["end"])]
    zs = [p[1] for s in segs for p in (s["start"], s["end"])]
    return [round(min(xs), 1), round(min(zs), 1), round(max(xs), 1), round(max(zs), 1)]


def _pt_seg(p, a, b):
    dx, dz = b[0] - a[0], b[1] - a[1]
    L = dx * dx + dz * dz
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dz) / L))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dz)


def _cross(o, a, b): return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def conflict(s, t):
    """True when segments s and t cross each other or run on top of each other (straight-line approximation)."""
    s0, s1, t0, t1 = s["start"], s["end"], t["start"], t["end"]
    near_s = [_pt_seg(p, t0, t1) < _ENDPOINT for p in (s0, s1)]          # ends of s lying on t
    near_t = [_pt_seg(q, s0, s1) < _ENDPOINT for q in (t0, t1)]          # ends of t lying on s
    shared = min(math.dist(p, q) for p in (s0, s1) for q in (t0, t1)) < _ENDPOINT
    if shared:
        # a shared node is a normal join; it is a conflict only when the two lie on top of each other from it
        return sum(near_s) + sum(near_t) >= 3
    if all(near_s) or all(near_t): return min(math.dist(s0, s1), math.dist(t0, t1)) > _OVERLAP_MIN   # one runs along the other
    if any(near_s) and any(near_t):                                      # partial overlap: ends of each on the other
        ds, dt = (s1[0] - s0[0], s1[1] - s0[1]), (t1[0] - t0[0], t1[1] - t0[1])
        par = abs(ds[0] * dt[1] - ds[1] * dt[0]) / (math.hypot(*ds) * math.hypot(*dt) or 1)
        return par < 0.1
    # proper crossing, interior points only
    d1, d2 = _cross(t0, t1, s0), _cross(t0, t1, s1)
    d3, d4 = _cross(s0, s1, t0), _cross(s0, s1, t1)
    return (d1 > 0) != (d2 > 0) and (d3 > 0) != (d4 > 0) and d1 * d2 != 0 and d3 * d4 != 0


def _slim(s):
    out = {"start": [round(v, 2) for v in s["start"]], "end": [round(v, 2) for v in s["end"]]}
    for k in ("type", "control", "controls", "elevation"):
        if s.get(k) is not None: out[k] = s[k]
    return out


MIN_KEEP = 0.8        # a staged design keeps at least this share of the segments sent; the rest (errors, loose pieces) is trimmed


def add(a, place_roads, rpc=None):
    name = (a.get("name") or "").strip()
    if not name: raise ValueError("name required")
    q = load()
    if any(d["name"] == name for d in q) and not a.get("replace"):
        raise ValueError(f"'{name}' is already staged (replace=true overwrites it, or cs2_queue_remove it)")
    q = [d for d in q if d["name"] != name]
    keys = {k: v for k, v in a.items() if k not in ("name", "zone", "replace", "note", "hold", "dry_run", "return_segments", "allow_errors", "allow_island", "demolish", "demolish_file", "min_keep", "allow_heavy_trim")}
    boxes = a.get("demolish") or []
    if isinstance(boxes, str): boxes = json.loads(boxes)            # some clients send the list as a JSON string
    boxes = [json.loads(b) if isinstance(b, str) else b for b in boxes]
    if a.get("demolish_file"):
        with open(os.path.expanduser(a["demolish_file"])) as fh: boxes = boxes + json.load(fh)
    keys.setdefault("auto_fix_rounds", 10)                           # staging insists a little more before giving up
    min_keep = float(a.get("min_keep", MIN_KEEP))
    res = place_roads(dict(keys, dry_run=True, hold=False, return_segments=True, follow_camera=False, zoning_check=False, auto_fix_ignore=boxes or None))
    segs = res.pop("segments_kept", None)
    if res.get("refused") or not segs:
        raise ValueError("nothing to stage: " + str(res.get("refused") or "no segments left after cleaning"))
    if res.get("errors") and not (res.get("auto_fix") or {}).get("clean"):
        raise ValueError(f"the design still has {res.get('errors')} error(s) after auto_fix ({res.get('error_reasons')}); not staged")
    af = res.get("auto_fix") or {}
    original = len(segs) + af.get("dropped_segments", 0)             # segments the caller sent
    islands = af.get("unconnected_groups")
    if islands is None and rpc is not None and (res.get("auto_fix") is None):          # auto_fix did not run (clean at once): check anyway
        try:
            import autofix
            islands = autofix.islands(rpc, segs) or None
        except Exception: islands = None
    island_dropped = 0
    if islands and not a.get("allow_island"):
        # a good design with a few loose pieces is staged without them (up to 1 - min_keep of the whole); only a mostly-loose one is refused
        try:
            import autofix
            full = autofix.islands(rpc, segs, members=True) if rpc is not None else []
        except Exception: full = []
        loose = {i for g in full for i in g.get("indices", [])}
        left = len(segs) - len(loose)
        if full and left > 0 and left / max(1, original) >= min_keep:
            segs = [s for i, s in enumerate(segs) if i not in loose]
            island_dropped = len(loose)
            af = dict(af, dropped_segments=af.get("dropped_segments", 0) + island_dropped, kept_segments=len(segs), dropped_island_segments=island_dropped)
            af.pop("unconnected_groups", None)
            res["auto_fix"] = af
        else:
            raise ValueError(f"the design would be built as an island: {islands} (nothing touches the existing network; keeping the connected part "
                             f"would leave {left} of {original} segments, under min_keep={min_keep}). Add links to the avenues "
                             "or pass allow_island=true if it is meant to connect later; not staged")
    if original and len(segs) / original < min_keep and not a.get("allow_heavy_trim"):
        raise ValueError(f"auto_fix would remove too much: {original - len(segs)} of {original} segments (keeps {len(segs)}, under min_keep={min_keep}); "
                         "fix the design, lower min_keep or pass allow_heavy_trim=true; not staged")
    segs = [_slim(s) for s in segs]
    dropped = []
    others = [s for d in q for s in d["segments"]]
    if others:
        keep = []
        for s in segs:
            (dropped if any(conflict(s, o) for o in others) else keep).append(s)
        segs = keep
    if not segs: raise ValueError("every segment conflicts with designs already staged")
    zone = a.get("zone")
    if isinstance(zone, str): zone = json.loads(zone)
    d = {"name": name, "segments": segs, "snap_tolerance": a.get("snap_tolerance", 2.0), "zone": zone, "note": a.get("note"), "demolish": boxes,
         "bbox": _bbox(segs), "added": time.strftime("%Y-%m-%d %H:%M:%S"), "dropped_by_queue": len(dropped),
         "auto_fix": res.get("auto_fix")}
    q.append(d)
    save(q)
    out = {"staged": name, "segments": len(segs), "bbox": d["bbox"], "preview_errors": 0, "auto_fix": res.get("auto_fix"),
           "dropped_for_conflict_with_queue": len(dropped), "queue_size": len(q), "file": _file()}
    if dropped: out["conflict_examples"] = [{"start": s["start"], "end": s["end"]} for s in dropped[:6]]
    return out


def listing(a=None):
    q = load()
    return {"designs": [{"name": d["name"], "segments": len(d["segments"]), "bbox": d["bbox"], "zone": d.get("zone"), "note": d.get("note"),
                         "added": d.get("added"), "dropped_by_autofix": (d.get("auto_fix") or {}).get("dropped_segments", 0),
                         "dropped_by_queue": d.get("dropped_by_queue", 0),
                         "unconnected_groups": (d.get("auto_fix") or {}).get("unconnected_groups")} for d in q],
            "total_segments": sum(len(d["segments"]) for d in q), "file": _file()}


def remove(a):
    q = load()
    if a.get("all"):
        save([]); return {"removed": len(q), "queue_size": 0}
    name = a.get("name")
    nq = [d for d in q if d["name"] != name]
    if len(nq) == len(q): raise ValueError(f"'{name}' is not staged")
    save(nq)
    return {"removed": name, "queue_size": len(nq)}


def _chunks(segs, n):
    segs = sorted(segs, key=lambda s: ((s["start"][0] + s["end"][0]) / 2, (s["start"][1] + s["end"][1]) / 2))
    k = max(1, math.ceil(len(segs) / max(1, n)))
    size = math.ceil(len(segs) / k)
    return [segs[i:i + size] for i in range(0, len(segs), size)]


def commit(a, rpc, place_roads, zone_along, inspect):
    q = load()
    names = a.get("names")
    todo = [d for d in q if not names or d["name"] in names]
    if not todo: raise ValueError("nothing staged" if not q else "none of those names is staged")
    chunk = int(a.get("chunk", 100))
    pause = float(a.get("pause_s", 1.0))
    stop = a.get("stop_on_error", True)
    report, done = [], []
    try: rpc("set_speed", {"speed": 0})
    except Exception: pass
    for d in todo:
        r = {"name": d["name"], "chunks": [], "built_segments": 0, "dropped": 0}
        ok = True
        for b in d.get("demolish") or []:                       # buildings in the corridors the design needs; roads are left alone
            try:
                br = rpc("bulldoze", {"xmin": b["xmin"], "zmin": b["zmin"], "xmax": b["xmax"], "zmax": b["zmax"], "roads": False, "buildings": True, "timeout_s": 90})
                r.setdefault("demolished", []).append({"box": b, "buildings": br.get("buildings"), "undo_id": br.get("undo_id")})
            except Exception as e:
                r.setdefault("demolished", []).append({"box": b, "error": str(e)[:200]}); ok = False
                if stop: break
        for i, ch in enumerate(_chunks(d["segments"], chunk) if ok or not stop else []):
            try:
                res = place_roads({"segments": ch, "hold": True, "auto_fix": True, "follow_camera": False, "zoning_check": False,
                                   "snap_tolerance": d.get("snap_tolerance", 2.0)})
            except Exception as e:
                r["chunks"].append({"chunk": i + 1, "error": str(e)[:300]}); ok = False
                if stop: break
                continue
            fx = res.get("auto_fix") or {}
            if not res.get("held"):
                r["chunks"].append({"chunk": i + 1, "built": False, "reason": "preview could not be cleaned", "auto_fix": fx}); ok = False
                if stop: break
                continue
            try: c = rpc("preview_commit")
            except Exception as e:
                r["chunks"].append({"chunk": i + 1, "error": "commit: " + str(e)[:300]}); ok = False
                break                                           # the game may be gone: never keep going blind
            n = res.get("segments", len(ch)); r["built_segments"] += n; r["dropped"] += fx.get("dropped_segments", 0)
            r["chunks"].append({"chunk": i + 1, "segments": n, "dropped": fx.get("dropped_segments", 0), "undo_id": c.get("undo_id")})
            time.sleep(pause)
        x0, z0, x1, z1 = d["bbox"]
        area = {"xmin": x0 - 40, "zmin": z0 - 40, "xmax": x1 + 40, "zmax": z1 + 40}
        if r["built_segments"]:
            try:
                isl = inspect(dict(area, sections=["roads.islands"], limit=10))
                r["islands"] = ((isl.get("roads") or {}).get("islands") or {}).get("count", 0)
                if r["islands"]: r["island_list"] = (isl["roads"]["islands"] or {}).get("list", [])[:5]
            except Exception as e: r["islands"] = "check failed: " + str(e)[:120]
        z = d.get("zone")
        if z and ok and a.get("zone", True) and r["built_segments"]:
            try:
                zr = zone_along(dict(area, zone_type=z["zone_type"], depth_m=z.get("depth_m", 32), types=z.get("types", ["Small Road"]),
                                     start_m=z.get("start_m", 0), side=z.get("side", "both"), overwrite=bool(z.get("overwrite", False)), follow_camera=False))
                r["zoned"] = {"quads": zr.get("zoned"), "refused": zr.get("refused"), "zone_type": z["zone_type"]}
            except Exception as e: r["zoned"] = "failed: " + str(e)[:200]
        r["complete"] = ok
        report.append(r)
        if ok: done.append(d["name"])
        elif stop: break
    if done: save([d for d in load() if d["name"] not in done])
    return {"built": done, "kept_in_queue": [d["name"] for d in load()], "designs": report}


def add_batch(a, place_roads, rpc):
    """Stage every design <prefix><n>_segments.json (+ optional <prefix><n>_demoler.json) found in `dir`; each one is cleaned and checked on its own."""
    d = os.path.expanduser(a["dir"]); pref = a.get("prefix", "hueco")
    names = sorted((f for f in os.listdir(d) if f.startswith(pref) and f.endswith("_segments.json")),
                   key=lambda f: int(f[len(pref):-len("_segments.json")]) if f[len(pref):-len("_segments.json")].isdigit() else 0)
    staged, refused = [], []
    for f in names:
        n = f[:-len("_segments.json")]
        args = {"name": a.get("name_prefix", "") + n, "segments_file": os.path.join(d, f), "snap_tolerance": a.get("snap_tolerance", 4),
                "zone": a.get("zone"), "replace": True, "note": a.get("note")}
        for k in ("min_keep", "allow_heavy_trim", "allow_island"):
            if a.get(k) is not None: args[k] = a[k]
        dem = os.path.join(d, n + "_demoler.json")
        if os.path.exists(dem): args["demolish_file"] = dem
        try:
            r = add(args, place_roads, rpc)
            staged.append({"name": r["staged"], "segments": r["segments"], "dropped_for_conflict": r["dropped_for_conflict_with_queue"],
                           "dropped_by_autofix": (r.get("auto_fix") or {}).get("dropped_segments", 0)})
        except Exception as e:
            refused.append({"name": n, "reason": str(e)[:240]})
    return {"staged": staged, "refused": refused, "queue_size": len(load()), "total_segments": sum(len(x["segments"]) for x in load())}
