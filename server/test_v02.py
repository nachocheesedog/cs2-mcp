"""Offline checks for the v0.2 geometry helpers."""
import math
from cs2_mcp import circle_segments, rect_corners, TOOLS

def bez(p0, c1, c2, p3, t):
    u = 1 - t
    return [u**3*p0[i] + 3*u*u*t*c1[i] + 3*u*t*t*c2[i] + t**3*p3[i] for i in range(2)]

for n in (4, 8):
    segs = circle_segments([100.0, 200.0], 50.0, n, False)
    worst = max(abs(math.dist(bez(s["start"], *s["controls"], s["end"], t / 20), [100, 200]) - 50) for s in segs for t in range(21))
    closed = math.dist(segs[-1]["end"], segs[0]["start"]) < 1e-6
    ccw = (segs[0]["end"][0] - 100) * 0 + (segs[0]["end"][1] - 200) > 0     # first arc heads towards +z from angle 0 -> CCW
    print(f"circle pieces={n} max radial error={worst:.4f} m closed={closed} ccw={ccw}")
    assert worst < 0.05 * (8 / n) ** 3 and closed and ccw
c = rect_corners([0, 0], 100, 40, 90)
print("rect 100x40 rotated 90:", c)
assert all(abs(abs(p[0]) - 20) < 1e-6 and abs(abs(p[1]) - 50) < 1e-6 for p in c)
names = [t[0] for t in TOOLS]; print(len(names), "tools:", ", ".join(names))
print("v0.2 checks passed")
