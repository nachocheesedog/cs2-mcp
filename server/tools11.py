"""v0.27 (2026-09-26): exact geometry for a road Claude has designed - ramps, flyovers, loops, connectors.

- design_path   from a start pose to an end pose (a point + heading, or a point on an existing road with the road's own
                direction, optionally branching off at branch_deg to one side): the shortest path whose curves are never
                tighter than the design speed's radius (a Dubins path: arcs + straights, true circles), cut into cubic
                pieces for place_roads, with an elevation profile that holds the grade limit and clears every road it
                crosses by clearance_m (over, or under with pass='under'), plus a network_check of the result.
                Returns segments to build with cs2_place_roads - nothing is built here.
Claude picks the topology (which anchors, left or right, over or under); this does the arithmetic.
"""
import math
import survey, tools10

R_EF = tools10.R_EF


def _mod(x): return x % (2 * math.pi)


def _dubins(p0, h0, p1, h1, R, only=None):
    """Shortest Dubins path. Poses (x, z) + heading (radians, 0 = +x, counter-clockwise toward +z).
    -> (word, [(kind, param)]) with kind 'L' (left arc, angle), 'R' (right arc, angle), 'S' (straight, length)."""
    dx, dz = p1[0] - p0[0], p1[1] - p0[1]
    D = math.hypot(dx, dz) / R; th = math.atan2(dz, dx)
    a, b = _mod(h0 - th), _mod(h1 - th)
    sa, sb, ca, cb, cab = math.sin(a), math.sin(b), math.cos(a), math.cos(b), math.cos(a - b)
    out = []
    # LSL
    p2 = 2 + D * D - 2 * cab + 2 * D * (sa - sb)
    if p2 >= 0:
        tmp = math.atan2(cb - ca, D + sa - sb); out.append(("LSL", _mod(-a + tmp), math.sqrt(p2), _mod(b - tmp)))
    # RSR
    p2 = 2 + D * D - 2 * cab + 2 * D * (sb - sa)
    if p2 >= 0:
        tmp = math.atan2(ca - cb, D - sa + sb); out.append(("RSR", _mod(a - tmp), math.sqrt(p2), _mod(-b + tmp)))
    # LSR
    p2 = -2 + D * D + 2 * cab + 2 * D * (sa + sb)
    if p2 >= 0:
        p = math.sqrt(p2); tmp = math.atan2(-ca - cb, D + sa + sb) - math.atan2(-2, p)
        out.append(("LSR", _mod(-a + tmp), p, _mod(-_mod(b) + tmp)))
    # RSL
    p2 = D * D - 2 + 2 * cab - 2 * D * (sa + sb)
    if p2 >= 0:
        p = math.sqrt(p2); tmp = math.atan2(ca + cb, D - sa - sb) - math.atan2(2, p)
        out.append(("RSL", _mod(a - tmp), p, _mod(b - tmp)))
    # RLR
    tmp = (6 - D * D + 2 * cab + 2 * D * (sa - sb)) / 8
    if abs(tmp) <= 1:
        p = _mod(2 * math.pi - math.acos(tmp)); t = _mod(a - math.atan2(ca - cb, D - sa + sb) + p / 2)
        out.append(("RLR", t, p, _mod(a - b - t + p)))
    # LRL
    tmp = (6 - D * D + 2 * cab + 2 * D * (sb - sa)) / 8
    if abs(tmp) <= 1:
        p = _mod(2 * math.pi - math.acos(tmp)); t = _mod(-a - math.atan2(ca - cb, D + sa - sb) + p / 2)
        out.append(("LRL", t, p, _mod(_mod(b) - a - t + p)))
    if only: out = [o for o in out if o[0] in only]
    if not out: raise ValueError("no Dubins path for these poses / radius (try another word or a smaller radius)")
    w, t, p, q = min(out, key=lambda o: o[1] + o[2] + o[3])
    parts = []
    for kind, val in zip(w, (t, p, q)):
        parts.append((kind, val * R if kind == "S" else val))
    return w, parts


