// Districts: create (outline + name + policies), list, rename, set policies, delete - the way the game's own area tool,
// bulldozer and policy panel do it (see ref\ Game.Tools.AreaToolSystem, BulldozeToolSystem, Game.UI.InGame.PoliciesUISystem).
// A district is an area: CreationDefinition{district prefab} + Updated + a Game.Areas.Node buffer whose last node repeats
// the first (closed outline). Names go through NameSystem; policies are Event + Modify entities.
using System;
using System.Collections.Generic;
using Game.Areas;
using Game.Common;
using Game.Policies;
using Game.Prefabs;
using Game.Tools;
using Game.UI;
using Unity.Collections;
using Unity.Entities;
using Unity.Mathematics;

namespace CS2MCP
{
    public partial class McpBridgeSystem
    {
        NameSystem m_Names;
        EntityQuery m_DistrictPrefabQuery, m_DistrictQuery, m_DistrictPolicyQuery;
        EntityArchetype m_PolicyEventArchetype;

        void CreateDistricts()
        {
            m_Names = World.GetOrCreateSystemManaged<NameSystem>();
            m_DistrictPrefabQuery = GetEntityQuery(ComponentType.ReadOnly<PrefabData>(), ComponentType.ReadOnly<DistrictData>());
            m_DistrictQuery = GetEntityQuery(new EntityQueryDesc
            {
                All = new[] { ComponentType.ReadOnly<District>(), ComponentType.ReadOnly<Area>(), ComponentType.ReadOnly<Game.Areas.Node>() },
                None = new[] { ComponentType.ReadOnly<Deleted>(), ComponentType.ReadOnly<Temp>() },
            });
            m_PolicyEventArchetype = EntityManager.CreateArchetype(ComponentType.ReadWrite<Game.Common.Event>(), ComponentType.ReadWrite<Modify>());
            m_DistrictPolicyQuery = GetEntityQuery(new EntityQueryDesc
            {
                All = new[] { ComponentType.ReadOnly<PolicyData>() },
                Any = new[] { ComponentType.ReadOnly<DistrictOptionData>(), ComponentType.ReadOnly<DistrictModifierData>() },
            });
        }

        Entity DistrictPrefab()
        {
            using (var ps = m_DistrictPrefabQuery.ToEntityArray(Allocator.Temp))
                if (ps.Length > 0) return ps[0];
            throw new InvalidOperationException("no district prefab found");
        }

        HashSet<Entity> LiveDistricts()
        {
            var set = new HashSet<Entity>();
            using (var ds = m_DistrictQuery.ToEntityArray(Allocator.Temp)) foreach (var d in ds) set.Add(d);
            return set;
        }

        Dictionary<string, Entity> DistrictPolicyMap()
        {
            var map = new Dictionary<string, Entity>(StringComparer.OrdinalIgnoreCase);
            using (var ps = m_DistrictPolicyQuery.ToEntityArray(Allocator.Temp))
                foreach (var p in ps) map[m_Prefabs.GetPrefabName(p)] = p;
            return map;
        }

        string DistrictName(Entity d) => m_Names.TryGetCustomName(d, out var n) ? n : m_Names.GetRenderedLabelName(d);

        Entity DistrictOf(Dictionary<string, object> p)
        {
            string id = Json.Str(p, "id", null);
            if (id != null)
            {
                var e = ParseId(id);
                if (!LiveDistricts().Contains(e)) throw new ArgumentException($"no district with id {id}");
                return e;
            }
            string name = Json.Str(p, "name", null) ?? throw new ArgumentException("give the district's id or name");
            foreach (var d in LiveDistricts()) if (string.Equals(DistrictName(d), name, StringComparison.OrdinalIgnoreCase)) return d;
            throw new ArgumentException($"no district named '{name}' (use list_districts)");
        }

        static double PolygonArea(IList<float2> pts)
        {
            double a = 0;
            for (int i = 0; i < pts.Count; i++) { var p = pts[i]; var q = pts[(i + 1) % pts.Count]; a += p.x * q.y - q.x * p.y; }
            return Math.Abs(a) / 2;
        }

        List<float2> OutlineOf(Entity d)
        {
            var nodes = EntityManager.GetBuffer<Game.Areas.Node>(d, true);
            var pts = new List<float2>();
            for (int i = 0; i < nodes.Length; i++) pts.Add(nodes[i].m_Position.xz);
            if (pts.Count > 1 && math.distance(pts[0], pts[pts.Count - 1]) < 0.01f) pts.RemoveAt(pts.Count - 1);
            return pts;
        }

