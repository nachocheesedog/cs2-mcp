// Parks & detailing (v0.16):
// - attractiveness: the game's attractiveness ("niceness") of parks / landmarks exactly as the building info panel computes
//   it (AttractivenessSection): base (AttractionData + upgrades) x efficiency x maintenance step x (1 + terrain bonus %),
//   with the factors (Efficiency, Maintenance, Forest, Beach, Height); and for any points / a grid, the terrain bonus a
//   park would get there (TerrainAttractivenessSystem.EvaluateAttractiveness), optionally times a park type's base value.
// - list_object_types: placeable trees / props (non-building static objects) by name filter.
// - place_objects: many buildings / parks / trees / props in one previewed, undoable operation (items with type, x, z,
//   angle_deg or facing); buildings with built-in sub-nets are refused here (use place_building).
using System;
using System.Collections.Generic;
using Game.Buildings;
using Game.Common;
using Game.Prefabs;
using Game.Simulation;
using Game.Tools;
using Unity.Collections;
using Unity.Entities;
using Unity.Mathematics;
using Transform = Game.Objects.Transform;

namespace CS2MCP
{
    public partial class McpBridgeSystem
    {
        EntityQuery m_ObjectPrefabQuery, m_TopObjectQuery;
        static readonly string[] AttrFactorNames = { "Efficiency", "Maintenance", "Forest", "Beach", "Height" };

        bool TryCombined<T>(Entity e, Entity prefab, out T data) where T : unmanaged, IComponentData, ICombineData<T>
            => UpgradeUtils.TryGetCombinedComponent(EntityManager, e, prefab, out data);

        object Attractiveness(Dictionary<string, object> p)
        {
            var tas = World.GetOrCreateSystemManaged<TerrainAttractivenessSystem>();
            var map = tas.GetData(true, out var deps); deps.Complete();
            var heights = m_Terrain.GetHeightData(true);
            var par = SingletonOrDefault<AttractivenessParameterData>();
            var factors = new NativeArray<int>(5, Allocator.Temp);
            Dictionary<string, object> Fac()
            {
                var f = new Dictionary<string, object>();
                for (int i = 0; i < 5; i++) if (factors[i] != 0) f[AttrFactorNames[i]] = factors[i];
                return f;
            }
            float Terrain(float3 pos) { for (int i = 0; i < 5; i++) factors[i] = 0; return TerrainAttractivenessSystem.EvaluateAttractiveness(pos, map, heights, par, factors); }
            var result = new Dictionary<string, object>();
            int baseOfType = 0;
            string type = Json.Str(p, "type", null);
            if (type != null)
            {
                if (!BuildingPrefabMap(true).TryGetValue(type, out var tp)) throw new ArgumentException($"unknown building '{type}'");
                baseOfType = EntityManager.HasComponent<AttractionData>(tp) ? EntityManager.GetComponentData<AttractionData>(tp).m_Attractiveness : 0;
                result["type_base_attractiveness"] = baseOfType;
            }
            var pts = Json.List(p, "points");
            if (pts != null || p.ContainsKey("step"))
            {
                var list = new List<object>();
                void Eval(float x, float z)
                {
                    var pos = new float3(x, SampleHeight(x, z), z);
                    float bonus = Terrain(pos);
                    var d = new Dictionary<string, object> { ["at"] = new[] { (float)Math.Round(x, 1), (float)Math.Round(z, 1) }, ["terrain_bonus_pct"] = R2(bonus), ["factors"] = Fac() };
                    if (baseOfType > 0) d["predicted"] = (int)Math.Round(baseOfType * (1f + 0.01f * bonus));
                    list.Add(d);
                }
                if (pts != null) foreach (var o in pts) { var q = (List<object>)o; Eval((float)(double)q[0], (float)(double)q[1]); }
                else
                {
                    var box = BoxOf(p); float step = (float)Json.Num(p, "step", 100);
                    if ((box.x1 - box.x0) / step * (box.z1 - box.z0) / step > 20000) throw new ArgumentException("grid too large (max 20000 points) - raise step");
                    for (float z = box.z0; z <= box.z1; z += step) for (float x = box.x0; x <= box.x1; x += step) Eval(x, z);
                }
                result["points"] = list;
            }
            else
            {
                var box = BoxOf(p); var list = new List<object>();
                using (var es = m_BuildingQuery.ToEntityArray(Allocator.Temp))
                    foreach (var b in es)
                    {
                        var t = EntityManager.GetComponentData<Transform>(b);
                        if (!box.In(t.m_Position)) continue;
                        var prefab = EntityManager.GetComponentData<PrefabRef>(b).m_Prefab;
                        bool has = TryCombined<AttractionData>(b, prefab, out var ad);
                        if (!has && !EntityManager.HasComponent<AttractivenessProvider>(b)) continue;
                        float a = ad.m_Attractiveness; for (int i = 0; i < 5; i++) factors[i] = 0;
                        if (!EntityManager.HasComponent<Signature>(b) && EntityManager.HasBuffer<Efficiency>(b))
                        {
                            float eff = BuildingUtils.GetEfficiency(EntityManager.GetBuffer<Efficiency>(b, true));
                            a *= eff; AttractionSystem.SetFactor(factors, AttractionSystem.AttractivenessFactor.Efficiency, (eff - 1f) * 100f);
                        }
                        float maint = -1f;
                        if (EntityManager.HasComponent<Game.Buildings.Park>(b) && TryCombined<ParkData>(b, prefab, out var pd))
                        {
                            maint = pd.m_MaintenancePool > 0 ? (float)EntityManager.GetComponentData<Game.Buildings.Park>(b).m_Maintenance / pd.m_MaintenancePool : 0f;
                            float step = math.min(1f, 0.25f + 0.25f * math.floor(maint / 0.3f));
                            a *= step; AttractionSystem.SetFactor(factors, AttractionSystem.AttractivenessFactor.Maintenance, (step - 1f) * 100f);
                        }
                        var keep = new int[5]; for (int i = 0; i < 5; i++) keep[i] = factors[i];
                        float bonus = TerrainAttractivenessSystem.EvaluateAttractiveness(t.m_Position, map, heights, par, factors);
                        for (int i = 0; i < 2; i++) factors[i] = keep[i];      // Evaluate only writes Forest / Beach / Height
                        a *= 1f + 0.01f * bonus;
                        var d = new Dictionary<string, object>
                        {
                            ["id"] = Id(b), ["type"] = m_Prefabs.GetPrefabName(prefab), ["position"] = new[] { (float)Math.Round(t.m_Position.x, 1), (float)Math.Round(t.m_Position.z, 1) },
                            ["base"] = ad.m_Attractiveness, ["attractiveness"] = (int)Math.Round(a), ["factors"] = Fac(), ["terrain_bonus_pct"] = R2(bonus),
                        };
                        if (maint >= 0) d["maintenance_pct"] = (int)Math.Round(maint * 100);
                        list.Add(d);
                    }
                list.Sort((x, y) => ((int)((Dictionary<string, object>)y)["attractiveness"]).CompareTo((int)((Dictionary<string, object>)x)["attractiveness"]));
                result["buildings"] = list;
            }
            factors.Dispose();
            result["note"] = "attractiveness = base x efficiency x maintenance step (25/50/75/100% at 0/30/60/90% maintenance) x (1 + terrain bonus%); terrain bonus = forest + beach (shore) + height, from the game's 128x128 attractiveness map";
            return result;
        }

