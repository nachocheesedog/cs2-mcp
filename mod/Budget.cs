// Service budgets (v0.16): read every city service's budget slider (%), the building efficiency it gives (the game's own
// ServiceBudgetEfficiencyFactor curve), estimated upkeep per month, and the city balance; `set` = {service: percent}
// moves the sliders exactly like the Budget panel (CityServiceBudgetSystem.SetServiceBudget); `preview` = percentages to
// show the efficiency each would give without changing anything.
using System;
using System.Collections.Generic;
using Game.Prefabs;
using Game.Simulation;
using Unity.Collections;
using Unity.Entities;

namespace CS2MCP
{
    public partial class McpBridgeSystem
    {
        EntityQuery m_ServiceQuery;

        object ServiceBudget(Dictionary<string, object> p)
        {
            if (m_ServiceQuery == default)
                m_ServiceQuery = GetEntityQuery(ComponentType.ReadOnly<PrefabData>(), ComponentType.ReadOnly<UIObjectData>(), ComponentType.ReadOnly<ServiceData>());
            var sys = World.GetOrCreateSystemManaged<CityServiceBudgetSystem>();
            var set = p.TryGetValue("set", out var so) ? so as Dictionary<string, object> : null;
            var preview = Json.List(p, "preview");
            var services = new List<object>(); var changed = new Dictionary<string, object>();
            var unknown = new List<string>();
            if (set != null) foreach (var k in set.Keys) unknown.Add(k);
            using (var es = m_ServiceQuery.ToEntityArray(Allocator.Temp))
                foreach (var s in es)
                {
                    var sd = EntityManager.GetComponentData<ServiceData>(s);
                    string name = m_Prefabs.GetPrefabName(s);
                    string match = null;
                    if (set != null) foreach (var k in set.Keys) if (string.Equals(k, name, StringComparison.OrdinalIgnoreCase)) match = k;
                    if (match != null)
                    {
                        unknown.Remove(match);
                        if (!sd.m_BudgetAdjustable) { changed[name] = "not adjustable"; }
                        else
                        {
                            int pct = Math.Max(50, Math.Min(150, (int)Math.Round(Convert.ToDouble(set[match]))));   // the Budget panel's range
                            int before = sys.GetServiceBudget(s);
                            sys.SetServiceBudget(s, pct);
                            changed[name] = $"{before}% -> {pct}%";
                        }
                    }
                    int budget = sys.GetServiceBudget(s);
                    sys.GetEstimatedServiceBudget(s, out int upkeep);
                    var d = new Dictionary<string, object>
                    {
                        ["service"] = name, ["budget_pct"] = budget, ["efficiency_pct"] = sys.GetServiceEfficiency(s, budget),
                        ["upkeep"] = upkeep, ["adjustable"] = sd.m_BudgetAdjustable,
                        ["locked"] = EntityManager.HasComponent<Locked>(s) && EntityManager.IsComponentEnabled<Locked>(s),
                    };
                    if (preview != null)
                    {
                        var pv = new Dictionary<string, object>();
                        foreach (var o in preview) { int b = (int)Convert.ToDouble(o); pv[b + "%"] = sys.GetServiceEfficiency(s, b) + "%"; }
                        d["efficiency_at"] = pv;
                    }
                    services.Add(d);
                }
            var r = new Dictionary<string, object>
            {
                ["services"] = services, ["balance_per_month"] = sys.GetBalance(), ["income"] = sys.GetTotalIncome(), ["expenses"] = sys.GetTotalExpenses(),
                ["note"] = "budget 50..150% like the Budget panel; efficiency = what that budget gives every building of the service (other factors come on top)",
            };
            if (set != null) { r["changed"] = changed; if (unknown.Count > 0) r["unknown_services"] = unknown; }
            return r;
        }
    }
}
