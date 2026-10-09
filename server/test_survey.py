"""Offline check of survey.py on a synthetic town with planted problems (no game needed)."""
import math
import survey


def straight(a, b, y=0.0):
    return [[a[0], y, a[1]], [a[0] + (b[0] - a[0]) / 3, y, a[1] + (b[1] - a[1]) / 3],
            [a[0] + 2 * (b[0] - a[0]) / 3, y, a[1] + 2 * (b[1] - a[1]) / 3], [b[0], y, b[1]]]


def fake_world():
    nodes, edges = {}, []
    def node(nid, x, z): nodes[nid] = {"id": nid, "pos": [x, 0, z], "degree": 0}
    def edge(eid, s, t, typ="Small Road", w=12.0, flow=0.95):
        a, b = nodes[s]["pos"], nodes[t]["pos"]
        c = straight((a[0], a[2]), (b[0], b[2]))
        edges.append({"id": eid, "type": typ, "width": w, "length": math.dist((a[0], a[2]), (b[0], b[2])), "curve": c,
                      "start_node": s, "end_node": t, "flow": flow})
        nodes[s]["degree"] += 1; nodes[t]["degree"] += 1
    # a 2x1 block loop plus planted problems
    for nid, x, z in [("1:1", 0, 0), ("2:1", 120, 0), ("3:1", 240, 0), ("4:1", 0, 110), ("5:1", 120, 110), ("6:1", 240, 110),
                      ("7:1", 120, 300),                  # dead end road going north from 5
                      ("8:1", 250, 180),                  # near miss: stops ~6 m short of the 3-6 road? no: road 3-6 is x=240 -> 10m gap minus width
                      ("9:1", 400, 120), ("10:1", 250, 200),
                      ("11:1", 125, 5)]:                  # sharp junction arm from node 2 (almost parallel to 2->3)
        node(nid, x, z)
    edge("100:1", "1:1", "2:1", "Medium Road", 18, 0.5); edge("101:1", "2:1", "3:1"); edge("102:1", "4:1", "5:1"); edge("103:1", "5:1", "6:1")
    edge("104:1", "1:1", "4:1"); edge("105:1", "2:1", "5:1"); edge("106:1", "3:1", "6:1")
    edge("107:1", "5:1", "7:1")
    edge("108:1", "9:1", "8:1")
    edge("110:1", "9:1", "10:1"); edge("111:1", "10:1", "8:1")   # island triangle-ish: 8-9-10 (8 is a corner)
    nodes["2:1"]["degree"] += 0
    edge("112:1", "2:1", "11:1")                                   # short stub, sharp against 2->3? direction (5,5) = 45deg... tweak below
    nodes["11:1"]["pos"] = [160, 0, 3]; edges[-1]["curve"] = straight((120, 0), (160, 3))
    blocks = []
    for e in edges[:4]:                                            # one-sided blocks along the x-direction roads, 6 deep
        c = e["curve"]; x0, z0, x1, z1 = c[0][0], c[0][2], c[3][0], c[3][2]
        L = math.hypot(x1 - x0, z1 - z0); dx, dz = (x1 - x0) / L, (z1 - z0) / L
        nx, nz = -dz, dx                                            # block lies on the +normal side, direction points back to road
        sx = int(L // 8) - 2
        cx, cz = (x0 + x1) / 2 + nx * (6 + 24), (z0 + z1) / 2 + nz * (6 + 24)
        cells = "".join("a" if (i % sx) < sx // 2 else "." for i in range(sx * 6))
        cells = cells[:sx * 4] + "#" * (sx * 2)                     # only 4 deep visible
        blocks.append({"id": "b" + e["id"], "pos": [cx, 0, cz], "dir": [-nx, -nz], "size": [sx, 6], "cells": cells, "road": e["id"]})
    nx_, nz_ = 41, 41
    heights = [[max(0.0, (i - 30) * 3.0) for i in range(nx_)] for j in range(nz_)]   # steep ramp on the east side
    water = [[3.0 if j < 3 else 0.0 for i in range(nx_)] for j in range(nz_)]        # water strip along the south
    grid = {"origin": [-100, -100], "step": 15, "nx": nx_, "nz": nz_, "heights": heights, "water_depth": water}
    blds = [{"id": "900:1", "type": "x", "position": [60, 0, 60], "forward": [0, 1], "lot": [4, 4]}]
    return {"road_network": {"edges": edges, "nodes": list(nodes.values())},
            "zone_blocks": {"blocks": blocks, "legend": {"a": "Residential Low"}},
            "query_buildings": {"buildings": blds}, "terrain_grid": grid}


def main():
    data = fake_world()
    rpc = lambda method, params=None: data[method]
    area = survey.Area(-100, -100, 500, 500)
    w = survey.World(rpc, area)
    f = survey.check(w)
    rd = f["roads"]
    assert rd["dead_ends"]["count"] >= 1, rd["dead_ends"]
    assert any(d["node"] == "7:1" for d in rd["dead_ends"]["list"])
    assert rd["near_miss_connections"]["count"] >= 1, rd["near_miss_connections"]
    assert rd["sharp_junctions"]["count"] >= 1, rd["sharp_junctions"]
    assert rd["islands"]["count"] >= 1, rd["islands"]
    assert rd["congested"]["count_below_0_75"] == 1
    zn = f["zoning"]
    assert zn["frontage_depth_histogram"]["4"] > 0 and zn["hidden_cells"] > 0
    assert f["unused_land"]["flat_unused_m2_within_zoning_reach"] > 0
    img = survey.render_map(w, px=900, facts=f, plan=[{"start": [0, 200], "end": [240, 200]}], marks=[{"pos": [120, 200], "label": "draft"}])
    path = survey.save(img, "test_map.png")
    print("counts", {k: v["count"] for k, v in rd.items() if isinstance(v, dict) and "count" in v},
          "efficiency", f["unused_land"]["zoning_efficiency"], "pockets", len(f["unused_land"]["largest_pockets"]))
    print("survey checks passed ->", path)


if __name__ == "__main__":
    main()
