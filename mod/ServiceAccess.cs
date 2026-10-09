// v0.41 (2026-10-06): why a hearse / ambulance never comes. For one building: its road edge, every spawn location the
// path target seeker uses (PathfindTargetSeeker.FindTargets: a spawn with a connected lane switches the road-edge "front
// connection" off, so an unreachable door = no service at all), what each spawn is connected to (lane kind, owner,
// access restriction), the building's sub-areas, and every citizen in it with a HealthProblem: flags, timer and the state of
// its HealthcareRequest (fail count, cooldown, dispatched handler, path result).
using System;
using System.Collections.Generic;
using Game.Buildings;
using Game.Citizens;
using Game.Common;
using Game.Net;
using Game.Objects;
using Game.Pathfind;
using Game.Prefabs;
using Game.Simulation;
using Unity.Collections;
using Unity.Entities;
using SubLane = Game.Net.SubLane;
using PedestrianLane = Game.Net.PedestrianLane;
using ConnectionLane = Game.Net.ConnectionLane;
using CarLane = Game.Net.CarLane;
using ParkingLane = Game.Net.ParkingLane;
using SpawnLocation = Game.Objects.SpawnLocation;

namespace CS2MCP
{
    public partial class McpBridgeSystem
    {
        string PrefabOf(Entity e) =>
            e != Entity.Null && EntityManager.HasComponent<PrefabRef>(e) ? m_Prefabs.GetPrefabName(EntityManager.GetComponentData<PrefabRef>(e).m_Prefab) : null;

        Dictionary<string, object> LaneInfo(Entity lane)
        {
            if (lane == Entity.Null) return null;
            var d = new Dictionary<string, object> { ["id"] = Id(lane), ["prefab"] = PrefabOf(lane) };
            if (EntityManager.HasComponent<PedestrianLane>(lane))
            {
                var pl = EntityManager.GetComponentData<PedestrianLane>(lane);
                d["kind"] = "pedestrian"; d["flags"] = pl.m_Flags.ToString(); d["restricted_to"] = pl.m_AccessRestriction == Entity.Null ? null : Id(pl.m_AccessRestriction);
            }
            else if (EntityManager.HasComponent<ConnectionLane>(lane))
            {
                var cl = EntityManager.GetComponentData<ConnectionLane>(lane);
                d["kind"] = "connection"; d["flags"] = cl.m_Flags.ToString(); d["restricted_to"] = cl.m_AccessRestriction == Entity.Null ? null : Id(cl.m_AccessRestriction);
            }
            else if (EntityManager.HasComponent<CarLane>(lane)) d["kind"] = "car";
            else if (EntityManager.HasComponent<ParkingLane>(lane)) d["kind"] = "parking";
            if (EntityManager.HasComponent<Owner>(lane))
            {
                var o = EntityManager.GetComponentData<Owner>(lane).m_Owner;
                d["owner"] = Id(o); d["owner_prefab"] = PrefabOf(o);
                if (EntityManager.HasComponent<Owner>(o)) d["owner_owner"] = Id(EntityManager.GetComponentData<Owner>(o).m_Owner);
            }
            return d;
        }

        EntityQuery m_HealthQuery2;

