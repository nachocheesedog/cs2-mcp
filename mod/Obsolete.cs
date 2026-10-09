// Missing-mod leftovers (v0.20): when a save references assets from a mod that is no longer installed, the game keeps
// them as "obsolete" prefabs (PrefabData.m_Index < 0, name from PrefabSystem.GetObsoleteID) so nothing is lost - and
// every save writes them again, which is why the "subscribe to the missing mods" popup never goes away. This lists every
// obsolete prefab with what still uses it (entities via PrefabRef - buildings, building extensions, vehicles, props -
// and transit lines' chosen vehicle models), and can clear the vehicle side: clear_vehicle_models resets those lines to
// automatic vehicle choice, delete_vehicles removes vehicles built from missing models (the depot sends new ones).
// Buildings / extensions are removed with bulldoze (by id) so the caller decides what replaces them.
using System;
using System.Collections.Generic;
using Game.Buildings;
using Game.Common;
using Game.Prefabs;
using Game.Routes;
using Game.Tools;
using Game.Vehicles;
using Unity.Collections;
using Unity.Entities;
using Transform = Game.Objects.Transform;

namespace CS2MCP
{
    public partial class McpBridgeSystem
    {
        EntityQuery m_AllPrefabQuery, m_PrefabRefQuery, m_VehicleModelQuery;

