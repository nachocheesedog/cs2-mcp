// mesh_export (v0.37): export a prefab's render meshes (every LOD) as Wavefront OBJ + a JSON sidecar, so stock game
// geometry (vehicles, trams, props, buildings) can be loaded into Blender next to our own models and compared
// numerically. Read-only for the game; writes files only into out_dir.
//   mesh_export {name, out_dir, lods?: [0,1,..]}   name = an object prefab (vehicle, building, prop: its
//               ObjectGeometryPrefab.m_Meshes) or a RenderPrefab ("... Mesh") directly.
//   -> per render mesh and LOD: <out_dir>\<mesh>_LOD<n>.obj / .mtl / .json, plus <out_dir>\<prefab>_mesh_export.json
//      (manifest: which files belong to the prefab + each mesh's placement in the object).
// Vertex data source: the mesh's GeometryAsset FILE (the .Geometry the game streams from). The mod reads it into its
// OWN GeometryAsset.Data / Loading structs with the game's public static GeometryAsset.LoadSync (sync read + the
// game's own meshopt/zstd decompression) and the static GetAttributeData / GetIndices accessors, then disposes them.
// The GeometryAsset instance, its refcounted Unity meshes and the renderer's streaming state are never touched, so
// this works whether or not the mesh is loaded / visible. Only if that file read fails AND the asset already holds
// CPU-side Unity meshes (LoadState.Object - they are created readable by ApplyAndDisposeWritableMeshData) does it fall
// back to those (positions + UV0 + triangles only; source = "unity_mesh_fallback"). No GPU readback is used.
// Packed attributes are decoded: positions Float32, normals SNorm16x2 octahedral (Colossal MathUtils.EncodeOctahedral,
// asset import AttributePackingPostProcessor) or plain xyz, UVs Float16, BlendIndices UInt8x4 / UInt32, BlendWeight any.
// The sidecar reports normals_agree_with_winding (fraction of triangle corners whose decoded normal faces the same way
// as the triangle) as a self-check of the decode.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using Colossal.IO.AssetDatabase;
using Game.Prefabs;
using Unity.Mathematics;
using UnityEngine;
using UnityEngine.Rendering;

namespace CS2MCP
{
    public partial class McpBridgeSystem
    {
        const string kMeshAxisNote = "metres; the game's (Unity) local mesh frame, LEFT-handed: +x = object right, +y = up, " +
                                     "+z = forward (vehicle front). Raw, not converted. To Blender (Z up, facing -Y): " +
                                     "B = (-x, -z, +y) and reverse face winding (the mapping mirrors).";

        sealed class MeshDump
        {
            public string Source;
            public int GeometryMeshes;
            public readonly List<Vector3> Pos = new List<Vector3>();
            public List<Vector3> Nrm;                      // null = none
            public List<Vector2> Uv;                       // null = none
            public int Influences;                         // 0 = not skinned
            public List<int> BoneIdx;                      // flat, Influences per vertex
            public List<float> BoneW;                      // flat, Influences per vertex
            public readonly List<(int material, List<int> tris)> Subs = new List<(int, List<int>)>();
            public readonly Dictionary<string, object> Formats = new Dictionary<string, object>();
            public string NormalEncoding;
        }

        object MeshExport(Dictionary<string, object> p)
        {
            string name = Json.Str(p, "name", null) ?? throw new ArgumentException("name required");
            string outDir = Json.Str(p, "out_dir", null) ?? throw new ArgumentException("out_dir required");
            HashSet<int> lodFilter = null;
            var lodList = Json.List(p, "lods");
            if (lodList != null) lodFilter = new HashSet<int>(lodList.OfType<double>().Select(d => (int)d));
            var prefab = FindPrefabByName(name, preferObject: false) ?? throw new ArgumentException("prefab not found: " + name);
            Directory.CreateDirectory(outDir);

