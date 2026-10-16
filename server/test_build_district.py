"""Offline end-to-end check of the v0.46 tools - cs2_build_district, cs2_remove_district, cs2_batch and fast mode -
against a small fake game (a 2 x 2 block town with zone blocks, buildings, districts and an undo stack; no game
needed): python test_build_district.py"""
import collections, json, math, os, shutil, tempfile
import cs2_mcp, tools2, tools16, survey

BridgeError = cs2_mcp.BridgeError


def bez(c, t):
    u = 1 - t
    return (u ** 3 * c[0][0] + 3 * u * u * t * c[1][0] + 3 * u * t * t * c[2][0] + t ** 3 * c[3][0],
            u ** 3 * c[0][2] + 3 * u * u * t * c[1][2] + 3 * u * t * t * c[2][2] + t ** 3 * c[3][2])


def dist_seg(p, a, b):
    dx, dz = b[0] - a[0], b[1] - a[1]; L = dx * dx + dz * dz
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dz) / L))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dz)


def in_quad(p, q):
    inside = False
    for (x1, z1), (x2, z2) in zip(q, q[1:] + q[:1]):
        if (z1 > p[1]) != (z2 > p[1]) and p[0] < x1 + (p[1] - z1) * (x2 - x1) / (z2 - z1): inside = not inside
    return inside


