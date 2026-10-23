// Street names: every street (the game's road aggregate) in a box with its internal id and the name the game shows.
// A road edge points at its street through Game.Net.Aggregated.m_Aggregate; the aggregate entity is what the game names
// (NameSystem: the player's custom name if any, else the rendered label - the same text as the info panel).
using System;
using System.Collections.Generic;
using Game.Common;
using Game.Net;
using Game.Prefabs;
using Unity.Collections;
using Unity.Entities;

namespace CS2MCP
{
    public partial class McpBridgeSystem
    {
        string StreetName(Entity street, out bool custom)
        {
            custom = m_Names.TryGetCustomName(street, out var n);
            return custom ? n : m_Names.GetRenderedLabelName(street);
        }

        // params: xmin/zmin/xmax/zmax (a road edge counts when one of its ends is inside, as in query_roads), limit (streets, default 200),
        // edges (bool, default false: also list each street's edge ids)
        object StreetNames(Dictionary<string, object> p)
        {
            var box = BoxOf(p); int limit = (int)Json.Num(p, "limit", 200); bool withEdges = Json.Bool(p, "edges", false);
            var streets = new Dictionary<Entity, StreetAcc>();
            int edgesSeen = 0, unnamedEdges = 0;
            using (var ents = m_EdgeQuery.ToEntityArray(Allocator.Temp))
                foreach (var e in ents)
                {
                    var cv = EntityManager.GetComponentData<Curve>(e);
                    if (!box.In(cv.m_Bezier.a) && !box.In(cv.m_Bezier.d)) continue;
                    edgesSeen++;
                    // highways, ramps and some utilities belong to no street
                    Entity street = EntityManager.HasComponent<Aggregated>(e) ? EntityManager.GetComponentData<Aggregated>(e).m_Aggregate : Entity.Null;
                    if (street == Entity.Null || !EntityManager.Exists(street)) { unnamedEdges++; continue; }
                    if (!streets.TryGetValue(street, out var acc)) streets[street] = acc = new StreetAcc { Types = new HashSet<string>() };
                    acc.Edges++; acc.Length += cv.m_Length;
                    acc.Types.Add(m_Prefabs.GetPrefabName(EntityManager.GetComponentData<PrefabRef>(e).m_Prefab));
                    if (withEdges) (acc.EdgeIds ?? (acc.EdgeIds = new List<object>())).Add(Id(e));
                }
            var list = new List<object>();
            foreach (var kv in streets)
            {
                var name = StreetName(kv.Key, out bool custom);
                var d = new Dictionary<string, object>
                {
                    ["id"] = Id(kv.Key), ["name"] = name, ["custom_name"] = custom, ["edges"] = kv.Value.Edges,
                    ["length"] = Math.Round(kv.Value.Length, 1), ["types"] = new List<string>(kv.Value.Types),
                };
                if (withEdges) d["edge_ids"] = kv.Value.EdgeIds;
                list.Add(d);
            }
            list.Sort((a, b) => string.CompareOrdinal((string)((Dictionary<string, object>)a)["name"], (string)((Dictionary<string, object>)b)["name"]));
            int total = list.Count;
            if (list.Count > limit) list.RemoveRange(limit, list.Count - limit);
            return new Dictionary<string, object>
            {
                ["edges_in_area"] = edgesSeen, ["edges_without_street"] = unnamedEdges, ["total_streets"] = total, ["returned"] = list.Count, ["streets"] = list,
            };
        }

        // params: renames [{id, new_name}] (ids from street_names), dry_run (bool, default false: only report what would change).
        // Names are set through NameSystem, like the street's info panel; the id must be a street (road aggregate), not an edge.
        object RenameStreets(Dictionary<string, object> p)
        {
            bool dry = Json.Bool(p, "dry_run", false);
            var items = Json.List(p, "renames") ?? throw new ArgumentException("renames: [{id, new_name}] required");
            var plan = new List<(Entity e, string was, string to)>();
            var seen = new HashSet<Entity>();
            foreach (var o in items)
            {
                var r = o as Dictionary<string, object> ?? throw new ArgumentException("each rename must be {id, new_name}");
                string id = Json.Str(r, "id", null) ?? throw new ArgumentException("rename needs an id (use street_names)");
                string to = (Json.Str(r, "new_name", null) ?? throw new ArgumentException($"rename {id} needs new_name")).Trim();
                if (to.Length == 0) throw new ArgumentException($"new_name for {id} is empty");
                var e = ParseId(id);
                if (!EntityManager.Exists(e) || !EntityManager.HasComponent<Aggregate>(e)) throw new ArgumentException($"{id} is not a street (use the id from street_names)");
                if (!seen.Add(e)) throw new ArgumentException($"street {id} listed twice");
                plan.Add((e, StreetName(e, out _), to));
            }
            var done = new List<object>();
            foreach (var (e, was, to) in plan)
            {
                if (!dry) m_Names.SetCustomName(e, to);
                done.Add(new Dictionary<string, object> { ["id"] = Id(e), ["was"] = was, ["name"] = to });
            }
            return new Dictionary<string, object> { ["dry_run"] = dry, ["renamed"] = dry ? 0 : done.Count, ["streets"] = done };
        }

        sealed class StreetAcc { public int Edges; public double Length; public HashSet<string> Types; public List<object> EdgeIds; }
    }
}
