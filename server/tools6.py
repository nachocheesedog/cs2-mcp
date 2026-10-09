"""v0.15 tools (2026-09-24): city services - where coverage is thin, from the game's own numbers (mod `coverage`).

- service_report   per district: resident-weighted coverage per service, residents under-served, crime / garbage over the
                   game's happiness thresholds, school-age children / teens not in school, sick; the facilities (schools by
                   level, hospitals, police, garbage) with load / capacity; and hotspots = clusters of grid cells where many
                   residents are under-served, with a centre to put the next building near.
Generic: the thresholds default to the game's own (neutral education, negligible crime, garbage baseline) or can be given.
"""
import math, collections
import tools5

SERVICES = ("healthcare", "police", "education", "garbage", "fire", "park", "post", "welfare", "deathcare")


def _fetch(rpc, a):
    areas = tools5.pick_areas(rpc, a)
    box = tools5.bbox([p for _, p in areas], 50)
    r = rpc("coverage", box, timeout=180)
    cols = {c: i for i, c in enumerate(r["columns"])}
    return areas, r, cols


def _bad(row, c, svc, thr):
    """(is this building under-served for svc, severity 0..1)."""
    if svc == "garbage":
        g = row[c["garbage"]]; return g > thr["garbage"], min(1.0, g / max(1, 4 * thr["garbage"]))
    if svc == "police":        # coverage is near-uniform in practice; crime is what hurts - flag it well before the game's threshold
        cr = row[c["crime"]]; cov = row[c["police"]]
        return (cr > thr["crime_watch"] or 0 <= cov < thr["police"]), min(1.0, cr / max(1, thr["crime"]))
    if svc == "education":
        out = (row[c["children"]] - row[c["children_in_school"]]) + (row[c["teens"]] - row[c["teens_in_school"]])
        cov = row[c["education"]]
        return (out > 0 or 0 <= cov < thr["education"]), min(1.0, out / 5)
    if svc == "deathcare":     # mod 0.27: bodies in this building waiting for a hearse
        n = row[c["dead_waiting"]] if "dead_waiting" in c else 0
        return n > 0, min(1.0, n / 3)
    cov = row[c[svc]]
    return 0 <= cov < thr[svc], 1.0 - max(0.0, cov) / max(1e-6, thr[svc])


