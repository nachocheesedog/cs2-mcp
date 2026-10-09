"""cs2_highway_fork (2026-09-28): a proper elevated highway Y from one two-way trunk to two two-way branches.

The game refuses a two-way 6-lane highway forking into two two-way 6-lane highways at one node ("Invalid Shape"), so a
real split works like the game's own interchanges: the trunk separates into its two one-way carriageways, the OUTBOUND
carriageway diverges into one leg per branch, the INBOUND carriageway is fed by a merge of the branches' inbound legs,
and every leg rejoins its branch's two-way road at that road's end node. With right-hand traffic the outbound leg to the
LEFT branch and the inbound leg from the RIGHT branch cross - that crossing is a flyover (the outbound-left leg rises
flyover_m above the viaduct level around the crossing; the other stays at deck level).

Geometry is plain cubic Hermite pieces (tangent-continuous, ends exactly on the nodes, pieces <= piece_m), heights are a
smooth profile clamped to max_grade; the plan is checked with join_check (direction-safe joins) and road_profile
(clearance at the crossing, grades), then dry-run in the game. Nothing is removed by this tool: clear the old junction with
cs2_road_snapshot + bulldoze first (and cs2_road_restore to go back).
"""
import math
import tools13

OW = "Highway Oneway - 3 lanes"


def _unit(a): return (math.cos(math.radians(a)), math.sin(math.radians(a)))


def _node_end(net, edge_id, at):
    """(pos xz, y, heading pointing AWAY from the edge = into the junction) of the edge end nearest `at`"""
    e = next(x for x in net if x["id"] == edge_id)
    c = e["curve"]
    ds = math.dist((c[0][0], c[0][2]), at); de = math.dist((c[-1][0], c[-1][2]), at)
    if ds < de:   # start end: away from the edge = opposite the start tangent
        p, q = c[0], c[1]
    else:
        p, q = c[-1], c[-2]
    h = math.degrees(math.atan2(p[2] - q[2], p[0] - q[0]))
    return (p[0], p[2]), p[1], h, e


def _hermite(p0, h0, p1, h1, k=0.38):
    L = math.dist(p0, p1); t0 = _unit(h0); t1 = _unit(h1)
    c1 = (p0[0] + t0[0] * L * k, p0[1] + t0[1] * L * k); c2 = (p1[0] - t1[0] * L * k, p1[1] - t1[1] * L * k)
    return [p0, c1, c2, p1]