        object ObsoletePrefabs(Dictionary<string, object> p)
        {
            if (m_AllPrefabQuery == default)
            {
                m_AllPrefabQuery = GetEntityQuery(new EntityQueryDesc { All = new[] { ComponentType.ReadOnly<PrefabData>() }, Options = EntityQueryOptions.IncludeDisabledEntities | EntityQueryOptions.IgnoreComponentEnabledState });   // unused prefabs have PrefabData disabled
                m_PrefabRefQuery = GetEntityQuery(new EntityQueryDesc { All = new[] { ComponentType.ReadOnly<PrefabRef>() }, None = new[] { ComponentType.ReadOnly<Deleted>(), ComponentType.ReadOnly<Temp>() } });
                m_VehicleModelQuery = GetEntityQuery(new EntityQueryDesc { All = new[] { ComponentType.ReadOnly<VehicleModel>() }, None = new[] { ComponentType.ReadOnly<Deleted>(), ComponentType.ReadOnly<Temp>() } });
            }
            var obsolete = new Dictionary<Entity, string>();
            using (var es = m_AllPrefabQuery.ToEntityArray(Allocator.Temp))
                foreach (var e in es)
                    if (EntityManager.GetComponentData<PrefabData>(e).m_Index < 0) obsolete[e] = m_Prefabs.GetPrefabName(e);
            bool clearModels = Json.Bool(p, "clear_vehicle_models", false), deleteVehicles = Json.Bool(p, "delete_vehicles", false);
            var users = new Dictionary<Entity, List<object>>(); var toDelete = new HashSet<Entity>();
            int limit = (int)Json.Num(p, "limit", 50);
            if (obsolete.Count > 0)
                using (var es = m_PrefabRefQuery.ToEntityArray(Allocator.Temp))
                using (var refs = m_PrefabRefQuery.ToComponentDataArray<PrefabRef>(Allocator.Temp))
                    for (int i = 0; i < es.Length; i++)
                    {
                        if (!obsolete.ContainsKey(refs[i].m_Prefab)) continue;
                        var e = es[i];
                        if (!users.TryGetValue(refs[i].m_Prefab, out var list)) users[refs[i].m_Prefab] = list = new List<object>();
                        string kind = EntityManager.HasComponent<Vehicle>(e) ? "vehicle"
                                    : EntityManager.HasComponent<Game.Buildings.Extension>(e) || EntityManager.HasComponent<Game.Buildings.ServiceUpgrade>(e) ? "building_extension"
                                    : EntityManager.HasComponent<Building>(e) ? "building" : "other";
                        if (kind == "vehicle" && deleteVehicles)
                        {
                            var head = EntityManager.HasComponent<Controller>(e) ? EntityManager.GetComponentData<Controller>(e).m_Controller : e;
                            if (head == Entity.Null) head = e;
                            toDelete.Add(head);
                            if (EntityManager.HasBuffer<LayoutElement>(head)) foreach (var le in EntityManager.GetBuffer<LayoutElement>(head, true)) toDelete.Add(le.m_Vehicle);
                        }
                        if (list.Count >= limit) continue;
                        var d = new Dictionary<string, object> { ["id"] = Id(e), ["kind"] = kind };
                        if (EntityManager.HasComponent<Transform>(e)) { var t = EntityManager.GetComponentData<Transform>(e).m_Position; d["position"] = new[] { (float)Math.Round(t.x, 1), (float)Math.Round(t.z, 1) }; }
                        if (EntityManager.HasComponent<Owner>(e))
                        {
                            var o = EntityManager.GetComponentData<Owner>(e).m_Owner;
                            if (EntityManager.HasComponent<PrefabRef>(o)) d["owner"] = $"{Id(o)} {m_Prefabs.GetPrefabName(EntityManager.GetComponentData<PrefabRef>(o).m_Prefab)}";
                        }
                        list.Add(d);
                    }
            var lineRefs = new List<object>(); int cleared = 0;
            // v0.33: tags go through the end-frame barrier (played back at the front of the next MainLoop, before
            // ModificationSystem), like the game's own deletions. Added directly here - at the tail of MainLoop - Updated was
            // stripped and Deleted vehicles destroyed the same frame, before any modification system could unhook them.
            EntityCommandBuffer tags = default; bool haveTags = false;
            EntityCommandBuffer Tags() { if (!haveTags) { tags = World.GetOrCreateSystemManaged<Game.EndFrameBarrier>().CreateCommandBuffer(); haveTags = true; } return tags; }
            if (obsolete.Count > 0)
                using (var es = m_VehicleModelQuery.ToEntityArray(Allocator.Temp))
                    foreach (var r in es)
                    {
                        var buf = EntityManager.GetBuffer<VehicleModel>(r, !clearModels);
                        for (int i = 0; i < buf.Length; i++)
                        {
                            var vm = buf[i]; bool a = obsolete.ContainsKey(vm.m_PrimaryPrefab), b = obsolete.ContainsKey(vm.m_SecondaryPrefab);
                            if (!a && !b) continue;
                            lineRefs.Add(new Dictionary<string, object>
                            {
                                ["line"] = Id(r), ["number"] = EntityManager.HasComponent<RouteNumber>(r) ? EntityManager.GetComponentData<RouteNumber>(r).m_Number : -1,
                                ["name"] = DistrictName(r),
                                ["primary"] = a ? obsolete[vm.m_PrimaryPrefab] : null, ["secondary"] = b ? obsolete[vm.m_SecondaryPrefab] : null,
                            });
                            if (clearModels)
                            {
                                if (a) vm.m_PrimaryPrefab = Entity.Null;
                                if (b) vm.m_SecondaryPrefab = Entity.Null;
                                buf[i] = vm; cleared++;
                            }
                        }
                        if (clearModels) Tags().AddComponent<Updated>(r);
                    }
            foreach (var v in toDelete) if (EntityManager.Exists(v) && !EntityManager.HasComponent<Deleted>(v)) Tags().AddComponent<Deleted>(v);
            var prefabs = new List<object>();
            foreach (var kv in obsolete)
                prefabs.Add(new Dictionary<string, object>
                {
                    ["name"] = kv.Value, ["used_by"] = users.TryGetValue(kv.Key, out var u) ? u.Count : 0,
                    ["users"] = u ?? new List<object>(),
                });
            var r2 = new Dictionary<string, object>
            {
                ["obsolete_prefabs"] = prefabs, ["line_vehicle_models"] = lineRefs,
                ["note"] = "an obsolete prefab with used_by 0 and no line reference is no longer written to saves; content packs (ContentPrefab) drop out once none of their assets are used",
            };
            if (clearModels) r2["vehicle_models_cleared"] = cleared;
            if (deleteVehicles) r2["vehicles_deleted"] = toDelete.Count;
            return r2;
        }

