"""Geometry helpers for the construction tools (cs2_place_path / cs2_place_pair / cs2_place_lines / cs2_ramp).

Everything here only turns geometry the caller already decided on into place_roads segments: smoothing a polyline
through the caller's knots, offsetting it, cutting it into cubic pieces with joints where the caller wants them,
elevation profiles, and the junction bookkeeping place_roads needs (shared, identical coordinates at every contact).
No layout decisions are made here. Coordinates are world x/z metres.
"""
import math

Pt = tuple


# ----------------------------------------------------------------------------- smoothing
def catmull_rom(knots, step=10.0):
    """Centripetal Catmull-Rom through `knots` (list of [x,z]); dense samples roughly `step` m apart, ends included."""
    P = [tuple(map(float, k)) for k in knots]
    if len(P) < 2: raise ValueError("need at least 2 points")
    if len(P) == 2:
        n = max(1, int(math.dist(*P) // step))
        return [(P[0][0] + (P[1][0] - P[0][0]) * i / n, P[0][1] + (P[1][1] - P[0][1]) * i / n) for i in range(n + 1)]
    Q = [(2 * P[0][0] - P[1][0], 2 * P[0][1] - P[1][1])] + P + [(2 * P[-1][0] - P[-2][0], 2 * P[-1][1] - P[-2][1])]
    out = []
    for i in range(1, len(Q) - 2):
        p0, p1, p2, p3 = Q[i - 1], Q[i], Q[i + 1], Q[i + 2]
        def tj(ti, a, b): return ti + max(math.dist(a, b), 1e-6) ** 0.5
        t0 = 0.0; t1 = tj(t0, p0, p1); t2 = tj(t1, p1, p2); t3 = tj(t2, p2, p3)
        m = max(2, int(math.dist(p1, p2) / step))
        for k in range(m):
            t = t1 + (t2 - t1) * k / m
            A1 = [(t1 - t) / (t1 - t0) * p0[j] + (t - t0) / (t1 - t0) * p1[j] for j in (0, 1)]
            A2 = [(t2 - t) / (t2 - t1) * p1[j] + (t - t1) / (t2 - t1) * p2[j] for j in (0, 1)]
            A3 = [(t3 - t) / (t3 - t2) * p2[j] + (t - t2) / (t3 - t2) * p3[j] for j in (0, 1)]
            B1 = [(t2 - t) / (t2 - t0) * A1[j] + (t - t0) / (t2 - t0) * A2[j] for j in (0, 1)]
            B2 = [(t3 - t) / (t3 - t1) * A2[j] + (t - t1) / (t3 - t1) * A3[j] for j in (0, 1)]
            out.append(tuple((t2 - t) / (t2 - t1) * B1[j] + (t - t1) / (t2 - t1) * B2[j] for j in (0, 1)))
    out.append(P[-1])
    return out


def cumlen(line):
    c = [0.0]
    for i in range(1, len(line)): c.append(c[-1] + math.dist(line[i - 1], line[i]))
    return c


def tangent(line, i):
    a = line[max(0, i - 1)]; b = line[min(len(line) - 1, i + 1)]; n = math.dist(a, b) or 1.0
    return ((b[0] - a[0]) / n, (b[1] - a[1]) / n)


def offset_line(line, d, taper_start=0.0, taper_end=0.0):
    """Offset `d` m to the LEFT of travel (negative = right). Optional linear-ease tapers (m) from 0 at the ends."""
    L = cumlen(line); tot = L[-1]; out = []
    for i, p in enumerate(line):
        tx, tz = tangent(line, i); nx, nz = -tz, tx          # left normal in the x/z plane (z up on the map)
        k = 1.0
        if taper_start > 0: k = min(k, 1 - (1 - min(1.0, L[i] / taper_start)) ** 2)
        if taper_end > 0: k = min(k, 1 - (1 - min(1.0, (tot - L[i]) / taper_end)) ** 2)
        out.append((p[0] + nx * d * k, p[1] + nz * d * k))
    return out


def min_radius(line):
    rmin = 1e9
    for i in range(2, len(line) - 2):
        a, b, c = line[i - 2], line[i], line[i + 2]
        ab, bc, ca = math.dist(a, b), math.dist(b, c), math.dist(c, a)
        cross = abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
        if cross > 1e-6: rmin = min(rmin, ab * bc * ca / (2 * cross))
    return rmin


# ----------------------------------------------------------------------------- elevation profiles
def elevation_fn(profile, total):
    """profile: None | number | [[distance_m, elevation_m], ...] (negative distance counts from the end). Linear between."""
    if profile is None: return lambda d: 0.0
    if isinstance(profile, (int, float)): return lambda d: float(profile)
    pts = sorted(((p[0] if p[0] >= 0 else total + p[0]), float(p[1])) for p in profile)
    def f(d):
        if d <= pts[0][0]: return pts[0][1]
        for (d0, e0), (d1, e1) in zip(pts, pts[1:]):
            if d <= d1: return e0 + (e1 - e0) * (d - d0) / ((d1 - d0) or 1)
        return pts[-1][1]
    return f


# ----------------------------------------------------------------------------- cutting into cubic pieces
def cut_indices(line, piece_m=110.0, avoid=None, avoid_m=25.0, force=None):
    """Indices where joints (nodes) go: roughly every piece_m, never within avoid_m of an `avoid` point
    (e.g. where a street or rail must pass under an elevated road: joints carry pillars), and at every `force` point."""
    L = cumlen(line); avoid = [tuple(p) for p in (avoid or [])]
    def ok(i): return all(math.dist(line[i], a) >= avoid_m for a in avoid)
    cuts = [0]; last = 0
    for i in range(1, len(line) - 1):
        if L[i] - L[last] >= piece_m and ok(i): cuts.append(i); last = i
    for f in force or []:
        j = min(range(len(line)), key=lambda k: math.dist(line[k], f))
        if 0 < j < len(line) - 1: cuts.append(j)
    cuts = sorted(set(cuts + [len(line) - 1]))
    clean = [cuts[0]]
    for c in cuts[1:]:
        if L[c] - L[clean[-1]] < 8 and c != cuts[-1]: continue
        clean.append(c)
    return clean


def to_segments(line, cuts, typ, elev=None, reverse=False, nd=3):
    """Cubic place_roads segments between consecutive cut indices. elev: function(distance along `line`) -> metres."""
    L = cumlen(line); out = []
    for a, b in zip(cuts, cuts[1:]):
        p0, p3 = line[a], line[b]; D = math.dist(p0, p3)
        t0, t3 = tangent(line, a), tangent(line, b)
        c1 = (p0[0] + t0[0] * D / 3, p0[1] + t0[1] * D / 3); c2 = (p3[0] - t3[0] * D / 3, p3[1] - t3[1] * D / 3)
        s = {"start": [round(v, nd) for v in p0], "end": [round(v, nd) for v in p3],
             "controls": [[round(v, nd) for v in c1], [round(v, nd) for v in c2]], "type": typ}
        if elev:
            e0, e1 = elev(L[a]), elev(L[b])
            if abs(e0) > 0.05 or abs(e1) > 0.05: s["elevation"] = [round(e0, 2), round(e1, 2)]
        if reverse:
            s = dict(s, start=s["end"], end=s["start"], controls=s["controls"][::-1])
            if "elevation" in s: s["elevation"] = s["elevation"][::-1]
        out.append(s)
    return out[::-1] if reverse else out


# ----------------------------------------------------------------------------- street-network bookkeeping
def _inter(p, q, r, s):
    d = (q[0] - p[0]) * (s[1] - r[1]) - (q[1] - p[1]) * (s[0] - r[0])
    if abs(d) < 1e-9: return None
    u = ((r[0] - p[0]) * (s[1] - r[1]) - (r[1] - p[1]) * (s[0] - r[0])) / d
    v = ((r[0] - p[0]) * (q[1] - p[1]) - (r[1] - p[1]) * (q[0] - p[0])) / d
    if -1e-6 <= u <= 1 + 1e-6 and -1e-6 <= v <= 1 + 1e-6: return (p[0] + u * (q[0] - p[0]), p[1] + u * (q[1] - p[1]))
    return None


def network_segments(lines, touch_m=1.5, merge_m=0.6, min_piece=1.0, nd=3):
    """lines: [{"points": [[x,z],...], "type": str}] (straight pieces between points).
    Splits every line where it crosses or touches (an end within touch_m of) another line, and gives every junction ONE
    shared coordinate, so place_roads builds real junctions (T-junctions included). Returns straight segments."""
    edges = []
    for li, ln in enumerate(lines):
        pts = [tuple(map(float, p)) for p in ln["points"]]
        for a, b in zip(pts, pts[1:]): edges.append((li, a, b, ln["type"]))
    cuts = {i: [0.0, 1.0] for i in range(len(edges))}
    for i, (li, a, b, _) in enumerate(edges):
        L = math.dist(a, b) or 1.0; ux, uz = (b[0] - a[0]) / L, (b[1] - a[1]) / L
        for j, (lj, c, d, _) in enumerate(edges):
            if li == lj: continue
            x = _inter(a, b, c, d)
            if x is not None:
                cuts[i].append(math.dist(a, x) / L); continue
            for e in (c, d):                                  # another line ends on this one (T junction)
                t = (e[0] - a[0]) * ux + (e[1] - a[1]) * uz
                if 0 < t < L and abs((e[0] - a[0]) * uz - (e[1] - a[1]) * ux) < touch_m: cuts[i].append(t / L)
    reg = []
    def canon(P):
        for q in reg:
            if math.dist(q, P) < merge_m: return q
        reg.append(P); return P
    out = []
    for i, (li, a, b, ty) in enumerate(edges):
        cs = sorted(set(round(c, 6) for c in cuts[i]))
        for u, v in zip(cs, cs[1:]):
            P = (a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u); Q = (a[0] + (b[0] - a[0]) * v, a[1] + (b[1] - a[1]) * v)
            if math.dist(P, Q) < min_piece: continue
            P, Q = canon(P), canon(Q)
            out.append({"start": [round(P[0], nd), round(P[1], nd)], "end": [round(Q[0], nd), round(Q[1], nd)], "type": ty})
    return out


def dist_point_seg(p, a, b):
    """Distance from point p to segment a-b (2D)."""
    dx, dz = b[0] - a[0], b[1] - a[1]; L = dx * dx + dz * dz
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dz) / L))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dz)