def _walk(p0, h0, parts, R):
    """Poses along the path; yields pieces (kind, start pose, end pose, value)."""
    x, z, h = p0[0], p0[1], h0
    for kind, v in parts:
        if kind == "S":
            nx, nz, nh = x + v * math.cos(h), z + v * math.sin(h), h
        else:
            s = 1 if kind == "L" else -1
            cx, cz = x - s * R * math.sin(h), z + s * R * math.cos(h)
            nh = h + s * v
            nx, nz = cx + s * R * math.sin(nh), cz - s * R * math.cos(nh)
        yield kind, (x, z, h), (nx, nz, nh), v
        x, z, h = nx, nz, nh


def _cubics(p0, h0, parts, R, piece_m):
    """Path -> cubic Bezier pieces (arcs split to <= 60 deg and <= piece_m; straights to <= piece_m)."""
    segs = []
    for kind, (x, z, h), _, v in _walk(p0, h0, parts, R):
        if kind == "S":
            if v < 0.5: continue
            n = max(1, math.ceil(v / piece_m))
            for i in range(n):
                a_ = (x + v * i / n * math.cos(h), z + v * i / n * math.sin(h)); b_ = (x + v * (i + 1) / n * math.cos(h), z + v * (i + 1) / n * math.sin(h))
                segs.append({"start": a_, "end": b_})
            continue
        if v < 1e-3: continue
        s = 1 if kind == "L" else -1
        cx, cz = x - s * R * math.sin(h), z + s * R * math.cos(h)
        n = max(1, math.ceil(v / math.radians(60)), math.ceil(v * R / piece_m))
        for i in range(n):
            ha, hb = h + s * v * i / n, h + s * v * (i + 1) / n
            A = (cx + s * R * math.sin(ha), cz - s * R * math.cos(ha)); B = (cx + s * R * math.sin(hb), cz - s * R * math.cos(hb))
            k = 4 / 3 * math.tan(v / n / 4) * R                  # circular arc as a cubic
            c1 = (A[0] + k * math.cos(ha), A[1] + k * math.sin(ha)); c2 = (B[0] - k * math.cos(hb), B[1] - k * math.sin(hb))
            segs.append({"start": A, "end": B, "controls": [c1, c2]})
    return segs


def _anchor(rpc, spec, net, is_start):
    """-> ((x, z), heading rad, absolute y or None). spec: {pos:[x,z], heading_deg} | {edge, at:[x,z], branch_deg?, side?,
    reverse?} (a point on an existing road, the road's direction; branch_deg turns off it toward side)."""
    if "edge" in spec:
        e = next((e for e in net["edges"] if e["id"] == spec["edge"]), None)
        if not e: raise ValueError(f"road {spec['edge']} not found near the path")
        c = e["curve"]; at = spec.get("at")
        best, bt = 1e18, 0.0
        for i in range(201):
            t = i / 200; P = tools10._bez(c, t)
            d = math.dist((P[0], P[2]), at) if at else (0 if (t == 0 and spec.get("end") == "start") or (t == 1 and spec.get("end", "end") == "end") else 1e9)
            if d < best: best, bt = d, t
        P = tools10._bez(c, bt); Q = tools10._bez(c, min(1, bt + 0.01)); Pm = tools10._bez(c, max(0, bt - 0.01))
        h = math.atan2(Q[2] - Pm[2], Q[0] - Pm[0])
        if spec.get("reverse"): h += math.pi
        if spec.get("branch_deg"):
            s = 1 if spec.get("side", "right") == "left" else -1   # left = counter-clockwise (x -> z)
            h += s * math.radians(spec["branch_deg"]) * (1 if is_start else -1)
        return (P[0], P[2]), h, P[1]
    return tuple(spec["pos"]), math.radians(spec.get("heading_deg", 0.0)), spec.get("y")


