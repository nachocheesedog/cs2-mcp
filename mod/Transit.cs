// Transit + movement readouts (v0.15):
// - transit: every passenger line (type, number, name, stops, length, vehicles, riders / capacity like the game's line
//   panel, waiting passengers per stop) and every passenger stop (incl. station platforms) with the lines serving it.
//   The game keeps no ridership history per stop, so these are live values - the server samples them over time.
// - movement: a snapshot of where people walk and cars drive right now, binned on a grid (pedestrians, cars, transit).
// - commute: home -> workplace (and home -> school) pairs of every worker / student, aggregated on a grid.
using System;
using System.Collections.Generic;
using Game.Buildings;
using Game.Citizens;
using Game.Common;
using Game.Creatures;
using Game.Prefabs;
using Game.Routes;
using Game.Tools;
using Game.UI.InGame;
using Game.Vehicles;
using Unity.Collections;
using Unity.Entities;
using Unity.Mathematics;
using Transform = Game.Objects.Transform;

namespace CS2MCP
{
    public partial class McpBridgeSystem
    {
        EntityQuery m_LineQuery, m_StopQuery, m_HumanQuery, m_CarQuery, m_WorkerQuery, m_StudentQuery;

        EntityQuery LiveOwned(params ComponentType[] all) => GetEntityQuery(new EntityQueryDesc
        {
            All = all, None = new[] { ComponentType.ReadOnly<Deleted>(), ComponentType.ReadOnly<Temp>() }
        });

        static float[] XZ(float3 p) => new[] { (float)Math.Round(p.x, 1), (float)Math.Round(p.z, 1) };

        object Transit(Dictionary<string, object> p)
        {
            if (m_LineQuery == default)
            {
                m_LineQuery = LiveOwned(ComponentType.ReadOnly<Route>(), ComponentType.ReadOnly<TransportLine>(), ComponentType.ReadOnly<PrefabRef>());
                m_StopQuery = LiveOwned(ComponentType.ReadOnly<Game.Routes.TransportStop>(), ComponentType.ReadOnly<Transform>(), ComponentType.ReadOnly<PrefabRef>());
            }
            bool cargo = Json.Bool(p, "include_cargo", false);
            var lines = new List<object>();
            var stopLines = new Dictionary<Entity, List<string>>();
            var stopWaiting = new Dictionary<Entity, int>();
            using (var es = m_LineQuery.ToEntityArray(Allocator.Temp))
                foreach (var l in es)
                {
                    var ld = EntityManager.GetComponentData<TransportLineData>(EntityManager.GetComponentData<PrefabRef>(l).m_Prefab);
                    if (ld.m_CargoTransport && !cargo) continue;
                    int riders = 0, capacity = 0;
                    int vehicles = TransportUIUtils.GetRouteVehiclesCount(EntityManager, l, ref riders, ref capacity);
                    var route = EntityManager.GetComponentData<Route>(l);
                    string label = (EntityManager.HasComponent<RouteNumber>(l) ? EntityManager.GetComponentData<RouteNumber>(l).m_Number.ToString() : "?");
                    var stops = new List<object>(); int waitingTotal = 0;
                    foreach (var w in EntityManager.GetBuffer<RouteWaypoint>(l, true))
                    {
                        if (!EntityManager.HasComponent<Connected>(w.m_Waypoint)) continue;
                        var stop = EntityManager.GetComponentData<Connected>(w.m_Waypoint).m_Connected;
                        if (!EntityManager.HasComponent<Game.Routes.TransportStop>(stop) || EntityManager.HasComponent<TaxiStand>(stop)) continue;
                        int waiting = 0, avgWait = 0;
                        if (EntityManager.HasComponent<WaitingPassengers>(w.m_Waypoint))
                        {
                            var wp = EntityManager.GetComponentData<WaitingPassengers>(w.m_Waypoint);
                            waiting = wp.m_Count; avgWait = wp.m_AverageWaitingTime;
                        }
                        waitingTotal += waiting;
                        var pos = EntityManager.HasComponent<Game.Routes.Position>(w.m_Waypoint) ? EntityManager.GetComponentData<Game.Routes.Position>(w.m_Waypoint).m_Position
                                : EntityManager.GetComponentData<Transform>(stop).m_Position;
                        stops.Add(new object[] { Id(stop), XZ(pos), waiting, avgWait });
                        if (!stopLines.TryGetValue(stop, out var sl)) stopLines[stop] = sl = new List<string>();
                        sl.Add(Id(l)); stopWaiting[stop] = (stopWaiting.TryGetValue(stop, out var sw) ? sw : 0) + waiting;
                    }
                    lines.Add(new Dictionary<string, object>
                    {
                        ["id"] = Id(l), ["number"] = label, ["name"] = DistrictName(l), ["transport"] = ld.m_TransportType.ToString(),
                        ["cargo"] = ld.m_CargoTransport, ["active"] = !RouteUtils.CheckOption(route, RouteOption.Inactive),
                        ["length_m"] = (int)TransportUIUtils.GetRouteLength(EntityManager, l), ["vehicles"] = vehicles,
                        ["riders"] = riders, ["capacity"] = capacity, ["usage"] = capacity > 0 ? R2((float)riders / capacity) : 0f,
                        ["ticket_price"] = (int)EntityManager.GetComponentData<TransportLine>(l).m_TicketPrice,
                        ["waiting"] = waitingTotal, ["stops"] = stops,
                    });
                }
            var allStops = new List<object>();
            using (var es = m_StopQuery.ToEntityArray(Allocator.Temp))
                foreach (var s in es)
                {
                    var pref = EntityManager.GetComponentData<PrefabRef>(s).m_Prefab;
                    if (EntityManager.HasComponent<TaxiStand>(s)) continue;
                    string mode = EntityManager.HasComponent<TransportStopData>(pref) ? EntityManager.GetComponentData<TransportStopData>(pref).m_TransportType.ToString() : "?";
                    if (EntityManager.HasComponent<TransportStopData>(pref) && !EntityManager.GetComponentData<TransportStopData>(pref).m_PassengerTransport && !cargo) continue;
                    string station = null;
                    var o = s;
                    while (EntityManager.HasComponent<Owner>(o)) { o = EntityManager.GetComponentData<Owner>(o).m_Owner; if (EntityManager.HasComponent<Building>(o)) { station = m_Prefabs.GetPrefabName(EntityManager.GetComponentData<PrefabRef>(o).m_Prefab); break; } }
                    allStops.Add(new Dictionary<string, object>
                    {
                        ["id"] = Id(s), ["type"] = m_Prefabs.GetPrefabName(pref), ["mode"] = mode, ["station"] = station,
                        ["position"] = XZ(EntityManager.GetComponentData<Transform>(s).m_Position),
                        ["lines"] = stopLines.TryGetValue(s, out var ls) ? ls : new List<string>(),
                        ["waiting"] = stopWaiting.TryGetValue(s, out var wt) ? wt : 0,
                    });
                }
            return new Dictionary<string, object>
            {
                ["lines"] = lines, ["stops"] = allStops,
                ["stop_columns"] = new[] { "stop_id", "position", "waiting", "avg_waiting_time" },
                ["note"] = "live values (riders on board now, passengers waiting now); usage = riders / capacity like the line panel",
            };
        }