            // render meshes of the prefab (deduplicated) with every placement that uses them
            var order = new List<RenderPrefab>();
            var placements = new Dictionary<RenderPrefab, List<object>>();
            var skipped = new List<object>();
            if (prefab is ObjectGeometryPrefab og)
            {
                if (og.m_Meshes != null)
                    for (int i = 0; i < og.m_Meshes.Length; i++)
                    {
                        var mi = og.m_Meshes[i];
                        if (!(mi?.m_Mesh is RenderPrefab rp))
                        {
                            skipped.Add(new Dictionary<string, object> { ["index"] = i, ["mesh"] = mi?.m_Mesh?.name,
                                ["type"] = mi?.m_Mesh?.GetType().Name, ["reason"] = "not a RenderPrefab" });
                            continue;
                        }
                        if (!placements.ContainsKey(rp)) { placements[rp] = new List<object>(); order.Add(rp); }
                        placements[rp].Add(new Dictionary<string, object>
                        {
                            ["index"] = i, ["position"] = V3(mi.m_Position),
                            ["rotation_xyzw"] = new List<object> { (double)mi.m_Rotation.value.x, (double)mi.m_Rotation.value.y,
                                                                   (double)mi.m_Rotation.value.z, (double)mi.m_Rotation.value.w },
                            ["require_state"] = mi.m_RequireState.ToString()
                        });
                    }
            }
            else if (prefab is RenderPrefab rpDirect) { order.Add(rpDirect); placements[rpDirect] = null; }
            else throw new ArgumentException($"{prefab.name} is a {prefab.GetType().Name}, not an object prefab or a render mesh prefab");

            string safePrefab = SafeFileName(prefab.name);
            var meshes = new List<object>();
            var summary = new List<object>();
            var usedBase = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var rp in order)
            {
                string baseName = SafeFileName(rp.name);
                for (int k = 2; !usedBase.Add(baseName); k++) baseName = SafeFileName(rp.name) + "_" + k;
                var chain = LodChain(rp, out float lodBias);
                var lodInfos = new List<Dictionary<string, object>>();
                var lodSummary = new List<object>();
                for (int n = 0; n < chain.Count; n++)
                {
                    if (lodFilter != null && !lodFilter.Contains(n)) continue;
                    var info = ExportLod(prefab, rp, chain[n], n, baseName, outDir, placements[rp]);
                    lodInfos.Add(info);
                    lodSummary.Add(new Dictionary<string, object>
                    {
                        ["lod"] = n, ["render_prefab"] = chain[n].name,
                        ["obj"] = info.TryGetValue("obj", out var o) ? o : null,
                        ["vertices"] = info.TryGetValue("vertex_count", out var vc) ? vc : null,
                        ["triangles"] = info.TryGetValue("triangle_count", out var tc) ? tc : null,
                        ["submeshes"] = info.TryGetValue("submeshes", out var sm) && sm is List<object> sl ? (object)sl.Count : null,
                        ["bones"] = info.TryGetValue("bones", out var b) && b is List<object> bl ? (object)bl.Count : 0,
                        ["skinned"] = info.ContainsKey("skin"),
                        ["source"] = info.TryGetValue("source", out var s) ? s : null,
                        ["error"] = info.TryGetValue("error", out var e) ? e : null
                    });
                }
                meshes.Add(new Dictionary<string, object>
                {
                    ["mesh"] = rp.name, ["file_base"] = baseName, ["placements"] = placements[rp], ["lod_count"] = chain.Count,
                    ["lod_bias"] = (double)lodBias,
                    ["lods"] = lodInfos.Select(x => (object)new Dictionary<string, object>
                    {
                        ["lod"] = x["lod"], ["render_prefab"] = x["render_prefab"],
                        ["obj"] = x.TryGetValue("obj", out var o) ? o : null, ["json"] = x.TryGetValue("json", out var j) ? j : null,
                        ["error"] = x.TryGetValue("error", out var e) ? e : null
                    }).ToList()
                });
                summary.Add(new Dictionary<string, object>
                {
                    ["mesh"] = rp.name, ["placements"] = placements[rp]?.Count ?? 0, ["lod_count"] = chain.Count, ["lods"] = lodSummary
                });
            }
            string manifest = Path.Combine(outDir, safePrefab + "_mesh_export.json");
            File.WriteAllText(manifest, Json.Write(new Dictionary<string, object>
            {
                ["prefab"] = prefab.name, ["prefab_type"] = prefab.GetType().Name, ["mod_version"] = Bridge.Version,
                ["axis"] = kMeshAxisNote, ["meshes"] = meshes, ["skipped"] = skipped
            }));
            return new Dictionary<string, object>
            {
                ["prefab"] = prefab.name, ["prefab_type"] = prefab.GetType().Name, ["out_dir"] = outDir, ["manifest"] = manifest,
                ["axis"] = kMeshAxisNote, ["meshes"] = summary, ["skipped"] = skipped
            };
        }