def design_path(rpc, a):
    typ = a.get("type", "Highway Oneway - 1 lane")
    v = float(a.get("design_speed_kmh", 60)); R = float(a.get("radius_m") or v * v / (127 * R_EF))
    grade = float(a.get("max_grade", 0.06)); clear = float(a.get("clearance_m", 7.0)); piece = float(a.get("piece_m", 90))
    S, E = a["start"], a["end"]
    guess = [S.get("pos") or S.get("at"), E.get("pos") or E.get("at")]
    pad = 400
    area = survey.Area(min(p[0] for p in guess if p) - pad, min(p[1] for p in guess if p) - pad,
                       max(p[0] for p in guess if p) + pad, max(p[1] for p in guess if p) + pad)
    net = rpc("road_network", dict(area.params, limit=30000))
    p0, h0, y0 = _anchor(rpc, S, net, True)
    p1, h1, y1 = _anchor(rpc, E, net, False)
    word, parts = _dubins(p0, h0, p1, h1, R, a.get("words"))
    segs = _cubics(p0, h0, parts, R, piece)
    L = sum(v_ if k == "S" else v_ * R for k, v_ in parts)

    # ---- elevation: absolute heights at the piece joints, linear between the anchors, then pushed over / under crossings
    joints = [s["start"] for s in segs] + [segs[-1]["end"]]
    s_at = [0.0]
    for sg in segs: s_at.append(s_at[-1] + (math.dist(sg["start"], sg["end"]) if "controls" not in sg else _arc_len(sg)))
    ground = [q[1] for q in rpc("terrain_height", {"points": [list(p) for p in joints]})["points"]]
    ya = y0 if y0 is not None else ground[0] + float(S.get("elevation", 0))
    yb = y1 if y1 is not None else ground[-1] + float(E.get("elevation", 0))
    Ltot = s_at[-1] or 1
    y = [ya + (yb - ya) * s / Ltot for s in s_at]
    # crossings: where the path crosses an existing road (not at the anchors) it must pass `clear` over (or under)
    under = a.get("pass", "over") == "under"
    need = []                                             # (s, required y, road)
    for e in net["edges"]:
        if any(k in e["type"] for k in ("Pipe", "Power", "voltage", "Cable")): continue
        ep = [tools10._bez(e["curve"], i / 16) for i in range(17)]
        for i, sg in enumerate(segs):
            pts = _seg_pts(sg, 8)
            for j in range(len(pts) - 1):
                for m in range(len(ep) - 1):
                    x = _x2(pts[j], pts[j + 1], (ep[m][0], ep[m][2]), (ep[m + 1][0], ep[m + 1][2]))
                    if not x: continue
                    s_here = s_at[i] + (s_at[i + 1] - s_at[i]) * (j + x[0]) / (len(pts) - 1)
                    if s_here < 30 or s_here > Ltot - 30: continue
                    ye = ep[m][1] + (ep[m + 1][1] - ep[m][1]) * x[1]
                    need.append((s_here, ye - clear if under else ye + clear, e["id"], e["type"]))
    # profile: the straight line between the anchors, raised (over) / lowered (under) just enough for every crossing with
    # ramps at the grade limit, then held inside what the grade allows from both anchors
    ctrl = sorted((s_, y_) for s_, y_, _, _ in need)
    infeasible = []
    for s_, y_ in ctrl:
        lo = max(ya - grade * s_, yb - grade * (Ltot - s_)); hi = min(ya + grade * s_, yb + grade * (Ltot - s_))
        if not lo - 0.01 <= y_ <= hi + 0.01: infeasible.append({"at_m": round(s_), "needs_y": round(y_, 1), "reachable": [round(lo, 1), round(hi, 1)]})
    def prof(s_):
        yl = ya + (yb - ya) * s_ / Ltot
        for sc, yc in ctrl:
            yl = max(yl, yc - grade * abs(s_ - sc)) if not under else min(yl, yc + grade * abs(s_ - sc))
        lo = max(ya - grade * s_, yb - grade * (Ltot - s_)); hi = min(ya + grade * s_, yb + grade * (Ltot - s_))
        return min(max(yl, lo), hi)
    y = [prof(s_) for s_ in s_at]
    for i, sg in enumerate(segs):
        sg["type"] = typ
        e0, e1 = round(y[i] - ground[i], 2), round(y[i + 1] - ground[i + 1], 2)
        if abs(e0) > 0.3 or abs(e1) > 0.3: sg["elevation"] = [e0, e1]
        sg["start"] = [round(sg["start"][0], 2), round(sg["start"][1], 2)]; sg["end"] = [round(sg["end"][0], 2), round(sg["end"][1], 2)]
        if "controls" in sg: sg["controls"] = [[round(c[0], 2), round(c[1], 2)] for c in sg["controls"]]
    max_g = max((abs(y[i + 1] - y[i]) / max(1e-6, s_at[i + 1] - s_at[i]) for i in range(len(segs))), default=0)
    out = {"type": typ, "word": word, "radius_m": round(R), "design_speed_kmh": v, "length_m": round(L),
           "turns_deg": [round(math.degrees(v_)) for k, v_ in parts if k != "S"], "straights_m": [round(v_) for k, v_ in parts if k == "S"],
           "start": {"pos": [round(p0[0], 1), round(p0[1], 1)], "heading_deg": round(math.degrees(h0) % 360, 1), "y": round(ya, 1)},
           "end": {"pos": [round(p1[0], 1), round(p1[1], 1)], "heading_deg": round(math.degrees(h1) % 360, 1), "y": round(yb, 1)},
           "max_grade_pct": round(100 * max_g, 1), "grade_limit_pct": round(100 * grade, 1),
           "crossings": [{"at_m": round(s_), "road": rid, "type": t, "needs_y": round(y_, 1)} for s_, y_, rid, t in sorted(need)][:20],
           "infeasible_crossings": infeasible, "segments": segs,
           "next": "check it (cs2_network_check segments=...), then cs2_place_roads segments=... (dry_run first)"}
    warn = []
    if max_g > grade + 1e-3: warn.append("grade limit exceeded - lengthen the path (other words / larger radius / move an anchor) or change clearances")
    crossed_anchor = [c for c in need if c[2] in {S.get("edge"), E.get("edge")} | _neighbours(net, {S.get("edge"), E.get("edge")})]
    if crossed_anchor:
        warn.append("the path crosses the road it leaves or joins (or its continuation) before meeting it - wrong side / "
                    "wrong turn direction: force another shape with words (e.g. ['LSL'] or ['RSR']) or move an anchor")
    if infeasible: warn.append(f"{len(infeasible)} crossing(s) cannot get clearance_m within the grade limit")
    if warn: out["warnings"] = warn
    try:
        chk = tools10.network_check(rpc, {"xmin": area.x0, "zmin": area.z0, "xmax": area.x1, "zmax": area.z1, "segments": segs,
                                          "design_speed_kmh": {"ramp": v, "highway": max(v, 80)}})
        mine = {k: [i for i in lst if any(str(r).startswith("draft:") for r in (i.get("roads") or [i.get("road")]))]
                for k, lst in chk["issues"].items()}
        out["check"] = {k: v_ for k, v_ in mine.items() if v_}
    except Exception as ex:
        out["check"] = {"error": str(ex)}
    return out


