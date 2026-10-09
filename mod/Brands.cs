// Custom company brands (v0.38).
// How the game does brands (Game.dll 1.6.2f1, decompiled): a BrandPrefab has m_Companies (CompanyPrefab[]) and
// m_BrandColors; CompanyInitializeSystem adds each brand to its companies' CompanyBrandElement buffers, and a new company
// entity draws its CompanyData.m_Brand at random from its company prefab's brands. The UI names a company after its brand
// (NameSystem: the brand prefab's localized title, Assets.NAME[<brand>]) and shows the brand prefab's UIObject.m_Icon.
// Sign props carry BrandObject(m_Brand) and only show for a renter of that brand.
//   prefab_create  {type, name, folder?}        new EMPTY user prefab of a Game.Prefabs type (e.g. BrandPrefab), saved to the
//                                               user asset database like prefab_clone (so it loads at every game start);
//                                               fill it with asset_configure (set m_Companies / m_BrandColors, icon, title)
//   brand_info     {filter?}                    BrandPrefabs (+ their companies) and CompanyPrefabs matching the filter
//   brand_bind     {building, brand, remove?}   persist "renters of this building prefab always carry this brand"
//                                               (ModsData\CS2MCP\brand_bindings.json); a pump stamps CompanyData.m_Brand
//   brand_bindings {}                           the persisted list
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Colossal.IO.AssetDatabase;
using Game.Buildings;
using Game.Companies;
using Game.Prefabs;
using Unity.Collections;
using Unity.Entities;

namespace CS2MCP
{
    public partial class McpBridgeSystem
    {
        static string BrandBindPath => Path.Combine(UnityEngine.Application.persistentDataPath, "ModsData", "CS2MCP", "brand_bindings.json");
        List<(string building, string brand)> m_BrandBinds;
        int m_BrandTick;
        EntityQuery m_RentedBuildings;

        object PrefabCreate(Dictionary<string, object> p)
        {
            string typeName = Json.Str(p, "type", null) ?? throw new ArgumentException("type required (e.g. BrandPrefab)");
            string name = Json.Str(p, "name", null) ?? throw new ArgumentException("name required");
            if (name.IndexOfAny(new[] { '/', '\\', ':', '*', '?', '"', '<', '>', '|' }) >= 0) throw new ArgumentException("bad characters in name");
            var t = typeof(PrefabBase).Assembly.GetTypes().FirstOrDefault(x => (x.Name == typeName || x.FullName == typeName)
                        && typeof(PrefabBase).IsAssignableFrom(x) && !x.IsAbstract)
                    ?? throw new ArgumentException("unknown prefab type: " + typeName);
            var existing = AllPrefabs().FirstOrDefault(x => x != null && x.name == name && t.IsInstanceOfType(x));
            if (existing != null)
            {
                if (!IsUserAsset(existing)) throw new ArgumentException(name + " already exists as a game/pack prefab");
                return new Dictionary<string, object> { ["name"] = name, ["type"] = t.Name, ["exists"] = true, ["asset_path"] = existing.asset?.path };
            }
            var prefab = (PrefabBase)UnityEngine.ScriptableObject.CreateInstance(t);
            prefab.name = name;
            var db = AssetDatabase.user;
            var path = AssetDataPath.Create(Json.Str(p, "folder", "ImportedData/" + name), name);
            var asset = db.AddAsset(path, prefab);
            asset.Save(force: true);
            m_Prefabs.AddOrUpdatePrefab(prefab);
            Log.Info($"prefab_create: {t.Name} {name} ({asset.path})");
            return new Dictionary<string, object> { ["name"] = name, ["type"] = t.Name, ["created"] = true, ["asset_path"] = asset.path,
                                                    ["summary"] = PrefabSummary(prefab) };
        }

        object BrandInfo(Dictionary<string, object> p)
        {
            string f = Json.Str(p, "filter", "") ?? "";
            bool Match(string n) => f.Length == 0 || n.IndexOf(f, StringComparison.OrdinalIgnoreCase) >= 0;
            var all = AllPrefabs().Where(x => x != null).ToList();
            var brands = all.OfType<BrandPrefab>()
                .Where(b => Match(b.name) || b.m_Companies.Any(c => c != null && Match(c.name)))
                .Select(b => (object)new Dictionary<string, object> { ["name"] = b.name, ["user"] = IsUserAsset(b),
                    ["companies"] = b.m_Companies.Where(c => c != null).Select(c => (object)c.name).ToList() }).ToList();
            var companies = all.OfType<CompanyPrefab>().Where(c => Match(c.name)).Select(c =>
            {
                var d = new Dictionary<string, object> { ["name"] = c.name, ["zone"] = c.zone.ToString(), ["profitability"] = c.profitability };
                var e = m_Prefabs.GetEntity(c);
                if (EntityManager.HasComponent<IndustrialProcessData>(e))
                {
                    var proc = EntityManager.GetComponentData<IndustrialProcessData>(e);
                    d["output"] = proc.m_Output.m_Resource.ToString();
                }
                if (EntityManager.HasBuffer<CompanyBrandElement>(e)) d["brands"] = EntityManager.GetBuffer<CompanyBrandElement>(e, true).Length;
                return (object)d;
            }).ToList();
            return new Dictionary<string, object> { ["brands"] = brands, ["brand_count"] = brands.Count, ["companies"] = companies };
        }

