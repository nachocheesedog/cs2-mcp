// mesh_materials (v0.18): how a prefab's meshes are shaded - for every render mesh of an object/building prefab, each
// material (SurfaceAsset): template material + shader, float / colour / vector properties and texture slots. Read-only;
// used to copy vanilla looks (e.g. how the Botanical Garden does its glass).
using System;
using System.Collections.Generic;
using System.Linq;
using Game.Prefabs;

namespace CS2MCP
{
    public partial class McpBridgeSystem
    {
        object MeshMaterials(Dictionary<string, object> p)
        {
            string name = Json.Str(p, "name", null) ?? throw new ArgumentException("name required");
            var prefab = FindPrefabByName(name, preferObject: true) ?? throw new ArgumentException("prefab not found: " + name);
            var meshes = new List<object>();
            if (prefab is ObjectGeometryPrefab og && og.m_Meshes != null)
                foreach (var mi in og.m_Meshes)
                {
                    var rp = mi.m_Mesh as RenderPrefab; if (rp == null) continue;
                    var mats = new List<object>();
                    for (int i = 0; i < rp.materialCount; i++)
                    {
                        var sa = rp.GetSurfaceAsset(i); if (sa == null) { mats.Add(null); continue; }
                        var d = new Dictionary<string, object> { ["surface"] = sa.name };
                        try { var t = sa.GetTemplateMaterial(); d["template"] = t != null ? t.name : null; d["shader"] = t?.shader != null ? t.shader.name : null; } catch (Exception e) { d["template_error"] = e.Message; }
                        try { d["floats"] = sa.floats.ToDictionary(k => k.Key, k => (object)(double)k.Value); } catch { }
                        try { d["colors"] = sa.colors.ToDictionary(k => k.Key, k => (object)new List<object> { (double)k.Value.r, (double)k.Value.g, (double)k.Value.b, (double)k.Value.a }); } catch { }
                        try { d["vectors"] = sa.vectors.ToDictionary(k => k.Key, k => (object)new List<object> { (double)k.Value.x, (double)k.Value.y, (double)k.Value.z, (double)k.Value.w }); } catch { }
                        try { d["textures"] = sa.textures.ToDictionary(k => k.Key, k => (object)(k.Value != null ? k.Value.name : null)); } catch { }
                        mats.Add(d);
                    }
                    meshes.Add(new Dictionary<string, object> { ["mesh"] = rp.name, ["materials"] = mats });
                }
            return new Dictionary<string, object> { ["prefab"] = prefab.name, ["meshes"] = meshes };
        }
    }
}