def service_report(rpc, a):
    areas, r, c = _fetch(rpc, a)
    want = a.get("services") or ["healthcare", "police", "education", "garbage"]
    if "deathcare" in want and "dead_waiting" not in c:
        return {"error": "deathcare needs mod 0.27+ (dead_waiting column); the game is running an older mod - restart it after deploying"}
    t = r["thresholds"]
    # coverage scales seen in game (2026-09-24): healthcare / fire 0..~11, park 0..~25, police ~9, education 0..~3 with the
    # game's neutral_education 5; crime happiness threshold (negligible_crime) 5000; garbage baseline 500
    ne = t.get("neutral_education") or 5
    thr = {"healthcare": 2.0, "fire": 2.0, "park": 2.0, "post": 1.0, "welfare": 1.0, "police": 2.0,
           "education": ne / 2, "crime": t.get("negligible_crime") or 5000, "garbage": t.get("garbage_baseline") or 500}
    thr["crime_watch"] = thr["crime"] / 5
    thr.update(a.get("thresholds") or {})
    grid = float(a.get("grid_m", 300)); min_res = int(a.get("min_residents", 50)); top = int(a.get("hotspots", 5))

    per = collections.defaultdict(lambda: collections.defaultdict(float))
    cells = {s: collections.defaultdict(lambda: [0, 0, 0.0, None]) for s in want}   # [bad residents, residents, severity sum, district]
    for row in r["rows"]:
        if row[c["kind"]] != 1: continue
        res = row[c["residents"]]
        if res <= 0: continue
        d = tools5.which((row[c["x"]], row[c["z"]]), areas) or "(no district)"
        P = per[d]; P["residents"] += res; P["buildings"] += 1
        for s in ("healthcare", "police", "education", "fire", "park", "post"):
            if row[c[s]] >= 0: P["cov_" + s] += row[c[s]] * res; P["covw_" + s] += res
        P["children"] += row[c["children"]]; P["teens"] += row[c["teens"]]
        P["children_out"] += row[c["children"]] - row[c["children_in_school"]]
        P["teens_out"] += row[c["teens"]] - row[c["teens_in_school"]]
        P["sick"] += row[c["sick"]]; P["health_sum"] += row[c["avg_health"]] * res
        if "dead_waiting" in c: P["dead_waiting"] += row[c["dead_waiting"]]
        if row[c["crime"]] > thr["crime"]: P["res_high_crime"] += res
        if row[c["crime"]] > thr["crime_watch"]: P["res_crime_watch"] += res
        if row[c["healthcare"]] == 0: P["res_no_healthcare"] += res
        if 0 <= row[c["education"]] < ne: P["res_edu_below_neutral"] += res
        if row[c["garbage"]] > thr["garbage"]: P["res_garbage"] += res
        key = (int(row[c["x"]] // grid), int(row[c["z"]] // grid))
        for s in want:
            bad, sev = _bad(row, c, s, thr)
            cell = cells[s][key]; cell[1] += res; cell[3] = cell[3] or d
            if bad: cell[0] += res; cell[2] += sev * res
            P["under_" + s] += res if bad else 0

    def pct(n, d): return f"{100.0 * n / d:.0f}%" if d else "-"
    out_d = {}
    for d, P in sorted(per.items(), key=lambda kv: -kv[1]["residents"]):
        R = P["residents"]
        e = {"residents": int(R)}
        for s in want:
            item = {"under_served": pct(P["under_" + s], R)}
            if s in ("healthcare", "police", "education", "fire", "park", "post") and P["covw_" + s]:
                item["avg_coverage"] = round(P["cov_" + s] / P["covw_" + s], 2)
            if s == "police":
                item["residents_over_game_crime_threshold"] = pct(P["res_high_crime"], R)
                item["residents_crime_rising"] = pct(P["res_crime_watch"], R)
            if s == "garbage": item["residents_over_baseline"] = pct(P["res_garbage"], R)
            if s == "education":
                item["children_not_in_school"] = f"{int(P['children_out'])} of {int(P['children'])}"
                item["teens_not_in_school"] = f"{int(P['teens_out'])} of {int(P['teens'])}"
                item["below_game_neutral"] = pct(P["res_edu_below_neutral"], R)
            if s == "healthcare":
                item["no_coverage_at_all"] = pct(P["res_no_healthcare"], R)
                item["sick"] = int(P["sick"]); item["avg_health"] = round(P["health_sum"] / R, 1)
            if s == "deathcare":
                item["dead_waiting_for_hearse"] = int(P["dead_waiting"])
                item["per_10k_residents"] = round(1e4 * P["dead_waiting"] / R, 1)
            e[s] = item
        out_d[d] = e

    # hotspots: flood-fill adjacent cells that are >= 40% under-served, rank clusters by under-served residents
    hot = {}
    for s in want:
        bad = {k for k, v in cells[s].items() if v[1] >= 10 and v[0] >= 0.4 * v[1]}
        seen, clusters = set(), []
        for k in bad:
            if k in seen: continue
            stack, members = [k], []
            seen.add(k)
            while stack:
                q = stack.pop(); members.append(q)
                for dx in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        n = (q[0] + dx, q[1] + dz)
                        if n in bad and n not in seen: seen.add(n); stack.append(n)
            nb = sum(cells[s][m][0] for m in members)
            if nb < min_res: continue
            wx = sum((m[0] + 0.5) * grid * cells[s][m][0] for m in members) / nb
            wz = sum((m[1] + 0.5) * grid * cells[s][m][0] for m in members) / nb
            ds = collections.Counter(cells[s][m][3] for m in members).most_common(1)[0][0]
            clusters.append({"centre": [round(wx), round(wz)], "under_served_residents": int(nb),
                             "area_km2": round(len(members) * grid * grid / 1e6, 2), "district": ds})
        clusters.sort(key=lambda h: -h["under_served_residents"])
        hot[s] = clusters[:top]

    fac = collections.defaultdict(list)
    for f in r["services"]:
        k = f["kind"] + (f" L{f['level']}" if f["kind"] == "school" else "")
        use = f"{f['load']}/{f['capacity']}" + (f" ({100 * f['load'] / f['capacity']:.0f}%)" if f["capacity"] else "")
        fac[k].append({"type": f["type"], "position": f["position"], "use": use, "efficiency": f["efficiency"], "why": f.get("efficiency_factors"),
                       "district": tools5.which(tuple(f["position"]), areas)})
    fsum = {}
    for k, L in sorted(fac.items()):
        load = sum(int(x["use"].split("/")[0]) for x in L); cap = sum(int(x["use"].split("/")[1].split()[0]) for x in L)
        full = [x for x in L if x["use"].endswith("%)") and int(x["use"].split("(")[1][:-2]) >= 90]
        fsum[k] = {"count": len(L), "use": f"{load}/{cap}" + (f" ({100 * load / cap:.0f}%)" if cap else ""),
                   "at_90pct_or_more": [(x["type"], x["position"], x["use"]) for x in full][:8],
                   "low_efficiency": [(x["type"], x["position"], x["efficiency"], x["why"]) for x in L if x["efficiency"] < 0.75][:8]}
    if "deathcare" in fac:
        L = [f for f in r["services"] if f["kind"] == "deathcare"]
        by_type = collections.Counter(f["type"] for f in L)
        proc = sum(f["processing"] * f["efficiency"] for f in L)
        fsum["deathcare"].update({
            "by_type": dict(by_type), "hearses": sum(f["hearses"] for f in L),
            "hearses_out": sum(f["vehicles_out"] for f in L),
            "processing_per_month": round(proc),
            "full_no_room": [(f["type"], f["position"], f"{f['load']}/{f['capacity']}") for f in L if not f["has_room"]][:12],
            "full_count": sum(1 for f in L if not f["has_room"]),
            "no_hearse_available": sum(1 for f in L if not f["hearses_available"]),
            "note": "a facility with no room stops sending hearses; processing = bodies per game month at current efficiency"})
    out = {"thresholds": thr, "districts": out_d, "hotspots": hot, "facilities": fsum,
            "legend": "under_served = share of residents whose building is below the threshold (coverage) or over it "
                      "(crime / garbage), or has children/teens not in school (education); hotspots = clusters of "
                      f"{grid:.0f} m cells with >=40% under-served, centre = where a new facility helps most"}
    if r.get("health_census"): out["health_census"] = r["health_census"]
    return out
