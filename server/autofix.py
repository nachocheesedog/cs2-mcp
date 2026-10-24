"""Auto-fix for road previews with errors (dry_run / hold).

cs2_place_roads used to hand back a preview with errors and leave the fixing to the caller. With auto_fix (default on for
dry_run / hold) the server drops the draft segments that sit on an error position, previews again, and repeats until the
preview is clean or a round limit is hit. Whatever was dropped, and any group of kept segments that no longer touches the
existing network, is reported so the caller can decide (link it, remove it, or accept it).
"""
import math

import survey

RADIUS = 12.0        # m: a segment closer than this to an error position is dropped
UNION = 3.0          # m: ends closer than this count as the same node
TOUCH = 4.0          # m: an end this close to an existing road counts as connected to it


def _dist(p, a, b):
    dx, dz = b[0] - a[0], b[1] - a[1]
    L = dx * dx + dz * dz
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dz) / L))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dz)


def drop_near(segs, positions, radius=RADIUS):
    """(kept, dropped): dropped = segments within `radius` of any error position (straight-line approximation)."""
    kept, dropped = [], []
    for s in segs:
        if any(_dist(p, s["start"], s["end"]) <= radius for p in positions): dropped.append(s)
        else: kept.append(s)
    return kept, dropped


def groups(segs):
    """connected components of the draft (segments sharing an end, or an end lying on another segment)."""
    parent = list(range(len(segs)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(segs):
        for j in range(i + 1, len(segs)):
            b = segs[j]
            ends_a, ends_b = (a["start"], a["end"]), (b["start"], b["end"])
            if (any(math.dist(p, q) < UNION for p in ends_a for q in ends_b)
                    or min(_dist(p, b["start"], b["end"]) for p in ends_a) < UNION
                    or min(_dist(q, a["start"], a["end"]) for q in ends_b) < UNION):
                parent[root(i)] = root(j)
    out = {}
    for i in range(len(segs)): out.setdefault(root(i), []).append(i)
    return sorted(out.values(), key=len, reverse=True)


def _existing_polylines(rpc, segs):
    xs = [c for s in segs for c in (s["start"][0], s["end"][0])]
    zs = [c for s in segs for c in (s["start"][1], s["end"][1])]
    net = rpc("road_network", {"xmin": min(xs) - 30, "zmin": min(zs) - 30, "xmax": max(xs) + 30, "zmax": max(zs) + 30, "limit": 30000})
    return [survey.polyline(e["curve"], 12) for e in net["edges"] if "flow" in e]


def islands(rpc, segs, members=False):
    """groups of the draft that touch no existing road: [{segments, near: [x, z]}]. The biggest group is never reported
    unless it too touches nothing (a draft in the open that connects to nothing)."""
    if not segs: return []
    try: lines = _existing_polylines(rpc, segs)
    except Exception: return []

    def touches(p):
        for pl in lines:
            for a, b in zip(pl, pl[1:]):
                if _dist(p, (a[0], a[1]) if len(a) == 2 else (a[0], a[2]), (b[0], b[1]) if len(b) == 2 else (b[0], b[2])) <= TOUCH: return True
        return False

    out = []
    for g in groups(segs):
        ends = [p for i in g for p in (segs[i]["start"], segs[i]["end"])]
        if not any(touches(p) for p in ends):
            mx = sum(p[0] for p in ends) / len(ends); mz = sum(p[1] for p in ends) / len(ends)
            out.append({"segments": len(g), "near": [round(mx), round(mz)], **({"indices": g} if members else {})})
    return out


def run(rpc, segs, params, res, max_rounds=4, radius=RADIUS, ignore=None):
    """Re-preview `segs` without the segments that hit errors. `params` are the place_roads params of the first call
    (without segments); `res` its result. Returns (segs, res, report). A held preview that still has errors is cancelled."""
    hold = bool(params.get("hold"))
    dropped, rounds, only_ignored = [], 0, False
    while res.get("errors") and rounds < max_rounds and segs:
        locs = [tuple(l["pos"]) for l in (res.get("error_locations") or []) if l.get("pos")]
        if ignore:                                          # errors inside a box that will be demolished first are not a reason to drop
            locs = [p for p in locs if not any(b["xmin"] <= p[0] <= b["xmax"] and b["zmin"] <= p[1] <= b["zmax"] for b in ignore)]
            if not locs:
                only_ignored = True
                break
        if not locs: break
        kept, gone = drop_near(segs, locs, radius)
        if not gone: break                                  # the errors are not on any draft segment (nothing to drop)
        if hold:
            try: rpc("preview_cancel")
            except Exception: pass
        rounds += 1
        dropped += gone
        segs = kept
        if not segs: break
        res = rpc("place_roads", dict(params, segments=segs))
    clean = bool(segs) and (not res.get("errors") or only_ignored)
    if hold and not clean:
        try: rpc("preview_cancel")                           # never leave a preview with errors on screen
        except Exception: pass
        res = dict(res, held=False)
    report = {"rounds": rounds, "dropped_segments": len(dropped), "kept_segments": len(segs), "clean": clean}
    if only_ignored: report["errors_in_demolish_boxes"] = res.get("errors")
    if dropped: report["dropped_examples"] = [{"start": s["start"], "end": s["end"]} for s in dropped[:12]]
    if not clean:
        report["remaining_errors"] = res.get("errors", 0)
        report["error_reasons"] = res.get("error_reasons")
    if clean:
        isl = islands(rpc, segs)
        if isl: report["unconnected_groups"] = isl[:8]
    return segs, res, report
