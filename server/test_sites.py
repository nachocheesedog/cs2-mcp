"""Offline check of the district sites (v0.46, after the first live test) against a lived-in fake town: every street
lined with houses, a highway to the north, a railway to the west, a river to the east, hills, two dead ends to the
south, one gap in the houses on the east side, a stub towards the railway, and city limits (unbought map tiles) east
of x = 520.

    python test_sites.py          (no game needed)

What it shows: a square drawn next to such a town drafts no connected grid (the first live test's FAIL), while
cs2_district_sites / cs2_build_district site='auto' find the dead ends and build a district joined to the town."""
import collections, json, math, os, shutil, tempfile
import cs2_mcp, tools2, tools16, sites
import test_build_district as T

BridgeError = cs2_mcp.BridgeError


class RealTown(T.FakeGame):
    WIDTHS = dict(T.FakeGame.WIDTHS, **{"Train Track": 8.0})
    LIMITS = (-320.0, -900.0, 520.0, 1600.0)           # the bought map tiles (x0, z0, x1, z1)
    GAP = (800.0, 150.0, 230.0)                        # east street x = 800: no houses on its east side for z in 150..230

    def __init__(self):
        self.mod_version = "0.45"
        self.nodes, self.edges, self.blocks, self.buildings, self.districts = {}, {}, {}, {}, {}
        self.undo, self.nid, self.uid, self.speed, self.codes = [], 1, 1, 1.0, {}
        self.money, self.broke = 1e6, False
        self.calls = collections.Counter()
        self.camera = [400.0, 0.0, 180.0]
        xs, zs = (0, 200, 400, 600, 800), (0, 120, 240, 360)
        for x in xs:
            for z in zs: self._node((x, z))
        for z in zs:
            for x0, x1 in zip(xs, xs[1:]): self._edge(self._at((x0, z)), self._at((x1, z)), "Small Road")
        for x in xs:
            for z0, z1 in zip(zs, zs[1:]): self._edge(self._at((x, z0)), self._at((x, z1)), "Small Road")
        for a, b in (((200, 0), (200, -100)), ((600, 0), (600, -60)), ((0, 240), (-150, 240))):   # dead ends
            self._edge(self._at(a), self._node(b), "Small Road")
        self._edge(self._node((-1500, 560)), self._node((1500, 560)), "Highway")
        self._edge(self._node((-300, -1500)), self._node((-300, 1500)), "Train Track")
        self._houses()
        self.districts["d0"] = {"id": "d0", "name": "Old Town", "polygon": [[-60, -60], [860, -60], [860, 420], [-60, 420]], "policies": []}

    def _edge(self, s, t, typ, controls=None, half=None):
        i = super()._edge(s, t, typ, controls, half)
        if "Track" in typ:                                   # tracks: no Road component (no flow), no zone blocks
            del self.edges[i]["flow"]
            for k in [k for k, b in self.blocks.items() if b["road"] == i]: del self.blocks[k]
        return i

    def _houses(self):
        """A row of 16 x 24 m lots along both sides of every town street (the gap on the east street excepted)."""
        res = self._code("NA Residential Low")
        for e in list(self.edges.values()):
            if e["type"] != "Small Road": continue
            (x0, z0), (x1, z1) = T.bez(e["curve"], 0), T.bez(e["curve"], 1)
            L = math.hypot(x1 - x0, z1 - z0); tx, tz = (x1 - x0) / L, (z1 - z0) / L
            for side in (1, -1):
                nx, nz = -tz * side, tx * side
                s = 16.0
                while s <= L - 16.0:
                    px, pz = x0 + tx * s, z0 + tz * s
                    gx, g0, g1 = self.GAP
                    if abs(x0 - gx) < 1 and abs(x1 - gx) < 1 and nx > 0.5 and g0 <= pz <= g1: s += 16.0; continue
                    c = (px + nx * (e["width"] / 2 + 12), pz + nz * (e["width"] / 2 + 12))
                    hid = self._id()
                    self.buildings[hid] = {"id": hid, "type": "NA_ResidentialLow01_L1", "position": [c[0], 0.0, c[1]],
                                           "forward": [-nx, -nz], "lot": [2, 3]}
                    s += 16.0
        for b in self.blocks.values():                        # the cells under the houses: zoned and built
            for k, c in enumerate(self._centres(b)):
                if b["cells"][k] == "#": continue
                if any(math.dist(c, (h["position"][0], h["position"][2])) < 14 for h in self.buildings.values()):
                    b["cells"][k] = res; b["built"][k] = True

    def _inside_limits(self, p):
        x0, z0, x1, z1 = self.LIMITS
        return x0 <= p[0] <= x1 and z0 <= p[1] <= z1

    def m_place_roads(self, p):
        segs = p["segments"]
        out = [s for s in segs if not all(self._inside_limits(q) for q in [s["start"], s["end"]] + list(s.get("controls") or []))]
        if out and not p.get("allow_errors"):
            return {"applied": False, "errors": len(out), "error_reasons": {"Exceeds City Limits": len(out)},
                    "reason": "dry_run" if p.get("dry_run") else "preview has errors"}
        return super().m_place_roads(p)

    def m_terrain_grid(self, p):
        st = float(p.get("step", 16)); x0, z0 = p["xmin"], p["zmin"]
        nx = int((p["xmax"] - x0) // st) + 1; nz = int((p["zmax"] - z0) // st) + 1
        H, Wd = [], []
        for j in range(nz):
            z = z0 + j * st; hr, wr = [], []
            for i in range(nx):
                x = x0 + i * st
                h = 20.0 + 0.01 * x + (0.3 * (z - 700) if (z > 700 and x > 900) else 0.0)     # hills north-east
                hr.append(round(h, 1)); wr.append(3.0 if 1100 <= x <= 1250 else 0.0)       # the river
            H.append(hr); Wd.append(wr)
        return {"origin": [x0, z0], "step": st, "nx": nx, "nz": nz, "heights": H, "water_depth": Wd}

    def m_list_districts(self, p):
        return {"count": len(self.districts), "districts": [
            dict({"id": i, "name": d["name"], "policies": d.get("policies", [])}, **({"outline": d["polygon"]} if p.get("outlines", True) else {}))
            for i, d in self.districts.items()]}

    def m_query_roads(self, p):
        box = self._box(p)
        R = [{"id": e["id"], "type": e["type"], "start": e["curve"][0], "end": e["curve"][3], "length": e["length"]}
             for e in self.edges.values() if self._inb(box, e["curve"][0][0], e["curve"][0][2]) or self._inb(box, e["curve"][3][0], e["curve"][3][2])]
        return {"total_in_area": len(R), "returned": len(R), "roads": R}

    def connected_to_town(self, edge_ids):
        """Every given edge reaches the town's street grid (node at 400, 120) through streets."""
        adj = collections.defaultdict(set)
        for e in self.edges.values():
            if "flow" in e and "Highway" not in e["type"]: adj[e["start_node"]].add(e["end_node"]); adj[e["end_node"]].add(e["start_node"])
        start = self._at((400, 120)); seen, todo = {start}, [start]
        while todo:
            n = todo.pop()
            for m in adj[n]:
                if m not in seen: seen.add(m); todo.append(m)
        return all(self.edges[i]["start_node"] in seen for i in edge_ids if i in self.edges)


class RotatedTown(RealTown):
    """The same town turned by `deg` around the origin (real cities are rarely lined up with the map axes): every rule
    of the base town (river, hills, city limits) applies in the town's own frame."""
    def __init__(self, deg=33.0):
        super().__init__()
        self.th = math.radians(deg)
        rot = lambda p: self.rot(p)
        for n in self.nodes.values(): n["pos"][0], n["pos"][2] = rot((n["pos"][0], n["pos"][2]))
        for e in self.edges.values():
            for q in e["curve"]: q[0], q[2] = rot((q[0], q[2]))
        for b in self.blocks.values():
            b["pos"][0], b["pos"][2] = rot((b["pos"][0], b["pos"][2])); b["dir"] = list(self.rot(b["dir"], vec=True))
        for h in self.buildings.values():
            h["position"][0], h["position"][2] = rot((h["position"][0], h["position"][2])); h["forward"] = list(self.rot(h["forward"], vec=True))
        for d in self.districts.values(): d["polygon"] = [list(rot(q)) for q in d["polygon"]]
        self.camera = [*rot((self.camera[0], self.camera[2]))]; self.camera.insert(1, 0.0)

    def rot(self, p, vec=False, back=False):
        t = -self.th if back else self.th
        c, s_ = math.cos(t), math.sin(t)
        return (p[0] * c - p[1] * s_, p[0] * s_ + p[1] * c)

    def _inside_limits(self, p): return super()._inside_limits(self.rot(p, back=True))

    def m_terrain_grid(self, p):
        st = float(p.get("step", 16)); x0, z0 = p["xmin"], p["zmin"]
        nx = int((p["xmax"] - x0) // st) + 1; nz = int((p["zmax"] - z0) // st) + 1
        H, Wd = [], []
        for j in range(nz):
            hr, wr = [], []
            for i in range(nx):
                x, z = self.rot((x0 + i * st, z0 + j * st), back=True)
                hr.append(round(20.0 + 0.01 * x + (0.3 * (z - 700) if (z > 700 and x > 900) else 0.0), 1))
                wr.append(3.0 if 1100 <= x <= 1250 else 0.0)
            H.append(hr); Wd.append(wr)
        return {"origin": [x0, z0], "step": st, "nx": nx, "nz": nz, "heights": H, "water_depth": Wd}

    def connected_to_town(self, edge_ids):
        x, z = self.rot((400, 120))
        hub = self._at((x, z))
        adj = collections.defaultdict(set)
        for e in self.edges.values():
            if "flow" in e and "Highway" not in e["type"]: adj[e["start_node"]].add(e["end_node"]); adj[e["end_node"]].add(e["start_node"])
        seen, todo = {hub}, [hub]
        while todo:
            n = todo.pop()
            for m in adj[n]:
                if m not in seen: seen.add(m); todo.append(m)
        return all(self.edges[i]["start_node"] in seen for i in edge_ids if i in self.edges)


def old_style_squares(g, S=800.0):
    """The first live test's candidates: squares past each side of the 3-97 % box of the streets + one at the camera."""
    pts = [(e["curve"][k][0], e["curve"][k][2]) for e in g.edges.values() if e["type"] == "Small Road" for k in (0, 3)]
    xs, zs = sorted(p[0] for p in pts), sorted(p[1] for p in pts)
    q = lambda v, f: v[min(len(v) - 1, max(0, int(f * (len(v) - 1))))]
    x0, x1, z0, z1 = q(xs, .03), q(xs, .97), q(zs, .03), q(zs, .97)
    xm, zm, o = (x0 + x1) / 2, (z0 + z1) / 2, S / 2 - 150
    return {"west": [x0 - o, zm], "east": [x1 + o, zm], "south": [xm, z0 - o], "north": [xm, z1 + o], "camera": [g.camera[0], g.camera[2]]}


def main():
    tmp = tempfile.mkdtemp(prefix="cs2mcp_sites_")
    cs2_mcp.PLAN_DIR = os.path.join(tmp, "plans")
    settings_bak = open(tools2.SETTINGS).read() if os.path.exists(tools2.SETTINGS) else None
    try:
        H = cs2_mcp.HANDLERS
        g = RealTown(); cs2_mcp.rpc = g
        town = g.state()
        print("town:", len(g.edges), "edges,", len(g.buildings), "houses,", g.zoned(), "zoned cells")

        # 1) the first live test's way: squares next to the town, the grid planned on its own (informational: the
        #    0.46.0 planner found nothing to join in any of them - repro_first_live_test.py runs that version)
        for side, c in old_style_squares(g).items():
            plan, area, extra = cs2_mcp._grid_plan({"polygon": tools16.outline({"center": c, "size": 800}, cs2_mcp._district_api()),
                                                    "local_type": "Small Road", "arterial_type": "Medium Road", "connect_beyond_m": 250.0,
                                                    "max_grade": 0.15})
            st = plan["stats"]
            print(f"  square {side:6s}: grid {st['grid_segments']:3d}, pieces that fit {st['diag']['pieces_fit']:3d}, "
                  f"joins {st['diag']['joins']}")
        assert g.state() == town

        # 2) the sites: the dead end at x = 200 to the south. The one at x = 600 leads beyond the city limits (the dry
        #    run says no), the stub to the west only reaches a sliver before the railway, the gap on the east side a
        #    sliver before the river; every other street side is lined with houses
        r = H["cs2_district_sites"]({"count": 4})
        info = json.loads(r["__mcp_content__"][-1]["text"])
        assert r["__mcp_content__"][0]["type"] == "image"
        ok = [s for s in info["sites"] if s["check"]["ok"]]
        print("sites:", json.dumps(info["found"]), "|", [(s["rank"], s["kind"], s["anchor"], s["free_ha"], s["check"]) for s in info["sites"]])
        assert [tuple(s["anchor"]) for s in ok] == [(200.0, -100.0)] and ok[0]["kind"] == "dead_end", info["sites"]
        refused = [s for s in info["sites"] if not s["check"]["ok"]]
        assert [tuple(s["anchor"]) for s in refused] == [(600.0, -60.0)], refused
        assert "Exceeds City Limits" in refused[0]["check"]["reasons"], refused
        assert info["found"]["spine_buildings"] > 0 and info["found"]["too_little_free_land"] >= 2, info["found"]
        assert g.state() == town and g.calls["place_roads"] >= 2                                            # dry runs only

        # 3) build with site='auto' (no outline): a grid joined to the town through a dead end, zoned, a district
        rep = H["cs2_build_district"]({"name": "South Fields"})
        print("build:", json.dumps({k: rep.get(k) for k in ("site", "roads", "zoning", "district", "check", "seconds")}))
        assert rep["built"] and rep["site"]["kind"] == "dead_end", rep
        assert rep["stats"]["to_dead_ends"] >= 1 and rep["roads"]["segments_built"] >= 8, rep["stats"]
        assert rep["check"]["islands"] == 0 and rep["district"]["created"], rep
        assert rep["roads"]["refused"] == 0, rep["roads"]
        assert g.connected_to_town([i for i, e in g.edges.items() if e["type"] in ("Small Road", "Medium Road")]), "an island"
        assert g.zoned() > town[3], (g.zoned(), town[3])
        out = H["cs2_remove_district"]({})
        assert out["method"] == "undo" and g.state() == town, (out, g.state(), town)
        print("remove:", out)

        # 4) an outline drawn south of the town (not touching it): its own grid reaches no street; laid out again from
        #    the dead end that reaches into it, it joins the town
        south = [[-100, -760], [520, -760], [520, -230], [-100, -230]]
        rep2 = H["cs2_build_district"]({"polygon": south, "preview": True})
        info2 = json.loads(rep2["__mcp_content__"][-1]["text"])
        print("outline:", info2.get("notes"), info2["stats"]["grid_segments"], info2["stats"]["to_dead_ends"])
        assert info2["stats"]["grid_segments"] > 0 and info2["stats"]["to_dead_ends"] >= 1 and info2.get("site"), info2
        rep3 = H["cs2_build_district"]({"plan_id": info2["plan_id"], "district": False})
        assert rep3["built"] and rep3["check"]["islands"] == 0, rep3
        assert g.connected_to_town([i for i, e in g.edges.items() if e["type"] in ("Small Road", "Medium Road")])
        H["cs2_remove_district"]({"plan_id": info2["plan_id"]})
        assert g.state() == town

        # 5) a site from the list, previewed, then built from its plan_id
        site = ok[-1]
        p = json.loads(H["cs2_build_district"]({"site": site, "preview": True, "preset": "suburb"})["__mcp_content__"][-1]["text"])
        assert p["site"]["anchor"] == site["anchor"] and p["stats"]["grid_segments"] > 0, p
        b = H["cs2_build_district"]({"plan_id": p["plan_id"]})
        assert b["built"] and b["check"]["islands"] == 0, b
        H["cs2_remove_district"]({"plan_id": p["plan_id"]})
        assert g.state() == town

        # 6) the same town turned 33 degrees: the same dead end, a grid along its street, joined, taken back
        g3 = RotatedTown(33.0); cs2_mcp.rpc = g3; st3 = g3.state()
        r7 = H["cs2_build_district"]({"name": "Turned Fields"})
        want = [round(v, 1) for v in g3.rot((200.0, -100.0))]
        print("turned town:", r7.get("site"), r7["roads"], r7["check"])
        assert r7["built"] and r7["site"]["anchor"] == want and r7["roads"]["refused"] == 0 and r7["check"]["islands"] == 0, r7
        assert g3.connected_to_town([i for i, e in g3.edges.items() if e["type"] in ("Small Road", "Medium Road")])
        ang = [math.degrees(math.atan2(e["curve"][3][2] - e["curve"][0][2], e["curve"][3][0] - e["curve"][0][0])) % 90
               for e in g3.edges.values() if e["type"] in ("Small Road", "Medium Road")]
        assert all(abs(a - 33.0) < 0.5 for a in ang), sorted(set(round(a, 1) for a in ang))           # all on the town's grid
        H["cs2_remove_district"]({})
        assert g3.state() == st3

        # 7) a city with no free land next to its streets: a clear reason, nothing built
        g2 = RealTown(); g2.LIMITS = (-320.0, -60.0, 850.0, 1600.0); cs2_mcp.rpc = g2
        st2 = g2.state()
        r6 = H["cs2_build_district"]({})
        print("no land:", r6.get("reason"))
        assert r6["built"] is False and "refused by the game" in r6["reason"] and g2.state() == st2, r6
        print("district sites checks passed")
    finally:
        if settings_bak is None:
            if os.path.exists(tools2.SETTINGS): os.remove(tools2.SETTINGS)
        else:
            open(tools2.SETTINGS, "w").write(settings_bak)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