        // ------------------------------------------------------------------ queries / direct edits
        object ListDistricts(Dictionary<string, object> p)
        {
            bool outlines = Json.Bool(p, "outlines", true);
            var list = new List<object>();
            foreach (var d in LiveDistricts())
            {
                var pts = OutlineOf(d);
                float2 c = 0; foreach (var q in pts) c += q; if (pts.Count > 0) c /= pts.Count;
                var pol = new List<object>();
                if (EntityManager.HasBuffer<Policy>(d))
                {
                    var buf = EntityManager.GetBuffer<Policy>(d, true);
                    for (int i = 0; i < buf.Length; i++)
                        if ((buf[i].m_Flags & PolicyFlags.Active) != 0) pol.Add(m_Prefabs.GetPrefabName(buf[i].m_Policy));
                }
                var o = new Dictionary<string, object>
                {
                    ["id"] = Id(d), ["name"] = DistrictName(d), ["centre"] = new[] { (float)Math.Round(c.x, 1), (float)Math.Round(c.y, 1) },
                    ["area_km2"] = Math.Round(PolygonArea(pts) / 1e6, 3), ["policies"] = pol,
                };
                if (outlines) o["outline"] = pts.ConvertAll(q => (object)new[] { (float)Math.Round(q.x, 1), (float)Math.Round(q.y, 1) });
                list.Add(o);
            }
            return new Dictionary<string, object> { ["count"] = list.Count, ["districts"] = list };
        }

        object ListDistrictPolicies()
        {
            var map = DistrictPolicyMap();
            var names = new List<string>(map.Keys); names.Sort(StringComparer.Ordinal);
            var sliders = new Dictionary<string, object>();          // adjustable policies: the slider the `adjustment` value sets
            foreach (var n in names)
                if (EntityManager.HasComponent<PolicySliderData>(map[n]))
                {
                    var sd = EntityManager.GetComponentData<PolicySliderData>(map[n]);
                    sliders[n] = new Dictionary<string, object> { ["min"] = sd.m_Range.min, ["max"] = sd.m_Range.max, ["default"] = sd.m_Default,
                                                                  ["step"] = sd.m_Step, ["unit"] = sd.m_Unit };
                }
            return new Dictionary<string, object> { ["count"] = names.Count, ["policies"] = names, ["sliders"] = sliders,
                ["note"] = "adjustment is in the slider's own unit (the value the policy panel shows); unit is the game's UI unit id" };
        }

        object RenameDistrict(Dictionary<string, object> p)
        {
            var d = DistrictOf(p);
            string to = Json.Str(p, "new_name", null) ?? throw new ArgumentException("new_name required");
            string was = DistrictName(d);
            m_Names.SetCustomName(d, to);
            return new Dictionary<string, object> { ["id"] = Id(d), ["was"] = was, ["name"] = to };
        }

        void SetPolicies(Entity district, IEnumerable<string> names, bool active, float adjustment)
        {
            var map = DistrictPolicyMap();
            foreach (var n in names)
            {
                if (!map.TryGetValue(n, out var pol)) throw new ArgumentException($"unknown district policy '{n}' (use list_district_policies)");
                // like PoliciesUISystem.ModifyPolicy: through the end-frame barrier, so the event exists when the policy system
                // runs next frame (created directly mid-frame it could be cleaned up before anything read it)
                var cb = World.GetOrCreateSystemManaged<Game.EndFrameBarrier>().CreateCommandBuffer();
                var ev = cb.CreateEntity(m_PolicyEventArchetype);
                cb.SetComponent(ev, new Modify(district, pol, active, adjustment));
            }
        }

        object SetDistrictPolicy(Dictionary<string, object> p)
        {
            var d = DistrictOf(p);
            var names = (Json.List(p, "policies") ?? throw new ArgumentException("policies: [names] required")).ConvertAll(o => (string)o);
            bool active = Json.Bool(p, "active", true);
            SetPolicies(d, names, active, (float)Json.Num(p, "adjustment", 0));
            return new Dictionary<string, object> { ["id"] = Id(d), ["name"] = DistrictName(d), ["policies"] = names, ["active"] = active };
        }