        static List<object> LoadBrandBinds()
        {
            try { return File.Exists(BrandBindPath) ? (Json.Parse(File.ReadAllText(BrandBindPath)) as List<object>) ?? new List<object>() : new List<object>(); }
            catch (Exception e) { Log.Info("brand_bindings.json unreadable: " + e.Message); return new List<object>(); }
        }

        object BrandBind(Dictionary<string, object> p)
        {
            string building = Json.Str(p, "building", null) ?? throw new ArgumentException("building (prefab name) required");
            string brand = Json.Str(p, "brand", null) ?? throw new ArgumentException("brand (BrandPrefab name) required");
            bool remove = Json.Bool(p, "remove", false);
            if (!remove)
            {
                if (!(FindPrefabByName(building, preferObject: true) is BuildingPrefab)) throw new ArgumentException("building prefab not found: " + building);
                if (!AllPrefabs().Any(x => x is BrandPrefab && x.name == brand)) throw new ArgumentException("brand prefab not found: " + brand);
            }
            var list = LoadBrandBinds();
            list.RemoveAll(o => o is Dictionary<string, object> d && Json.Str(d, "building", "") == building);
            if (!remove) list.Add(new Dictionary<string, object> { ["building"] = building, ["brand"] = brand });
            Directory.CreateDirectory(Path.GetDirectoryName(BrandBindPath));
            File.WriteAllText(BrandBindPath, Json.Write(list));
            m_BrandBinds = null;                                                  // re-read by the pump
            int stamped = CityLoaded ? StampBrands() : 0;
            return new Dictionary<string, object> { ["file"] = BrandBindPath, ["bindings"] = list, ["stamped_now"] = stamped };
        }

        object BrandBindings() => new Dictionary<string, object> { ["file"] = BrandBindPath, ["bindings"] = LoadBrandBinds() };

        // called from OnUpdate: every ~256 frames in a loaded city, give the renters of bound buildings their brand
        void PumpBrands()
        {
            if (!CityLoaded || GameLoading || (++m_BrandTick & 255) != 0) return;
            StampBrands();
        }

        int StampBrands()
        {
            if (m_BrandBinds == null)
                m_BrandBinds = LoadBrandBinds().OfType<Dictionary<string, object>>()
                    .Select(d => (Json.Str(d, "building", ""), Json.Str(d, "brand", ""))).ToList();
            if (m_BrandBinds.Count == 0) return 0;
            var map = new Dictionary<Entity, Entity>();                           // building prefab entity -> brand prefab entity
            foreach (var (b, br) in m_BrandBinds)
            {
                var bp = FindPrefabByName(b, preferObject: true);
                var brp = AllPrefabs().FirstOrDefault(x => x is BrandPrefab && x.name == br);
                if (bp == null || brp == null) continue;
                map[m_Prefabs.GetEntity(bp)] = m_Prefabs.GetEntity(brp);
            }
            if (map.Count == 0) return 0;
            if (m_RentedBuildings == default)
                m_RentedBuildings = GetEntityQuery(new EntityQueryDesc
                {
                    All = new[] { ComponentType.ReadOnly<Building>(), ComponentType.ReadOnly<PrefabRef>(), ComponentType.ReadOnly<Renter>() },
                    None = new[] { ComponentType.ReadOnly<Game.Common.Deleted>(), ComponentType.ReadOnly<Game.Tools.Temp>() }
                });
            int n = 0;
            using (var ents = m_RentedBuildings.ToEntityArray(Allocator.Temp))
            {
                foreach (var e in ents)
                {
                    if (!map.TryGetValue(EntityManager.GetComponentData<PrefabRef>(e).m_Prefab, out var brand)) continue;
                    var renters = EntityManager.GetBuffer<Renter>(e, true);
                    for (int i = 0; i < renters.Length; i++)
                    {
                        var r = renters[i].m_Renter;
                        if (!EntityManager.HasComponent<CompanyData>(r)) continue;
                        var cd = EntityManager.GetComponentData<CompanyData>(r);
                        if (cd.m_Brand == brand) continue;
                        cd.m_Brand = brand; EntityManager.SetComponentData(r, cd); n++;
                    }
                }
            }
            if (n > 0) Log.Info($"brand pump: {n} renter(s) re-branded");
            return n;
        }
    }
}