        // LOD 0 = the mesh itself, then LodProperties.m_LodMeshes in the game's order (MeshSystem sorts them by index count)
        static List<RenderPrefab> LodChain(RenderPrefab rp, out float bias)
        {
            var chain = new List<RenderPrefab> { rp };
            bias = 0f;
            var lp = rp.GetComponent<LodProperties>();
            if (lp != null)
            {
                bias = lp.m_Bias;
                if (lp.m_LodMeshes != null)
                    chain.AddRange(lp.m_LodMeshes.Where(x => x != null && x != rp).Distinct().OrderByDescending(x => x.indexCount));
            }
            return chain;
        }

        Dictionary<string, object> ExportLod(PrefabBase prefab, RenderPrefab baseRp, RenderPrefab rp, int lod, string baseName,
                                             string outDir, List<object> placements)
        {
            var info = new Dictionary<string, object>
            {
                ["prefab"] = prefab.name, ["mesh"] = baseRp.name, ["render_prefab"] = rp.name, ["lod"] = lod,
                ["axis"] = kMeshAxisNote, ["placements"] = placements
            };
            MeshDump dump;
            try { dump = ReadRenderMesh(rp); }
            catch (Exception e) { info["error"] = e.GetType().Name + ": " + e.Message; return info; }

            string stem = baseName + "_LOD" + lod;
            string objPath = Path.Combine(outDir, stem + ".obj"), mtlPath = Path.Combine(outDir, stem + ".mtl"),
                   jsonPath = Path.Combine(outDir, stem + ".json");
            int vcount = dump.Pos.Count;

            // materials (submesh index == material index across the geometry's meshes, like RenderPrefab.ObtainMesh)
            var matNames = new List<string>();
            var matInfo = new List<object>();
            for (int i = 0; i < rp.materialCount; i++)
            {
                string surf = null, template = null;
                try { var sa = rp.GetSurfaceAsset(i); surf = sa?.name; try { template = sa?.GetTemplateMaterial()?.name; } catch { } } catch { }
                matNames.Add(surf ?? ("material" + i));
                matInfo.Add(new Dictionary<string, object> { ["index"] = i, ["surface"] = surf, ["template"] = template });
            }
            string MatId(int m) => SafeObjName((m < matNames.Count ? matNames[m] : "material") + "_" + m);

            // self-check of the normal decode: decoded vertex normals vs. triangle winding (game: numeric cross = front)
            double agree = -1;
            if (dump.Nrm != null)
            {
                long good = 0, total = 0;
                foreach (var (_, tris) in dump.Subs)
                    for (int t = 0; t + 2 < tris.Count; t += 3)
                    {
                        Vector3 a = dump.Pos[tris[t]], b = dump.Pos[tris[t + 1]], c = dump.Pos[tris[t + 2]];
                        var fn = Vector3.Cross(b - a, c - a);
                        if (fn.sqrMagnitude < 1e-12f) continue;
                        for (int k = 0; k < 3; k++) { total++; if (Vector3.Dot(fn, dump.Nrm[tris[t + k]]) > 0) good++; }
                    }
                agree = total > 0 ? (double)good / total : -1;
            }
            bool writeNormals = dump.Nrm != null && agree >= 0.5;

            // ---- OBJ + MTL
            var ci = CultureInfo.InvariantCulture;
            var sb = new StringBuilder(vcount * 80);
            sb.Append("# CS2MCP ").Append(Bridge.Version).Append(" mesh_export: ").Append(rp.name).Append(" LOD").Append(lod)
              .Append(" (mesh ").Append(baseRp.name).Append(", prefab ").Append(prefab.name).Append(")\n");
            sb.Append("# Units: metres. Frame: the game's LOCAL mesh frame (Unity, LEFT-handed): +X = object right, +Y = up, +Z = forward.\n");
            sb.Append("# Raw game coordinates, NOT converted. Game -> Blender (Z up, vehicle facing -Y): Bx = -x, By = -z, Bz = +y,\n");
            sb.Append("#   then reverse every face's winding (the mapping is a mirror). tools\\import_mesh_export.py does both.\n");
            sb.Append("# Faces keep the game's winding: loaded raw into a right-handed viewer they face outward but the model is mirrored in X.\n");
            sb.Append("# Vertex order = game vertex order, 1:1 with the per-vertex arrays in the sidecar .json (seams are split, not welded).\n");
            sb.Append("# Groups: one g/usemtl per submesh = material. UV0 as stored (Unity convention, v up = same as Blender).\n");
            if (dump.Nrm != null && !writeNormals) sb.Append("# Normals omitted: decoded normals disagree with the winding (see normals_agree_with_winding).\n");
            sb.Append("mtllib ").Append(Path.GetFileName(mtlPath)).Append('\n');
            foreach (var v in dump.Pos) sb.Append("v ").Append(F(v.x, ci)).Append(' ').Append(F(v.y, ci)).Append(' ').Append(F(v.z, ci)).Append('\n');
            if (dump.Uv != null) foreach (var t in dump.Uv) sb.Append("vt ").Append(F(t.x, ci)).Append(' ').Append(F(t.y, ci)).Append('\n');
            if (writeNormals) foreach (var n in dump.Nrm) sb.Append("vn ").Append(F(n.x, ci)).Append(' ').Append(F(n.y, ci)).Append(' ').Append(F(n.z, ci)).Append('\n');
            int triTotal = 0;
            var subInfo = new List<object>();
            for (int s = 0; s < dump.Subs.Count; s++)
            {
                var (mat, tris) = dump.Subs[s];
                string mid = MatId(mat);
                sb.Append("g ").Append(SafeObjName(baseName + "_LOD" + lod + "_sm" + s)).Append('\n').Append("usemtl ").Append(mid).Append('\n');
                var used = new HashSet<int>();
                for (int t = 0; t + 2 < tris.Count; t += 3)
                {
                    sb.Append('f');
                    for (int k = 0; k < 3; k++)
                    {
                        int ix = tris[t + k] + 1; used.Add(ix - 1);
                        sb.Append(' ').Append(ix);
                        if (dump.Uv != null && writeNormals) sb.Append('/').Append(ix).Append('/').Append(ix);
                        else if (dump.Uv != null) sb.Append('/').Append(ix);
                        else if (writeNormals) sb.Append("//").Append(ix);
                    }
                    sb.Append('\n');
                }
                triTotal += tris.Count / 3;
                subInfo.Add(new Dictionary<string, object>
                {
                    ["submesh"] = s, ["group"] = SafeObjName(baseName + "_LOD" + lod + "_sm" + s), ["usemtl"] = mid,
                    ["material_index"] = mat, ["material"] = mat < matNames.Count ? matNames[mat] : null,
                    ["triangle_count"] = tris.Count / 3, ["vertex_count"] = used.Count,
                    ["bounds"] = BoundsOf(used.Select(i => dump.Pos[i]))
                });
            }
            File.WriteAllText(objPath, sb.ToString());
            var mtl = new StringBuilder("# CS2MCP mesh_export materials (names only; textures: cs2_texture_export)\n");
            foreach (var mid in dump.Subs.Select(x => MatId(x.material)).Distinct())
                mtl.Append("newmtl ").Append(mid).Append("\nKd 0.8 0.8 0.8\n");
            File.WriteAllText(mtlPath, mtl.ToString());

            // ---- sidecar JSON
            info["obj"] = objPath; info["json"] = jsonPath; info["source"] = dump.Source;
            info["vertex_count"] = vcount; info["triangle_count"] = triTotal; info["geometry_meshes"] = dump.GeometryMeshes;
            info["prefab_counts"] = new Dictionary<string, object> { ["vertices"] = rp.vertexCount, ["indices"] = rp.indexCount, ["meshes"] = rp.meshCount };
            info["attributes"] = dump.Formats;
            info["normal_encoding"] = dump.NormalEncoding;
            info["normals_agree_with_winding"] = agree < 0 ? null : (object)Math.Round(agree, 4);
            info["normals_written"] = writeNormals;
            info["has_uv0"] = dump.Uv != null;
            info["materials"] = matInfo;
            info["submeshes"] = subInfo;
            info["bounds"] = BoundsOf(dump.Pos);
            try { var b = rp.bounds; info["prefab_bounds"] = new Dictionary<string, object> { ["min"] = V3(b.min), ["max"] = V3(b.max) }; } catch { }
            try { info["surface_area"] = (double)rp.surfaceArea; } catch { }

            // bones: the LOD's own ProceduralAnimationProperties, else LOD0's (skin indices refer to this list)
            var pap = rp.GetComponent<ProceduralAnimationProperties>();
            string bonesFrom = rp.name;
            if ((pap == null || pap.m_Bones == null) && rp != baseRp) { pap = baseRp.GetComponent<ProceduralAnimationProperties>(); bonesFrom = baseRp.name; }
            var bones = new List<object>();
            ProceduralAnimationProperties.BoneInfo[] boneArr = pap?.m_Bones;
            if (boneArr != null)
            {
                info["bones_from"] = bonesFrom;
                for (int i = 0; i < boneArr.Length; i++)
                {
                    var bi = boneArr[i];
                    var d = new Dictionary<string, object>
                    {
                        ["index"] = i, ["name"] = bi.name, ["parent"] = bi.parentId,
                        ["parent_name"] = bi.parentId >= 0 && bi.parentId < boneArr.Length ? boneArr[bi.parentId].name : null,
                        ["type"] = bi.m_Type.ToString(),
                        ["local_position"] = V3(bi.position),
                        ["local_rotation_xyzw"] = new List<object> { (double)bi.rotation.x, (double)bi.rotation.y, (double)bi.rotation.z, (double)bi.rotation.w },
                        ["local_scale"] = V3(bi.scale),
                        ["bind_pose_rows"] = M4Rows(bi.bindPose),
                        ["speed"] = (double)bi.m_Speed, ["acceleration"] = (double)bi.m_Acceleration,
                        ["connection_id"] = bi.m_ConnectionID, ["source_id"] = bi.m_SourceID
                    };
                    try { d["mesh_space_position"] = V3(bi.bindPose.inverse.MultiplyPoint3x4(Vector3.zero)); } catch { }
                    bones.Add(d);
                }
            }
            info["bones"] = bones;
            if (dump.Influences > 0)
            {
                // per bone: vertices it dominates + their bounds (wheels / bogies show up as small boxes at the axles)
                var dom = new Dictionary<int, List<Vector3>>();
                for (int v = 0; v < vcount; v++)
                {
                    int best = -1; float bw = -1;
                    for (int k = 0; k < dump.Influences; k++)
                    {
                        float w = dump.BoneW[v * dump.Influences + k];
                        if (w > bw) { bw = w; best = dump.BoneIdx[v * dump.Influences + k]; }
                    }
                    if (!dom.TryGetValue(best, out var l)) dom[best] = l = new List<Vector3>();
                    l.Add(dump.Pos[v]);
                }
                info["skin"] = new Dictionary<string, object>
                {
                    ["influences"] = dump.Influences,
                    ["note"] = "indices/weights are flat arrays, `influences` entries per vertex in OBJ vertex order; index = position in `bones`",
                    ["per_bone"] = dom.OrderBy(kv => kv.Key).Select(kv => (object)new Dictionary<string, object>
                    {
                        ["bone"] = kv.Key,
                        ["name"] = boneArr != null && kv.Key >= 0 && kv.Key < boneArr.Length ? boneArr[kv.Key].name : null,
                        ["type"] = boneArr != null && kv.Key >= 0 && kv.Key < boneArr.Length ? boneArr[kv.Key].m_Type.ToString() : null,
                        ["dominant_vertices"] = kv.Value.Count, ["bounds"] = BoundsOf(kv.Value)
                    }).ToList(),
                    ["indices"] = dump.BoneIdx,
                    ["weights"] = dump.BoneW.Select(w => (object)Math.Round(w, 5)).ToList()
                };
            }
            File.WriteAllText(jsonPath, Json.Write(info));
            return info;
        }

