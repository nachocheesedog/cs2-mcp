// v0.39 (2026-09-28): stop the "[CRITICAL] Cached ServiceUpkeepData not found Entity(..)" popups after our prefab work.
// Game code (decompiled): GameModeSystem.OnUpdate runs at every city load - ModeSetting.RestoreDefaultData (undo the previous
// load's mode) then StoreDefaultData (cache again) + ApplyMode. ServiceConsumptionGlobalMode.StoreDefaultData caches each
// service prefab's Money ServiceUpkeepData in a private Dictionary<Entity, ServiceUpkeepData>; RestoreDefaultData logs a
// CRITICAL (= an error popup) for every service prefab entity missing from it. Prefabs imported / cloned / reconfigured
// after the last city load (imports at the main menu, prefab_clone, a re-import) are missing -> one popup each at the next
// load. It is the ONLY global mode with such a cache (checked all 16 *GlobalMode classes). Fix: after every prefab-changing
// command, add the missing entities with their current Money upkeep (fresh prefab data = the unmultiplied default, since
// the mode was applied before they existed). Also callable on its own: method "mode_cache_sync".
// FOUND LATER THE SAME DAY (Player.log): the big popup flood (~600 per load) has a second cause - StoreDefaultData uses
// Dictionary.Add, so a prefab with TWO Money entries in its ServiceUpkeepData throws "An item with the same key has already
// been added" mid-loop, and every prefab after it goes uncached (and ApplyMode is skipped). So we also sync right after every
// city load (OnGameLoaded) and report the duplicate-Money prefabs by name ("duplicate_money") so they can be fixed at source.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using Game.Economy;
using Game.Prefabs;
using Game.Prefabs.Modes;
using Unity.Collections;
using Unity.Entities;

namespace CS2MCP
{
    public partial class McpBridgeSystem
    {
        static readonly FieldInfo s_ModeGlobals = typeof(ModeSetting).GetField("m_GlobalModePrefabs", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
        static readonly FieldInfo s_UpkeepCache = typeof(ServiceConsumptionGlobalMode).GetField("m_CachedUpkeepDatasDatas", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);

        Dictionary<string, object> SyncModeCache()
        {
            var r = new Dictionary<string, object> { ["added"] = 0 };
            try
            {
                var mode = World.GetExistingSystemManaged<GameModeSystem>()?.modeSetting;
                if (mode == null) { r["note"] = "no game mode applied yet (no city loaded this session) - nothing cached, nothing to fix"; return r; }
                if (s_ModeGlobals == null || s_UpkeepCache == null) { r["error"] = "game fields not found (game update?)"; return r; }
                int added = 0, checkedN = 0; var dup = new List<string>();
                foreach (var gm in (IEnumerable)s_ModeGlobals.GetValue(mode))
                {
                    if (!(gm is ServiceConsumptionGlobalMode sc)) continue;
                    if (!(s_UpkeepCache.GetValue(sc) is Dictionary<Entity, ServiceUpkeepData> cache)) continue;
                    var q = EntityManager.CreateEntityQuery(sc.GetEntityQueryDesc());
                    var ents = q.ToEntityArray(Allocator.Temp);
                    try
                    {
                        foreach (var e in ents)
                        {
                            checkedN++;
                            var ub = EntityManager.GetBuffer<ServiceUpkeepData>(e, true); int money = 0;
                            for (int j = 0; j < ub.Length; j++) if (ub[j].m_Upkeep.m_Resource == Resource.Money) money++;
                            if (money > 1) dup.Add((World.GetExistingSystemManaged<PrefabSystem>().TryGetPrefab<PrefabBase>(e, out var pb) ? pb.name : "?") + " " + e.Index + ":" + e.Version + " x" + money);
                            if (cache.ContainsKey(e)) continue;
                            var buf = EntityManager.GetBuffer<ServiceUpkeepData>(e, true);
                            for (int j = 0; j < buf.Length; j++)
                                if (buf[j].m_Upkeep.m_Resource == Resource.Money) { cache[e] = buf[j]; added++; break; }
                        }
                    }
                    finally { ents.Dispose(); }
                }
                r["added"] = added; r["checked"] = checkedN; if (dup.Count > 0) r["duplicate_money"] = dup;
                if (added > 0) Log.Info($"mode_cache_sync: added {added} service prefab(s) to the upkeep cache (no CRITICAL popup at the next load)");
            }
            catch (Exception e) { r["error"] = e.GetType().Name + ": " + e.Message; Log.Info("mode_cache_sync failed: " + e); }
            return r;
        }

        // wraps a prefab-changing command's result: sync, and report what was added
        object WithModeCache(object result)
        {
            var s = SyncModeCache();
            if (result is Dictionary<string, object> d && (!(s["added"] is int n) || n != 0 || s.ContainsKey("error"))) d["mode_cache"] = s;
            return result;
        }
    }
}
