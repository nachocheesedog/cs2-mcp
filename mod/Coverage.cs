// City services readout (v0.15; v0.27 adds deathcare facilities + the dead waiting for a hearse): per building the service coverage the game uses for happiness (ServiceCoverage on
// the building's road edge, lerped at its curve position, exactly like NetUtils.GetServiceCoverage), its garbage and
// crime levels, and who lives there (residents, school-age children / teens and how many of them are in school, sick);
// plus every school / hospital / police station / garbage facility with capacity (incl. upgrades), load and efficiency.
// The thresholds the game compares against (neutral education, negligible crime, garbage baseline) come along.
using System;
using System.Collections.Generic;
using Game.Buildings;
using Game.Citizens;
using Game.Economy;
using Game.Net;
using Game.Prefabs;
using Game.Vehicles;
using Unity.Collections;
using Unity.Entities;
using Transform = Game.Objects.Transform;

namespace CS2MCP
{
    public partial class McpBridgeSystem
    {
        static readonly string[] CoverageCols =
        {
            "x", "z", "healthcare", "fire", "police", "park", "post", "education", "shelter", "welfare",
            "garbage", "crime", "residents", "children", "teens", "children_in_school", "teens_in_school", "sick", "avg_health", "kind",
            "dead_waiting",
        };

        static float R2(float v) => (float)Math.Round(v, 2);

        EntityQuery m_HealthProblemQuery;

        // v0.27: every citizen with a HealthProblem, bucketed by the building they are in (CurrentBuilding). The dead leave their
        // household at death (DeathCheckSystem.PerformAfterDeathActions) and carry the "waiting for a hearse" icon themselves,
        // so neither the Renter/HouseholdCitizen walk nor the building's IconElement buffer sees them.
        Dictionary<string, object> HealthCensus(Dictionary<Entity, int> deadWaitingAt)
        {
            if (m_HealthProblemQuery == default)
                m_HealthProblemQuery = GetEntityQuery(ComponentType.ReadOnly<HealthProblem>(), ComponentType.ReadOnly<Citizen>(), ComponentType.Exclude<Game.Common.Deleted>());
            int deadWaiting = 0, deadCollected = 0, deadInTransit = 0, sick = 0, injured = 0, needAmbulance = 0, maxTimer = 0;
            using (var es = m_HealthProblemQuery.ToEntityArray(Allocator.Temp))
                foreach (var c in es)
                {
                    var hp = EntityManager.GetComponentData<HealthProblem>(c);
                    if ((hp.m_Flags & HealthProblemFlags.Dead) != 0)
                    {
                        // v0.29: the dead keep RequireTransport in the hearse and at the crematorium too (HealthProblemSystem),
                        // so split by where the body is: at a deathcare facility / in transit (no building) / waiting in a building
                        var at = EntityManager.HasComponent<CurrentBuilding>(c) ? EntityManager.GetComponentData<CurrentBuilding>(c).m_CurrentBuilding : Entity.Null;
                        if (at == Entity.Null) { deadInTransit++; continue; }
                        if (EntityManager.HasComponent<Game.Buildings.DeathcareFacility>(at) || EntityManager.HasComponent<Game.Buildings.Hospital>(at)) { deadCollected++; continue; }
                        deadWaiting++; maxTimer = Math.Max(maxTimer, (int)hp.m_Timer);
                        deadWaitingAt[at] = (deadWaitingAt.TryGetValue(at, out var n) ? n : 0) + 1;
                        continue;
                    }
                    if ((hp.m_Flags & HealthProblemFlags.Sick) != 0) sick++;
                    if ((hp.m_Flags & HealthProblemFlags.Injured) != 0) injured++;
                    if ((hp.m_Flags & HealthProblemFlags.RequireTransport) != 0) needAmbulance++;
                }
            return new Dictionary<string, object>
            {
                ["dead_waiting_for_hearse"] = deadWaiting, ["dead_at_deathcare_or_hospital"] = deadCollected, ["dead_in_transit"] = deadInTransit,
                ["longest_wait_timer"] = maxTimer, ["sick"] = sick, ["injured"] = injured, ["waiting_for_ambulance"] = needAmbulance,
            };
        }

