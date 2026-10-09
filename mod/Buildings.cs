// Buildings: catalogue of placeable (non-growable) buildings, precise placement the way the object tool does it
// (CreationDefinition + ObjectDefinition -> Temp preview -> errors -> apply), and the nets a building owns (e.g. a
// station's platform tracks) with their free ends, so roads/tracks can be connected to them.
using System;
using System.Collections.Generic;
using Colossal.Mathematics;
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
        EntityQuery m_BuildingPrefabQuery, m_OwnedEdgeQuery;

        Dictionary<string, Entity> BuildingPrefabMap(bool includeGrowables)
        {
            if (m_BuildingPrefabQuery == default)
                m_BuildingPrefabQuery = GetEntityQuery(ComponentType.ReadOnly<PrefabData>(), ComponentType.ReadOnly<BuildingData>());
            var map = new Dictionary<string, Entity>();
            using (var ents = m_BuildingPrefabQuery.ToEntityArray(Allocator.Temp))
                foreach (var e in ents)
                {
                    if (!includeGrowables && EntityManager.HasComponent<SpawnableBuildingData>(e)) continue;
                    if (m_Prefabs.TryGetPrefab<PrefabBase>(e, out var pb)) map[pb.name] = e;
                }
            return map;
        }

        object ListBuildingTypes(Dictionary<string, object> p)
        {
            string filter = Json.Str(p, "filter", "");
            int limit = (int)Json.Num(p, "limit", 200);
            var list = new List<object>();
            foreach (var kv in BuildingPrefabMap(Json.Bool(p, "growables", false)))
            {
                if (filter.Length > 0 && kv.Key.IndexOf(filter, StringComparison.OrdinalIgnoreCase) < 0) continue;
                var bd = EntityManager.GetComponentData<BuildingData>(kv.Value);
                var d = new Dictionary<string, object> { ["name"] = kv.Key, ["lot_cells"] = new[] { bd.m_LotSize.x, bd.m_LotSize.y },
                                                         ["lot_m"] = new[] { bd.m_LotSize.x * 8, bd.m_LotSize.y * 8 } };
                if (EntityManager.HasComponent<ServiceObjectData>(kv.Value))
                {
                    var svc = EntityManager.GetComponentData<ServiceObjectData>(kv.Value).m_Service;
                    if (svc != Entity.Null) d["service"] = m_Prefabs.GetPrefabName(svc);
                }
                if (EntityManager.HasComponent<TransportStationData>(kv.Value)) d["transport_station"] = true;
                if (EntityManager.HasComponent<PlaceableObjectData>(kv.Value)) d["cost"] = EntityManager.GetComponentData<PlaceableObjectData>(kv.Value).m_ConstructionCost;
                list.Add(d);
            }
            list.Sort((a, b) => string.CompareOrdinal((string)((Dictionary<string, object>)a)["name"], (string)((Dictionary<string, object>)b)["name"]));
            int total = list.Count;
            if (list.Count > limit) list = list.GetRange(0, limit);
            return new Dictionary<string, object> { ["count"] = total, ["returned"] = list.Count, ["buildings"] = list,
                ["note"] = "lot_m = [width along the front, depth]; the front (road/track access side) faces the placement direction" };
        }

        DefJob BuildPlaceBuilding(Dictionary<string, object> p, bool recordUndo)
        {
            string type = Json.Str(p, "type", null) ?? throw new ArgumentException("type (a name from list_building_types) required");
            if (!BuildingPrefabMap(true).TryGetValue(type, out var prefab) && !UpgradePrefabMap().TryGetValue(type, out prefab))
                throw new ArgumentException($"unknown building '{type}' (use list_building_types)");
            // v0.33: the sub-net definitions below are a partial copy of ObjectToolBaseSystem (no OnWater, half-parented curves,
            // lefthand traffic...), and the generate jobs fault natively on a bad tree: place_building of sub-net buildings
            // crashed the game (2026-09-23 apply; 2026-09-26 21:30 on a DRY RUN). Refused - previews included - unless the
            // caller passes allow_subnets=true (test save, saved first). Same check as place_objects.
            int subNetCount = EntityManager.HasBuffer<Game.Prefabs.SubNet>(prefab) ? EntityManager.GetBuffer<Game.Prefabs.SubNet>(prefab, true).Length : 0;
            if (subNetCount > 0 && !Json.Bool(p, "allow_subnets", false))
                throw new ArgumentException($"'{type}' has {subNetCount} built-in nets (tracks/roads/quays/fences); placing or even previewing such " +
                    "buildings through the MCP crashed the game. Place it from the build menu, or pass allow_subnets=true (dry runs too) " +
                    "after saving, ideally in a test save.");
            // v0.27: parent = an existing building id -> install `type` as its service upgrade, like the game's upgrade tool
            var up = UpgradeParent(p, type, prefab);
            if (up.parent != Entity.Null && !p.ContainsKey("x"))
            {
                if (EntityManager.HasComponent<BuildingExtensionData>(prefab))
                    throw new ArgumentException($"{type} is an extension with its own lot: give x, z (+ facing) next to the parent (cs2_place_building parent=... finds a spot)");
                p["x"] = (double)up.tr.m_Position.x; p["z"] = (double)up.tr.m_Position.z;          // a wing/add-on sits at the parent's origin
                if (!p.ContainsKey("facing") && !p.ContainsKey("angle_deg"))
                {
                    var f = math.mul(up.tr.m_Rotation, new float3(0, 0, 1));
                    p["facing"] = new List<object> { (double)f.x, (double)f.z };
                }
            }
            float x = (float)Json.Num(p, "x", double.NaN), z = (float)Json.Num(p, "z", double.NaN);
            if (float.IsNaN(x) || float.IsNaN(z)) throw new ArgumentException("x and z required (the lot centre)");
            // facing: the direction the front of the lot points to, as an angle (deg, 0 = +z, 90 = +x) or a vector [dx, dz]
            float yaw;
            var facing = Json.List(p, "facing");
            if (facing != null && facing.Count == 2) yaw = math.atan2((float)(double)facing[0], (float)(double)facing[1]);
            else yaw = math.radians((float)Json.Num(p, "angle_deg", 0));
            var rot = quaternion.RotateY(yaw);
            var pos = new float3(x, SampleHeight(x, z), z);
            var before = new HashSet<Entity>();
            using (var bs = m_BuildingQuery.ToEntityArray(Allocator.Temp)) foreach (var b in bs) before.Add(b);
            var job = new DefJob { Kind = "place_building", DryRun = Json.Bool(p, "dry_run", false), AllowErrors = Json.Bool(p, "allow_errors", false),
                                   Definitions = 1, Hold = Json.Bool(p, "hold", false) };
            job.Info["type"] = type;
            if (EntityManager.HasComponent<BuildingData>(prefab))
            {
                var bd = EntityManager.GetComponentData<BuildingData>(prefab);
                job.Info["lot_m"] = new[] { bd.m_LotSize.x * 8, bd.m_LotSize.y * 8 };
            }
            if (up.parent != Entity.Null) { job.Info["parent"] = Id(up.parent); job.Kind = "place_upgrade"; }
            job.Info["position"] = new[] { (float)Math.Round(x, 1), (float)Math.Round(pos.y, 1), (float)Math.Round(z, 1) };
            job.Info["facing"] = new[] { (float)Math.Round(math.sin(yaw), 3), (float)Math.Round(math.cos(yaw), 3) };
            int seed = m_Rng.NextInt();
            var subCourses = SubNetCourses(prefab, pos, rot);          // the building's own tracks / roads (like the object tool)
            job.Info["sub_nets"] = subCourses.Count;
            var owner = new OwnerDefinition { m_Prefab = prefab, m_Position = pos, m_Rotation = rot };
            var subAreas = SubAreaDefs(prefab, pos, rot);              // v0.19: walking/park areas + surfaces, like the object tool
            job.Info["sub_areas"] = subAreas.Count;
            job.Write = cb =>
            {
                var e = cb.CreateEntity();
                cb.AddComponent(e, new CreationDefinition { m_Prefab = prefab, m_RandomSeed = seed });
                var od = new ObjectDefinition
                {
                    m_Position = pos, m_Rotation = rot, m_LocalPosition = pos, m_LocalRotation = rot,
                    m_Scale = 1f, m_Intensity = 1f, m_Probability = 100, m_PrefabSubIndex = -1, m_ParentMesh = -1,
                };
                if (up.parent != Entity.Null)
                {
                    WriteUpgradeParent(cb, up.parent, up.parentPrefab, up.tr);
                    var local = Game.Objects.ObjectUtils.WorldToLocal(Game.Objects.ObjectUtils.InverseTransform(up.tr), new Transform(pos, rot));
                    od.m_LocalPosition = local.m_Position; od.m_LocalRotation = local.m_Rotation;
                    cb.AddComponent(e, new OwnerDefinition { m_Prefab = up.parentPrefab, m_Position = up.tr.m_Position, m_Rotation = up.tr.m_Rotation });
                }
                cb.AddComponent(e, od);
                cb.AddComponent(e, default(Updated));
                foreach (var (netPrefab, course, upgrades) in subCourses)
                {
                    var n = cb.CreateEntity();
                    cb.AddComponent(n, new CreationDefinition { m_Prefab = netPrefab, m_RandomSeed = m_Rng.NextInt() });
                    cb.AddComponent(n, default(Updated));
                    cb.AddComponent(n, owner);
                    cb.AddComponent(n, course);
                    if (upgrades != default(CompositionFlags)) cb.AddComponent(n, new Upgraded { m_Flags = upgrades });
                }
                foreach (var (areaPrefab, hidden, nodes) in subAreas)
                {
                    var ae = cb.CreateEntity();
                    var cd = new CreationDefinition { m_Prefab = areaPrefab, m_RandomSeed = m_Rng.NextInt() };
                    if (hidden) cd.m_Flags |= CreationFlags.Hidden;
                    cb.AddComponent(ae, cd);
                    cb.AddComponent(ae, default(Updated));
                    cb.AddComponent(ae, owner);
                    var buf = cb.AddBuffer<Game.Areas.Node>(ae);
                    foreach (var nd in nodes) buf.Add(nd);
                }
            };
            job.OnApplied = summary =>
            {
                Entity made = Entity.Null; float best = 60f;
                using (var bs = m_BuildingQuery.ToEntityArray(Allocator.Temp))
                    foreach (var b in bs)
                    {
                        if (before.Contains(b) || EntityManager.GetComponentData<PrefabRef>(b).m_Prefab != prefab) continue;
                        float d = math.distance(EntityManager.GetComponentData<Transform>(b).m_Position.xz, pos.xz);
                        if (d < best) { best = d; made = b; }
                    }
                if (made == Entity.Null && up.parent != Entity.Null && EntityManager.HasBuffer<InstalledUpgrade>(up.parent))
                    foreach (var iu in EntityManager.GetBuffer<InstalledUpgrade>(up.parent, true))       // wings are not in the building query
                        if (EntityManager.HasComponent<PrefabRef>(iu.m_Upgrade) && EntityManager.GetComponentData<PrefabRef>(iu.m_Upgrade).m_Prefab == prefab) made = iu.m_Upgrade;
                if (made == Entity.Null) { summary["warning"] = "applied, but the new building was not found"; return summary; }
                summary["building_id"] = Id(made);
                if (recordUndo)
                {
                    var u = new UndoEntry { Kind = "place_building", Desc = $"place {type}" };
                    u.Created.Add(made); PushUndo(u); summary["undo_id"] = u.Id;
                }
                return summary;
            };
            return job;
        }

        EntityQuery m_UpgradePrefabQuery;

        // service upgrades without a lot of their own (hospital wings, extra cells...) are not BuildingData prefabs
        Dictionary<string, Entity> UpgradePrefabMap()
        {
            if (m_UpgradePrefabQuery == default)
                m_UpgradePrefabQuery = GetEntityQuery(ComponentType.ReadOnly<PrefabData>(), ComponentType.ReadOnly<ServiceUpgradeData>());
            var map = new Dictionary<string, Entity>();
            using (var ents = m_UpgradePrefabQuery.ToEntityArray(Allocator.Temp))
                foreach (var e in ents) if (m_Prefabs.TryGetPrefab<PrefabBase>(e, out var pb)) map[pb.name] = e;
            return map;
        }

        // the building an upgrade goes on (p["parent"] = id): must be a building whose prefab the upgrade lists in its
        // ServiceUpgradeBuilding buffer, and must not already have it (the game allows one of each)
        (Entity parent, Entity parentPrefab, Transform tr) UpgradeParent(Dictionary<string, object> p, string type, Entity prefab)
        {
            var id = Json.Str(p, "parent", null);
            if (id == null) return (Entity.Null, Entity.Null, default);
            var parent = ParseId(id);
            if (!EntityManager.Exists(parent) || !EntityManager.HasComponent<Building>(parent)) throw new ArgumentException($"parent {id} is not a building");
            var parentPrefab = EntityManager.GetComponentData<PrefabRef>(parent).m_Prefab;
            string parentType = m_Prefabs.GetPrefabName(parentPrefab);
            if (!EntityManager.HasBuffer<ServiceUpgradeBuilding>(prefab)) throw new ArgumentException($"{type} is not a service upgrade");
            bool fits = false; var fitsList = new List<string>();
            foreach (var sb in EntityManager.GetBuffer<ServiceUpgradeBuilding>(prefab, true))
            {
                if (sb.m_Building == parentPrefab) fits = true;
                fitsList.Add(m_Prefabs.GetPrefabName(sb.m_Building));
            }
            if (!fits) throw new ArgumentException($"{type} does not fit {parentType}; it upgrades: {string.Join(", ", fitsList)}");
            if (EntityManager.HasBuffer<InstalledUpgrade>(parent))
                foreach (var iu in EntityManager.GetBuffer<InstalledUpgrade>(parent, true))
                    if (EntityManager.HasComponent<PrefabRef>(iu.m_Upgrade) && EntityManager.GetComponentData<PrefabRef>(iu.m_Upgrade).m_Prefab == prefab)
                        throw new ArgumentException($"{parentType} {id} already has {type}");
            return (parent, parentPrefab, EntityManager.GetComponentData<Transform>(parent));
        }

        // mirrors ObjectToolBaseSystem for an upgrade: the parent itself is re-defined in place (m_Original = parent, flags
        // Upgrade | Parent, no prefab) and each upgrade it already has is re-defined as a Parent-flagged child, so applying
        // keeps them; the parent's own sub-nets / sub-areas are left alone (the game only regenerates them for new objects)
        void WriteUpgradeParent(EntityCommandBuffer cb, Entity parent, Entity parentPrefab, Transform tr)
        {
            var pe = cb.CreateEntity();
            var cd = new CreationDefinition { m_Original = parent, m_RandomSeed = m_Rng.NextInt(), m_Flags = CreationFlags.Upgrade | CreationFlags.Parent };
            if (EntityManager.HasComponent<Owner>(parent)) cd.m_Owner = EntityManager.GetComponentData<Owner>(parent).m_Owner;
            cb.AddComponent(pe, cd);
            float elev = EntityManager.HasComponent<Game.Objects.Elevation>(parent) ? EntityManager.GetComponentData<Game.Objects.Elevation>(parent).m_Elevation : 0f;
            cb.AddComponent(pe, new ObjectDefinition
            {
                m_Position = tr.m_Position, m_Rotation = tr.m_Rotation, m_LocalPosition = tr.m_Position, m_LocalRotation = tr.m_Rotation,
                m_Scale = 1f, m_Intensity = 1f, m_Probability = 100, m_PrefabSubIndex = -1, m_ParentMesh = -1, m_Elevation = elev,
            });
            cb.AddComponent(pe, default(Updated));
            if (!EntityManager.HasBuffer<InstalledUpgrade>(parent)) return;
            var inv = Game.Objects.ObjectUtils.InverseTransform(tr);
            var owner = new OwnerDefinition { m_Prefab = parentPrefab, m_Position = tr.m_Position, m_Rotation = tr.m_Rotation };
            foreach (var iu in EntityManager.GetBuffer<InstalledUpgrade>(parent, true))
            {
                var u = iu.m_Upgrade;
                if (!EntityManager.HasComponent<Transform>(u)) continue;
                var ue = cb.CreateEntity();
                cb.AddComponent(ue, new CreationDefinition { m_Original = u, m_Flags = CreationFlags.Parent | CreationFlags.Upgrade });
                cb.AddComponent(ue, default(Updated));
                cb.AddComponent(ue, owner);
                var ut = EntityManager.GetComponentData<Transform>(u);
                var local = Game.Objects.ObjectUtils.WorldToLocal(inv, ut);
                var uod = new ObjectDefinition
                {
                    m_Position = ut.m_Position, m_Rotation = ut.m_Rotation, m_LocalPosition = local.m_Position, m_LocalRotation = local.m_Rotation,
                    m_Scale = 1f, m_Intensity = 1f, m_Probability = 100, m_PrefabSubIndex = -1,
                    m_ParentMesh = EntityManager.HasComponent<Building>(u) ? -1 : 0,
                };
                if (EntityManager.HasComponent<Game.Objects.Elevation>(u)) uod.m_Elevation = EntityManager.GetComponentData<Game.Objects.Elevation>(u).m_Elevation;
                cb.AddComponent(ue, uod);
            }
        }

        // mirrors ObjectToolBaseSystem.UpdateSubAreas for a NEW top-level object (not editor mode, no clear areas): one area
        // creation definition per prefab sub-area, owned by the building, nodes in world space starting at the node nearest
        // the object's origin (GetFirstNodeIndex); a node on a parent mesh keeps its local height as elevation, a terrain
        // node gets float.MinValue. Placeholder areas (e.g. "Grass Surface Placeholder") resolve to their first variant.
        List<(Entity prefab, bool hidden, List<Game.Areas.Node> nodes)> SubAreaDefs(Entity prefab, float3 pos, quaternion rot)
        {
            var outp = new List<(Entity, bool, List<Game.Areas.Node>)>();
            if (!EntityManager.HasBuffer<Game.Prefabs.SubArea>(prefab)) return outp;
            var subAreas = EntityManager.GetBuffer<Game.Prefabs.SubArea>(prefab, true);
            var subNodes = EntityManager.GetBuffer<SubAreaNode>(prefab, true);
            var tr = new Transform(pos, rot);
            for (int i = 0; i < subAreas.Length; i++)
            {
                var sa = subAreas[i]; var ap = sa.m_Prefab;
                if (EntityManager.HasBuffer<PlaceholderObjectElement>(ap))
                {
                    var ph = EntityManager.GetBuffer<PlaceholderObjectElement>(ap, true);
                    if (ph.Length == 0) continue;
                    ap = ph[0].m_Object;
                }
                if (!EntityManager.HasComponent<AreaGeometryData>(ap)) continue;
                var geo = EntityManager.GetComponentData<AreaGeometryData>(ap);
                var nodes = new List<Game.Areas.Node>();
                int num = ObjectToolBaseSystem.GetFirstNodeIndex(subNodes, sa.m_NodeRange);
                for (int j = sa.m_NodeRange.x; j <= sa.m_NodeRange.y; j++)          // N+1 nodes: closes the loop, like the tool
                {
                    var n = subNodes[num];
                    float3 w = Game.Objects.ObjectUtils.LocalToWorld(tr, n.m_Position);
                    nodes.Add(new Game.Areas.Node(w, n.m_ParentMesh >= 0 ? n.m_Position.y : float.MinValue));
                    if (++num == sa.m_NodeRange.y) num = sa.m_NodeRange.x;
                }
                outp.Add((ap, geo.m_Type != 0, nodes));
            }
            return outp;
        }

        // mirrors ObjectToolBaseSystem.UpdateSubNets / CreateSubNet for a new top-level building: one NetCourse per prefab
        // sub-net, in world space, sharing node positions (averaged per node index)
        List<(Entity prefab, NetCourse course, CompositionFlags upgrades)> SubNetCourses(Entity prefab, float3 pos, quaternion rot)
        {
            var outp = new List<(Entity, NetCourse, CompositionFlags)>();
            if (!EntityManager.HasBuffer<Game.Prefabs.SubNet>(prefab)) return outp;
            var subNets = EntityManager.GetBuffer<Game.Prefabs.SubNet>(prefab, true);
            var nodePos = new List<float4>();
            for (int i = 0; i < subNets.Length; i++)
            {
                var sn = subNets[i];
                if (sn.m_NodeIndex.x >= 0) { while (nodePos.Count <= sn.m_NodeIndex.x) nodePos.Add(default); nodePos[sn.m_NodeIndex.x] += new float4(sn.m_Curve.a, 1f); }
                if (sn.m_NodeIndex.y >= 0) { while (nodePos.Count <= sn.m_NodeIndex.y) nodePos.Add(default); nodePos[sn.m_NodeIndex.y] += new float4(sn.m_Curve.d, 1f); }
            }
            for (int j = 0; j < nodePos.Count; j++) nodePos[j] /= math.max(1f, nodePos[j].w);
            var geomLookup = GetComponentLookup<NetGeometryData>(true);
            for (int k = 0; k < subNets.Length; k++)
            {
                var sn = NetUtils.GetSubNet(subNets, k, false, ref geomLookup);
                var geo = EntityManager.HasComponent<NetGeometryData>(sn.m_Prefab) ? EntityManager.GetComponentData<NetGeometryData>(sn.m_Prefab) : default;
                var local = sn.m_Curve;
                bool parented = sn.m_ParentMesh.x >= 0 || sn.m_ParentMesh.y >= 0;
                var world = Game.Objects.ObjectUtils.LocalToWorld(pos, rot, local);
                if (!parented && (geo.m_Flags & Game.Net.GeometryFlags.FlattenTerrain) == 0)
                {   // follow the terrain, keeping the sub-net's own height offsets
                    world.a.y = SampleHeight(world.a.x, world.a.z) + local.a.y; world.b.y = SampleHeight(world.b.x, world.b.z) + local.b.y;
                    world.c.y = SampleHeight(world.c.x, world.c.z) + local.c.y; world.d.y = SampleHeight(world.d.x, world.d.z) + local.d.y;
                }
                var course = new NetCourse { m_Curve = world, m_Length = MathUtils.Length(world), m_FixedIndex = -1 };
                course.m_StartPosition.m_Position = world.a; course.m_StartPosition.m_Rotation = NetUtils.GetNodeRotation(MathUtils.StartTangent(world), rot);
                course.m_StartPosition.m_CourseDelta = 0f; course.m_StartPosition.m_Elevation = local.a.y; course.m_StartPosition.m_ParentMesh = sn.m_ParentMesh.x;
                if (sn.m_NodeIndex.x >= 0) course.m_StartPosition.m_Position = Game.Objects.ObjectUtils.LocalToWorld(pos, rot, nodePos[sn.m_NodeIndex.x].xyz);
                course.m_EndPosition.m_Position = world.d; course.m_EndPosition.m_Rotation = NetUtils.GetNodeRotation(MathUtils.EndTangent(world), rot);
                course.m_EndPosition.m_CourseDelta = 1f; course.m_EndPosition.m_Elevation = local.d.y; course.m_EndPosition.m_ParentMesh = sn.m_ParentMesh.y;
                if (sn.m_NodeIndex.y >= 0) course.m_EndPosition.m_Position = Game.Objects.ObjectUtils.LocalToWorld(pos, rot, nodePos[sn.m_NodeIndex.y].xyz);
                course.m_StartPosition.m_Flags |= CoursePosFlags.IsFirst; course.m_EndPosition.m_Flags |= CoursePosFlags.IsLast;
                if (course.m_StartPosition.m_Position.Equals(course.m_EndPosition.m_Position)) { course.m_StartPosition.m_Flags |= CoursePosFlags.IsLast; course.m_EndPosition.m_Flags |= CoursePosFlags.IsFirst; }
                outp.Add((sn.m_Prefab, course, sn.m_Upgrades));
            }
            return outp;
        }

        // nets owned by a building (platform tracks, internal roads) with each node's total connection count, so the
        // caller can see the free ends to connect lines to
        // road access + notification icons (the warning bubbles players see) for buildings: ids, or every building in a box;
        // problems_only keeps the ones without road access or with icons
        object BuildingStatus(Dictionary<string, object> p)
        {
            var targets = new List<Entity>();
            var ids = Json.List(p, "ids");
            if (ids != null) foreach (var o in ids) targets.Add(ParseId((string)o));
            else if (Json.Str(p, "id", null) is string one) targets.Add(ParseId(one));
            else
            {
                var box = BoxOf(p);
                using (var es = m_BuildingQuery.ToEntityArray(Allocator.Temp))
                    foreach (var e in es) if (box.In(EntityManager.GetComponentData<Transform>(e).m_Position)) targets.Add(e);
            }
            bool problemsOnly = Json.Bool(p, "problems_only", false); int limit = (int)Json.Num(p, "limit", 200);
            bool iconDetails = Json.Bool(p, "icon_details", false);                         // v0.45: where each icon is + what it flags
            var list = new List<object>(); var iconCounts = new Dictionary<string, object>(); int noRoad = 0;
            foreach (var b in targets)
            {
                if (!EntityManager.Exists(b) || !EntityManager.HasComponent<Building>(b)) continue;
                var bd = EntityManager.GetComponentData<Building>(b);
                bool road = bd.m_RoadEdge != Entity.Null && EntityManager.Exists(bd.m_RoadEdge);
                var icons = new List<object>(); var details = new List<object>();
                if (EntityManager.HasBuffer<Game.Notifications.IconElement>(b))
                    foreach (var ic in EntityManager.GetBuffer<Game.Notifications.IconElement>(b, true))
                    {
                        if (!EntityManager.Exists(ic.m_Icon) || !EntityManager.HasComponent<PrefabRef>(ic.m_Icon)) continue;
                        string n = m_Prefabs.GetPrefabName(EntityManager.GetComponentData<PrefabRef>(ic.m_Icon).m_Prefab);
                        icons.Add(n); iconCounts[n] = (iconCounts.TryGetValue(n, out var c) ? (int)c : 0) + 1;
                        if (iconDetails && details.Count < 100) details.Add(IconDetail(ic.m_Icon, n));
                    }
                if (!road) noRoad++;
                if (problemsOnly && road && icons.Count == 0) continue;
                if (list.Count >= limit) continue;
                var pos = EntityManager.GetComponentData<Transform>(b).m_Position;
                var d = new Dictionary<string, object>
                {
                    ["id"] = Id(b), ["type"] = m_Prefabs.GetPrefabName(EntityManager.GetComponentData<PrefabRef>(b).m_Prefab),
                    ["position"] = new[] { (float)Math.Round(pos.x, 1), (float)Math.Round(pos.z, 1) }, ["has_road_access"] = road, ["icons"] = icons,
                };
                if (iconDetails) d["icon_details"] = details;
                var bpref = EntityManager.GetComponentData<PrefabRef>(b).m_Prefab;   // growables: the zone style they belong to
                if (EntityManager.HasComponent<SpawnableBuildingData>(bpref))
                {
                    var sp = EntityManager.GetComponentData<SpawnableBuildingData>(bpref);
                    if (sp.m_ZonePrefab != Entity.Null) d["zone"] = m_Prefabs.GetPrefabName(sp.m_ZonePrefab);
                    d["level"] = (int)sp.m_Level;
                }
                if (road)
                {
                    d["road_edge"] = Id(bd.m_RoadEdge);
                    d["road_type"] = m_Prefabs.GetPrefabName(EntityManager.GetComponentData<PrefabRef>(bd.m_RoadEdge).m_Prefab);
                }
                list.Add(d);
            }
            return new Dictionary<string, object> { ["checked"] = targets.Count, ["without_road_access"] = noRoad, ["icon_counts"] = iconCounts, ["returned"] = list.Count, ["buildings"] = list };
        }

        // v0.45: one notification icon -> its world location and the element it flags (Target: a lane, an object, a net
        // edge...), with that element's prefab, its curve ends (lanes / edges) and its owner chain up to the building
        Dictionary<string, object> IconDetail(Entity icon, string name)
        {
            var r = new Dictionary<string, object> { ["icon"] = name };
            if (EntityManager.HasComponent<Game.Notifications.Icon>(icon))
            {
                var at = EntityManager.GetComponentData<Game.Notifications.Icon>(icon).m_Location;
                r["at"] = new[] { (float)Math.Round(at.x, 1), (float)Math.Round(at.y, 1), (float)Math.Round(at.z, 1) };
            }
            if (!EntityManager.HasComponent<Game.Common.Target>(icon)) return r;
            var t = EntityManager.GetComponentData<Game.Common.Target>(icon).m_Target;
            if (t == Entity.Null || !EntityManager.Exists(t)) return r;
            r["target"] = Id(t); r["target_kind"] = DescribeEntity(t);
            if (EntityManager.HasComponent<Game.Net.Curve>(t))
            {
                var c = EntityManager.GetComponentData<Game.Net.Curve>(t).m_Bezier;
                r["curve"] = new[] { new[] { (float)Math.Round(c.a.x, 1), (float)Math.Round(c.a.y, 1), (float)Math.Round(c.a.z, 1) },
                                     new[] { (float)Math.Round(c.d.x, 1), (float)Math.Round(c.d.y, 1), (float)Math.Round(c.d.z, 1) } };
            }
            else if (EntityManager.HasComponent<Transform>(t))
            {
                var tp = EntityManager.GetComponentData<Transform>(t).m_Position;
                r["target_at"] = new[] { (float)Math.Round(tp.x, 1), (float)Math.Round(tp.y, 1), (float)Math.Round(tp.z, 1) };
            }
            var chain = new List<object>(); var e = t;
            for (int i = 0; i < 6 && EntityManager.HasComponent<Owner>(e); i++)
            {
                e = EntityManager.GetComponentData<Owner>(e).m_Owner;
                if (e == Entity.Null || !EntityManager.Exists(e)) break;
                chain.Add(Id(e) + " " + DescribeEntity(e));
                if (EntityManager.HasComponent<Building>(e)) break;
            }
            r["owners"] = chain;
            return r;
        }

        string DescribeEntity(Entity e)
        {
            string pref = EntityManager.HasComponent<PrefabRef>(e) ? m_Prefabs.GetPrefabName(EntityManager.GetComponentData<PrefabRef>(e).m_Prefab) : "?";
            string kind = EntityManager.HasComponent<Game.Net.PedestrianLane>(e) ? "pedestrian_lane" : EntityManager.HasComponent<Game.Net.ConnectionLane>(e) ? "connection_lane"
                : EntityManager.HasComponent<Game.Net.CarLane>(e) ? "car_lane" : EntityManager.HasComponent<Game.Net.Edge>(e) ? "net_edge"
                : EntityManager.HasComponent<Game.Areas.Area>(e) ? "area" : EntityManager.HasComponent<Building>(e) ? "building"
                : EntityManager.HasComponent<Game.Objects.SpawnLocation>(e) ? "spawn_location" : EntityManager.HasComponent<Transform>(e) ? "object" : "entity";
            return kind + ":" + pref;
        }

        object BuildingNets(Dictionary<string, object> p)
        {
            var b = ParseId(Json.Str(p, "id", null) ?? throw new ArgumentException("id (a building id) required"));
            if (!EntityManager.Exists(b)) throw new ArgumentException("no such building");
            if (m_OwnedEdgeQuery == default)
                m_OwnedEdgeQuery = GetEntityQuery(new EntityQueryDesc
                {
                    All = new[] { ComponentType.ReadOnly<Edge>(), ComponentType.ReadOnly<Curve>(), ComponentType.ReadOnly<PrefabRef>(), ComponentType.ReadOnly<Owner>() },
                    None = new[] { ComponentType.ReadOnly<Deleted>(), ComponentType.ReadOnly<Temp>() },
                });
            var edges = new List<object>(); var nodeDeg = new Dictionary<Entity, int>();
            using (var es = m_OwnedEdgeQuery.ToEntityArray(Allocator.Temp))
                foreach (var e in es)
                {
                    var owner = EntityManager.GetComponentData<Owner>(e).m_Owner;
                    int guard = 0;
                    while (owner != b && owner != Entity.Null && EntityManager.HasComponent<Owner>(owner) && guard++ < 4) owner = EntityManager.GetComponentData<Owner>(owner).m_Owner;
                    if (owner != b) continue;
                    var edge = EntityManager.GetComponentData<Edge>(e); var c = EntityManager.GetComponentData<Curve>(e).m_Bezier;
                    string type = m_Prefabs.GetPrefabName(EntityManager.GetComponentData<PrefabRef>(e).m_Prefab);
                    edges.Add(new Dictionary<string, object> { ["id"] = Id(e), ["type"] = type, ["track"] = EntityManager.HasComponent<Game.Net.TrackLane>(e) || EntityManager.HasComponent<Game.Net.TrainTrack>(e),
                        ["start"] = new[] { (float)Math.Round(c.a.x, 2), (float)Math.Round(c.a.y, 2), (float)Math.Round(c.a.z, 2) },
                        ["end"] = new[] { (float)Math.Round(c.d.x, 2), (float)Math.Round(c.d.y, 2), (float)Math.Round(c.d.z, 2) },
                        ["start_node"] = Id(edge.m_Start), ["end_node"] = Id(edge.m_End) });
                    foreach (var n in new[] { edge.m_Start, edge.m_End })
                        if (!nodeDeg.ContainsKey(n)) nodeDeg[n] = EntityManager.HasBuffer<ConnectedEdge>(n) ? EntityManager.GetBuffer<ConnectedEdge>(n, true).Length : 0;
                }
            var free = new List<object>();
            foreach (var kv in nodeDeg)
                if (kv.Value <= 1 && EntityManager.HasComponent<Node>(kv.Key))
                {
                    var q = EntityManager.GetComponentData<Node>(kv.Key).m_Position;
                    free.Add(new Dictionary<string, object> { ["node"] = Id(kv.Key), ["pos"] = new[] { (float)Math.Round(q.x, 2), (float)Math.Round(q.y, 2), (float)Math.Round(q.z, 2) } });
                }
            var t = EntityManager.GetComponentData<Transform>(b);
            return new Dictionary<string, object> { ["building"] = Id(b), ["type"] = m_Prefabs.GetPrefabName(EntityManager.GetComponentData<PrefabRef>(b).m_Prefab),
                ["position"] = new[] { t.m_Position.x, t.m_Position.y, t.m_Position.z }, ["edges"] = edges, ["free_ends"] = free,
                ["note"] = "free_ends = owned net nodes with a single connection: connect tracks/roads to these exact positions" };
        }
    }
}