        object Movement(Dictionary<string, object> p)
        {
            if (m_HumanQuery == default)
            {
                m_HumanQuery = GetEntityQuery(new EntityQueryDesc { All = new[] { ComponentType.ReadOnly<Human>(), ComponentType.ReadOnly<Transform>() },
                    None = new[] { ComponentType.ReadOnly<Deleted>(), ComponentType.ReadOnly<Temp>(), ComponentType.ReadOnly<Game.Objects.Unspawned>() } });
                m_CarQuery = GetEntityQuery(new EntityQueryDesc { All = new[] { ComponentType.ReadOnly<Car>(), ComponentType.ReadOnly<Transform>() },
                    None = new[] { ComponentType.ReadOnly<Deleted>(), ComponentType.ReadOnly<Temp>(), ComponentType.ReadOnly<Game.Objects.Unspawned>() } });
            }
            var box = BoxOf(p); float g = (float)Json.Num(p, "grid_m", 100);
            var cells = new Dictionary<long, int[]>();
            int peds = 0, cars = 0, transit = 0;
            void Add(float3 q, int k) { long key = ((long)(int)math.floor(q.x / g) << 32) ^ (uint)(int)math.floor(q.z / g); if (!cells.TryGetValue(key, out var c)) cells[key] = c = new int[3]; c[k]++; }
            using (var hs = m_HumanQuery.ToComponentDataArray<Transform>(Allocator.Temp))
                foreach (var t in hs) if (box.In(t.m_Position)) { Add(t.m_Position, 0); peds++; }
            using (var cs = m_CarQuery.ToEntityArray(Allocator.Temp))
                foreach (var c in cs)
                {
                    var q = EntityManager.GetComponentData<Transform>(c).m_Position;
                    if (!box.In(q)) continue;
                    if (EntityManager.HasComponent<Game.Vehicles.PublicTransport>(c)) { Add(q, 2); transit++; } else { Add(q, 1); cars++; }
                }
            var list = new List<object>();
            foreach (var kv in cells)
            {
                int ix = (int)(kv.Key >> 32), iz = (int)(uint)(kv.Key & 0xffffffff);
                list.Add(new object[] { (ix + 0.5f) * g, (iz + 0.5f) * g, kv.Value[0], kv.Value[1], kv.Value[2] });
            }
            return new Dictionary<string, object>
            {
                ["grid_m"] = g, ["pedestrians"] = peds, ["cars"] = cars, ["transit_vehicles"] = transit,
                ["columns"] = new[] { "x", "z", "pedestrians", "cars", "transit_vehicles" }, ["cells"] = list,
                ["note"] = "snapshot of spawned agents now (people inside vehicles / buildings are not counted as pedestrians)",
            };
        }