        // ------------------------------------------------------------------ reading vertex data
        static MeshDump ReadRenderMesh(RenderPrefab rp)
        {
            if (!rp.hasGeometryAsset) throw new InvalidOperationException(rp.name + " has no geometry asset");
            var ga = rp.geometryAsset ?? throw new InvalidOperationException(rp.name + ": geometry asset missing");
            Exception fileErr;
            try { return ReadGeometryFile(ga); }
            catch (Exception e) { fileErr = e; }
            if (ga.state == LoadState.Object)
            {
                try { return ReadLoadedMeshes(ga); }
                catch (Exception e2) { throw new InvalidOperationException($"geometry file read failed ({fileErr.Message}); loaded-mesh fallback failed ({e2.Message})"); }
            }
            throw new InvalidOperationException("geometry file read failed: " + fileErr.Message);
        }

        // Private copy of the geometry data: the asset's own m_Data / m_Loading (shared with the renderer's streaming) stay untouched.
        static unsafe MeshDump ReadGeometryFile(GeometryAsset ga)
        {
            var desc = ga.GetAsyncReadDescriptor();
            if (string.IsNullOrEmpty(desc.path)) throw new InvalidOperationException("geometry asset has no file on disk");
            var data = default(GeometryAsset.Data);
            var loading = default(GeometryAsset.Loading);
            try
            {
                GeometryAsset.LoadSync(desc, uint.MaxValue, ref data, ref loading);
                var dump = new MeshDump { Source = "geometry_asset_file", GeometryMeshes = data.meshCount };
                int vbase = 0, material = 0;
                bool anyNrm = true, anyUv = true;
                for (int m = 0; m < data.meshCount; m++)
                {
                    int vc = GeometryAsset.GetVertexCount(ref data, m);
                    // positions
                    var pos = Attr(ref data, m, VertexAttribute.Position, vc, dump, out int pd);
                    if (pos == null || pd < 3) throw new InvalidOperationException("mesh " + m + " has no 3D positions");
                    for (int v = 0; v < vc; v++) dump.Pos.Add(new Vector3(pos[v * pd], pos[v * pd + 1], pos[v * pd + 2]));
                    // normals
                    var nrm = Attr(ref data, m, VertexAttribute.Normal, vc, dump, out int nd);
                    if (nrm == null || nd < 2) anyNrm = false;
                    else
                    {
                        if (dump.Nrm == null) dump.Nrm = new List<Vector3>(new Vector3[vbase]);
                        dump.NormalEncoding = nd == 2 ? "octahedral (snorm xy -> unit xyz)" : "xyz";
                        for (int v = 0; v < vc; v++)
                            dump.Nrm.Add(nd == 2 ? OctDecode(nrm[v * 2], nrm[v * 2 + 1])
                                                 : new Vector3(nrm[v * nd], nrm[v * nd + 1], nrm[v * nd + 2]).normalized);
                    }
                    // UV0
                    var uv = Attr(ref data, m, VertexAttribute.TexCoord0, vc, dump, out int ud);
                    if (uv == null || ud < 2) anyUv = false;
                    else
                    {
                        if (dump.Uv == null) dump.Uv = new List<Vector2>(new Vector2[vbase]);
                        for (int v = 0; v < vc; v++) dump.Uv.Add(new Vector2(uv[v * ud], uv[v * ud + 1]));
                    }
                    // skinning
                    var bix = Attr(ref data, m, VertexAttribute.BlendIndices, vc, dump, out int bd);
                    if (bix != null && bd > 0)
                    {
                        var bw = Attr(ref data, m, VertexAttribute.BlendWeight, vc, dump, out int wd);
                        if (dump.Influences == 0)
                        {
                            dump.Influences = bd; dump.BoneIdx = new List<int>(); dump.BoneW = new List<float>();
                            for (int v = 0; v < vbase * bd; v++) { dump.BoneIdx.Add(0); dump.BoneW.Add(v % bd == 0 ? 1f : 0f); }
                        }
                        int inf = dump.Influences;
                        for (int v = 0; v < vc; v++)
                        {
                            float sum = 0;
                            for (int k = 0; k < inf; k++)
                            {
                                int idx = k < bd ? (int)bix[v * bd + k] : 0;
                                float w;
                                if (bw == null || wd == 0) w = k == 0 ? 1f : 0f;                          // rigid skinning
                                else if (k < wd) w = bw[v * wd + k];
                                else if (k == wd) w = Mathf.Max(0f, 1f - sum);                             // implicit last weight
                                else w = 0f;
                                if (k >= bd) w = 0f;
                                sum += w;
                                dump.BoneIdx.Add(idx); dump.BoneW.Add(w);
                            }
                        }
                    }
                    else if (dump.Influences > 0)
                        for (int v = 0; v < vc; v++) for (int k = 0; k < dump.Influences; k++) { dump.BoneIdx.Add(0); dump.BoneW.Add(k == 0 ? 1f : 0f); }
                    // indices per submesh (submesh order across meshes == material order)
                    var fmt = GeometryAsset.GetIndexFormat(ref data, m);
                    var ib = GeometryAsset.GetIndices(ref data, m).ToArray();
                    int subCount = data.meshInfos[m].subMeshCount, subOff = data.meshOffsets[m].subMeshesOffset;
                    for (int s = 0; s < subCount; s++)
                    {
                        var sd = data.subMeshInfos[subOff + s].ToSubMeshDescriptor();
                        var tris = new List<int>(sd.indexCount);
                        for (int i = 0; i < sd.indexCount; i++)
                        {
                            int at = sd.indexStart + i;
                            int ix = fmt == IndexFormat.UInt32 ? (int)BitConverter.ToUInt32(ib, at * 4) : BitConverter.ToUInt16(ib, at * 2);
                            ix += sd.baseVertex;
                            if (ix < 0 || ix >= vc) throw new InvalidOperationException($"mesh {m} submesh {s}: index {ix} out of range {vc}");
                            tris.Add(vbase + ix);
                        }
                        dump.Subs.Add((material++, tris));
                    }
                    vbase += vc;
                }
                if (!anyNrm) dump.Nrm = null;
                if (!anyUv) dump.Uv = null;
                return dump;
            }
            finally
            {
                try { loading.Dispose(); } catch { }
                try { data.Dispose(); } catch { }
            }
        }

