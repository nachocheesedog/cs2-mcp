// Demand readout (the game's demand bars and their factors): residential low/medium/high, commercial, industrial,
// office, storage. Reads the demand systems' last computed values; factor arrays are completed before reading.
using System;
using System.Collections.Generic;
using Game.Simulation;
using Unity.Collections;
using Unity.Jobs;

namespace CS2MCP
{
    public partial class McpBridgeSystem
    {
        static Dictionary<string, object> Factors(NativeArray<int> arr, JobHandle deps)
        {
            deps.Complete();
            var list = new List<KeyValuePair<string, int>>();
            for (int i = 0; i < arr.Length && i < (int)DemandFactor.Count; i++)
                if (arr[i] != 0) list.Add(new KeyValuePair<string, int>(((DemandFactor)i).ToString(), arr[i]));
            list.Sort((a, b) => Math.Abs(b.Value).CompareTo(Math.Abs(a.Value)));
            var d = new Dictionary<string, object>();
            foreach (var kv in list) d[kv.Key] = kv.Value;
            return d;
        }

        object Demand(Dictionary<string, object> p)
        {
            var res = World.GetOrCreateSystemManaged<ResidentialDemandSystem>();
            var com = World.GetOrCreateSystemManaged<CommercialDemandSystem>();
            var ind = World.GetOrCreateSystemManaged<IndustrialDemandSystem>();
            bool factors = Json.Bool(p, "factors", true);
            var bd = res.buildingDemand;
            var r = new Dictionary<string, object>
            {
                ["household_demand"] = res.householdDemand,
                ["building_demand"] = new Dictionary<string, object> { ["low"] = bd.x, ["medium"] = bd.y, ["high"] = bd.z },
            };
            var c = new Dictionary<string, object> { ["company_demand"] = com.companyDemand, ["building_demand"] = com.buildingDemand };
            var i = new Dictionary<string, object> { ["company_demand"] = ind.industrialCompanyDemand, ["building_demand"] = ind.industrialBuildingDemand };
            var o = new Dictionary<string, object> { ["company_demand"] = ind.officeCompanyDemand, ["building_demand"] = ind.officeBuildingDemand };
            var s = new Dictionary<string, object> { ["company_demand"] = ind.storageCompanyDemand, ["building_demand"] = ind.storageBuildingDemand };
            if (factors)
            {
                r["factors_low"] = Factors(res.GetLowDensityDemandFactors(out var j1), j1);
                r["factors_medium"] = Factors(res.GetMediumDensityDemandFactors(out var j2), j2);
                r["factors_high"] = Factors(res.GetHighDensityDemandFactors(out var j3), j3);
                c["factors"] = Factors(com.GetDemandFactors(out var j4), j4);
                i["factors"] = Factors(ind.GetIndustrialDemandFactors(out var j5), j5);
                o["factors"] = Factors(ind.GetOfficeDemandFactors(out var j6), j6);
            }
            return new Dictionary<string, object>
            {
                ["residential"] = r, ["commercial"] = c, ["industrial"] = i, ["office"] = o, ["storage"] = s,
                ["note"] = "same numbers as the game's demand bars (roughly -100..100 per bar); factors: positive pushes demand up",
            };
        }
    }
}
