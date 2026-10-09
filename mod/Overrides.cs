// prefab_entity (v0.24): read / write fields of a prefab's ECS component data (the runtime data systems actually use,
// e.g. CarData.m_SizeClass / m_EnergyType that vehicle selection filters on), optionally PERSISTED so the mod re-applies
// them on every game start. Needed because edits to vanilla prefabs are not saved anywhere (no asset to save into).
//   prefab_entity  {name, component, set?: {field: value}, persist?: bool (default true when set), remove?: bool}
//   prefab_overrides {} -> the persisted list
// File: %LOCALAPPDATA%Low\Colossal Order\Cities Skylines II\ModsData\CS2MCP\prefab_overrides.json
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using Game.Prefabs;
using Unity.Entities;

namespace CS2MCP
{
    public partial class McpBridgeSystem
    {
        static string OverridesPath => Path.Combine(UnityEngine.Application.persistentDataPath, "ModsData", "CS2MCP", "prefab_overrides.json");
        bool m_OverridesApplied;
        int m_OverrideTries;

        static Type FindEcsType(string name)
        {
            foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
            {
                if (asm.GetName().Name != "Game") continue;                  // v0.33: exactly Game.dll (a mod named Game* must not match)
                Type[] types; try { types = asm.GetTypes(); } catch (ReflectionTypeLoadException e) { types = e.Types.Where(t => t != null).ToArray(); }
                var t = types.FirstOrDefault(x => x.IsValueType && typeof(IComponentData).IsAssignableFrom(x) && (x.FullName == name || x.Name == name));
                if (t != null) return t;
            }
            return null;
        }

        object GetComp(Entity e, Type t) =>
            typeof(EntityManager).GetMethods().First(m => m.Name == "GetComponentData" && m.IsGenericMethodDefinition && m.GetParameters().Length == 1 && m.GetParameters()[0].ParameterType == typeof(Entity))
                .MakeGenericMethod(t).Invoke(EntityManager, new object[] { e });

        void SetComp(Entity e, Type t, object v) =>
            typeof(EntityManager).GetMethods().First(m => m.Name == "SetComponentData" && m.IsGenericMethodDefinition && m.GetParameters().Length == 2 && m.GetParameters()[0].ParameterType == typeof(Entity))
                .MakeGenericMethod(t).Invoke(EntityManager, new object[] { e, v });

        bool HasComp(Entity e, Type t) => EntityManager.HasComponent(e, ComponentType.ReadWrite(t));

        Dictionary<string, object> ApplyEntitySet(string prefabName, string compName, Dictionary<string, object> set, List<object> changed)
        {
            var prefab = FindPrefabByName(prefabName) ?? throw new ArgumentException("prefab not found: " + prefabName);
            var t = FindEcsType(compName) ?? throw new ArgumentException("ECS component type not found: " + compName);
            var e = m_Prefabs.GetEntity(prefab);
            if (!HasComp(e, t)) throw new ArgumentException($"{prefabName} has no {t.Name} (components: {string.Join(", ", EntityManager.GetComponentTypes(e).Select(c => c.GetManagedType()?.Name))})");
            object v = GetComp(e, t);
            var fields = t.GetFields(BindingFlags.Public | BindingFlags.Instance);
            if (set != null && set.Count > 0)
            {
                foreach (var kv in set)
                {
                    var fi = fields.FirstOrDefault(f => f.Name == kv.Key) ?? throw new ArgumentException($"{t.Name} has no field {kv.Key} (fields: {string.Join(", ", fields.Select(f => f.Name))})");
                    fi.SetValue(v, ConvertValue(kv.Value, fi.FieldType)); changed.Add(t.Name + "." + kv.Key);
                }
                SetComp(e, t, v);
            }
            return fields.ToDictionary(f => f.Name, f => DescribeValue(f.GetValue(v)));
        }

        static List<object> LoadOverrides()
        {
            try { return File.Exists(OverridesPath) ? (Json.Parse(File.ReadAllText(OverridesPath)) as List<object>) ?? new List<object>() : new List<object>(); }
            catch (Exception e) { Log.Info("prefab_overrides.json unreadable: " + e.Message); return new List<object>(); }
        }

        static void SaveOverrides(List<object> list)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(OverridesPath));
            File.WriteAllText(OverridesPath, Json.Write(list));
        }

        object PrefabEntity(Dictionary<string, object> p)
        {
            string name = Json.Str(p, "name", null) ?? throw new ArgumentException("name required");
            string comp = Json.Str(p, "component", null) ?? throw new ArgumentException("component required (e.g. CarData)");
            var set = p.TryGetValue("set", out var s) ? s as Dictionary<string, object> : null;
            var changed = new List<object>();
            var fields = ApplyEntitySet(name, comp, set, changed);
            bool persist = Json.Bool(p, "persist", set != null && set.Count > 0);
            bool remove = Json.Bool(p, "remove", false);
            if (persist || remove)
            {
                var list = LoadOverrides();
                list.RemoveAll(o => o is Dictionary<string, object> d && Json.Str(d, "name", "") == name && Json.Str(d, "component", "") == comp);
                if (!remove && set != null && set.Count > 0) list.Add(new Dictionary<string, object> { ["name"] = name, ["component"] = comp, ["set"] = set });
                SaveOverrides(list);
            }
            return new Dictionary<string, object> { ["name"] = name, ["component"] = comp, ["fields"] = fields, ["changed"] = changed,
                ["persisted"] = persist && !remove, ["removed"] = remove, ["file"] = OverridesPath };
        }

        object PrefabOverrides() => new Dictionary<string, object> { ["file"] = OverridesPath, ["overrides"] = LoadOverrides() };

        // called from OnUpdate: apply the persisted overrides once the prefabs exist (retries until every one applied)
        [System.Runtime.CompilerServices.MethodImpl(System.Runtime.CompilerServices.MethodImplOptions.NoInlining)]
        void PumpOverrides()
        {
            if (m_OverridesApplied || ++m_OverrideTries % 120 != 0) return;
            var list = LoadOverrides(); int ok = 0;
            foreach (var o in list.OfType<Dictionary<string, object>>())
            {
                try { ApplyEntitySet(Json.Str(o, "name", ""), Json.Str(o, "component", ""), o.TryGetValue("set", out var s) ? s as Dictionary<string, object> : null, new List<object>()); ok++; }
                catch (Exception e) { if (m_OverrideTries > 120 * 60) Log.Info("override failed: " + e.Message); }
            }
            if (ok == list.Count || m_OverrideTries > 120 * 60) { m_OverridesApplied = true; Log.Info($"prefab overrides applied: {ok}/{list.Count}"); }
        }
    }
}
