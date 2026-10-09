// Obstacles: real footprints of what stands on the ground in a box - buildings (prefab geometry bounds + lot), pillars of
// elevated roads (owned by the road's edge/node), trees and free-standing props. Facts only; the client decides.
using System;
using System.Collections.Generic;
using Game.Buildings;
using Game.Common;
using Game.Net;
using Game.Prefabs;
using Game.Tools;
using Unity.Collections;
using Unity.Entities;
using Unity.Mathematics;
using Transform = Game.Objects.Transform;

namespace CS2MCP
{
    public partial class McpBridgeSystem
    {
        EntityQuery m_ObjectQuery;

        object Obstacles(Dictionary<string, object> p)
        {
            if (m_ObjectQuery == default)
                m_ObjectQuery = GetEntityQuery(new EntityQueryDesc
                {
                    All = new[] { ComponentType.ReadOnly<Game.Objects.Object>(), ComponentType.ReadOnly<Transform>(), ComponentType.ReadOnly<PrefabRef>() },
                    None = new[] { ComponentType.ReadOnly<Deleted>(), ComponentType.ReadOnly<Temp>() },
                });
            var box = BoxOf(p); float pad = (float)Json.Num(p, "pad", 40);
            var wide = new Box { x0 = box.x0 - pad, x1 = box.x1 + pad, z0 = box.z0 - pad, z1 = box.z1 + pad };
            int limit = (int)Json.Num(p, "limit", 5000), total = 0;
            var cats = Json.List(p, "categories");
            var want = cats == null ? null : new HashSet<string>(cats.ConvertAll(o => (string)o));
            bool names = Json.Bool(p, "names", false), subProps = Json.Bool(p, "building_props", false);
            var list = new List<object>(); var counts = new Dictionary<string, object>();
            using (var ents = m_ObjectQuery.ToEntityArray(Allocator.Temp))
                foreach (var e in ents)
                {
                    var t = EntityManager.GetComponentData<Transform>(e);
                    if (!wide.In(t.m_Position)) continue;
                    Entity owner = EntityManager.HasComponent<Owner>(e) ? EntityManager.GetComponentData<Owner>(e).m_Owner : Entity.Null;
                    string cat;
                    if (EntityManager.HasComponent<Game.Objects.Pillar>(e)) cat = "pillar";
                    else if (EntityManager.HasComponent<Building>(e)) cat = owner == Entity.Null ? "building" : "building_part";
                    else if (EntityManager.HasComponent<Game.Objects.Tree>(e)) cat = "tree";
                    else if (owner != Entity.Null && (EntityManager.HasComponent<Edge>(owner) || EntityManager.HasComponent<Node>(owner))) cat = "road_prop";
                    else if (owner != Entity.Null) { if (!subProps) continue; cat = "building_prop"; }
                    else cat = "prop";
                    if (want != null && !want.Contains(cat)) continue;
                    var prefab = EntityManager.GetComponentData<PrefabRef>(e).m_Prefab;
                    if (!EntityManager.HasComponent<ObjectGeometryData>(prefab)) continue;
                    var g = EntityManager.GetComponentData<ObjectGeometryData>(prefab);
                    var b = g.m_Bounds;
                    var cs = new List<float[]>();
                    foreach (var q in new[] { new float3(b.min.x, 0, b.min.z), new float3(b.max.x, 0, b.min.z), new float3(b.max.x, 0, b.max.z), new float3(b.min.x, 0, b.max.z) })
                    {
                        var w = t.m_Position + math.mul(t.m_Rotation, q);
                        cs.Add(new[] { (float)Math.Round(w.x, 1), (float)Math.Round(w.z, 1) });
                    }
                    bool inside = false;
                    foreach (var c in cs) if (c[0] >= box.x0 && c[0] <= box.x1 && c[1] >= box.z0 && c[1] <= box.z1) { inside = true; break; }
                    if (!inside && !box.In(t.m_Position)) continue;
                    total++; counts[cat] = (counts.TryGetValue(cat, out var n) ? (int)n : 0) + 1;
                    if (list.Count >= limit) continue;
                    var d = new Dictionary<string, object>
                    {
                        ["id"] = Id(e), ["cat"] = cat, ["corners"] = cs,
                        ["y"] = (float)Math.Round(t.m_Position.y, 1), ["top"] = (float)Math.Round(t.m_Position.y + b.max.y, 1),
                    };
                    if (names || cat == "building") d["type"] = m_Prefabs.GetPrefabName(prefab);
                    if (owner != Entity.Null) d["owner"] = Id(owner);
                    if (cat == "building" && EntityManager.HasComponent<BuildingData>(prefab))
                    {
                        var l = EntityManager.GetComponentData<BuildingData>(prefab).m_LotSize;
                        var lc = new List<float[]>();
                        foreach (var q in new[] { new float3(-l.x * 4, 0, -l.y * 4), new float3(l.x * 4, 0, -l.y * 4), new float3(l.x * 4, 0, l.y * 4), new float3(-l.x * 4, 0, l.y * 4) })
                        {
                            var w = t.m_Position + math.mul(t.m_Rotation, q);
                            lc.Add(new[] { (float)Math.Round(w.x, 1), (float)Math.Round(w.z, 1) });
                        }
                        d["lot"] = lc;
                    }
                    list.Add(d);
                }
            return new Dictionary<string, object> { ["total"] = total, ["by_category"] = counts, ["returned"] = list.Count, ["objects"] = list,
                ["note"] = "corners = footprint from prefab geometry bounds; lot = the building's zoning lot. categories: building, building_part, pillar, tree, road_prop, prop, building_prop (only with building_props=true)" };
        }
    }
}