        object Coverage(Dictionary<string, object> p)
        {
            var box = BoxOf(p);
            bool rows = Json.Bool(p, "rows", true);
            var deadAt = new Dictionary<Entity, int>();
            var census = HealthCensus(deadAt);
            var list = new List<object>();
            var services = new List<object>();
            using (var es = m_BuildingQuery.ToEntityArray(Allocator.Temp))
                foreach (var b in es)
                {
                    var pos = EntityManager.GetComponentData<Transform>(b).m_Position;
                    if (!box.In(pos)) continue;
                    var prefab = EntityManager.GetComponentData<PrefabRef>(b).m_Prefab;
                    var svc = ServiceRow(b, prefab, pos);
                    if (svc != null) services.Add(svc);
                    if (!rows) continue;
                    var bd = EntityManager.GetComponentData<Building>(b);
                    var r = new float[CoverageCols.Length];
                    r[0] = (float)Math.Round(pos.x, 1); r[1] = (float)Math.Round(pos.z, 1);
                    if (bd.m_RoadEdge != Entity.Null && EntityManager.HasBuffer<Game.Net.ServiceCoverage>(bd.m_RoadEdge))
                    {
                        var cov = EntityManager.GetBuffer<Game.Net.ServiceCoverage>(bd.m_RoadEdge, true);
                        for (int s = 0; s < 8; s++) r[2 + s] = R2(NetUtils.GetServiceCoverage(cov, (CoverageService)s, bd.m_CurvePosition));
                    }
                    else for (int s = 0; s < 8; s++) r[2 + s] = -1f;           // no road: no coverage
                    if (EntityManager.HasComponent<GarbageProducer>(b)) r[10] = EntityManager.GetComponentData<GarbageProducer>(b).m_Garbage;
                    if (EntityManager.HasComponent<CrimeProducer>(b)) r[11] = R2(EntityManager.GetComponentData<CrimeProducer>(b).m_Crime);
                    int res = 0, kids = 0, teens = 0, kidsIn = 0, teensIn = 0, sick = 0, healthSum = 0;
                    bool residential = EntityManager.HasComponent<BuildingPropertyData>(prefab) && EntityManager.GetComponentData<BuildingPropertyData>(prefab).m_ResidentialProperties > 0;
                    if (residential && EntityManager.HasBuffer<Renter>(b))
                        foreach (var rent in EntityManager.GetBuffer<Renter>(b, true))
                        {
                            if (!EntityManager.HasBuffer<HouseholdCitizen>(rent.m_Renter)) continue;
                            foreach (var hc in EntityManager.GetBuffer<HouseholdCitizen>(rent.m_Renter, true))
                            {
                                if (!EntityManager.HasComponent<Citizen>(hc.m_Citizen)) continue;
                                var c = EntityManager.GetComponentData<Citizen>(hc.m_Citizen);
                                res++; healthSum += c.m_Health;
                                var age = c.GetAge();
                                bool student = EntityManager.HasComponent<Game.Citizens.Student>(hc.m_Citizen);
                                if (age == CitizenAge.Child) { kids++; if (student) kidsIn++; }
                                else if (age == CitizenAge.Teen) { teens++; if (student) teensIn++; }
                                if (EntityManager.HasComponent<HealthProblem>(hc.m_Citizen) &&
                                    (EntityManager.GetComponentData<HealthProblem>(hc.m_Citizen).m_Flags & (HealthProblemFlags.Sick | HealthProblemFlags.Injured)) != 0) sick++;
                            }
                        }
                    r[12] = res; r[13] = kids; r[14] = teens; r[15] = kidsIn; r[16] = teensIn; r[17] = sick;
                    r[18] = res > 0 ? R2((float)healthSum / res) : 0f;
                    // kind: 1 residential (incl. mixed), 2 city service, 0 other (work places, etc.)
                    r[19] = residential ? 1 : (EntityManager.HasComponent<ServiceObjectData>(prefab) || svc != null ? 2 : 0);
                    r[20] = deadAt.TryGetValue(b, out var dw) ? dw : 0;
                    list.Add(r);
                }
            var hp = SingletonOrDefault<CitizenHappinessParameterData>();
            var gp = SingletonOrDefault<GarbageParameterData>();
            return new Dictionary<string, object>
            {
                ["columns"] = CoverageCols, ["rows"] = list, ["services"] = services, ["health_census"] = census,
                ["thresholds"] = new Dictionary<string, object>
                {
                    ["neutral_education"] = hp.m_NeutralEducation, ["negligible_crime"] = hp.m_NegligibleCrime,
                    ["crime_multiplier"] = hp.m_CrimeMultiplier, ["healthcare_health_multiplier"] = hp.m_HealthCareHealthMultiplier,
                    ["garbage_baseline"] = gp.m_HappinessEffectBaseline, ["garbage_step"] = gp.m_HappinessEffectStep,
                },
                ["note"] = "coverage = the game's ServiceCoverage at the building's road position (-1 = no road); garbage / crime = the building's accumulated level",
            };
        }

        T SingletonOrDefault<T>() where T : unmanaged, IComponentData
        {
            var q = GetEntityQuery(ComponentType.ReadOnly<T>());
            return q.IsEmptyIgnoreFilter ? default : q.GetSingleton<T>();
        }