        // decode one attribute of one mesh into floats (dimension `dim` per vertex); null if the mesh lacks it
        static float[] Attr(ref GeometryAsset.Data data, int mesh, VertexAttribute attr, int vc, MeshDump dump, out int dim)
        {
            GeometryAsset.GetAttributeFormat(ref data, mesh, attr, out var fmt, out dim);
            if (dim <= 0) { dim = 0; return null; }
            var raw = GeometryAsset.GetAttributeData(ref data, mesh, attr).ToArray();
            int cs = FmtSize(fmt), stride = cs * dim;
            if (raw.Length < vc * stride) throw new InvalidOperationException($"{attr}: {raw.Length} bytes for {vc} x {fmt}x{dim}");
            dump.Formats[attr.ToString()] = fmt + "x" + dim;
            var o = new float[vc * dim];
            for (int v = 0; v < vc; v++)
                for (int k = 0; k < dim; k++)
                    o[v * dim + k] = ReadComp(raw, v * stride + k * cs, fmt);
            return o;
        }

        static int FmtSize(VertexAttributeFormat f)
        {
            switch (f)
            {
                case VertexAttributeFormat.Float32: case VertexAttributeFormat.UInt32: case VertexAttributeFormat.SInt32: return 4;
                case VertexAttributeFormat.Float16: case VertexAttributeFormat.UNorm16: case VertexAttributeFormat.SNorm16:
                case VertexAttributeFormat.UInt16: case VertexAttributeFormat.SInt16: return 2;
                default: return 1;
            }
        }

