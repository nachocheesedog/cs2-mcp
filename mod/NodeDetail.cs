// v0.40: junction inspector (check that conflicting directions never connect, especially on
// highways). For one node (id, or the nearest node to [x, z]): every road meeting there (type, which end, heading away from
// the node, elevation, car lanes flowing in / out of the node) and every car movement THROUGH the node - the node's own car
// lanes (SubLane buffer of the node), each matched to the road it comes from and the road it leaves by (nearest connected
// edge curve to the lane's start / end point), with the game's turn flags. Issues: roads whose lanes only flow in with no
// way out (trap) or only out with nothing arriving (source), and movements joining a highway to a non-highway road at grade.
using System;
using System.Collections.Generic;
using Colossal.Mathematics;
using Game.Common;
using Game.Net;
using Game.Prefabs;
using Unity.Entities;
using Unity.Mathematics;
using Edge = Game.Net.Edge;

namespace CS2MCP
{
    public partial class McpBridgeSystem
    {
        object NodeDetail(Dictionary<string, object> p)
        {
            Entity node = Entity.Null;
            var sid = Json.Str(p, "id", null);
            if (sid != null) node = ParseId(sid);
            else
            {
                var atl = Json.List(p, "at") ?? throw new ArgumentException("id (node) or at [x, z] required");
                var at = new[] { Convert.ToSingle(atl[0]), Convert.ToSingle(atl[1]) };
                float best = (float)Json.Num(p, "radius", 40.0);
                var q = EntityManager.CreateEntityQuery(ComponentType.ReadOnly<Node>(), ComponentType.ReadOnly<ConnectedEdge>(), ComponentType.Exclude<Deleted>());
                using (var ents = q.ToEntityArray(Unity.Collections.Allocator.Temp))
                    foreach (var n in ents)
                    {
                        var pos = EntityManager.GetComponentData<Node>(n).m_Position;
                        float dd = math.distance(pos.xz, new float2(at[0], at[1]));
                        if (dd < best) { best = dd; node = n; }
                    }
                if (node == Entity.Null) throw new ArgumentException("no node within radius of at");
            }
            if (!EntityManager.Exists(node) || !EntityManager.HasComponent<Node>(node)) throw new ArgumentException("not a node");
            var npos = EntityManager.GetComponentData<Node>(node).m_Position;
            var edges = new List<Entity>(); var info = new List<object>(); var kind = new Dictionary<Entity, string>();
            var lanesIn = new Dictionary<Entity, int>(); var lanesOut = new Dictionary<Entity, int>();
            foreach (var ce in EntityManager.GetBuffer<ConnectedEdge>(node, true))
            {
                var e = ce.m_Edge; if (!EntityManager.HasComponent<Edge>(e)) continue;
                var ed = EntityManager.GetComponentData<Edge>(e);
                bool atStart = ed.m_Start == node, atEnd = ed.m_End == node;
                if (!atStart && !atEnd) continue;                 // a node in the middle of an edge (sub-object), not a junction arm
                edges.Add(e);
                var c = EntityManager.GetComponentData<Curve>(e).m_Bezier;
                float3 tan = atStart ? MathUtils.StartTangent(c) : -MathUtils.EndTangent(c);
                var (fw, bk, sp) = EdgeLanes(e);
                // forward lanes run start -> end: they flow OUT of the node when the node is the edge's start
                int outN = atStart ? fw : bk, inN = atStart ? bk : fw;
                lanesIn[e] = inN; lanesOut[e] = outN;
                string type = EntityManager.HasComponent<PrefabRef>(e) ? m_Prefabs.GetPrefabName(EntityManager.GetComponentData<PrefabRef>(e).m_Prefab) : "?";
                kind[e] = type.Contains("Highway") ? "highway" : type.Contains("Ramp") ? "ramp" : "road";
                var d = new Dictionary<string, object> { ["edge"] = Id(e), ["type"] = type, ["end"] = atStart ? "start" : "end",
                    ["heading_out_deg"] = Math.Round((math.degrees(math.atan2(tan.z, tan.x)) + 360.0) % 360.0, 1),
                    ["car_lanes_in"] = inN, ["car_lanes_out"] = outN, ["speed_kmh"] = Math.Round(sp * 3.6, 0) };
                if (EntityManager.HasComponent<Elevation>(e)) { var el = EntityManager.GetComponentData<Elevation>(e).m_Elevation; d["elevation"] = new[] { el.x, el.y }; }
                info.Add(d);
            }
            Entity Nearest(float3 pt)
            {
                Entity bestE = Entity.Null; float bd = float.MaxValue;
                foreach (var e in edges)
                {
                    var c = EntityManager.GetComponentData<Curve>(e).m_Bezier;
                    for (int i = 0; i <= 16; i++) { float dd = math.distance(MathUtils.Position(c, i / 16f), pt); if (dd < bd) { bd = dd; bestE = e; } }
                }
                return bestE;
            }
            var moves = new Dictionary<string, Dictionary<string, object>>(); var usedFrom = new HashSet<Entity>(); var usedTo = new HashSet<Entity>();
            if (EntityManager.HasBuffer<Game.Net.SubLane>(node))
                foreach (var sl in EntityManager.GetBuffer<Game.Net.SubLane>(node, true))
                {
                    var l = sl.m_SubLane;
                    if (!EntityManager.HasComponent<Game.Net.CarLane>(l) || !EntityManager.HasComponent<Curve>(l)) continue;
                    if (EntityManager.HasComponent<MasterLane>(l) || EntityManager.HasComponent<Game.Net.ParkingLane>(l)) continue;
                    var c = EntityManager.GetComponentData<Curve>(l).m_Bezier;
                    var from = Nearest(c.a); var to = Nearest(c.d);
                    if (from == Entity.Null || to == Entity.Null) continue;
                    usedFrom.Add(from); usedTo.Add(to);
                    var flags = EntityManager.GetComponentData<Game.Net.CarLane>(l).m_Flags;
                    string key = Id(from) + ">" + Id(to);
                    if (!moves.TryGetValue(key, out var m))
                        moves[key] = m = new Dictionary<string, object> { ["from"] = Id(from), ["to"] = Id(to), ["lanes"] = 0, ["turn"] = new HashSet<string>(),
                            ["kinds"] = kind[from] + ">" + kind[to] };
                    m["lanes"] = (int)m["lanes"] + 1;
                    var turns = (HashSet<string>)m["turn"];
                    if (from == to) turns.Add("u-turn");
                    foreach (var fl in new[] { "UTurnLeft", "UTurnRight", "TurnLeft", "TurnRight", "GentleTurnLeft", "GentleTurnRight", "Forward" })
                        if (flags.ToString().Contains(fl)) turns.Add(fl);
                }
            var mv = new List<object>(); var issues = new List<object>();
            foreach (var m in moves.Values)
            {
                m["turn"] = new List<string>((HashSet<string>)m["turn"]);
                mv.Add(m);
                var k = (string)m["kinds"];
                if ((k.StartsWith("highway>") && k.EndsWith(">road")) || (k.StartsWith("road>") && k.EndsWith(">highway")))
                    issues.Add(new Dictionary<string, object> { ["issue"] = "highway_joins_road_at_grade", ["from"] = m["from"], ["to"] = m["to"] });
                if ((string)m["from"] == (string)m["to"] && kind[ParseId((string)m["from"])] == "highway")
                    issues.Add(new Dictionary<string, object> { ["issue"] = "u_turn_on_highway", ["edge"] = m["from"] });
            }
            foreach (var e in edges)
            {
                if (lanesIn[e] > 0 && !usedFrom.Contains(e) && edges.Count > 1)
                    issues.Add(new Dictionary<string, object> { ["issue"] = "trap: lanes arrive but no movement leaves", ["edge"] = Id(e) });
                if (lanesOut[e] > 0 && !usedTo.Contains(e) && edges.Count > 1)
                    issues.Add(new Dictionary<string, object> { ["issue"] = "source: lanes leave but no movement feeds them", ["edge"] = Id(e) });
            }
            int totIn = 0, totOut = 0; foreach (var e in edges) { totIn += lanesIn[e]; totOut += lanesOut[e]; }
            if (edges.Count == 1) issues.Add(new Dictionary<string, object> { ["issue"] = "dead end", ["edge"] = Id(edges[0]) });
            if (edges.Count >= 2 && (totIn == 0 || totOut == 0))
                issues.Add(new Dictionary<string, object> { ["issue"] = totIn == 0 ? "head-to-head: every arm flows OUT of this node" : "head-on: every arm flows INTO this node" });
            return new Dictionary<string, object> { ["node"] = Id(node), ["pos"] = new[] { npos.x, npos.y, npos.z }, ["arms"] = info,
                ["movements"] = mv, ["issues"] = issues };
        }
    }
}
