"""Offline test for autofix.run with a fake bridge: python test_autofix.py"""
import autofix

BAD = [(150.0, 2.0), (350.0, 52.0)]          # error positions the fake game reports while a draft segment is near them


def seg(x0, z0, x1, z1): return {"start": [x0, z0], "end": [x1, z1], "type": "Small Road"}


calls = []


def rpc(method, params=None):
    calls.append(method)
    if method == "preview_cancel": return {"applied": False}
    if method == "road_network":
        return {"edges": [{"id": "e1", "curve": [[0, 0, 0], [0, 0, 33], [0, 0, 66], [0, 0, 100]], "flow": 1.0}], "nodes": []}
    if method == "place_roads":
        hit = [p for p in BAD for s in params["segments"] if autofix._dist(p, s["start"], s["end"]) <= 5]
        res = {"errors": len(hit), "held": bool(params.get("hold")), "applied": False}
        if hit: res.update(error_locations=[{"reason": "Overlap Existing", "pos": list(p)} for p in hit], error_reasons={"Overlap Existing": len(hit)})
        return res
    raise AssertionError(method)


segs = [seg(0, 0, 100, 0), seg(100, 0, 200, 0), seg(200, 0, 300, 0), seg(300, 0, 400, 0), seg(300, 50, 400, 50), seg(900, 900, 950, 900)]
params = {"hold": True, "snap_tolerance": 2.0}
res = rpc("place_roads", dict(params, segments=segs))
assert res["errors"] == 2
segs2, res2, rep = autofix.run(rpc, segs, params, res)
print(rep)
assert rep["clean"] and res2["errors"] == 0
assert rep["dropped_segments"] == 2 and rep["kept_segments"] == 4
assert rep["unconnected_groups"], "the far segment touches nothing and must be reported"
assert "preview_cancel" in calls

# cannot be fixed: errors not on any segment -> the held preview is cancelled and reported
calls.clear()
BAD[:] = [(5000.0, 5000.0)]
def rpc2(method, params=None):
    if method == "place_roads": return {"errors": 1, "error_locations": [{"reason": "x", "pos": [5000, 5000]}], "held": True}
    calls.append(method); return {}
s3, r3, rep3 = autofix.run(rpc2, segs, params, rpc2("place_roads", {}))
print(rep3)
assert not rep3["clean"] and "preview_cancel" in calls and r3["held"] is False
print("ok")
