"""Offline check of the district tools (pave.py) on test_survey's synthetic town (no game needed)."""
import math
import pave, survey
from test_survey import fake_world


def main():
    data = fake_world()
    data["list_road_types"] = {"road_types": [{"name": "Small Road", "width": 16}, {"name": "Medium Road", "width": 24}]}
    rpc = lambda method, params=None: data[method]
    area = survey.Area(-100, -100, 500, 500)
    poly = [[-90, 330], [490, 330], [490, 490], [-90, 490]]          # empty land north of the town

    # district grid inside the outline: every segment's ends inside the outline, nothing on the steep east ramp
    plan = pave.plan_fill(rpc, area, angle_deg=0, origin=(0, 0), pitch_u=112, pitch_v=112, local_type="Small Road",
                          seed=(200, 400), polygon=poly, rim=False, keep_islands=True, max_grade=0.15)
    segs = plan["segments"]
    assert segs and all(s["phase"] == "grid" for s in segs), plan["stats"]
    inside = lambda p: -90 <= p[0] <= 490 and 330 <= p[1] <= 490
    assert all(inside(s["start"]) and inside(s["end"]) for s in segs), "grid left its outline"
    # the east ramp rises 20% eastward from x=350: streets may run along it (north-south) but not climb it (east-west)
    h = lambda x: max(0.0, (x + 100) / 15 - 30) * 3
    for s in segs:
        L = math.dist(s["start"], s["end"])
        assert abs(h(s["end"][0]) - h(s["start"][0])) / L <= 0.16, ("street climbs the ramp", s)

    # connected_only: a district that touches nothing is dropped entirely
    lone = pave.plan_fill(rpc, area, angle_deg=0, origin=(0, 0), pitch_u=112, pitch_v=112, local_type="Small Road",
                          seed=(200, 400), polygon=poly, rim=False, keep_islands=False)
    assert all(s["phase"] != "grid" for s in lone["segments"]) or lone["stats"]["to_existing"] > 0

    # rim road only: follows the water strip along the south at the setback, no grid pieces
    rim = pave.plan_fill(rpc, area, local_type="Small Road", shore_setback=40, seed=(200, 400), grid=False, rim=True, min_run_m=80)
    assert all(s["phase"] == "shore" for s in rim["segments"]), rim["stats"]

    # connect options: the planted dead end at node 7 (120, 300) gets at least one option with a verdict
    co = pave.connect_options(rpc, area)
    d7 = [d for d in co["dead_ends"] and co["list"] if d["dead_end"] == [120, 300]]
    assert d7 and d7[0]["options"] and all("ok" in o for o in d7[0]["options"]), co

    lat = pave.detect_lattice(rpc, area, 120, 110)
    assert abs(lat["angle_deg"] % 90) < 3 or abs(lat["angle_deg"] % 90) > 87, lat
    print("district grid", plan["stats"]["grid_segments"], "segs | rim", rim["stats"]["shore_segments"], "segs |",
          "dead ends", co["dead_ends"], "clean options", co["with_a_clean_option"], "| lattice", lat["angle_deg"])
    print("district checks passed")


if __name__ == "__main__":
    main()