def _seg_pts(sg, n):
    if "controls" not in sg: return [sg["start"], sg["end"]]
    c = [(sg["start"][0], 0, sg["start"][1]), (sg["controls"][0][0], 0, sg["controls"][0][1]),
         (sg["controls"][1][0], 0, sg["controls"][1][1]), (sg["end"][0], 0, sg["end"][1])]
    return [(p[0], p[2]) for p in (tools10._bez(c, i / n) for i in range(n + 1))]


def _arc_len(sg):
    P = _seg_pts(sg, 16); return sum(math.dist(a, b) for a, b in zip(P, P[1:]))


def _x2(p, q, r, s):
    d = (q[0] - p[0]) * (s[1] - r[1]) - (q[1] - p[1]) * (s[0] - r[0])
    if abs(d) < 1e-9: return None
    t = ((r[0] - p[0]) * (s[1] - r[1]) - (r[1] - p[1]) * (s[0] - r[0])) / d
    u = ((r[0] - p[0]) * (q[1] - p[1]) - (r[1] - p[1]) * (q[0] - p[0])) / d
    return (t, u) if 0 <= t <= 1 and 0 <= u <= 1 else None


def _neighbours(net, ids):
    """Roads sharing a node with any of ids (a carriageway's continuation past the anchor)."""
    ends = {}
    for e in net["edges"]: ends[e["id"]] = (e["start_node"], e["end_node"])
    nodes = {n for i in ids if i in ends for n in ends[i]}
    return {eid for eid, (a_, b_) in ends.items() if (a_ in nodes or b_ in nodes) and eid not in ids}