        // what the build menus offer: every prefab with a menu entry (UIObjectData) whose menu group or name matches,
        // with its icon (UIObject.m_Icon - empty means the icon-less entries players see), where it comes from (base game /
        // mod id from the asset path) and how many objects in the city use it
        object MenuAssets(Dictionary<string, object> p)
        {
            string group = Json.Str(p, "group", ""), filter = Json.Str(p, "filter", "");
            bool noIcon = Json.Bool(p, "no_icon_only", false); int limit = (int)Json.Num(p, "limit", 300);
            var q = GetEntityQuery(ComponentType.ReadOnly<PrefabData>(), ComponentType.ReadOnly<UIObjectData>());
            var used = new Dictionary<Entity, int>();
            if (Json.Bool(p, "count_use", true))
            {
                if (m_PrefabRefQuery == default)
                    m_PrefabRefQuery = GetEntityQuery(new EntityQueryDesc { All = new[] { ComponentType.ReadOnly<PrefabRef>() }, None = new[] { ComponentType.ReadOnly<Deleted>(), ComponentType.ReadOnly<Temp>() } });
                using (var refs = m_PrefabRefQuery.ToComponentDataArray<PrefabRef>(Allocator.Temp))
                    foreach (var r in refs) used[r.m_Prefab] = (used.TryGetValue(r.m_Prefab, out var n) ? n : 0) + 1;
            }
            var list = new List<object>(); var bySource = new Dictionary<string, int>();
            using (var es = q.ToEntityArray(Allocator.Temp))
                foreach (var e in es)
                {
                    if (EntityManager.GetComponentData<PrefabData>(e).m_Index < 0 || !m_Prefabs.TryGetPrefab<PrefabBase>(e, out var pb)) continue;
                    var ui = EntityManager.GetComponentData<UIObjectData>(e);
                    string g = ui.m_Group != Entity.Null ? m_Prefabs.GetPrefabName(ui.m_Group) : "";
                    if (group.Length > 0 && g.IndexOf(group, StringComparison.OrdinalIgnoreCase) < 0) continue;
                    if (filter.Length > 0 && pb.name.IndexOf(filter, StringComparison.OrdinalIgnoreCase) < 0) continue;
                    string icon = pb.TryGet<UIObject>(out var uio) ? uio.m_Icon : null;
                    bool hasIcon = !string.IsNullOrEmpty(icon);
                    if (noIcon && hasIcon) continue;
                    string path = pb.asset?.path ?? "";
                    string source = pb.isBuiltin ? "base game" : "mod";
                    var m = System.Text.RegularExpressions.Regex.Match(path.Replace('\\', '/'), @"/(\d+)_\d+/");
                    if (m.Success) source = "mod " + m.Groups[1].Value;
                    else if (!pb.isBuiltin && path.Length > 0) source = "local: " + System.IO.Path.GetFileName(System.IO.Path.GetDirectoryName(path));
                    bySource[source] = (bySource.TryGetValue(source, out var c) ? c : 0) + 1;
                    if (list.Count >= limit) continue;
                    list.Add(new Dictionary<string, object>
                    {
                        ["name"] = pb.name, ["group"] = g, ["has_icon"] = hasIcon, ["icon"] = icon, ["source"] = source,
                        ["file"] = System.IO.Path.GetFileName(path), ["in_city"] = used.TryGetValue(e, out var u) ? u : 0,
                    });
                }
            return new Dictionary<string, object> { ["count"] = list.Count, ["by_source"] = bySource, ["assets"] = list };
        }
    }
}
