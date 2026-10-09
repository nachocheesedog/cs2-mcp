"""Offline checks for the grid planner (no game needed): python test_grid.py"""
import math, collections
from cs2_mcp import plan_grid

W = {"Small Road": 12.0, "Medium Road": 20.0, "Large Road": 32.0}

def check(pattern, arterial=None, every=0):
    segs, info = plan_grid([1000.0, -500.0], 30.0, 5, 4, "Small Road", arterial, every, 6, 12, pattern, W)
    ends = collections.Counter()
    for s in segs:
        ends[tuple(s["start"])] += 1; ends[tuple(s["end"])] += 1
    deg = collections.Counter(ends.values())             # 1 = dead end, 2 = straight join, 3 = T, 4 = cross
    # zoning gap check on the unrotated plan: distance between neighbouring parallel road edges must be 96 m
    s0, _ = plan_grid([0.0, 0.0], 0.0, 5, 4, "Small Road", arterial, every, 6, 12, pattern, W)
    zs = sorted({s["start"][1] for s in s0 if s["start"][1] == s["end"][1]})
    types = {s["start"][1]: s["type"] for s in s0 if s["start"][1] == s["end"][1]}
    gaps = [round(b - a - W[types[a]] / 2 - W[types[b]] / 2, 3) for a, b in zip(zs, zs[1:])]
    print(f"{pattern:9s} arterial={arterial or '-':11s} segs={info['segments']:3d} extent={info['extent_m']} "
          f"road={info['road_length_m']}m junction-degrees={dict(sorted(deg.items()))} zoning_gaps={sorted(set(gaps))}")
    assert all(g == 96.0 for g in gaps), gaps
    assert 1 not in deg, "dead ends present"
    return deg

d_grid = check("grid")
d_stag = check("staggered")
assert d_stag.get(4, 0) < d_grid.get(4, 0) and d_stag.get(3, 0) > d_grid.get(3, 0), "staggered should trade crosses for T-junctions"
check("grid", "Large Road", 2)
check("staggered", "Medium Road", 3)
print("all grid checks passed")