        object Commute(Dictionary<string, object> p)
        {
            if (m_WorkerQuery == default)
            {
                m_WorkerQuery = LiveOwned(ComponentType.ReadOnly<Citizen>(), ComponentType.ReadOnly<Worker>(), ComponentType.ReadOnly<HouseholdMember>());
                m_StudentQuery = LiveOwned(ComponentType.ReadOnly<Citizen>(), ComponentType.ReadOnly<Game.Citizens.Student>(), ComponentType.ReadOnly<HouseholdMember>());
            }
            float g = (float)Json.Num(p, "grid_m", 400); int limit = (int)Json.Num(p, "limit", 4000);
            bool students = Json.Bool(p, "students", true);
            var flows = new Dictionary<(int, int, int, int, int), int>();
            int counted = 0, unknown = 0;
            bool Home(Entity citizen, out float3 pos)
            {
                pos = default;
                var hh = EntityManager.GetComponentData<HouseholdMember>(citizen).m_Household;
                if (!EntityManager.HasComponent<PropertyRenter>(hh)) return false;
                var b = EntityManager.GetComponentData<PropertyRenter>(hh).m_Property;
                if (!EntityManager.HasComponent<Transform>(b)) return false;
                pos = EntityManager.GetComponentData<Transform>(b).m_Position; return true;
            }
            bool Place(Entity e, out float3 pos)
            {
                pos = default;
                if (EntityManager.HasComponent<PropertyRenter>(e)) e = EntityManager.GetComponentData<PropertyRenter>(e).m_Property;   // company -> its building
                if (!EntityManager.HasComponent<Transform>(e)) return false;
                pos = EntityManager.GetComponentData<Transform>(e).m_Position; return true;
            }
            void Count(EntityQuery q, int kind, Func<Entity, Entity> target)
            {
                using (var es = q.ToEntityArray(Allocator.Temp))
                    foreach (var c in es)
                    {
                        if (!Home(c, out var h) || !Place(target(c), out var w)) { unknown++; continue; }
                        var k = ((int)math.floor(h.x / g), (int)math.floor(h.z / g), (int)math.floor(w.x / g), (int)math.floor(w.z / g), kind);
                        flows[k] = (flows.TryGetValue(k, out var n) ? n : 0) + 1; counted++;
                    }
            }
            Count(m_WorkerQuery, 0, c => EntityManager.GetComponentData<Worker>(c).m_Workplace);
            if (students) Count(m_StudentQuery, 1, c => EntityManager.GetComponentData<Game.Citizens.Student>(c).m_School);
            var sorted = new List<KeyValuePair<(int, int, int, int, int), int>>(flows);
            sorted.Sort((a, b) => b.Value.CompareTo(a.Value));
            var list = new List<object>();
            int homeOnly = 0;
            foreach (var kv in sorted)
            {
                if (list.Count >= limit) { homeOnly += kv.Value; continue; }
                var k = kv.Key;
                list.Add(new object[] { (k.Item1 + 0.5f) * g, (k.Item2 + 0.5f) * g, (k.Item3 + 0.5f) * g, (k.Item4 + 0.5f) * g, kv.Value, k.Item5 == 0 ? "work" : "school" });
            }
            return new Dictionary<string, object>
            {
                ["grid_m"] = g, ["trips"] = counted, ["unresolved"] = unknown, ["pairs"] = flows.Count, ["not_listed_trips"] = homeOnly,
                ["columns"] = new[] { "home_x", "home_z", "dest_x", "dest_z", "people", "purpose" }, ["flows"] = list,
            };
        }
    }
}