        // ------------------------------------------------------------------ create / delete (tool jobs: preview -> errors -> apply)
        DefJob BuildDistrict(Dictionary<string, object> p, bool recordUndo, Action onApplied = null)
        {
            var poly = Json.List(p, "polygon") ?? throw new ArgumentException("polygon: [[x,z], ...] (3+ points, in order around the district) required");
            var pts = poly.ConvertAll(XZ);
            if (pts.Count > 1 && math.distance(pts[0], pts[pts.Count - 1]) < 0.5f) pts.RemoveAt(pts.Count - 1);
            if (pts.Count < 3) throw new ArgumentException("a district needs at least 3 outline points");
            string name = Json.Str(p, "name", null);
            var policies = (Json.List(p, "policies") ?? new List<object>()).ConvertAll(o => (string)o);
            var map = DistrictPolicyMap();
            foreach (var n in policies) if (!map.ContainsKey(n)) throw new ArgumentException($"unknown district policy '{n}' (use list_district_policies)");
            var prefab = DistrictPrefab();
            var before = LiveDistricts();
            var job = new DefJob { Kind = "create_district", DryRun = Json.Bool(p, "dry_run", false), AllowErrors = Json.Bool(p, "allow_errors", false),
                                   Definitions = 1, Hold = Json.Bool(p, "hold", false) };
            job.Info["points"] = pts.Count; job.Info["area_km2"] = Math.Round(PolygonArea(pts) / 1e6, 3);
            job.Write = cb => WriteArea(cb, prefab, Entity.Null, pts, (CreationFlags)0);
            job.OnApplied = summary =>
            {
                Entity made = Entity.Null;
                foreach (var d in LiveDistricts()) if (!before.Contains(d)) { made = d; break; }
                if (made == Entity.Null) { summary["warning"] = "applied, but the new district was not found"; return summary; }
                // naming and policies create command buffers: never inside the tool's apply stage (that threw
                // "Trying to create EntityCommandBuffer when it's not allowed!" although the district was made) - run next update
                var d0 = made;
                if (!string.IsNullOrEmpty(name)) m_Deferred.Add(() => m_Names.SetCustomName(d0, name));
                if (policies.Count > 0) m_Deferred.Add(() => SetPolicies(d0, policies, true, 0));
                summary["district_id"] = Id(made); summary["name"] = string.IsNullOrEmpty(name) ? DistrictName(made) : name;
                if (policies.Count > 0) summary["policies"] = policies;
                if (recordUndo)
                {
                    var u = new UndoEntry { Kind = "create_district", Desc = $"create district {DistrictName(made)}" };
                    u.Districts.Add(made); PushUndo(u); summary["undo_id"] = u.Id;
                }
                onApplied?.Invoke();
                return summary;
            };
            return job;
        }

        DefJob BuildDeleteDistrict(Dictionary<string, object> p, bool recordUndo) => MakeDeleteDistrictJob(DistrictOf(p), Json.Bool(p, "dry_run", false), recordUndo, null);

        DefJob MakeDeleteDistrictJob(Entity d, bool dryRun, bool recordUndo, Action onApplied)
        {
            var pts = OutlineOf(d); string name = DistrictName(d);
            var prefab = EntityManager.GetComponentData<PrefabRef>(d).m_Prefab;
            var job = new DefJob { Kind = "delete_district", DryRun = dryRun, AllowErrors = true, Definitions = 1 };
            job.Info["name"] = name;
            job.Write = cb => WriteArea(cb, prefab, d, pts, CreationFlags.Delete);
            job.OnApplied = summary =>
            {
                if (recordUndo)
                {
                    var u = new UndoEntry { Kind = "delete_district", Desc = $"delete district {name}" };
                    u.DistrictShapes.Add((pts, name)); PushUndo(u); summary["undo_id"] = u.Id;
                }
                onApplied?.Invoke();
                return summary;
            };
            return job;
        }

        void WriteArea(EntityCommandBuffer cb, Entity prefab, Entity original, List<float2> pts, CreationFlags flags)
        {
            var e = cb.CreateEntity();
            cb.AddComponent(e, new CreationDefinition { m_Prefab = prefab, m_Original = original, m_Flags = flags });
            cb.AddComponent(e, default(Updated));
            var buf = cb.AddBuffer<Game.Areas.Node>(e);
            foreach (var q in pts) buf.Add(new Game.Areas.Node(new float3(q.x, SampleHeight(q.x, q.y), q.y), float.MinValue));
            var f = pts[0];
            buf.Add(new Game.Areas.Node(new float3(f.x, SampleHeight(f.x, f.y), f.y), float.MinValue));   // closed outline
        }
    }
}
