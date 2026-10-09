// texture_export (v0.32): save a vanilla prefab's textures as PNG files, e.g. to reuse the game's own grass look on a
// custom mesh. Sources: RenderedArea (surfaces / lots: m_BaseColorMap, m_NormalMap, m_MaskMap + the textures of its
// m_Material) and render-mesh surfaces (every texture slot of every material). GPU textures are copied with a Blit, so
// non-readable textures work too. Read-only for the game; writes PNGs only into out_dir.
using System;
using System.Collections.Generic;
using System.IO;
using Colossal.IO.AssetDatabase;
using Game.Prefabs;
using UnityEngine;

namespace CS2MCP
{
    public partial class McpBridgeSystem
    {
        object TextureExport(Dictionary<string, object> p)
        {
            string name = Json.Str(p, "name", null) ?? throw new ArgumentException("name required");
            string outDir = Json.Str(p, "out_dir", null) ?? throw new ArgumentException("out_dir required");
            string only = Json.Str(p, "slot", null);
            Directory.CreateDirectory(outDir);
            var prefab = FindPrefabByName(name, preferObject: false) ?? throw new ArgumentException("prefab not found: " + name);
            var found = new List<(string slot, Texture tex, bool linear)>();
            void add(string slot, Texture t)
            {
                if (t == null) return;
                string s = slot.ToLowerInvariant();
                bool linear = s.Contains("normal") || s.Contains("mask");
                found.Add((slot, t, linear));
            }
            Texture load(TextureAsset ta)
            {
                if (ta == null) return null;
                try { return ta.Load(); } catch { return null; }
            }
            var ra = prefab.GetComponent<RenderedArea>();
            if (ra != null)
            {
                try { add("BaseColorMap", load(ra.m_BaseColorMap)); } catch { }
                try { add("NormalMap", load(ra.m_NormalMap)); } catch { }
                try { add("MaskMap", load(ra.m_MaskMap)); } catch { }
                var m = ra.m_Material;
                if (m != null)
                    foreach (var prop in m.GetTexturePropertyNames())
                    {
                        var t = m.GetTexture(prop);
                        if (t != null) add("material" + prop, t);
                    }
            }
            if (prefab is ObjectGeometryPrefab og && og.m_Meshes != null)
                foreach (var mi in og.m_Meshes)
                {
                    var rp = mi.m_Mesh as RenderPrefab; if (rp == null) continue;
                    for (int i = 0; i < rp.materialCount; i++)
                    {
                        var sa = rp.GetSurfaceAsset(i); if (sa == null) continue;
                        foreach (var kv in sa.textures)
                            add(rp.name + "_" + i + kv.Key, load(kv.Value as TextureAsset));
                    }
                }
            var files = new List<object>();
            foreach (var (slot, tex, linear) in found)
            {
                if (only != null && slot.IndexOf(only, StringComparison.OrdinalIgnoreCase) < 0) continue;
                var info = new Dictionary<string, object> { ["slot"] = slot, ["texture"] = tex.name, ["width"] = tex.width,
                                                            ["height"] = tex.height, ["dimension"] = tex.dimension.ToString() };
                if (tex.dimension != UnityEngine.Rendering.TextureDimension.Tex2D) { info["error"] = "not a 2D texture"; files.Add(info); continue; }
                try
                {
                    int w = tex.width, h = tex.height;
                    var rt = RenderTexture.GetTemporary(w, h, 0, RenderTextureFormat.ARGB32,
                                                        linear ? RenderTextureReadWrite.Linear : RenderTextureReadWrite.sRGB);
                    var prev = RenderTexture.active;
                    Graphics.Blit(tex, rt);
                    RenderTexture.active = rt;
                    var dst = new Texture2D(w, h, TextureFormat.RGBA32, false, linear);
                    dst.ReadPixels(new Rect(0, 0, w, h), 0, 0); dst.Apply();
                    RenderTexture.active = prev;
                    RenderTexture.ReleaseTemporary(rt);
                    string safe = string.Join("_", (prefab.name + "_" + slot).Split(Path.GetInvalidFileNameChars()));
                    string path = Path.Combine(outDir, safe + ".png");
                    File.WriteAllBytes(path, dst.EncodeToPNG());
                    UnityEngine.Object.Destroy(dst);
                    info["file"] = path;
                }
                catch (Exception e) { info["error"] = e.Message; }
                files.Add(info);
            }
            return new Dictionary<string, object> { ["prefab"] = prefab.name, ["count"] = files.Count, ["files"] = files };
        }
    }
}
