// prefab_clone (v0.34): duplicate a user (imported) prefab under a new name, saved next to it in the user asset database
// and registered live - the Editor's own path (PrefabBase.Clone + AssetDatabase.user.AddAsset + asset.Save +
// PrefabSystem.AddOrUpdatePrefab, see AssetImportPanel / EditorPrefabUtils). The clone REFERENCES the same meshes
// (RenderPrefabs), so e.g. one imported growable house can become its 1x2..1x6 lot-depth variants without importing the
// mesh + textures five times. Configure the clone afterwards with asset_configure (lot, level, sub-objects, mesh offset).
//   prefab_clone {source, name, replace?}  -> {name, source, created | exists, asset_path}
using System;
using System.Collections.Generic;
using Colossal.IO.AssetDatabase;           // PrefabAssetExtensions.AddAsset(db, path, ScriptableObject)
using Game.Prefabs;

namespace CS2MCP
{
    public partial class McpBridgeSystem
    {
        object PrefabClone(Dictionary<string, object> p)
        {
            string srcName = Json.Str(p, "source", null) ?? throw new ArgumentException("source required");
            string name = Json.Str(p, "name", null) ?? throw new ArgumentException("name required");
            if (name.IndexOfAny(new[] { '/', '\\', ':', '*', '?', '"', '<', '>', '|' }) >= 0) throw new ArgumentException("bad characters in name");
            var src = FindPrefabByName(srcName, preferObject: true) ?? throw new ArgumentException("source prefab not found: " + srcName);
            if (!IsUserAsset(src)) throw new ArgumentException(srcName + " is not a user (imported) asset - only those can be cloned");
            var existing = FindPrefabByName(name, preferObject: true);
            if (existing != null)
            {
                if (!IsUserAsset(existing)) throw new ArgumentException(name + " already exists as a game/pack prefab");
                return new Dictionary<string, object> { ["name"] = name, ["source"] = srcName, ["exists"] = true,
                                                        ["asset_path"] = existing.asset?.path };
            }
            var clone = src.Clone(name);
            var db = Colossal.IO.AssetDatabase.AssetDatabase.user;
            var path = Colossal.IO.AssetDatabase.AssetDataPath.Create(src.asset.subPath, name);
            var asset = db.AddAsset(path, clone);
            asset.Save(force: true);
            m_Prefabs.AddOrUpdatePrefab(clone);
            Log.Info($"prefab_clone: {srcName} -> {name} ({asset.path})");
            return new Dictionary<string, object> { ["name"] = name, ["source"] = srcName, ["created"] = true, ["asset_path"] = asset.path,
                                                    ["summary"] = PrefabSummary(clone) };
        }
    }
}