        static float ReadComp(byte[] b, int o, VertexAttributeFormat f)
        {
            switch (f)
            {
                case VertexAttributeFormat.Float32: return BitConverter.ToSingle(b, o);
                case VertexAttributeFormat.Float16: return HalfToFloat(BitConverter.ToUInt16(b, o));
                case VertexAttributeFormat.UNorm8: return b[o] / 255f;
                case VertexAttributeFormat.SNorm8: return Mathf.Max((sbyte)b[o] / 127f, -1f);
                case VertexAttributeFormat.UNorm16: return BitConverter.ToUInt16(b, o) / 65535f;
                case VertexAttributeFormat.SNorm16: return Mathf.Max(BitConverter.ToInt16(b, o) / 32767f, -1f);
                case VertexAttributeFormat.UInt8: return b[o];
                case VertexAttributeFormat.SInt8: return (sbyte)b[o];
                case VertexAttributeFormat.UInt16: return BitConverter.ToUInt16(b, o);
                case VertexAttributeFormat.SInt16: return BitConverter.ToInt16(b, o);
                case VertexAttributeFormat.UInt32: return BitConverter.ToUInt32(b, o);
                case VertexAttributeFormat.SInt32: return BitConverter.ToInt32(b, o);
                default: throw new NotSupportedException("vertex format " + f);
            }
        }

