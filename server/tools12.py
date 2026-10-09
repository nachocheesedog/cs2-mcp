"""v0.27 (2026-09-26): ribs - a street layout for land beside a spine road (curved or straight), built for zoning.

- ribs   streets leaving a spine road (road_ids chained, or knots) at 90 deg toward `side`, one full zoning depth apart
         (pitch = street width + 96 m, so every block is two 48 m lots back to back), anchored so they CONTINUE the
         streets already joining the spine from the other side (junctions stay four-way, no 20 m offsets): the arc between
         two anchors is split into equal blocks no shorter than the pitch. Each rib runs until it meets a target road
         (target_types; the first road it crosses otherwise) - arriving square: if the straight line would hit at under
         perp_deg (80) it bends so its last approach_m (52) come in perpendicular - or stops before water / steep ground
         (then it is dropped unless keep_dead_ends). Each rib is dry-run through place_roads; refused ones are dropped and
         reported. Returns segments + a zoning forecast; build=true places them.
Generic: spine, side, types, pitch and targets are the caller's choices. For a curved district (a waterfront avenue, a
highway frontage) this beats a straight lattice, which leaves wedges and gaps against the curve.
"""
import math, time
import numpy as np
import geom, survey, tools2, tools4, zoning


def _chain(rpc, ids, box):
    net = rpc("road_network", dict(box, limit=30000))
    es = {e["id"]: e for e in net["edges"]}
    return [e for i in ids for e in [es.get(i)] if e], net


def _spine(rpc, a):
    if a.get("knots"):
        return [tuple(p) for p in a["knots"]]
    line, _ = tools4.work_line(rpc, {"road_ids": a["road_ids"]}, None)
    return [tuple(p) for p in line]