        Dictionary<string, object> ServiceRow(Entity b, Entity prefab, Unity.Mathematics.float3 pos)
        {
            string kind = null; var d = new Dictionary<string, object>();
            if (EntityManager.HasComponent<Game.Buildings.School>(b) && UpgradeUtils.TryGetCombinedComponent<SchoolData>(EntityManager, b, prefab, out var sd))
            {
                kind = "school"; d["level"] = (int)sd.m_EducationLevel; d["capacity"] = sd.m_StudentCapacity;
                d["load"] = EntityManager.HasBuffer<Game.Buildings.Student>(b) ? EntityManager.GetBuffer<Game.Buildings.Student>(b, true).Length : 0;
            }
            else if (EntityManager.HasComponent<Game.Buildings.Hospital>(b) && UpgradeUtils.TryGetCombinedComponent<HospitalData>(EntityManager, b, prefab, out var hd))
            {
                kind = "hospital"; d["capacity"] = hd.m_PatientCapacity; d["ambulances"] = hd.m_AmbulanceCapacity;
                d["load"] = EntityManager.HasBuffer<Patient>(b) ? EntityManager.GetBuffer<Patient>(b, true).Length : 0;
            }
            else if (EntityManager.HasComponent<Game.Buildings.PoliceStation>(b) && UpgradeUtils.TryGetCombinedComponent<PoliceStationData>(EntityManager, b, prefab, out var pd))
            {
                kind = "police"; d["capacity"] = pd.m_JailCapacity; d["patrol_cars"] = pd.m_PatrolCarCapacity;
                d["load"] = EntityManager.HasBuffer<Occupant>(b) ? EntityManager.GetBuffer<Occupant>(b, true).Length : 0;
            }
            else if (EntityManager.HasComponent<Game.Buildings.GarbageFacility>(b) && UpgradeUtils.TryGetCombinedComponent<GarbageFacilityData>(EntityManager, b, prefab, out var gd))
            {
                kind = "garbage"; d["capacity"] = gd.m_GarbageCapacity; d["trucks"] = gd.m_VehicleCapacity; d["processing"] = gd.m_ProcessingSpeed;
                d["industrial_only"] = gd.m_IndustrialWasteOnly;
                d["load"] = EntityManager.HasBuffer<Resources>(b) ? EconomyUtils.GetResources(Resource.Garbage, EntityManager.GetBuffer<Resources>(b, true)) : 0;
            }
            else if (EntityManager.HasComponent<Game.Buildings.DeathcareFacility>(b) && UpgradeUtils.TryGetCombinedComponent<DeathcareFacilityData>(EntityManager, b, prefab, out var dd))
            {
                // capacity = bodies it can hold; processing = bodies per game month (DeathcareFacilityAISystem: rate / 1024 per
                // tick, 1024 ticks a day); a full facility (no room) stops dispatching hearses - the usual cause of piles of dead
                var f = EntityManager.GetComponentData<Game.Buildings.DeathcareFacility>(b);
                kind = "deathcare"; d["capacity"] = dd.m_StorageCapacity; d["hearses"] = dd.m_HearseCapacity;
                d["processing"] = dd.m_ProcessingRate; d["long_term"] = dd.m_LongTermStorage;
                d["load"] = (EntityManager.HasBuffer<Patient>(b) ? EntityManager.GetBuffer<Patient>(b, true).Length : 0) + f.m_LongTermStoredCount;
                d["has_room"] = (f.m_Flags & DeathcareFacilityFlags.HasRoomForBodies) != 0;
                d["hearses_available"] = (f.m_Flags & DeathcareFacilityFlags.HasAvailableHearses) != 0;
            }
            if (kind == null) return null;
            d["kind"] = kind; d["id"] = Id(b); d["type"] = m_Prefabs.GetPrefabName(prefab);
            d["position"] = new[] { (float)Math.Round(pos.x, 1), (float)Math.Round(pos.z, 1) };
            d["efficiency"] = EntityManager.HasBuffer<Efficiency>(b) ? R2(BuildingUtils.GetEfficiency(EntityManager.GetBuffer<Efficiency>(b, true))) : 1f;
            if (EntityManager.HasBuffer<Efficiency>(b))                 // why: every factor below 100% (budget, staffing, power...)
            {
                var why = new Dictionary<string, object>();
                foreach (var f in EntityManager.GetBuffer<Efficiency>(b, true)) if (f.m_Efficiency < 0.995f) why[f.m_Factor.ToString()] = R2(f.m_Efficiency);
                if (why.Count > 0) d["efficiency_factors"] = why;
            }
            d["vehicles_out"] = EntityManager.HasBuffer<OwnedVehicle>(b) ? EntityManager.GetBuffer<OwnedVehicle>(b, true).Length : 0;
            return d;
        }
    }
}