        static float HalfToFloat(ushort h)
        {
            int sign = (h >> 15) & 1, exp = (h >> 10) & 0x1F, man = h & 0x3FF;
            float v;
            if (exp == 0) v = man * (1f / 16777216f);                                        // subnormal: man * 2^-24
            else if (exp == 31) v = man == 0 ? float.PositiveInfinity : float.NaN;
            else v = (1f + man / 1024f) * (float)Math.Pow(2, exp - 15);
            return sign == 1 ? -v : v;
        }

        // inverse of Colossal.Mathematics.MathUtils.EncodeOctahedral (z hemisphere fold)
        static Vector3 OctDecode(float x, float y)
        {
            var n = new Vector3(x, y, 1f - Mathf.Abs(x) - Mathf.Abs(y));
            if (n.z < 0f)
            {
                float ox = n.x;
                n.x = (1f - Mathf.Abs(n.y)) * (ox >= 0f ? 1f : -1f);
                n.y = (1f - Mathf.Abs(ox)) * (n.y >= 0f ? 1f : -1f);
            }
            return n.normalized;
        }

        // fallback: the asset's CPU-side Unity meshes that already exist (refcount bumped and restored; never created here)
        static MeshDump ReadLoadedMeshes(GeometryAsset ga)
        {
            var arr = ga.ObtainMeshes();
            try
            {
                var dump = new MeshDump { Source = "unity_mesh_fallback", GeometryMeshes = arr?.Length ?? 0, Uv = new List<Vector2>() };
                if (arr == null) throw new InvalidOperationException("no meshes");
                int vbase = 0, material = 0;
                foreach (var mesh in arr)
                {
                    if (!mesh.isReadable) throw new InvalidOperationException(mesh.name + " is not CPU-readable");
                    var verts = mesh.vertices;
                    dump.Pos.AddRange(verts);
                    var uvs = new List<Vector2>(); mesh.GetUVs(0, uvs);
                    if (uvs.Count == verts.Length) dump.Uv.AddRange(uvs); else dump.Uv.AddRange(new Vector2[verts.Length]);
                    for (int s = 0; s < mesh.subMeshCount; s++)
                        dump.Subs.Add((material++, mesh.GetIndices(s, true).Select(i => vbase + i).ToList()));
                    vbase += verts.Length;
                }
                dump.Formats["note"] = "fallback: positions, UV0, triangles only (no normals / skin)";
                return dump;
            }
            finally { ga.ReleaseMeshes(); }
        }