def _bez(P, t):
    w = ((1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3)
    return (sum(a * p[0] for a, p in zip(w, P)), sum(a * p[1] for a, p in zip(w, P)))


def _split(P, n):
    """cut one cubic into n cubic pieces (de Casteljau at equal t)"""
    out = []; cur = P
    for i in range(n, 0, -1):
        t = 1.0 / i
        a, b, c, d = cur
        ab = ((1 - t) * a[0] + t * b[0], (1 - t) * a[1] + t * b[1]); bc = ((1 - t) * b[0] + t * c[0], (1 - t) * b[1] + t * c[1])
        cd = ((1 - t) * c[0] + t * d[0], (1 - t) * c[1] + t * d[1])
        abc = ((1 - t) * ab[0] + t * bc[0], (1 - t) * ab[1] + t * bc[1]); bcd = ((1 - t) * bc[0] + t * cd[0], (1 - t) * bc[1] + t * cd[1])
        m = ((1 - t) * abc[0] + t * bcd[0], (1 - t) * abc[1] + t * bcd[1])
        out.append([a, ab, abc, m]); cur = [m, bcd, cd, d]
    return out


def _length(P, n=40): return sum(math.dist(_bez(P, i / n), _bez(P, (i + 1) / n)) for i in range(n))


def _min_radius(P, n=40):
    best = 1e9
    for i in range(1, n):
        a, b, c = _bez(P, (i - 1) / n), _bez(P, i / n), _bez(P, (i + 1) / n)
        ab, bc, ca = math.dist(a, b), math.dist(b, c), math.dist(c, a)
        area = abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / 2
        if area > 1e-6: best = min(best, ab * bc * ca / (4 * area))
    return best


def _leg(name, p0, h0, e0, p1, h1, e1, piece_m, bump=None, hold_end=0.0):
    """segments for one leg: heights linear e0 -> e1, plus an optional flyover bump (centre_frac, height, half_len_frac)"""
    P = _hermite(p0, h0, p1, h1); L = _length(P); n = max(1, math.ceil(L / piece_m))
    parts = _split(P, n); segs = []
    def el(f):
        g = min(1.0, f / (1.0 - hold_end)) if hold_end else f        # reach e1 early and hold it for the last hold_end
        g = 0.5 - 0.5 * math.cos(math.pi * g) if hold_end else g      # eased so the grade stays gentle
        v = e0 + (e1 - e0) * g
        if bump:
            c, hgt, hw = bump
            x = (f - c) / hw
            if abs(x) < 1: v += hgt * (0.5 + 0.5 * math.cos(math.pi * x))
        return round(v, 2)
    for i, q in enumerate(parts):
        segs.append({"start": [round(q[0][0], 2), round(q[0][1], 2)], "end": [round(q[3][0], 2), round(q[3][1], 2)],
                     "controls": [[round(q[1][0], 2), round(q[1][1], 2)], [round(q[2][0], 2), round(q[2][1], 2)]], "type": OW,
                     "elevation": [el(i / n), el((i + 1) / n)], "leg": name})
    return segs, {"leg": name, "length_m": round(L), "min_radius_m": round(_min_radius(P)), "pieces": n}


def _leg_via(name, wps, e0, e1, piece_m, bump=None, hold_end=0.0):
    """leg through waypoints [(xz, heading), ...]: one Hermite span per pair, heights along the TOTAL length"""
    spans = [_hermite(wps[i][0], wps[i][1], wps[i + 1][0], wps[i + 1][1]) for i in range(len(wps) - 1)]
    parts = []
    for P in spans:
        parts += _split(P, max(1, math.ceil(_length(P) / piece_m)))
    lens = [_length(q, 12) for q in parts]; tot = sum(lens) or 1.0
    def el(f):
        g = min(1.0, f / (1.0 - hold_end)) if hold_end else f
        g = 0.5 - 0.5 * math.cos(math.pi * g) if hold_end else g
        v = e0 + (e1 - e0) * g
        if bump:
            c, hgt, hw = bump; x = (f - c) / hw
            if abs(x) < 1: v += hgt * (0.5 + 0.5 * math.cos(math.pi * x))
        return round(v, 2)
    segs, acc = [], 0.0
    for q, L in zip(parts, lens):
        f0, f1 = acc / tot, (acc + L) / tot; acc += L
        segs.append({"start": [round(q[0][0], 2), round(q[0][1], 2)], "end": [round(q[3][0], 2), round(q[3][1], 2)],
                     "controls": [[round(q[1][0], 2), round(q[1][1], 2)], [round(q[2][0], 2), round(q[2][1], 2)]], "type": OW,
                     "elevation": [el(f0), el(f1)], "leg": name})
    return segs, {"leg": name, "length_m": round(tot), "min_radius_m": round(min(_min_radius(P) for P in spans)), "pieces": len(parts)}


def highway_fork(rpc, a):
    """a: trunk {edge, at}, branches [{edge, at}, {edge, at}] (left, right as seen driving outbound - auto-sorted),
    split [x, z] (where the outbound carriageway diverges), merge [x, z] (where the inbound one joins, default = split),
    deck_m (viaduct height above ground, default 18), sep_m (carriageway spacing, default 20), diverge_deg (default 12),
    flyover_m (default 10), piece_m (default 70), design_speed_kmh (60), plan_only / dry_run / build."""
    pts_all = [a["trunk"]["at"]] + [b["at"] for b in a["branches"]] + [a["split"]]
    xs = [p[0] for p in pts_all]; zs = [p[1] for p in pts_all]
    net = rpc("road_network", {"xmin": min(xs) - 300, "xmax": max(xs) + 300, "zmin": min(zs) - 300, "zmax": max(zs) + 300, "limit": 40000}, timeout=180)["edges"]
    T, Ty, Th, _ = _node_end(net, a["trunk"]["edge"], a["trunk"]["at"])
    br = [_node_end(net, b["edge"], b["at"]) for b in a["branches"]]
    S = tuple(a["split"]); M = tuple(a.get("merge") or a["split"])
    deck = a.get("deck_m", 18.0); sep = a.get("sep_m", 24.0); deck_in = a.get("deck_in_m", deck - 9.0); sep = a.get("sep_m", 20.0); dv = a.get("diverge_deg", 12.0); fly = a.get("flyover_m", 10.0)
    piece = a.get("piece_m", 70.0)
    gh = {k: v[1] for k, v in zip(("T", "S", "B0", "B1"), rpc("terrain_height", {"points": [list(T), list(S), list(br[0][0]), list(br[1][0])]}, timeout=60)["points"])}
    eT = Ty - gh["T"]; eB = [br[0][1] - gh["B0"], br[1][1] - gh["B1"]]
    # left / right branch as seen driving from the trunk towards the split
    hd = math.degrees(math.atan2(S[1] - T[1], S[0] - T[0])); u = _unit(hd); left = (-u[1], u[0])
    side = lambda p: (p[0] - S[0]) * left[0] + (p[1] - S[1]) * left[1]
    if side(br[0][0]) < side(br[1][0]): br = [br[1], br[0]]; eB = [eB[1], eB[0]]
    L_, R_ = br                                           # L_ = left branch, R_ = right branch
    rt = (-left[0], -left[1])
    Sr = (S[0] + rt[0] * sep / 2, S[1] + rt[1] * sep / 2)   # outbound (right-hand traffic: right side) diverge point
    Ml = (M[0] + left[0] * sep / 2, M[1] + left[1] * sep / 2)  # inbound merge point
    legs, info = [], []
    def put(res): legs.extend(res[0]); info.append(res[1])
    G = a.get("guide_m", 60.0)                                          # the guide point that sets the carriageway spacing
    off = lambda p, h, d, side: (p[0] + _unit(h)[0] * d + (-_unit(h)[1]) * side * sep / 2, p[1] + _unit(h)[1] * d + _unit(h)[0] * side * sep / 2)
    # trunk: outbound on the right of Th, inbound on the left; both hang on the trunk end node
    put(_leg_via("out_trunk", [(T, Th), (off(T, Th, G, -1), Th), (Sr, hd)], eT, deck, piece))
    put(_leg_via("in_trunk", [(Ml, hd + 180), (off(T, Th, G, +1), Th + 180), (T, Th + 180)], deck_in, eT, piece, hold_end=a.get("in_hold", 0.35)))
    # branch ends: uB = direction INTO the branch; outbound arrives on its right, inbound leaves on its left
    def bguides(X):
        uB = X[2] + 180
        return off(X[0], uB, -G, -1), off(X[0], uB, -G, +1), uB
    qoR, qiR, uR = bguides(R_); qoL, qiL, uL = bguides(L_)
    put(_leg_via("out_right", [(Sr, hd - dv), (qoR, uR), (R_[0], uR)], deck, eB[1], piece))
    put(_leg_via("in_left", [(L_[0], uL + 180), (qiL, uL + 180), (Ml, hd + 180 + dv)], eB[0], deck_in, piece))
    put(_leg_via("in_right", [(R_[0], uR + 180), (qiR, uR + 180), (Ml, hd + 180 - dv)], eB[1], deck_in, piece))
    wl = [(Sr, hd + dv), (qoL, uL), (L_[0], uL)]
    probe, _ = _leg_via("probe", wl, deck, eB[0], piece)
    A = tools13._pts(probe, step=4.0); Lp = sum(math.dist(A[i][:2], A[i + 1][:2]) for i in range(len(A) - 1)) or 1
    fr, hi = [], []
    for other in ("in_left", "in_right", "in_trunk"):
        B = tools13._pts([x for x in legs if x["leg"] == other], step=4.0); acc = 0.0
        for i in range(len(A) - 1):
            for j in range(len(B) - 1):
                r = tools13._seg_x(A[i][:2], A[i + 1][:2], B[j][:2], B[j + 1][:2])
                if r and math.dist(A[i][:2], A[-1][:2]) > 30:
                    fr.append((acc + r[0] * math.dist(A[i][:2], A[i + 1][:2])) / Lp); hi.append(B[j][3] + r[1] * (B[j + 1][3] - B[j][3]))
            acc += math.dist(A[i][:2], A[i + 1][:2])
    bump = None
    if fr:
        c = (min(fr) + max(fr)) / 2; hw = max((max(fr) - min(fr)) / 2 + 0.18, a.get("flyover_len_frac", 0.32))
        own = deck + (eB[0] - deck) * c; need = max(hi) + a.get("min_clearance_m", 7.0) - own
        if need > 0:
            # keep the bump off both end nodes (a raised split node no longer matches the trunk -> "Not Enough Clearance";
            # seen 2026-09-28), then scale it so the lift at every crossing still reaches `need`
            hw = min(hw, 0.45); c = min(max(c, hw + 0.03), 0.97 - hw)
            k = min(0.5 + 0.5 * math.cos(math.pi * min(1.0, abs(f - c) / hw)) for f in fr)
            bump = (c, need / max(k, 0.25), hw)
    put(_leg_via("out_left", wl, deck, eB[0], piece, bump=bump))
    out = {"legs": info, "segments": len(legs), "deck_m": deck, "left_branch": a["branches"][0]["edge"] if br[0] is L_ else a["branches"][1]["edge"]}
    vmin = (a.get("design_speed_kmh", 60) / 3.6) ** 2 / (9.81 * 0.25)
    out["tight_legs"] = [i for i in info if i["min_radius_m"] < vmin * 0.8]
    jc = tools13.join_check(rpc, legs, 3.0); out["join_check"] = jc
    prof = {}
    for name in ("out_left", "in_right"):
        pr = tools13.road_profile(rpc, {"segments": [s for s in legs if s["leg"] == name]})
        prof[name] = {"max_grade_pct": pr["max_grade_pct"], "min_above_ground_m": pr["min_above_ground_m"],
                      "crossings": [c for c in pr["crossings"] if c["problem"]][:6], "chart": pr.get("chart")}
    out["profiles"] = prof
    # draft-vs-draft crossings (the flyover): heights of both legs where their plan lines cross
    gp = lambda name: tools13._pts([s for s in legs if s["leg"] == name], step=4.0)
    xs_ = []
    names = [i["leg"] for i in info]
    for i1 in range(len(names)):
        for i2 in range(i1 + 1, len(names)):
            A, B = gp(names[i1]), gp(names[i2])
            for i in range(len(A) - 1):
                for j in range(len(B) - 1):
                    r = tools13._seg_x(A[i][:2], A[i + 1][:2], B[j][:2], B[j + 1][:2])
                    if not r: continue
                    pa = A[i]; pb = B[j]
                    shared = [q for q in (A[0][:2], A[-1][:2]) if min(math.dist(q, B[0][:2]), math.dist(q, B[-1][:2])) < 5]
                    if any(math.dist(pa[:2], q) < 30 for q in shared): continue          # the legs meet at a junction there
                    ea = A[i][3] + r[0] * (A[i + 1][3] - A[i][3]); eb = B[j][3] + r[1] * (B[j + 1][3] - B[j][3])
                    xs_.append({"legs": [names[i1], names[i2]], "at": [round(pa[0]), round(pa[1])], "heights_m": [round(ea, 1), round(eb, 1)],
                                "clearance_m": round(abs(ea - eb), 1), "problem": abs(ea - eb) < 6.0})
    out["leg_crossings"] = xs_
    # side-by-side clash: two legs within 17 m in plan (not at a shared junction) with a height difference that is
    # neither level (< 1 m) nor a real flyover (>= 6 m) -> the game refuses it ("Not Enough Clearance")
    clash = []
    for i1 in range(len(names)):
        for i2 in range(i1 + 1, len(names)):
            A, B = gp(names[i1]), gp(names[i2])
            shared = [q for q in (A[0][:2], A[-1][:2]) if min(math.dist(q, B[0][:2]), math.dist(q, B[-1][:2])) < 5]
            for pa in A[::2]:
                if any(math.dist(pa[:2], q) < 35 for q in shared): continue
                pb = min(B, key=lambda q: math.dist(q[:2], pa[:2]))
                if math.dist(pa[:2], pb[:2]) < a.get("min_spacing_m", 18.0) and 1.0 < abs(pa[3] - pb[3]) < 6.0:
                    clash.append({"legs": [names[i1], names[i2]], "at": [round(pa[0]), round(pa[1])], "gap_m": round(math.dist(pa[:2], pb[:2]), 1),
                                  "height_diff_m": round(abs(pa[3] - pb[3]), 1)}); break
    out["side_by_side_clashes"] = clash
    clean = [dict((k, v) for k, v in s.items() if k != "leg") for s in legs]
    out["plan"] = clean if a.get("plan_only") else None
    if a.get("plan_only"): return out
    import cs2_mcp
    r = cs2_mcp.t_place_roads({"segments": clean, "dry_run": not a.get("build"), "lattice": "off", "snap_tolerance": 3.0,
                               "zoning_check": False, "follow_camera": False})
    out["game"] = {k: r.get(k) for k in ("applied", "errors", "error_hits", "refused", "conflicts") if k in r}
    return out