def ribs(rpc, a):
    widths = {r["name"]: r["width"] for r in rpc("list_road_types")["road_types"]}
    typ = a.get("type", "Small Road"); w = widths.get(typ, 16)
    pitch = float(a.get("pitch_m") or (w + 96))
    reach = float(a.get("reach_m", 450)); perp = float(a.get("perp_deg", 80)); appr = float(a.get("approach_m", 52))
    side = 1 if a.get("side", "left") == "left" else -1
    line = _spine(rpc, a)
    if a.get("toward"):                                 # pick the side facing this point
        mid = line[len(line) // 2]; nx = line[min(len(line) - 1, len(line) // 2 + 1)]
        t0 = (nx[0] - mid[0], nx[1] - mid[1]); lft = (-t0[1], t0[0])
        side = 1 if (a["toward"][0] - mid[0]) * lft[0] + (a["toward"][1] - mid[1]) * lft[1] > 0 else -1
    L = geom.cumlen(line)
    s0, s1 = float(a.get("start_m", 0.0)), L[-1] - float(a.get("end_m", 0.0))
    box = tools4.box_around(line, reach + 100)
    net = rpc("road_network", dict(box, limit=30000))
    wg = tools4.WaterGrid(rpc, box)
    spine_w = float(a.get("spine_width", 24))

    def at(s):                                          # point + unit tangent at arc length s
        i = max(1, min(len(line) - 1, int(np.searchsorted(L, s))))
        f = (s - L[i - 1]) / ((L[i] - L[i - 1]) or 1)
        p = (line[i - 1][0] + (line[i][0] - line[i - 1][0]) * f, line[i - 1][1] + (line[i][1] - line[i - 1][1]) * f)
        t = (line[i][0] - line[i - 1][0], line[i][1] - line[i - 1][1]); n = math.hypot(*t) or 1
        return p, (t[0] / n, t[1] / n)

    def arc_of(p):
        best = (1e18, 0.0)
        for i in range(1, len(line)):
            a_, b_ = line[i - 1], line[i]; ab = (b_[0] - a_[0], b_[1] - a_[1]); ll = ab[0] ** 2 + ab[1] ** 2 or 1
            f = max(0, min(1, ((p[0] - a_[0]) * ab[0] + (p[1] - a_[1]) * ab[1]) / ll))
            q = (a_[0] + ab[0] * f, a_[1] + ab[1] * f); d = math.dist(p, q)
            if d < best[0]: best = (d, L[i - 1] + f * (L[i] - L[i - 1]))
        return best

    # anchors: where other roads meet the spine (within spine_w), on either side -> continue them
    anchors = sorted({round(arc_of(tuple(n["pos"][::2]))[1], 1) for n in net["nodes"]
                      if n.get("degree", 0) >= 3 and arc_of(tuple(n["pos"][::2]))[0] < spine_w / 2 + 2}
                     | set(float(x) for x in (a.get("anchors_m") or [])))
    anchors = [x for x in anchors if s0 <= x <= s1]
    # anchors already have a street (that is why they are anchors): only fill BETWEEN them unless build_anchors
    stations = list(anchors) if a.get("build_anchors") else []
    if not anchors:
        k = 0
        while s0 + k * pitch <= s1 - pitch / 4: stations.append(s0 + k * pitch); k += 1
    else:
        k = 1
        while anchors[0] - k * pitch >= s0 + pitch / 4: stations.append(anchors[0] - k * pitch); k += 1
        for lo, hi in zip(anchors, anchors[1:]):        # equal blocks between anchors, never shorter than the pitch
            n = max(1, int((hi - lo) // pitch)); step = (hi - lo) / n
            stations += [lo + step * m for m in range(1, n)]
        k = 1
        while anchors[-1] + k * pitch <= s1 - pitch / 4: stations.append(anchors[-1] + k * pitch); k += 1
    stations = sorted(set(round(x, 1) for x in stations))

    # targets: road polylines the ribs may end on
    tt = a.get("target_types")
    targ = []
    for e in net["edges"]:
        if "flow" not in e or "Highway" in e["type"]: continue
        if tt and not any(k.lower() in e["type"].lower() for k in tt): continue
        P = survey.polyline(e["curve"], max(4, int(e["length"] / 6)))
        targ += [((P[i][0], P[i][1]), (P[i + 1][0], P[i + 1][1]), e["id"]) for i in range(len(P) - 1)]
    spine_ids = set(a.get("road_ids") or [])

    def hit(p, d, skip_m):
        best = None
        for A, B, eid in targ:
            if eid in spine_ids: continue
            e = (B[0] - A[0], B[1] - A[1]); den = d[0] * e[1] - d[1] * e[0]
            if abs(den) < 1e-9: continue
            wv = (A[0] - p[0], A[1] - p[1])
            t = (wv[0] * e[1] - wv[1] * e[0]) / den; u = (wv[0] * d[1] - wv[1] * d[0]) / den
            if skip_m < t <= reach and 0 <= u <= 1 and (best is None or t < best[0]): best = (t, A, B, eid)
        return best

    segs, report = [], {"stations": len(stations), "anchored": len(anchors), "bent": 0}
    def attempt(s):
        """-> (rib or None, why)"""
        p, t = at(s)
        n = (-t[1] * side, t[0] * side)
        h = hit(p, n, spine_w / 2 + 5)
        if not h:
            if not a.get("keep_dead_ends"): return None, ("no_target", [round(p[0]), round(p[1])])
            end = (p[0] + n[0] * reach, p[1] + n[1] * reach); rib = {"start": list(p), "end": list(end), "type": typ}
        else:
            tdist, A, B, eid = h
            H = (p[0] + n[0] * tdist, p[1] + n[1] * tdist)
            T = (B[0] - A[0], B[1] - A[1]); tl = math.hypot(*T) or 1; T = (T[0] / tl, T[1] / tl)
            ang = math.degrees(math.asin(min(1, abs(n[0] * T[1] - n[1] * T[0]))))   # rib vs target: 90 = square
            rib = {"start": [p[0], p[1]], "end": [H[0], H[1]], "type": typ, "to": eid}
            if ang < perp and tdist > appr + 20:
                N2 = (-T[1], T[0])
                if N2[0] * (p[0] - H[0]) + N2[1] * (p[1] - H[1]) < 0: N2 = (-N2[0], -N2[1])
                foot = (H[0] + T[0] * ((p[0] - H[0]) * T[0] + (p[1] - H[1]) * T[1]), H[1] + T[1] * ((p[0] - H[0]) * T[0] + (p[1] - H[1]) * T[1]))
                E = ((H[0] + foot[0]) / 2, (H[1] + foot[1]) / 2)
                Lr = math.dist(p, E)
                rib = {"start": [p[0], p[1]], "end": [E[0], E[1]], "type": typ, "to": eid,
                       "controls": [[p[0] + n[0] * Lr * 0.35, p[1] + n[1] * Lr * 0.35], [E[0] + N2[0] * max(Lr * 0.4, appr), E[1] + N2[1] * max(Lr * 0.4, appr)]]}
                rib["_bent"] = True
        pl = zoning.seg_polyline(rib)
        if any(wg.depth(q) > 0.3 for q in pl[1:-1]): return None, ("wet", None)
        bent = rib.get("_bent", False)
        rib = {k: ([round(v[0], 2), round(v[1], 2)] if k in ("start", "end") else [[round(c[0], 2), round(c[1], 2)] for c in v] if k == "controls" else v)
               for k, v in rib.items() if k in ("start", "end", "controls", "type")}
        if a.get("verify", True):
            r = rpc("place_roads", {"segments": [rib], "snap_tolerance": 3.0, "dry_run": True, "timeout_s": 60})
            if r.get("errors"):
                return None, ("refused", {"at": rib["start"], "reasons": r.get("error_reasons"), "where": (r.get("error_locations") or [])[:2]})
        return rib, ("bent" if bent else "ok")

    shifts = [0.0] + [d * sgn for d in (8.0, 16.0, 24.0) for sgn in (1, -1)] if a.get("shift", True) else [0.0]
    for s in stations:
        rib, why = None, ("refused", None)
        for k, dsh in enumerate(shifts):
            if not (s0 <= s + dsh <= s1): continue
            rib, why = attempt(s + dsh)
            if rib:
                segs.append(rib)
                if why[0] == "bent" if isinstance(why, tuple) else why == "bent": report["bent"] += 1
                if dsh: report["shifted"] = report.get("shifted", 0) + 1; report.setdefault("shifts_m", []).append(dsh)
                break
            if why[0] in ("no_target", "wet"): break          # moving a few metres will not fix these
        if not rib:
            kind, info = why
            report[kind] = report.get(kind, 0) + 1
            if info is not None: report.setdefault(kind + "_at", []).append(info)

    # v0.27b: a dropped rib leaves a block two pitches wide - a hole nothing can zone (layout rule: no holes). List them.
    built_s = sorted([arc_of(tuple(r["start"]))[1] for r in segs] + ([] if a.get("build_anchors") else list(anchors)))
    wide = [{"from_m": round(a_), "to_m": round(b_), "gap_m": round(b_ - a_), "at": [round(v) for v in at((a_ + b_) / 2)[0]]}
            for a_, b_ in zip(built_s, built_s[1:]) if b_ - a_ > pitch + 20]
    if wide: report["blocks_too_wide"] = wide
    out = {"type": typ, "pitch_m": round(pitch, 1), "spine_length_m": round(L[-1]), "ribs": len(segs), "report": report,
           "verdict": "good" if not wide else f"{len(wide)} block(s) wider than one pitch - holes: add / shift / bend a rib there",
           "anchors_m": anchors, "segments": segs}
    if segs:
        try:
            import cs2_mcp
            out["zoning_forecast"] = cs2_mcp._forecast_for(segs)
        except Exception as ex: out["zoning_forecast"] = {"error": str(ex)}
    if a.get("build") and segs:
        import pave                                     # a batch can fail where every rib alone passed: skip only the culprits
        res, bad = pave.place_skip_failing(rpc, segs, snap_tolerance=3.0)
        out["build"] = {"built": len(segs) - len(bad), "refused": len(bad), "refused_segments": bad[:10]}
    return out


# ----------------------------------------------------------------------------- square ends (v0.27b)
def square_ends(rpc, a):
    """Every street (street_types) that meets another road at a junction whose angle is off square (< max_deg, default 88)
    gets its LAST piece rebuilt as a bend that arrives at exactly 90 deg: from the piece's inner node, leaving along the
    street's own direction, to a point on the target road halfway between the old junction and the foot of the
    perpendicular, the last approach_m (52) perpendicular. Old piece bulldozed, new one dry-run first; if the new one is
    refused the old piece is rebuilt (undo). area = box; dry_run lists what would change."""
    import survey as _s
    _rpc = rpc
    def rpc(m, prm=None, **kw):                           # another session may hold the MCP tool: wait for it
        for k in range(20):
            try: return _rpc(m, prm, **kw)
            except Exception as ex:
                if "tool busy" not in str(ex) or k == 19: raise
                time.sleep(3)
    box = {k: a[k] for k in ("xmin", "zmin", "xmax", "zmax")}
    net = rpc("road_network", dict(box, limit=30000))
    st_types = a.get("street_types", ["Small Road"]); max_deg = float(a.get("max_deg", 88)); appr = float(a.get("approach_m", 52))
    edges = [e for e in net["edges"] if "flow" in e]
    by_node = {}
    for e in edges:
        for n in (e["start_node"], e["end_node"]): by_node.setdefault(n, []).append(e)
    npos = {n["id"]: (n["pos"][0], n["pos"][2]) for n in net["nodes"]}
    def tan_at(e, node):                                  # unit direction leaving `node` along e (exact: Bezier control)
        c = e["curve"]; a_, b_ = ((c[0], c[1]) if e["start_node"] == node else (c[3], c[2]))
        if math.dist((a_[0], a_[2]), (b_[0], b_[2])) < 1e-3: b_ = c[2] if e["start_node"] == node else c[1]
        d = (b_[0] - a_[0], b_[2] - a_[2]); L = math.hypot(*d) or 1; return (d[0] / L, d[1] / L)
    plan = []
    for node, es in by_node.items():
        if len(es) != 3 or node not in npos: continue                             # a T: one street onto a through road
        # the through road = the two arms most nearly in line; the third arm is the street (any street type)
        pairs = [(i, j) for i in range(3) for j in range(i + 1, 3)]
        i, j = min(pairs, key=lambda ij: tan_at(es[ij[0]], node)[0] * tan_at(es[ij[1]], node)[0] + tan_at(es[ij[0]], node)[1] * tan_at(es[ij[1]], node)[1])
        s = es[3 - i - j]; others = [es[i], es[j]]
        if s["type"] not in st_types: continue
        ts = tan_at(s, node)
        to = tan_at(others[0], node); t1 = tan_at(others[1], node)
        if to[0] * t1[0] + to[1] * t1[1] > -0.9: continue                         # the through road must be straight-ish here
        ang = math.degrees(math.asin(min(1.0, abs(ts[0] * to[1] - ts[1] * to[0]))))
        if ang >= max_deg or s["length"] < appr + 15: continue
        H = npos[node]; inner = s["end_node"] if s["start_node"] == node else s["start_node"]; P = npos[inner]
        d = (-tan_at(s, inner)[0], -tan_at(s, inner)[1])                          # direction at P toward... (leaving P along s)
        d = tan_at(s, inner)
        T = to; N = (-T[1], T[0])
        if N[0] * (P[0] - H[0]) + N[1] * (P[1] - H[1]) < 0: N = (-N[0], -N[1])
        k = (P[0] - H[0]) * T[0] + (P[1] - H[1]) * T[1]; foot = (H[0] + T[0] * k, H[1] + T[1] * k)
        E0 = ((H[0] + foot[0]) / 2, (H[1] + foot[1]) / 2)
        # snap E onto the target road's Bezier and take the EXACT tangent there (sampled tangents left 3-6 deg errors)
        best = None
        for o in others:
            c = o["curve"]
            for i in range(201):
                t = i / 200; u = 1 - t
                q = [u ** 3 * c[0][k] + 3 * u * u * t * c[1][k] + 3 * u * t * t * c[2][k] + t ** 3 * c[3][k] for k in (0, 2)]
                dd = math.dist(q, E0)
                if best is None or dd < best[0]:
                    dq = [3 * u * u * (c[1][k] - c[0][k]) + 6 * u * t * (c[2][k] - c[1][k]) + 3 * t * t * (c[3][k] - c[2][k]) for k in (0, 2)]
                    best = (dd, (q[0], q[1]), dq)
        E = best[1]; L = math.dist(P, E)
        tl = math.hypot(*best[2]) or 1; T = (best[2][0] / tl, best[2][1] / tl); N = (-T[1], T[0])
        if N[0] * (P[0] - E[0]) + N[1] * (P[1] - E[1]) < 0: N = (-N[0], -N[1])
        seg = {"start": [round(P[0], 2), round(P[1], 2)], "end": [round(E[0], 2), round(E[1], 2)], "type": s["type"],
               "controls": [[round(P[0] + d[0] * L * 0.35, 2), round(P[1] + d[1] * L * 0.35, 2)],
                            [round(E[0] + N[0] * max(L * 0.4, appr), 2), round(E[1] + N[1] * max(L * 0.4, appr), 2)]]}
        plan.append({"at": [round(H[0]), round(H[1])], "angle_deg": round(ang, 1), "old": s["id"], "new": seg})
    if a.get("polygon"): plan = [p for p in plan if tools2._in_poly(tuple(p["at"]), [tuple(q) for q in a["polygon"]])]
    out = {"candidates": len(plan), "plan": [{k: p[k] for k in ("at", "angle_deg")} for p in plan]}
    if a.get("dry_run"): return out
    done, failed = [], []
    for p in plan:
        if a.get("clear_buildings"):
            pl = zoning.seg_polyline(p["new"]); xs_ = [q[0] for q in pl]; zs_ = [q[1] for q in pl]
            bs = rpc("query_buildings", {"xmin": min(xs_) - 30, "xmax": max(xs_) + 30, "zmin": min(zs_) - 30, "zmax": max(zs_) + 30, "limit": 500})["buildings"]
            grow = [b["id"] for b in bs if any(k in b["type"] for k in ("Residential", "Commercial", "Office", "Mixed"))
                    and min(math.dist((b["position"][0], b["position"][2]), q) for q in pl) < 16]
            if grow: rpc("bulldoze", {"ids": grow, "roads": False, "buildings": True, "timeout_s": 90}); p["cleared_buildings"] = len(grow)
        r = rpc("bulldoze", {"ids": [p["old"]], "roads": True, "buildings": False, "timeout_s": 90})
        undo_id = r.get("undo_id")
        pre = rpc("place_roads", {"segments": [p["new"]], "snap_tolerance": 1.0, "dry_run": True, "timeout_s": 60})
        if pre.get("errors"):
            if undo_id is not None: rpc("undo", {"expect_id": undo_id, "timeout_s": 90})
            failed.append({"at": p["at"], "angle_deg": p["angle_deg"], "why": pre.get("error_reasons")}); continue
        rr = rpc("place_roads", {"segments": [p["new"]], "snap_tolerance": 1.0, "timeout_s": 60})
        (done if rr.get("applied") else failed).append({"at": p["at"], "angle_deg": p["angle_deg"]})
        time.sleep(1)
    out.update({"squared": done, "failed": failed})
    return out