        // ------------------------------------------------------------------ small helpers
        static string F(float v, CultureInfo ci) => v.ToString("0.######", ci);
        static List<object> V3(Vector3 v) => new List<object> { (double)v.x, (double)v.y, (double)v.z };
        static List<object> V3(float3 v) => new List<object> { (double)v.x, (double)v.y, (double)v.z };
        static List<object> M4Rows(Matrix4x4 m)
        {
            var rows = new List<object>();
            for (int r = 0; r < 4; r++) rows.Add(new List<object> { (double)m[r, 0], (double)m[r, 1], (double)m[r, 2], (double)m[r, 3] });
            return rows;
        }
        static Dictionary<string, object> BoundsOf(IEnumerable<Vector3> pts)
        {
            bool any = false; Vector3 mn = Vector3.positiveInfinity, mx = Vector3.negativeInfinity;
            foreach (var p in pts) { any = true; mn = Vector3.Min(mn, p); mx = Vector3.Max(mx, p); }
            if (!any) return null;
            return new Dictionary<string, object> { ["min"] = V3(mn), ["max"] = V3(mx), ["size"] = V3(mx - mn), ["center"] = V3((mn + mx) * 0.5f) };
        }
        static string SafeFileName(string s)
        {
            var bad = Path.GetInvalidFileNameChars();
            var sb = new StringBuilder(s.Length);
            foreach (char c in s) sb.Append(bad.Contains(c) ? '_' : c);
            return sb.ToString().Trim();
        }
        static string SafeObjName(string s)
        {
            var sb = new StringBuilder(s.Length);
            foreach (char c in s) sb.Append(char.IsWhiteSpace(c) || c == '#' ? '_' : c);
            return sb.ToString();
        }
    }
}