        Dictionary<string, Entity> ObjectPrefabMap()
        {
            if (m_ObjectPrefabQuery == default)
                m_ObjectPrefabQuery = GetEntityQuery(new EntityQueryDesc
                {
                    All = new[] { ComponentType.ReadOnly<PrefabData>(), ComponentType.ReadOnly<ObjectGeometryData>(), ComponentType.ReadOnly<StaticObjectData>() },
                    None = new[] { ComponentType.ReadOnly<BuildingData>(), ComponentType.ReadOnly<MovingObjectData>(), ComponentType.ReadOnly<BuildingExtensionData>() },
                });
            var m = new Dictionary<string, Entity>();
            using (var es = m_ObjectPrefabQuery.ToEntityArray(Allocator.Temp))
                foreach (var e in es) if (m_Prefabs.TryGetPrefab<PrefabBase>(e, out var pb)) m[pb.name] = e;
            return m;
        }

        object ListObjectTypes(Dictionary<string, object> p)
        {
            string filter = Json.Str(p, "filter", ""), kind = Json.Str(p, "kind", "all");
            int limit = (int)Json.Num(p, "limit", 200);
            var list = new List<object>();
            foreach (var kv in ObjectPrefabMap())
            {
                if (filter.Length > 0 && kv.Key.IndexOf(filter, StringComparison.OrdinalIgnoreCase) < 0) continue;
                bool tree = EntityManager.HasComponent<TreeData>(kv.Value);
                if (kind == "tree" && !tree || kind == "prop" && tree) continue;
                var g = EntityManager.GetComponentData<ObjectGeometryData>(kv.Value);
                list.Add(new Dictionary<string, object> { ["name"] = kv.Key, ["kind"] = tree ? "tree" : "prop",
                    ["size_m"] = new[] { R2(g.m_Size.x), R2(g.m_Size.z), R2(g.m_Size.y) } });
            }
            list.Sort((a, b) => string.CompareOrdinal((string)((Dictionary<string, object>)a)["name"], (string)((Dictionary<string, object>)b)["name"]));
            int total = list.Count; if (list.Count > limit) list = list.GetRange(0, limit);
            return new Dictionary<string, object> { ["count"] = total, ["returned"] = list.Count, ["objects"] = list, ["note"] = "size_m = [width, depth, height]" };
        }