class FakeGame:
    """Just enough of the CS2MCP bridge for the district tools: roads with nodes, splitting and zone blocks, zoning,
    buildings, districts, an undo stack with expect_id, speed."""
    WIDTHS = {"Small Road": 16.0, "Medium Road": 24.0, "Large Road": 40.0, "Highway": 24.0}
    ZONES = ["EU Residential Low", "EU Commercial Low", "NA Residential Low", "NA Residential Medium", "NA Residential Medium Row",
             "NA Residential High", "NA Residential Mixed", "Residential LowRent", "NA Commercial Low", "NA Commercial High",
             "Office Low", "Office High", "Industrial Manufacturing"]
    BUILDINGS = {"MedicalClinic01": (40, 40), "PoliceStation02": (32, 40), "ElementarySchool01": (64, 48)}
    SUBNETS = {"PoliceStation02"}

    def __init__(self, mod_version="0.45"):
        self.mod_version = mod_version
        self.nodes, self.edges, self.blocks, self.buildings, self.districts = {}, {}, {}, {}, {}
        self.undo, self.nid, self.uid, self.speed, self.codes = [], 1, 1, 1.0, {}
        self.money, self.broke = 1e6, False
        self.calls = collections.Counter()
        self.camera = [112.0, 0.0, 112.0]
        for x in (0, 112, 224):                                    # the town: a 2 x 2 block grid of Small Roads
            for z in (0, 112, 224): self._node((x, z))
        for z in (0, 112, 224):
            for x0, x1 in ((0, 112), (112, 224)): self._edge(self._at((x0, z)), self._at((x1, z)), "Small Road")
        for x in (0, 112, 224):
            for z0, z1 in ((0, 112), (112, 224)): self._edge(self._at((x, z0)), self._at((x, z1)), "Small Road")
        for b in list(self.blocks.values())[:6]:                   # some NA low density on it (theme detection)
            b["cells"] = [self._code("NA Residential Low") if c == "." else c for c in b["cells"]]

    # -------------------------------------------------------------- state helpers
    def _id(self):
        self.nid += 1; return f"{self.nid}:1"

    def _code(self, name):
        if name not in self.codes: self.codes[name] = "abcdefghijklmnopqrstuvwxyz"[len(self.codes)]
        return self.codes[name]

    def _node(self, p):
        i = self._id(); self.nodes[i] = {"id": i, "pos": [float(p[0]), 0.0, float(p[1])]}; return i

    def _at(self, p, tol=0.5):
        for i, n in self.nodes.items():
            if math.dist((n["pos"][0], n["pos"][2]), p) <= tol: return i
        return None

    def _degree(self, i): return sum(1 for e in self.edges.values() if i in (e["start_node"], e["end_node"]))

    def _poly(self, e, n=8): return [bez(e["curve"], k / n) for k in range(n + 1)]

    def _edge(self, s, t, typ, controls=None, half=None):
        a, b = self.nodes[s]["pos"], self.nodes[t]["pos"]
        if controls: c = [a, [controls[0][0], 0.0, controls[0][1]], [controls[1][0], 0.0, controls[1][1]], b]
        else: c = [a, [a[0] + (b[0] - a[0]) / 3, 0.0, a[2] + (b[2] - a[2]) / 3], [a[0] + 2 * (b[0] - a[0]) / 3, 0.0, a[2] + 2 * (b[2] - a[2]) / 3], b]
        i = self._id()
        e = {"id": i, "type": typ, "width": self.WIDTHS[typ], "curve": [list(map(float, q)) for q in c], "start_node": s, "end_node": t,
             "flow": 0.95, "half": half or [False, False]}
        e["length"] = round(sum(math.dist(p, q) for p, q in zip(self._poly(e, 16), self._poly(e, 16)[1:])), 2)
        self.edges[i] = e
        self._hide_near(e)
        if "Highway" not in typ: self._blocks_for(e)
        return i

    def _blocks_for(self, e):
        (x0, z0), (x1, z1) = bez(e["curve"], 0), bez(e["curve"], 1)
        L = math.hypot(x1 - x0, z1 - z0); tx, tz = (x1 - x0) / L, (z1 - z0) / L
        sx = int(L // 8)
        if sx < 1: return
        mx, mz = (x0 + x1) / 2, (z0 + z1) / 2
        for side in (1, -1):
            nx, nz = -tz * side, tx * side
            b = {"id": self._id(), "road": e["id"], "pos": [mx + nx * (e["width"] / 2 + 24), 0.0, mz + nz * (e["width"] / 2 + 24)],
                 "dir": [-nx, -nz], "size": [sx, 6], "built": [False] * (sx * 6)}
            b["cells"] = ["#" if self._hidden(c, e["id"]) else "." for c in self._centres(b)]
            self.blocks[b["id"]] = b

    def _centres(self, b):
        px, _, pz = b["pos"]; dx, dz = b["dir"]; rx, rz = dz, -dx; sx, sy = b["size"]
        out = []
        for i in range(sx * sy):
            y, x = divmod(i, sx); oy, ox = 4 * (sy - 2 * y - 1), 4 * (sx - 2 * x - 1)
            out.append((px + dx * oy + rx * ox, pz + dz * oy + rz * ox))
        return out

    def _hidden(self, c, own):
        for e in self.edges.values():
            if e["id"] == own: continue
            pl = self._poly(e)
            if min(dist_seg(c, p, q) for p, q in zip(pl, pl[1:])) < e["width"] / 2 + 3: return True
        for b in self.blocks.values():                             # first come keeps an overlapping cell
            if math.dist(c, (b["pos"][0], b["pos"][2])) > 80: continue
            for k, c2 in enumerate(self._centres(b)):
                if b["cells"][k] != "#" and math.dist(c, c2) < 5.5: return True
        return False

    def _hide_near(self, e):
        pl = self._poly(e)
        for b in self.blocks.values():
            for k, c in enumerate(self._centres(b)):
                if b["cells"][k] != "#" and min(dist_seg(c, p, q) for p, q in zip(pl, pl[1:])) < e["width"] / 2 + 3: b["cells"][k] = "#"

    def _remove_edge(self, i):
        e = self.edges.pop(i, None)
        if not e: return None
        for k in [k for k, b in self.blocks.items() if b["road"] == i]: del self.blocks[k]
        for n in (e["start_node"], e["end_node"]):
            if n in self.nodes and self._degree(n) == 0: del self.nodes[n]
        return e

    def _push(self, kind, desc, undo):
        u = {"id": self.uid, "kind": kind, "description": desc, "undo": undo}; self.uid += 1
        self.undo.append(u); del self.undo[:-50]
        return u["id"]

    def _box(self, p, pad=0.0):
        return (p.get("xmin", -1e9) - pad, p.get("zmin", -1e9) - pad, p.get("xmax", 1e9) + pad, p.get("zmax", 1e9) + pad)

    @staticmethod
    def _inb(box, x, z): return box[0] <= x <= box[2] and box[1] <= z <= box[3]

    # -------------------------------------------------------------- bridge
    def __call__(self, method, params=None, timeout=90):
        p = dict(params or {}); self.calls[method] += 1
        fn = getattr(self, "m_" + method, None)
        if fn is None: raise BridgeError(f"fake game: no method {method}")
        return fn(p)

    def m_ping(self, p):
        return {"mod_version": self.mod_version, "city_loaded": True, "loading": False, "load_generation": 1, "save_name": "test",
                "city_name": "Testville", "tool_busy": False}

    def m_city_info(self, p):
        return {"speed": self.speed, "money": self.money, "unlimited_money": False, "population": 1234, "road_edges": len(self.edges),
                "buildings": len(self.buildings)}
    def m_set_speed(self, p): self.speed = float(p["speed"]); return {"speed": self.speed}
    def m_camera(self, p): return {"ground_point": list(self.camera)}
    def m_camera_goto(self, p): self.camera = [p.get("x", self.camera[0]), 0.0, p.get("z", self.camera[2])]; return {"pivot": self.camera}
    def m_list_road_types(self, p): return {"road_types": [{"name": n, "width": w, "kind": "road"} for n, w in self.WIDTHS.items()]}
    def m_list_zone_types(self, p): return {"count": len(self.ZONES), "zone_types": sorted(self.ZONES)}

    def m_list_building_types(self, p):
        f = (p.get("filter") or "").lower()
        return {"buildings": [{"name": n, "lot_m": list(v)} for n, v in self.BUILDINGS.items() if f in n.lower()]}

    def m_road_network(self, p):
        box = self._box(p)
        E = [e for e in self.edges.values() if any(self._inb(box, *bez(e["curve"], t)) for t in (0, 0.5, 1))]
        ids = {n for e in E for n in (e["start_node"], e["end_node"])}
        N = [dict(self.nodes[i], degree=self._degree(i)) for i in ids if i in self.nodes]
        return {"edges": [{k: v for k, v in e.items()} for e in E], "nodes": N}

    def m_zone_blocks(self, p):
        box = self._box(p, 60)
        legend = {c: n for n, c in self.codes.items()}
        B = [{"id": b["id"], "road": b["road"], "pos": b["pos"], "dir": b["dir"], "size": b["size"], "cells": "".join(b["cells"]),
              "built": "".join("1" if x else "0" for x in b["built"])} for b in self.blocks.values() if self._inb(box, b["pos"][0], b["pos"][2])]
        return {"blocks": B[:p.get("limit", 100000)], "legend": legend}

    def m_query_buildings(self, p):
        box = self._box(p)
        return {"buildings": [dict(b) for b in self.buildings.values() if self._inb(box, b["position"][0], b["position"][2])][:p.get("limit", 10000)]}

    def m_terrain_grid(self, p):
        st = float(p.get("step", 16)); x0, z0 = p["xmin"], p["zmin"]
        nx = int((p["xmax"] - x0) // st) + 1; nz = int((p["zmax"] - z0) // st) + 1
        H = [[0.0] * nx for _ in range(nz)]
        Wd = [[(3.0 if x0 + i * st > 900 else 0.0) for i in range(nx)] for _ in range(nz)]      # a lake east of x = 900
        return {"origin": [x0, z0], "step": st, "nx": nx, "nz": nz, "heights": H, "water_depth": Wd}

    def _lot(self, b):
        fx, fz = b["forward"]; rx, rz = fz, -fx; w, d = b["lot"][0] * 4.0, b["lot"][1] * 4.0; x, _, z = b["position"]
        return [[x + fx * a + rx * c, z + fz * a + rz * c] for a, c in ((d, w), (d, -w), (-d, -w), (-d, w))]

    def m_obstacles(self, p):
        box = self._box(p, 40)
        objs = [{"cat": "building", "corners": self._lot(b), "lot": self._lot(b)} for b in self.buildings.values()
                if self._inb(box, b["position"][0], b["position"][2])]
        return {"objects": objs, "by_category": {"building": len(objs)}}

    def _snap(self, p, tol, made):
        for i in made + list(self.nodes):
            n = self.nodes.get(i)
            if n and math.dist((n["pos"][0], n["pos"][2]), p) <= tol: return i, "node"
        for e in list(self.edges.values()):                        # an end on an existing road splits it (a T junction)
            pl = self._poly(e, 16)
            if min(dist_seg(p, a, b) for a, b in zip(pl, pl[1:])) <= tol:
                self._remove_edge(e["id"])
                m = self._node(p)
                self._edge(e["start_node"], m, e["type"]); self._edge(m, e["end_node"], e["type"])
                return m, "edge"
        return None, None

    def _blocked(self, s):
        w = self.WIDTHS[s.get("type")] / 2
        a, b = s["start"], s["end"]
        for bl in self.buildings.values():
            c = (bl["position"][0], bl["position"][2]); r = max(bl["lot"]) * 4
            if dist_seg(c, a, b) < w + r - 2: return True
        return False

    def m_place_roads(self, p):
        segs = p["segments"]; tol = float(p.get("snap_tolerance", 2.0))
        for s in segs:
            if s.get("type") not in self.WIDTHS: raise BridgeError(f"unknown road type '{s.get('type')}'")
        if self.broke: return {"applied": False, "errors": len(segs), "error_reasons": {"NotEnoughMoney": len(segs)}}
        bad = [s for s in segs if self._blocked(s)]
        if bad and not p.get("allow_errors"):
            return {"applied": False, "errors": len(bad), "error_reasons": {"Overlap Existing": len(bad)},
                    "error_locations": [s["start"] for s in bad][:30], "reason": "dry_run" if p.get("dry_run") else "preview has errors"}
        if p.get("dry_run"): return {"applied": False, "errors": 0, "reason": "dry_run"}
        made_nodes, created = [], []
        for s in segs:
            ends = []
            for q in (s["start"], s["end"]):
                n, _ = self._snap(q, tol, made_nodes)
                if n is None: n = self._node(q); made_nodes.append(n)
                ends.append(n)
            if ends[0] == ends[1]: continue
            created.append(self._edge(ends[0], ends[1], s["type"], s.get("controls")))
            self.money -= self.edges[created[-1]]["length"] * 3
        def undo():
            for i in created: self._remove_edge(i)
        uid = self._push("place_roads", f"place {len(segs)} road segment(s)", undo)
        return {"applied": True, "errors": 0, "created_edges": len(created), "created_ids": created[:200], "undo_id": uid}

    def m_zone(self, p):
        specs = [(a["corners"], a.get("zone_type", p.get("zone_type")), a.get("overwrite", p.get("overwrite", True))) for a in p["areas"]] \
            if p.get("areas") else [(p["corners"], p["zone_type"], p.get("overwrite", True))]
        for _, z, _ in specs:
            if z != "none" and z not in self.ZONES: raise BridgeError(f"unknown zone type '{z}'")
        old = []
        if p.get("dry_run"): return {"applied": False, "reason": "dry_run", "errors": 0}
        for b in self.blocks.values():
            for k, c in enumerate(self._centres(b)):
                if b["cells"][k] == "#": continue
                for q, z, ow in specs:
                    if not in_quad(c, q): continue
                    new = "." if z == "none" else self._code(z)
                    if (ow or b["cells"][k] == ".") and b["cells"][k] != new: old.append((b["id"], k, b["cells"][k])); b["cells"][k] = new
                    break
        def undo():
            for bid, k, ch in old:
                if bid in self.blocks: self.blocks[bid]["cells"][k] = ch
        out = {"applied": True, "errors": 0, "cells_to_change": len(old)}
        if old: out["undo_id"] = self._push("zone", f"zone {len(specs)} areas", undo)
        return out

    def m_road_align(self, p):
        old = []
        for ch in p["edges"]:
            e = self.edges.get(ch["id"])
            if e: old.append((e["id"], list(e["half"]))); e["half"] = [ch["start_half"], ch["end_half"]]
        def undo():
            for i, h in old:
                if i in self.edges: self.edges[i]["half"] = h
        return {"applied": True, "edges_to_change": len(old), "undo_id": self._push("road_align", "align", undo)}

    def m_place_building(self, p):
        typ = p["type"]
        if typ not in self.BUILDINGS: raise BridgeError(f"unknown building '{typ}' (use list_building_types)")
        if typ in self.SUBNETS and not p.get("allow_subnets"):
            raise BridgeError(f"'{typ}' has 3 built-in nets (tracks/roads/quays/fences); placing or even previewing such buildings crashed the game")
        W, D = self.BUILDINGS[typ]
        f = p.get("facing") or [0, 1]
        b = {"id": self._id(), "type": typ, "position": [p["x"], 0.0, p["z"]], "forward": f, "lot": [W // 8, D // 8]}
        for o in self.buildings.values():
            if math.dist((o["position"][0], o["position"][2]), (p["x"], p["z"])) < (max(W, D) + max(o["lot"]) * 8) / 2 - 1:
                return {"applied": False, "errors": 1, "error_reasons": {"Overlap Existing": 1}}
        if p.get("dry_run"): return {"applied": False, "errors": 0, "reason": "dry_run"}
        self.buildings[b["id"]] = b
        uid = self._push("place_building", f"place {typ}", lambda: self.buildings.pop(b["id"], None))
        return {"applied": True, "errors": 0, "building_id": b["id"], "undo_id": uid}

    def m_create_district(self, p):
        poly = p["polygon"]
        for d in self.districts.values():
            if any(in_quad(q, d["polygon"]) for q in poly) or any(in_quad(q, poly) for q in d["polygon"]):
                return {"applied": False, "errors": 1, "error_reasons": {"Overlap": 1}, "reason": "preview has errors"}
        i = self._id(); name = p.get("name") or f"District {len(self.districts) + 1}"
        self.districts[i] = {"id": i, "name": name, "polygon": poly, "policies": p.get("policies", [])}
        uid = self._push("create_district", f"create district {name}", lambda: self.districts.pop(i, None))
        out = {"applied": True, "district_id": i, "name": name, "undo_id": uid}
        if p.get("policies"): out["policies"] = p["policies"]
        return out

    def m_delete_district(self, p):
        i = next((k for k, d in self.districts.items() if k == p.get("id") or d["name"] == p.get("name")), None)
        if i is None: raise BridgeError("no such district")
        d = self.districts.pop(i)
        return {"applied": True, "undo_id": self._push("delete_district", f"delete district {d['name']}", lambda: self.districts.__setitem__(i, d))}

    def m_bulldoze(self, p):
        gone_b, gone_e = [], []
        for i in p.get("ids") or []:
            if i in self.buildings and p.get("buildings", True) is not False: gone_b.append(self.buildings.pop(i))
            elif i in self.edges and p.get("roads", True) is not False: gone_e.append(self._remove_edge(i))
        def undo():
            for e in gone_e:
                s = self._at((e["curve"][0][0], e["curve"][0][2])) or self._node((e["curve"][0][0], e["curve"][0][2]))
                t = self._at((e["curve"][3][0], e["curve"][3][2])) or self._node((e["curve"][3][0], e["curve"][3][2]))
                self._edge(s, t, e["type"])
        uid = self._push("bulldoze", f"bulldoze {len(gone_e)} roads {len(gone_b)} buildings", undo)
        return {"applied": True, "roads": len(gone_e), "buildings": len(gone_b), "undo_id": uid}

    def m_history(self, p):
        return {"undo_depth": len(self.undo), "entries_newest_first": [{"id": u["id"], "kind": u["kind"], "description": u["description"]}
                                                                        for u in reversed(self.undo)]}

    def m_undo(self, p):
        if not self.undo: return {"undone": False, "reason": "nothing to undo"}
        u = self.undo[-1]
        if p.get("expect_id") is not None and p["expect_id"] != u["id"]:
            raise BridgeError(f"undo refused: the newest change is #{u['id']} ({u['description']}), not #{p['expect_id']}")
        self.undo.pop(); u["undo"]()
        return {"undone": True, "undo_of": u["description"]}

    # -------------------------------------------------------------- facts for the asserts
    def zoned(self, poly=None):
        n = 0
        for b in self.blocks.values():
            for k, c in enumerate(self._centres(b)):
                if b["cells"][k] not in "#." and (poly is None or tools2._in_poly(c, poly)): n += 1
        return n

    def state(self):
        return (len(self.edges), len(self.buildings), len(self.districts), self.zoned())


# ------------------------------------------------------------------------------- checks
def check_resolver():
    Z = FakeGame.ZONES
    r = tools16.resolve_zone
    assert r("residential low", Z, "NA") == "NA Residential Low"
    assert r("residential low", Z, "EU") == "EU Residential Low"
    assert r("residential low", Z) == "EU Residential Low"            # no theme: plain names first, then alphabetical
    assert r("residential medium", Z, "NA") == "NA Residential Medium"
    assert r("residential row", Z, "NA") == "NA Residential Medium Row"
    assert r("residential lowrent", Z) == "Residential LowRent"
    assert r("mixed", Z, "NA") == "NA Residential Mixed"
    assert r("commercial high", Z, "EU") == "NA Commercial High"       # EU has none: another theme's
    assert r("office high", Z) == "Office High" and r("office low", Z) == "Office Low" and r("office", Z) in ("Office High", "Office Low")
    assert r("industrial", Z) == "Industrial Manufacturing"
    assert r("NA Commercial Low", Z) == "NA Commercial Low"            # exact names pass through
    assert r("medium row", Z) == "NA Residential Medium Row"           # a fragment of a name
    assert r("residential row", ["EU Residential Low", "EU Residential Medium"], "EU") == "EU Residential Medium"   # falls back
    assert r("spaceport", Z) is None
    assert tools16.theme_of("NA Residential Low") == "NA" and tools16.theme_of("Office High") is None
    s = tools16.spec_of({"preset": "suburb", "zoning": {"local": "residential row"}, "arterial_type": "none"})
    assert s["arterial_type"] is None and s["zones"] == {"arterial": ["commercial low"], "local": ["residential row"]}, s
    assert tools16.spec_of({"zoning": False})["zones"] == {}
    assert tools16.spec_of({"zoning": "industrial"})["zones"] == {"arterial": ["industrial"], "local": ["industrial"]}
    s = tools16.spec_of({"preset": "downtown"})
    notes = tools16.check_road_types(s, {"Small Road": 16, "Medium Road": 24}, [])
    assert s["arterial_type"] is None and notes, (s, notes)          # Large Road missing -> Medium = local -> no arterials
    try:
        tools16.check_road_types(tools16.spec_of({"local_type": "Hover Road"}), {"Small Road": 16}, ["local_type"]); raise AssertionError
    except ValueError: pass
    print("zone resolver + presets ok")


def main():
    tmp = tempfile.mkdtemp(prefix="cs2mcp_test_")
    cs2_mcp.PLAN_DIR = os.path.join(tmp, "plans")
    settings_bak = open(tools2.SETTINGS).read() if os.path.exists(tools2.SETTINGS) else None
    try:
        check_resolver()
        H = cs2_mcp.HANDLERS
        g = FakeGame(); cs2_mcp.rpc = g
        before = g.state()
        north = [[-40, 300], [360, 300], [360, 700], [-40, 700]]

        # 1) preview: a map + plan_id, nothing built, speed untouched
        r = H["cs2_build_district"]({"polygon": north, "preset": "residential", "arterial_every": 2, "preview": True, "name": "North Hill"})
        info = json.loads(r["__mcp_content__"][-1]["text"])
        assert r["__mcp_content__"][0]["type"] == "image" and info["plan_id"].startswith("district_"), info
        assert g.state() == before and g.speed == 1.0 and g.calls["place_roads"] == 0
        assert info["theme"] == "NA" and info["zones"] == {"arterial": ["NA Residential Medium", "NA Residential Mixed"],
                                                           "local": ["NA Residential Medium Row"]}, info["zones"]
        assert info["stats"]["grid_segments"] > 0 and "zoning_forecast" in info, info
        print("preview:", info["stats"]["grid_segments"], "grid segments, verdict:", info["zoning_forecast"].get("verdict"))

        # 2) build that draft: roads joined to the town, services, zoning by role, district; speed back to 1.
        #    A house grew on one planned street since the preview: that street is refused, the rest is built.
        town = g.state()
        with open(os.path.join(cs2_mcp.PLAN_DIR, info["plan_id"] + ".json")) as fh: drafted = json.load(fh)
        s0 = next(x for x in drafted["segments"] if x["start"][0] == x["end"][0] and min(x["start"][1], x["end"][1]) > 330)
        hid = g._id(); g.buildings[hid] = {"id": hid, "type": "NA_ResidentialLow01_L1", "lot": [2, 2], "forward": [1, 0],
                                           "position": [s0["start"][0], 0.0, (s0["start"][1] + s0["end"][1]) / 2]}
        before = g.state()
        g.speed = 2.0
        rep = H["cs2_build_district"]({"plan_id": info["plan_id"], "services": [{"type": "MedicalClinic01"}, {"type": "PoliceStation02"}]})
        print("build:", json.dumps({k: rep[k] for k in ("roads", "services", "zoning", "district", "check", "undo_entries", "seconds")}))
        assert rep["built"] and g.speed == 2.0, rep
        assert rep["roads"]["segments_built"] > 0 and rep["roads"]["refused"] == 1, rep["roads"]
        assert rep["precheck"]["errors"] >= 1 and rep["money_spent"] > 0, rep
        assert rep["district"]["created"] and g.districts and list(g.districts.values())[0]["name"] == "North Hill"
        clinic, police = rep["services"]
        assert len(clinic["placed"]) == 1 and "allow_subnets" in police["error"], rep["services"]
        assert rep["zoning"]["new_edges"] > 0 and g.zoned(north) > 0, rep["zoning"]
        zl = {k for k in rep["zoning"] if k != "new_edges"}
        assert "NA Residential Medium Row" in zl and zl & {"NA Residential Medium", "NA Residential Mixed"}, zl   # by role
        assert rep["check"]["islands"] == 0 and rep["check"]["dead_ends_from_refused"] == 2, rep["check"]   # the refused street
        assert g.speed == 2.0 and g.calls["set_speed"] >= 2
        assert rep["alignment"].get("error") is None, rep["alignment"]
        # a cell of a new street is zoned, a town cell next to it kept its own zone (overwrite false)
        town_codes = {g.codes["NA Residential Low"]}
        assert any(ch in town_codes for b in list(g.blocks.values())[:6] for ch in b["cells"])
        built_state = g.state()
        assert built_state[0] > before[0] and built_state[1] == before[1] + 1 and built_state[2] == 1
        del g.buildings[hid]                                      # the house goes again (by hand: not part of the undo)

        # 3) the same draft cannot be built twice
        try: H["cs2_build_district"]({"plan_id": info["plan_id"]}); raise AssertionError("built twice")
        except ValueError: pass

        # 4) remove: exact undo of its own history entries -> the town as it was
        dry = H["cs2_remove_district"]({"plan_id": info["plan_id"], "dry_run": True})
        assert dry["method"] == "undo" and dry["undo_entries"] == rep["undo_entries"], dry
        out = H["cs2_remove_district"]({"plan_id": info["plan_id"]})
        assert out["method"] == "undo" and out["undone"] == rep["undo_entries"], out
        assert g.state() == town, (g.state(), town)
        assert H["cs2_remove_district"]({"plan_id": info["plan_id"]})["removed"] is False
        print("remove (undo):", out)

        # 5) build in one call (no preview), then edit something after it: removal falls back to shapes
        rep2 = H["cs2_build_district"]({"polygon": north, "preset": "suburb", "theme": "EU", "services": ["MedicalClinic01"], "map": True})
        txt = json.loads(rep2["__mcp_content__"][-1]["text"])
        assert txt["built"] and txt["zones"]["local"] == ["EU Residential Low"], txt["zones"]
        g.m_place_building({"type": "ElementarySchool01", "x": 800.0, "z": 800.0})        # an unrelated later edit
        grown = g._id(); g.buildings[grown] = {"id": grown, "type": "EU_ResidentialLow01_L1", "position": [150.0, 0.0, 450.0],
                                               "forward": [0, 1], "lot": [2, 3]}
        dry = H["cs2_remove_district"]({"dry_run": True, "clear_buildings": True})
        assert dry["method"] == "shape" and dry["buildings_to_clear"] == 1 and dry["services"] == 1, dry
        out = H["cs2_remove_district"]({"clear_buildings": True})
        assert out["method"] == "shape" and out["services_removed"] == 1 and out["buildings_cleared"] == 1 and out["district"] is True, out
        assert len(g.edges) == town[0], (len(g.edges), town[0])
        assert set(b["type"] for b in g.buildings.values()) == {"ElementarySchool01"}
        print("remove (shape):", out)

        # 6) an outline that touches no street: nothing built, a clear reason, nothing paused
        far = [[500, 1200], [800, 1200], [800, 1500], [500, 1500]]
        st = g.state(); r3 = H["cs2_build_district"]({"polygon": far})
        assert r3["built"] is False and "no existing street" in r3["reason"] and g.state() == st and g.speed == 2.0, r3
        print("isolated outline:", r3["reason"][:60], "...")

        # 7) through the MCP layer: tools/list carries the new tools, tools/call returns text content
        names = [t["name"] for t in cs2_mcp.handle({"id": 1, "method": "tools/list"})["tools"]]
        assert {"cs2_build_district", "cs2_remove_district", "cs2_batch"} <= set(names)
        res = cs2_mcp.handle({"id": 2, "method": "tools/call", "params": {"name": "cs2_build_district", "arguments": {"polygon": far}}})
        assert not res.get("isError") and json.loads(res["content"][0]["text"])["built"] is False

        # 8) batch: several calls in one, stop at the first failure, results shortened
        b = H["cs2_batch"]({"calls": [{"tool": "cs2_set_speed", "args": {"speed": 0}}, {"tool": "cs2_list_zone_types"},
                                      {"tool": "cs2_nope"}, {"tool": "cs2_set_speed", "args": {"speed": 3}}], "max_chars": 200})
        assert b["ran"] == 3 and b["ok"] == 2 and b["results"][2]["error"] == "unknown tool" and g.speed == 0.0, b
        assert b["results"][1]["result"].get("truncated") or len(json.dumps(b["results"][1]["result"])) <= 200
        b = H["cs2_batch"]({"calls": [{"tool": "cs2_batch", "args": {"calls": []}}, {"tool": "cs2_set_speed", "args": {"speed": 1}}],
                            "stop_on_error": False})
        assert b["results"][0]["error"] == "not allowed in a batch" and b["ok"] == 1 and g.speed == 1.0, b
        b = H["cs2_batch"]({"calls": [{"tool": "cs2_place_roads", "args": {"segments": [{"start": [-200, 0], "end": [-200, 112], "type": "Small Road"}]}}],
                            "pause": True})
        assert b["ok"] == 1 and "check" not in b["results"][0]["result"] and g.speed == 1.0, b       # fast: no post-build check
        print("batch ok:", b["seconds"], "s")

        # 9) fast mode: builders skip the post-build check unless asked
        seg = {"segments": [{"start": [-300, 0], "end": [-300, 112], "type": "Small Road"}]}
        H["cs2_settings"]({"fast_mode": False})
        assert "check" in H["cs2_place_roads"](dict(seg))
        H["cs2_settings"]({"fast_mode": True})
        assert tools2.fast() and "check" not in H["cs2_place_roads"]({"segments": [{"start": [-400, 0], "end": [-400, 112], "type": "Small Road"}]})
        assert "check" in H["cs2_place_roads"]({"segments": [{"start": [-500, 0], "end": [-500, 112], "type": "Small Road"}], "check": True})
        print("fast mode ok")

        # 10) no money: one preview says so, nothing is bisected or built, the speed comes back
        g3 = FakeGame(); g3.broke = True; cs2_mcp.rpc = g3; st = g3.state()
        r5 = H["cs2_build_district"]({"polygon": north})
        assert r5["built"] is False and "money" in r5["reason"] and g3.state() == st and g3.speed == 1.0 and g3.calls["place_roads"] == 1, r5
        print("no money:", r5["reason"])

        # 11) an old mod (no alignment, single-quad zoning) still builds
        g2 = FakeGame(mod_version="0.30"); cs2_mcp.rpc = g2
        rep4 = H["cs2_build_district"]({"polygon": north, "zoning": "residential low"})
        assert rep4["built"] and "alignment" not in rep4 and g2.zoned(north) > 0, rep4
        print("old mod ok:", rep4["zoning"])
        print("build_district checks passed")
    finally:
        if settings_bak is None:
            if os.path.exists(tools2.SETTINGS): os.remove(tools2.SETTINGS)
        else:
            open(tools2.SETTINGS, "w").write(settings_bak)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