        object ServiceAccess(Dictionary<string, object> p)
        {
            var b = ParseId(Json.Str(p, "id", null) ?? throw new ArgumentException("id (a building id) required"));
            if (!EntityManager.Exists(b) || !EntityManager.HasComponent<Building>(b)) throw new ArgumentException("not a building: " + Id(b));
            var bd = EntityManager.GetComponentData<Building>(b);
            var res = new Dictionary<string, object> { ["id"] = Id(b), ["prefab"] = PrefabOf(b), ["road_edge"] = bd.m_RoadEdge == Entity.Null ? null : Id(bd.m_RoadEdge) };
            var spawns = new List<object>();
            if (EntityManager.HasBuffer<SpawnLocationElement>(b))
                foreach (var s in EntityManager.GetBuffer<SpawnLocationElement>(b, true))
                {
                    var d = new Dictionary<string, object> { ["id"] = Id(s.m_SpawnLocation), ["type"] = s.m_Type.ToString(), ["prefab"] = PrefabOf(s.m_SpawnLocation) };
                    if (EntityManager.HasComponent<Game.Objects.Transform>(s.m_SpawnLocation))
                    {
                        var t = EntityManager.GetComponentData<Game.Objects.Transform>(s.m_SpawnLocation).m_Position;
                        d["pos"] = new List<object> { R2(t.x), R2(t.y), R2(t.z) };
                    }
                    if (EntityManager.HasComponent<SpawnLocation>(s.m_SpawnLocation))
                    {
                        var sl = EntityManager.GetComponentData<SpawnLocation>(s.m_SpawnLocation);
                        d["flags"] = sl.m_Flags.ToString(); d["restricted_to"] = sl.m_AccessRestriction == Entity.Null ? null : Id(sl.m_AccessRestriction);
                        d["lane1"] = LaneInfo(sl.m_ConnectedLane1); d["lane2"] = LaneInfo(sl.m_ConnectedLane2);
                    }
                    if (EntityManager.HasComponent<PrefabRef>(s.m_SpawnLocation))
                    {
                        var pf = EntityManager.GetComponentData<PrefabRef>(s.m_SpawnLocation).m_Prefab;
                        if (EntityManager.HasComponent<SpawnLocationData>(pf))
                        {
                            var sd = EntityManager.GetComponentData<SpawnLocationData>(pf);
                            d["connection_type"] = sd.m_ConnectionType.ToString(); d["require_auth"] = sd.m_RequireAuthorization; d["activity_mask"] = sd.m_ActivityMask.m_Mask;
                        }
                    }
                    if (EntityManager.HasBuffer<SubLane>(s.m_SpawnLocation)) d["sublanes"] = EntityManager.GetBuffer<SubLane>(s.m_SpawnLocation, true).Length;
                    spawns.Add(d);
                }
            res["spawn_locations"] = spawns;
            // v0.43: every route-connection marker (Pedestrian Access Location = TakeoffLocation) among the building's
            // sub-objects, and the lanes it was linked to (AccessLane = the outside lane, RouteLane = start/end) - shows a
            // marker that joined a NEIGHBOUR's restricted walk instead of the sidewalk.
            var markers = new List<object>();
            if (EntityManager.HasBuffer<Game.Objects.SubObject>(b))
                foreach (var so in EntityManager.GetBuffer<Game.Objects.SubObject>(b, true))
                {
                    var e = so.m_SubObject;
                    bool isMarker = EntityManager.HasComponent<Game.Routes.TakeoffLocation>(e) || EntityManager.HasComponent<Game.Routes.AccessLane>(e)
                                    || EntityManager.HasComponent<Game.Routes.RouteLane>(e);
                    if (!isMarker) continue;
                    var d = new Dictionary<string, object> { ["id"] = Id(e), ["prefab"] = PrefabOf(e) };
                    if (EntityManager.HasComponent<Game.Objects.Transform>(e))
                    {
                        var t = EntityManager.GetComponentData<Game.Objects.Transform>(e).m_Position;
                        d["pos"] = new List<object> { R2(t.x), R2(t.y), R2(t.z) };
                    }
                    if (EntityManager.HasComponent<Game.Routes.TakeoffLocation>(e))
                    {
                        var tl = EntityManager.GetComponentData<Game.Routes.TakeoffLocation>(e);
                        d["flags"] = tl.m_Flags.ToString(); d["restricted_to"] = tl.m_AccessRestriction == Entity.Null ? null : Id(tl.m_AccessRestriction);
                    }
                    if (EntityManager.HasComponent<Game.Routes.AccessLane>(e)) d["access_lane"] = LaneInfo(EntityManager.GetComponentData<Game.Routes.AccessLane>(e).m_Lane);
                    if (EntityManager.HasComponent<Game.Routes.RouteLane>(e))
                    {
                        var rl = EntityManager.GetComponentData<Game.Routes.RouteLane>(e);
                        d["route_start"] = LaneInfo(rl.m_StartLane); d["route_end"] = LaneInfo(rl.m_EndLane);
                    }
                    markers.Add(d);
                }
            res["markers"] = markers;
            var areas = new List<object>();
            if (EntityManager.HasBuffer<Game.Areas.SubArea>(b))
                foreach (var a in EntityManager.GetBuffer<Game.Areas.SubArea>(b, true))
                {
                    var d = new Dictionary<string, object> { ["id"] = Id(a.m_Area), ["prefab"] = PrefabOf(a.m_Area) };
                    if (EntityManager.HasBuffer<SubLane>(a.m_Area))
                    {
                        var kinds = new Dictionary<string, int>();
                        foreach (var l in EntityManager.GetBuffer<SubLane>(a.m_Area, true))
                        {
                            var k = EntityManager.HasComponent<PedestrianLane>(l.m_SubLane) ? "pedestrian" : EntityManager.HasComponent<ConnectionLane>(l.m_SubLane) ? "connection" : "other";
                            kinds[k] = (kinds.TryGetValue(k, out var n) ? n : 0) + 1;
                        }
                        d["lanes"] = kinds;
                    }
                    areas.Add(d);
                }
            res["areas"] = areas;
            if (EntityManager.HasBuffer<SubLane>(b))
            {
                var kinds = new Dictionary<string, int>();
                foreach (var l in EntityManager.GetBuffer<SubLane>(b, true))
                {
                    var k = EntityManager.HasComponent<PedestrianLane>(l.m_SubLane) ? "pedestrian" : EntityManager.HasComponent<ConnectionLane>(l.m_SubLane) ? "connection" : EntityManager.HasComponent<CarLane>(l.m_SubLane) ? "car" : "other";
                    kinds[k] = (kinds.TryGetValue(k, out var n) ? n : 0) + 1;
                }
                res["building_lanes"] = kinds;
            }
            if (m_HealthQuery2 == default)
                m_HealthQuery2 = GetEntityQuery(ComponentType.ReadOnly<HealthProblem>(), ComponentType.ReadOnly<Citizen>(), ComponentType.ReadOnly<CurrentBuilding>(), ComponentType.Exclude<Deleted>());
            var people = new List<object>();
            using (var es = m_HealthQuery2.ToEntityArray(Allocator.Temp))
                foreach (var c in es)
                {
                    if (EntityManager.GetComponentData<CurrentBuilding>(c).m_CurrentBuilding != b) continue;
                    var hp = EntityManager.GetComponentData<HealthProblem>(c);
                    var d = new Dictionary<string, object> { ["citizen"] = Id(c), ["flags"] = hp.m_Flags.ToString(), ["timer"] = (int)hp.m_Timer };
                    var r = hp.m_HealthcareRequest;
                    if (r != Entity.Null && EntityManager.Exists(r))
                    {
                        var rq = new Dictionary<string, object> { ["id"] = Id(r) };
                        if (EntityManager.HasComponent<HealthcareRequest>(r)) rq["type"] = EntityManager.GetComponentData<HealthcareRequest>(r).m_Type.ToString();
                        if (EntityManager.HasComponent<ServiceRequest>(r))
                        {
                            var sr = EntityManager.GetComponentData<ServiceRequest>(r);
                            rq["fail_count"] = (int)sr.m_FailCount; rq["cooldown"] = (int)sr.m_Cooldown; rq["flags"] = sr.m_Flags.ToString();
                        }
                        if (EntityManager.HasComponent<Dispatched>(r)) { var h = EntityManager.GetComponentData<Dispatched>(r).m_Handler; rq["handler"] = Id(h); rq["handler_prefab"] = PrefabOf(h); }
                        if (EntityManager.HasComponent<PathInformation>(r))
                        {
                            var pi = EntityManager.GetComponentData<PathInformation>(r);
                            rq["path_state"] = pi.m_State.ToString(); rq["path_origin"] = pi.m_Origin == Entity.Null ? null : Id(pi.m_Origin);
                            rq["path_destination"] = pi.m_Destination == Entity.Null ? null : Id(pi.m_Destination); rq["path_distance"] = R2(pi.m_Distance);
                        }
                        d["request"] = rq;
                    }
                    else d["request"] = null;
                    people.Add(d);
                }
            res["health_problems"] = people;
            return res;
        }
    }
}
