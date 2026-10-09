// Areas (surfaces, lots, spaces, map tiles) in a box, prefab diagnostics (which components a prefab has - to spot
// placeholders left by removed mods), and deleting areas the way the area tool does (CreationFlags.Delete).
using System;
using System.Collections.Generic;
using Game.Common;
using Game.Prefabs;
using Game.Tools;
using Unity.Collections;
using Unity.Entities;
using Unity.Mathematics;

namespace CS2MCP
{
    public partial class McpBridgeSystem
    {
        EntityQuery m_AreaQuery;

        EntityQuery AreaQuery()
        {
            if (m_AreaQuery == default)
                m_AreaQuery = GetEntityQuery(new EntityQueryDesc
                {
                    All = new[] { ComponentType.ReadOnly<Game.Areas.Area>(), ComponentType.ReadOnly<Game.Areas.Node>(), ComponentType.ReadOnly<PrefabRef>() },
                    None = new[] { ComponentType.ReadOnly<Deleted>(), ComponentType.ReadOnly<Temp>() },
                });
            return m_AreaQuery;
        }

        string AreaKind(Entity e) =>
            EntityManager.HasComponent<Game.Areas.Surface>(e) ? "surface" : EntityManager.HasComponent<Game.Areas.Lot>(e) ? "lot" :
            EntityManager.HasComponent<Game.Areas.Space>(e) ? "space" : EntityManager.HasComponent<Game.Areas.District>(e) ? "district" :
            EntityManager.HasComponent<Game.Areas.MapTile>(e) ? "map_tile" : "other";

        object Areas(Dictionary<string, object> p)
        {
            var box = BoxOf(p); int limit = (int)Json.Num(p, "limit", 500);
            var kinds = Json.List(p, "kinds");
            var want = kinds == null ? new HashSet<string> { "surface", "lot", "space", "other" } : new HashSet<string>(kinds.ConvertAll(o => (string)o));
            var list = new List<object>(); var counts = new Dictionary<string, object>();
            using (var es = AreaQuery().ToEntityArray(Allocator.Temp))
                foreach (var e in es)
                {
                    string kind = AreaKind(e);
                    if (!want.Contains(kind)) continue;
                    var nodes = EntityManager.GetBuffer<Game.Areas.Node>(e, true);
                    bool inside = false;
                    for (int i = 0; i < nodes.Length && !inside; i++) inside = box.In(nodes[i].m_Position);
                    if (!inside) continue;
                    string prefab = m_Prefabs.GetPrefabName(EntityManager.GetComponentData<PrefabRef>(e).m_Prefab);
                    string key = kind + ": " + prefab;
                    counts[key] = (counts.TryGetValue(key, out var c) ? (int)c : 0) + 1;
                    if (list.Count >= limit) continue;
                    var pts = new List<float[]>();
                    for (int i = 0; i < nodes.Length; i++) pts.Add(new[] { (float)Math.Round(nodes[i].m_Position.x, 1), (float)Math.Round(nodes[i].m_Position.z, 1) });
                    var d = new Dictionary<string, object> { ["id"] = Id(e), ["kind"] = kind, ["prefab"] = prefab, ["points"] = pts };
                    if (EntityManager.HasComponent<Owner>(e)) d["owner"] = Id(EntityManager.GetComponentData<Owner>(e).m_Owner);
                    list.Add(d);
                }
            return new Dictionary<string, object> { ["counts"] = counts, ["returned"] = list.Count, ["areas"] = list };
        }

        // which ECS components a prefab carries + its C# class: placeholders from removed mods lack mesh/render data
        object PrefabComponents(Dictionary<string, object> p)
        {
            string name = Json.Str(p, "name", null) ?? throw new ArgumentException("name required");
            var found = new List<object>();
            using (var ents = GetEntityQuery(ComponentType.ReadOnly<PrefabData>()).ToEntityArray(Allocator.Temp))
                foreach (var e in ents)
                {
                    if (!m_Prefabs.TryGetPrefab<PrefabBase>(e, out var pb) || pb.name != name) continue;
                    var comps = new List<string>();
                    using (var types = EntityManager.GetComponentTypes(e, Allocator.Temp))
                        foreach (var t in types) comps.Add(t.GetManagedType()?.Name ?? t.ToString());
                    comps.Sort(StringComparer.Ordinal);
                    var authoring = new List<string>();
                    if (pb.components != null) foreach (var c in pb.components) if (c != null) authoring.Add(c.GetType().Name);
                    found.Add(new Dictionary<string, object> { ["class"] = pb.GetType().FullName, ["components"] = comps, ["authoring"] = authoring });
                }
            return new Dictionary<string, object> { ["name"] = name, ["matches"] = found };
        }

        DefJob BuildDeleteAreas(Dictionary<string, object> p)
        {
            var ids = (Json.List(p, "ids") ?? throw new ArgumentException("ids: [area ids] required")).ConvertAll(o => ParseId((string)o));
            var items = new List<(Entity e, Entity prefab, List<float2> pts)>();
            foreach (var e in ids)
            {
                if (!EntityManager.Exists(e) || !EntityManager.HasComponent<Game.Areas.Area>(e)) throw new ArgumentException($"{Id(e)} is not an area");
                var nodes = EntityManager.GetBuffer<Game.Areas.Node>(e, true);
                var pts = new List<float2>();
                for (int i = 0; i < nodes.Length; i++) pts.Add(nodes[i].m_Position.xz);
                items.Add((e, EntityManager.GetComponentData<PrefabRef>(e).m_Prefab, pts));
            }
            var job = new DefJob { Kind = "delete_areas", DryRun = Json.Bool(p, "dry_run", false), AllowErrors = true, Definitions = items.Count };
            job.Info["areas"] = items.Count;
            job.Write = cb => { foreach (var (e, prefab, pts) in items) WriteArea(cb, prefab, e, pts, CreationFlags.Delete); };
            return job;
        }
    }
}
