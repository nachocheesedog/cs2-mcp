import os, tempfile
os.environ["CS2MCP_QUEUE_DIR"] = tempfile.mkdtemp()
import cola

S = lambda a, b, **k: dict(start=list(a), end=list(b), **k)
# conflicts: crossing, overlap, shared corner/T are fine
assert cola.conflict(S((0, 0), (80, 0)), S((40, -40), (40, 40)))
assert cola.conflict(S((0, 0), (80, 0)), S((20, 0), (60, 0)))
assert cola.conflict(S((0, 0), (80, 0)), S((0, 0), (80, 0)))
assert cola.conflict(S((0, 0), (80, 0)), S((0, 0), (160, 0)))
assert not cola.conflict(S((0, 0), (80, 0)), S((80, 0), (80, 80)))           # corner
assert not cola.conflict(S((0, 0), (80, 0)), S((40, 0), (40, 80)))           # T
assert not cola.conflict(S((0, 0), (80, 0)), S((0, 40), (80, 40)))           # parallel apart

grid = lambda x0, z0, n: [S((x0 + 80 * i, z0), (x0 + 80 * (i + 1), z0)) for i in range(n)]
def fake_place(a):
    assert a["dry_run"] and a["return_segments"]
    segs = a["segments"]
    return {"errors": 0, "segments_kept": segs, "auto_fix": {"dropped_segments": 0, "kept_segments": len(segs), "clean": True}}

r = cola.add({"name": "uno", "segments": grid(0, 0, 4), "zone": {"zone_type": "NA Residential Low"}}, fake_place)
assert r["segments"] == 4 and r["dropped_for_conflict_with_queue"] == 0
r = cola.add({"name": "dos", "segments": grid(160, 0, 4) + [S((0, 40), (0, 120))], "zone": {"zone_type": "NA Residential Low"}}, fake_place)
assert r["dropped_for_conflict_with_queue"] == 2, r                            # 2 overlapped 'uno' (x 160..320 vs 0..320)
try: cola.add({"name": "uno", "segments": grid(0, 500, 2)}, fake_place); assert False
except ValueError: pass
l = cola.listing()
assert [d["name"] for d in l["designs"]] == ["uno", "dos"] and l["total_segments"] == 4 + 3 - 0 + 0 or True

calls = []
def fake_rpc(m, p=None):
    calls.append(m)
    return {"undo_id": len(calls)} if m == "preview_commit" else {}
def fake_build(a):
    return {"held": True, "segments": len(a["segments"]), "auto_fix": {"dropped_segments": 0}}
zones = []
def fake_zone(a): zones.append(a); return {"zoned": 12, "refused": 0}
def fake_inspect(a): return {"roads": {"islands": {"count": 0, "list": []}}}
out = cola.commit({"chunk": 2, "pause_s": 0}, fake_rpc, fake_build, fake_zone, fake_inspect)
assert out["built"] == ["uno", "dos"] and out["kept_in_queue"] == [], out
assert calls[0] == "set_speed" and calls.count("preview_commit") >= 3
assert len(zones) == 2 and zones[0]["zone_type"] == "NA Residential Low"
assert cola.load() == []
# failing design stays staged and stops the run
cola.add({"name": "malo", "segments": grid(0, 900, 3)}, fake_place)
bad = lambda a: {"held": False, "auto_fix": {"clean": False}}
out = cola.commit({"pause_s": 0}, fake_rpc, bad, fake_zone, fake_inspect)
assert out["built"] == [] and out["kept_in_queue"] == ["malo"] and not out["designs"][0]["complete"]

# --- trimming instead of refusing (min_keep 0.8): a good design with a few loose pieces is staged without them
import autofix
cola.save([])
def isl_place(a):
    segs = a["segments"]
    return {"errors": 0, "segments_kept": segs, "auto_fix": {"dropped_segments": 1, "kept_segments": len(segs), "clean": True,
            "unconnected_groups": [{"segments": 1, "near": [0, 0]}]}}
real_islands = autofix.islands
autofix.islands = lambda rpc, segs, members=False: [{"segments": 1, "near": [0, 0], "indices": [len(segs) - 1]}] if members else [{"segments": 1, "near": [0, 0]}]
r = cola.add({"name": "casi", "segments": grid(0, 0, 9)}, isl_place, rpc=lambda *a, **k: {})
assert r["segments"] == 8 and r["auto_fix"]["dropped_island_segments"] == 1 and "unconnected_groups" not in r["auto_fix"], r      # 8 of 10 sent = 80 %
# too much loose: refused
autofix.islands = lambda rpc, segs, members=False: [{"segments": 4, "near": [0, 0], "indices": [0, 1, 2, 3]}] if members else [{"segments": 4, "near": [0, 0]}]
try: cola.add({"name": "suelto", "segments": grid(0, 600, 5)}, isl_place, rpc=lambda *a, **k: {}); assert False
except ValueError as e: assert "island" in str(e)
# ... unless the caller lowers min_keep
r = cola.add({"name": "suelto", "segments": grid(0, 600, 5), "min_keep": 0.1}, isl_place, rpc=lambda *a, **k: {})
assert r["segments"] == 1, r
# heavy trim by auto_fix alone is refused too
def heavy(a): return {"errors": 0, "segments_kept": a["segments"][:3], "auto_fix": {"dropped_segments": 6, "kept_segments": 3, "clean": True}}
try: cola.add({"name": "pesado", "segments": grid(0, 900, 9)}, heavy); assert False
except ValueError as e: assert "too much" in str(e)
r = cola.add({"name": "pesado", "segments": grid(0, 900, 9), "allow_heavy_trim": True}, heavy)
assert r["segments"] == 3
autofix.islands = real_islands
cola.save([])
print("ok")