        DefJob BuildPlaceObjects(Dictionary<string, object> p)
        {
            var items = Json.List(p, "items") ?? throw new ArgumentException("items = [{type, x, z, angle_deg | facing}] required");
            var bmap = BuildingPrefabMap(true); var omap = ObjectPrefabMap();
            var defs = new List<(Entity prefab, float3 pos, quaternion rot)>();
            var prefabs = new HashSet<Entity>();
            foreach (var o in items)
            {
                var it = (Dictionary<string, object>)o;
                string type = Json.Str(it, "type", null) ?? throw new ArgumentException("each item needs a type");
                if (!bmap.TryGetValue(type, out var prefab) && !omap.TryGetValue(type, out prefab))
                    throw new ArgumentException($"unknown object '{type}' (list_building_types / list_object_types)");
                if (EntityManager.HasBuffer<Game.Prefabs.SubNet>(prefab) && EntityManager.GetBuffer<Game.Prefabs.SubNet>(prefab, true).Length > 0)
                    throw new ArgumentException($"'{type}' has built-in roads/tracks - place it with place_building");
                float x = (float)Json.Num(it, "x", double.NaN), z = (float)Json.Num(it, "z", double.NaN);
                if (float.IsNaN(x) || float.IsNaN(z)) throw new ArgumentException("each item needs x and z");
                var facing = Json.List(it, "facing");
                float yaw = facing != null && facing.Count == 2 ? math.atan2((float)(double)facing[0], (float)(double)facing[1]) : math.radians((float)Json.Num(it, "angle_deg", 0));
                defs.Add((prefab, new float3(x, SampleHeight(x, z) + (float)Json.Num(it, "y_offset", 0), z), quaternion.RotateY(yaw)));
                prefabs.Add(prefab);
            }
            if (m_TopObjectQuery == default)
                m_TopObjectQuery = GetEntityQuery(new EntityQueryDesc
                {
                    All = new[] { ComponentType.ReadOnly<Game.Objects.Object>(), ComponentType.ReadOnly<Transform>(), ComponentType.ReadOnly<PrefabRef>() },
                    None = new[] { ComponentType.ReadOnly<Deleted>(), ComponentType.ReadOnly<Temp>(), ComponentType.ReadOnly<Owner>() },
                });
            var before = new HashSet<Entity>();
            using (var es = m_TopObjectQuery.ToEntityArray(Allocator.Temp))
                foreach (var e in es) if (prefabs.Contains(EntityManager.GetComponentData<PrefabRef>(e).m_Prefab)) before.Add(e);
            var job = new DefJob { Kind = "place_objects", DryRun = Json.Bool(p, "dry_run", false), AllowErrors = Json.Bool(p, "allow_errors", false),
                                   Definitions = defs.Count, Hold = Json.Bool(p, "hold", false) };
            job.Info["objects"] = defs.Count;
            job.Write = cb =>
            {
                foreach (var (prefab, pos, rot) in defs)
                {
                    var e = cb.CreateEntity();
                    cb.AddComponent(e, new CreationDefinition { m_Prefab = prefab, m_RandomSeed = m_Rng.NextInt() });
                    cb.AddComponent(e, new ObjectDefinition
                    {
                        m_Position = pos, m_Rotation = rot, m_LocalPosition = pos, m_LocalRotation = rot,
                        m_Scale = 1f, m_Intensity = 1f, m_Probability = 100, m_PrefabSubIndex = -1, m_ParentMesh = -1,
                    });
                    cb.AddComponent(e, default(Updated));
                }
            };
            job.OnApplied = summary =>
            {
                var made = new List<Entity>();
                using (var es = m_TopObjectQuery.ToEntityArray(Allocator.Temp))
                    foreach (var e in es)
                    {
                        if (before.Contains(e) || !prefabs.Contains(EntityManager.GetComponentData<PrefabRef>(e).m_Prefab)) continue;
                        var q = EntityManager.GetComponentData<Transform>(e).m_Position.xz;
                        foreach (var d in defs) if (math.distance(q, d.pos.xz) < 3f) { made.Add(e); break; }
                    }
                summary["placed"] = made.Count;
                if (made.Count > 0)
                {
                    var u = new UndoEntry { Kind = "place_building", Desc = $"place {made.Count} object(s)" };
                    u.Created.AddRange(made); PushUndo(u); summary["undo_id"] = u.Id;
                }
                return summary;
            };
            return job;
        }
    }
}
