// v0.27: lane facts and turning movements for junction / interchange design.
// - EdgeLanes: per road edge the car lanes running forward (start -> end node) and backward, and the speed limit, from the
//   edge's SubLane buffer (EdgeLane.m_EdgeDelta says which way a lane runs; master lanes of merged groups are skipped).
// - SiteMovements: the cars about to use a site (circle x, z, radius): for each car on an approach edge (an edge that
//   crosses the circle) whose planned path runs through the site, the approach it enters by and the one it leaves by
//   (or "ends_inside"), read from CarCurrentLane + the PathElement buffer. The server samples this over time.
using System;
using System.Collections.Generic;
using Colossal.Mathematics;
using Game.Common;
using Game.Net;
using Game.Pathfind;
using Game.Prefabs;
using Game.Tools;
using Edge = Game.Net.Edge;
using Game.Vehicles;
using Unity.Collections;
using Unity.Entities;
using Unity.Mathematics;
using Transform = Game.Objects.Transform;

namespace CS2MCP
{
    public partial class McpBridgeSystem
    {
        EntityQuery m_MovingCarQuery;

        // (forward car lanes, backward car lanes, speed limit m/s) of a road edge
        (int fwd, int back, float speed) EdgeLanes(Entity e)
        {
            int f = 0, b = 0; float sp = 0f;
            if (!EntityManager.HasBuffer<Game.Net.SubLane>(e)) return (0, 0, 0f);
            foreach (var sl in EntityManager.GetBuffer<Game.Net.SubLane>(e, true))
            {
                var l = sl.m_SubLane;
                if (!EntityManager.HasComponent<Game.Net.CarLane>(l) || !EntityManager.HasComponent<EdgeLane>(l)) continue;
                if (EntityManager.HasComponent<MasterLane>(l)) continue;
                if (EntityManager.HasComponent<Game.Net.ParkingLane>(l)) continue;
                // v0.29: a lane on a long edge is split into pieces (EdgeDelta 0-0.5, 0.5-1): count each lane once, by the
                // piece that starts at its own start (forward lanes start at 0, backward lanes at 1)
                var d = EntityManager.GetComponentData<EdgeLane>(l).m_EdgeDelta;
                if (d.y > d.x) { if (d.x < 0.001f) f++; }
                else if (d.x > 0.999f) b++;
                sp = math.max(sp, EntityManager.GetComponentData<Game.Net.CarLane>(l).m_SpeedLimit);
            }
            return (f, b, sp);
        }

        Entity LaneEdge(Entity lane)
        {
            if (lane == Entity.Null || !EntityManager.HasComponent<Owner>(lane)) return Entity.Null;
            var o = EntityManager.GetComponentData<Owner>(lane).m_Owner;
            return EntityManager.HasComponent<Edge>(o) ? o : Entity.Null;
        }

        // v0.28 debug: every sub-lane of one edge with its components and direction (to verify lane counting)
        object EdgeLaneDetail(Dictionary<string, object> p)
        {
            var e = ParseId(Json.Str(p, "id", null) ?? throw new ArgumentException("id (edge) required"));
            if (!EntityManager.Exists(e) || !EntityManager.HasBuffer<Game.Net.SubLane>(e)) throw new ArgumentException("not an edge with lanes");
            var list = new List<object>();
            foreach (var sl in EntityManager.GetBuffer<Game.Net.SubLane>(e, true))
            {
                var l = sl.m_SubLane; var comps = new List<string>();
                using (var types = EntityManager.GetComponentTypes(l)) foreach (var t in types) comps.Add(t.GetManagedType().Name);
                var d = new Dictionary<string, object> { ["lane"] = Id(l), ["path_methods"] = sl.m_PathMethods.ToString(), ["components"] = comps,
                    ["prefab"] = EntityManager.HasComponent<PrefabRef>(l) ? m_Prefabs.GetPrefabName(EntityManager.GetComponentData<PrefabRef>(l).m_Prefab) : null };
                if (EntityManager.HasComponent<EdgeLane>(l)) { var dd = EntityManager.GetComponentData<EdgeLane>(l).m_EdgeDelta; d["edge_delta"] = new[] { dd.x, dd.y }; }
                if (EntityManager.HasComponent<Game.Net.CarLane>(l)) d["car_flags"] = EntityManager.GetComponentData<Game.Net.CarLane>(l).m_Flags.ToString();
                list.Add(d);
            }
            var (fw, bk, sp) = EdgeLanes(e);
            return new Dictionary<string, object> { ["edge"] = Id(e), ["counted"] = new[] { fw, bk }, ["sub_lanes"] = list };
        }

        object SiteMovements(Dictionary<string, object> p)
        {
            float cx = (float)Json.Num(p, "x", double.NaN), cz = (float)Json.Num(p, "z", double.NaN);
            if (float.IsNaN(cx) || float.IsNaN(cz)) throw new ArgumentException("x, z (site centre) required");
            float r = (float)Json.Num(p, "radius", 150), ring = (float)Json.Num(p, "ring", 250);
            int maxPath = (int)Json.Num(p, "max_path", 80);
            var c = new float2(cx, cz);
            bool Inside(float3 q) => math.distance(q.xz, c) <= r;

