// v0.41 (2026-10-06): run the game's own service-dispatch pathfind on demand. path_test enqueues exactly the query
// HealthcareDispatchSystem.FindVehicleSource sends for a hearse / ambulance (origin = that service's vehicles in the target's
// district, destination = CurrentLocation of the target with Pedestrian | Road | Boarding), owned by a scratch entity;
// path_result reads it back once the pathfinder is done: found or not, which facility / vehicle, distance, methods and the
// lanes the route ends on. mode "hearse_road" / "ambulance_road" = the same with a Road-only destination (reaches the kerb?).
using System;
using System.Collections.Generic;
using Game.Areas;
using Game.Pathfind;
using Game.Net;
using Game.Prefabs;
using Game.Simulation;
using Unity.Entities;
using Unity.Mathematics;

namespace CS2MCP
{
    public partial class McpBridgeSystem
    {
        PathfindSetupSystem m_PathSetup;

        object PathTest(Dictionary<string, object> p)
        {
            var target = ParseId(Json.Str(p, "id", null) ?? throw new ArgumentException("id (building or citizen) required"));
            if (!EntityManager.Exists(target)) throw new ArgumentException("no such entity " + Id(target));
            var mode = Json.Str(p, "mode", "hearse");
            if (m_PathSetup == null) m_PathSetup = World.GetOrCreateSystemManaged<PathfindSetupSystem>();
            var building = EntityManager.HasComponent<Game.Citizens.CurrentBuilding>(target) ? EntityManager.GetComponentData<Game.Citizens.CurrentBuilding>(target).m_CurrentBuilding : target;
            var district = EntityManager.HasComponent<CurrentDistrict>(building) ? EntityManager.GetComponentData<CurrentDistrict>(building).m_District : Entity.Null;
            var owner = EntityManager.CreateEntity(typeof(PathInformation));
            EntityManager.AddBuffer<PathElement>(owner);
            EntityManager.SetComponentData(owner, new PathInformation { m_State = PathFlags.Pending });
            bool hearse = mode.StartsWith("hearse"), roadOnly = mode.EndsWith("_road");
            var par = new PathfindParameters
            {
                m_MaxSpeed = new float2(hearse ? 111.111115f : 277.77777f),
                m_WalkSpeed = new float2(1.6666667f),
                m_Weights = hearse ? new PathfindWeights(1f, 1f, 1f, 1f) : new PathfindWeights(1f, 0f, 0f, 0f),
                m_Methods = PathMethod.Pedestrian | PathMethod.Road | PathMethod.Boarding | (hearse ? 0 : PathMethod.Flying),
                m_IgnoredRules = hearse ? (RuleFlags.ForbidCombustionEngines | RuleFlags.ForbidHeavyTraffic | RuleFlags.ForbidSlowTraffic | RuleFlags.AvoidBicycles)
                                        : (RuleFlags.ForbidCombustionEngines | RuleFlags.ForbidTransitTraffic | RuleFlags.ForbidHeavyTraffic | RuleFlags.ForbidPrivateTraffic | RuleFlags.ForbidSlowTraffic | RuleFlags.AvoidBicycles),
                m_ParkingTarget = owner,
            };
            var origin = new SetupQueueTarget
            {
                m_Type = hearse ? SetupTargetType.Hearse : SetupTargetType.Ambulance,
                m_Methods = PathMethod.Road | PathMethod.Boarding | (hearse ? 0 : PathMethod.Flying),
                m_RoadTypes = hearse ? RoadTypes.Car : (RoadTypes.Car | RoadTypes.Helicopter),
                m_FlyingTypes = hearse ? RoadTypes.None : RoadTypes.Helicopter,
                m_Entity = district,
            };
            var dest = new SetupQueueTarget
            {
                m_Type = SetupTargetType.CurrentLocation,
                m_Methods = roadOnly ? PathMethod.Road : (PathMethod.Pedestrian | PathMethod.Road | PathMethod.Boarding | (hearse ? 0 : PathMethod.Flying)),
                m_RoadTypes = RoadTypes.Car,
                m_FlyingTypes = hearse ? RoadTypes.None : RoadTypes.Helicopter,
                m_Entity = target,
            };
            m_PathSetup.GetQueue(this, 4).Enqueue(new SetupQueueItem(owner, par, origin, dest));
            return new Dictionary<string, object> { ["token"] = Id(owner), ["target"] = Id(target), ["building"] = Id(building), ["district"] = district == Entity.Null ? null : Id(district), ["mode"] = mode };
        }

        object PathResult(Dictionary<string, object> p)
        {
            var owner = ParseId(Json.Str(p, "token", null) ?? throw new ArgumentException("token required"));
            if (!EntityManager.Exists(owner) || !EntityManager.HasComponent<PathInformation>(owner)) throw new ArgumentException("unknown token");
            var pi = EntityManager.GetComponentData<PathInformation>(owner);
            var res = new Dictionary<string, object> { ["state"] = pi.m_State.ToString() };
            if ((pi.m_State & PathFlags.Pending) != 0) { res["done"] = false; return res; }
            res["done"] = true;
            res["found"] = pi.m_Origin != Entity.Null;
            res["origin"] = pi.m_Origin == Entity.Null ? null : Id(pi.m_Origin); res["origin_prefab"] = PrefabOf(pi.m_Origin);
            res["destination"] = pi.m_Destination == Entity.Null ? null : Id(pi.m_Destination); res["destination_prefab"] = PrefabOf(pi.m_Destination);
            res["distance"] = R2(pi.m_Distance); res["methods"] = pi.m_Methods.ToString(); res["cost"] = R2(pi.m_TotalCost);
            var els = EntityManager.GetBuffer<PathElement>(owner, true);
            res["elements"] = els.Length;
            var tail = new List<object>();
            for (int i = Math.Max(0, els.Length - 6); i < els.Length; i++)
            {
                var t = els[i].m_Target; var d = LaneInfo(t) ?? new Dictionary<string, object>();
                if (!d.ContainsKey("prefab")) d["prefab"] = PrefabOf(t);
                d["element_flags"] = els[i].m_Flags.ToString();
                tail.Add(d);
            }
            res["route_end"] = tail;
            if (Json.Bool(p, "release", true)) EntityManager.DestroyEntity(owner);
            return res;
        }
    }
}