            // v0.28: edges by how they meet the circle - inside (both ends in), approach (one end in), chord (both ends out but
            // the curve passes through: a long road piece crossing the whole site, e.g. a highway carriageway)
            var approach = new Dictionary<Entity, Dictionary<string, object>>();
            var inside = new HashSet<Entity>(); var chord = new HashSet<Entity>();
            var bounds = new Bounds2(c - (r + ring), c + (r + ring));
            using (var ents = m_EdgeQuery.ToEntityArray(Allocator.Temp))
                foreach (var e in ents)
                {
                    var cv = EntityManager.GetComponentData<Curve>(e);
                    if (!MathUtils.Intersect(MathUtils.Bounds(cv.m_Bezier.xz), bounds)) continue;
                    if (!EntityManager.HasBuffer<Game.Net.SubLane>(e)) continue;
                    bool a = Inside(cv.m_Bezier.a), d = Inside(cv.m_Bezier.d), any = a || d;
                    for (int k = 1; k < 16 && !any; k++) any = Inside(MathUtils.Position(cv.m_Bezier, k / 16f));
                    if (!any) continue;
                    if (a && d) { inside.Add(e); continue; }
                    var (fw, bk, sp) = EdgeLanes(e);
                    if (fw + bk == 0) continue;
                    bool isChord = !a && !d;
                    if (isChord) chord.Add(e);
                    var outer = a ? cv.m_Bezier.d : cv.m_Bezier.a;
                    var dir = math.normalizesafe(outer.xz - c);
                    approach[e] = new Dictionary<string, object>
                    {
                        ["edge"] = Id(e), ["type"] = m_Prefabs.GetPrefabName(EntityManager.GetComponentData<PrefabRef>(e).m_Prefab),
                        ["outer_end"] = P3(outer), ["bearing_deg"] = Math.Round((math.degrees(math.atan2(dir.x, dir.y)) + 360) % 360, 1),
                        // lanes running INTO the site / OUT of it (a chord: forward / backward)
                        ["lanes_in"] = isChord ? fw : (a ? bk : fw), ["lanes_out"] = isChord ? bk : (a ? fw : bk),
                        ["speed_limit"] = Math.Round(sp, 1), ["speed_kmh"] = Math.Round(sp * 1.8f),
                        ["passes_through"] = isChord,
                    };
                }

            if (m_MovingCarQuery == default)
                m_MovingCarQuery = GetEntityQuery(new EntityQueryDesc
                {
                    All = new[] { ComponentType.ReadOnly<Car>(), ComponentType.ReadOnly<CarCurrentLane>(), ComponentType.ReadOnly<Transform>() },
                    None = new[] { ComponentType.ReadOnly<Deleted>(), ComponentType.ReadOnly<Temp>(), ComponentType.ReadOnly<Game.Objects.Unspawned>() },
                });
            var rows = new List<object>(); int seen = 0, noPath = 0;
            using (var cars = m_MovingCarQuery.ToEntityArray(Allocator.Temp))
                foreach (var car in cars)
                {
                    var pos = EntityManager.GetComponentData<Transform>(car).m_Position;
                    float dist = math.distance(pos.xz, c);
                    if (dist > r + ring) continue;
                    var cur = LaneEdge(EntityManager.GetComponentData<CarCurrentLane>(car).m_Lane);
                    // the edges ahead, in order: current edge, then the path's edge lanes
                    var seq = new List<Entity>();
                    if (cur != Entity.Null) seq.Add(cur);
                    if (EntityManager.HasBuffer<PathElement>(car) && EntityManager.HasComponent<PathOwner>(car))
                    {
                        var path = EntityManager.GetBuffer<PathElement>(car, true);
                        int i0 = math.max(0, EntityManager.GetComponentData<PathOwner>(car).m_ElementIndex);
                        for (int i = i0; i < path.Length && i < i0 + maxPath; i++)
                        {
                            var ed = LaneEdge(path[i].m_Target);
                            if (ed != Entity.Null && (seq.Count == 0 || seq[seq.Count - 1] != ed)) seq.Add(ed);
                        }
                    }
                    else noPath++;
                    // entry = the first approach / chord edge ahead of the car
                    int ei = -1;
                    for (int i = 0; i < seq.Count; i++) if (approach.ContainsKey(seq[i])) { ei = i; break; }
                    if (ei < 0) continue;
                    string exit = null;
                    if (chord.Contains(seq[ei])) exit = Id(seq[ei]);             // straight through on the same road piece
                    else
                    {
                        if (ei + 1 >= seq.Count) exit = "path_unknown";
                        else if (!inside.Contains(seq[ei + 1]) && !approach.ContainsKey(seq[ei + 1])) continue;   // heading away: leaving
                        else
                        {
                            bool through = false;
                            for (int i = ei + 1; i < seq.Count; i++)
                            {
                                if (inside.Contains(seq[i])) { through = true; continue; }
                                if (approach.ContainsKey(seq[i])) { exit = Id(seq[i]); break; }
                                if (through) break;
                            }
                            if (exit == null) exit = "ends_inside";
                        }
                    }
                    seen++;
                    rows.Add(new object[] { Id(car), Id(seq[ei]), exit });
                }
            return new Dictionary<string, object>
            {
                ["site"] = new[] { cx, cz }, ["radius"] = r, ["approaches"] = new List<object>(approach.Values),
                ["columns"] = new[] { "vehicle", "entry_edge", "exit_edge" }, ["cars"] = rows, ["cars_counted"] = seen,
                ["cars_without_path"] = noPath,
                ["note"] = "snapshot: cars on or approaching the site with their entry and (planned) exit approach; sample repeatedly and dedupe by vehicle",
            };
        }
    }
}
