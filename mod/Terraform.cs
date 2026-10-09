// Terraforming the way the game's terrain tool does it: every frame, TerrainSystem.ApplyBrush(type, bounds, brush,
// texture) for each brush position (Level / Shift / Soften / Slope on height), for `frames` frames. The terrain tool
// also re-applies its brush every frame while the mouse is held; the height moves toward the target a bit each frame.
// Not undoable through the MCP undo stack (terrain is not an entity).
using System;
using System.Collections.Generic;
using Game.Prefabs;
using Game.Tools;
using Unity.Collections;
using Unity.Entities;
using Unity.Mathematics;

namespace CS2MCP
{
    public partial class McpBridgeSystem
    {
        sealed class TerraformRun
        {
            public Command Cmd; public TerraformingType Type; public UnityEngine.Texture Tex;
            public List<Brush> Brushes = new List<Brush>(); public int Frames, Done; public List<float2> Pts = new List<float2>();
            public List<float> Before = new List<float>(); public string Tool, BrushName;
        }
        TerraformRun m_Terraform;

        void StartTerraform(Command cmd, Dictionary<string, object> p)
        {
            if (m_Terraform != null) throw new InvalidOperationException("a terraform run is still in progress");
            var typeName = Json.Str(p, "type", "level");
            if (!Enum.TryParse(typeName, true, out TerraformingType type) || type == TerraformingType.Count)
                throw new ArgumentException("type must be level, shift, soften or slope");
            // the height tool prefab of that type, and a brush shape (by name, default the first circular-looking one)
            Entity tool = Entity.Null; string toolName = null;
            using (var es = GetEntityQuery(ComponentType.ReadOnly<PrefabData>(), ComponentType.ReadOnly<TerraformingData>()).ToEntityArray(Allocator.Temp))
                foreach (var e in es)
                {
                    var td = EntityManager.GetComponentData<TerraformingData>(e);
                    if (td.m_Type == type && td.m_Target == TerraformingTarget.Height) { tool = e; toolName = m_Prefabs.GetPrefabName(e); break; }
                }
            if (tool == Entity.Null) throw new InvalidOperationException($"no height terraforming tool of type {type}");
            string want = Json.Str(p, "brush", null);
            Entity brushE = Entity.Null; BrushPrefab brushP = null; var names = new List<string>();
            using (var es = GetEntityQuery(ComponentType.ReadOnly<PrefabData>(), ComponentType.ReadOnly<BrushData>()).ToEntityArray(Allocator.Temp))
                foreach (var e in es)
                {
                    if (!m_Prefabs.TryGetPrefab<BrushPrefab>(e, out var bp) || bp.m_Texture == null) continue;
                    names.Add(bp.name);
                    bool match = want != null ? bp.name.Equals(want, StringComparison.OrdinalIgnoreCase)
                                              : (brushP == null || bp.name.IndexOf("circle", StringComparison.OrdinalIgnoreCase) >= 0 && brushP.name.IndexOf("circle", StringComparison.OrdinalIgnoreCase) < 0);
                    if (match) { brushE = e; brushP = bp; }
                }
            if (brushP == null) throw new ArgumentException("no brush" + (want != null ? $" named '{want}'" : "") + "; available: " + string.Join(", ", names));
            float size = (float)Json.Num(p, "size", 40), strength = (float)Json.Num(p, "strength", 1.0);
            int frames = (int)Math.Max(1, Math.Min(600, Json.Num(p, "frames", 90)));
            var pts = Json.List(p, "points") ?? throw new ArgumentException("points: [[x,z] or [x,z,target_y], ...] required");
            if (pts.Count > 400) throw new ArgumentException("at most 400 brush points per call");
            var run = new TerraformRun { Cmd = cmd, Type = type, Tex = brushP.m_Texture, Frames = frames, Tool = toolName, BrushName = brushP.name };
            float? defTarget = p.ContainsKey("target_height") ? (float?)Json.Num(p, "target_height", 0) : null;
            foreach (var o in pts)
            {
                var l = (List<object>)o; float x = Convert.ToSingle(l[0]), z = Convert.ToSingle(l[1]);
                float h = SampleHeight(x, z);
                float ty = l.Count > 2 && l[2] != null ? Convert.ToSingle(l[2]) : (defTarget ?? h);
                run.Brushes.Add(new Brush { m_Tool = tool, m_Position = new float3(x, h, z), m_Target = new float3(x, ty, z), m_Start = new float3(x, h, z),
                                            m_Angle = 0f, m_Size = size, m_Strength = strength, m_Opacity = 1f });
                run.Pts.Add(new float2(x, z)); run.Before.Add(h);
            }
            if (Json.Bool(p, "dry_run", false))
            {
                cmd.Complete(new Dictionary<string, object> { ["tool"] = toolName, ["brush"] = brushP.name, ["brushes"] = names, ["points"] = run.Brushes.Count,
                                                              ["frames"] = frames, ["applied"] = false, ["reason"] = "dry_run" });
                return;
            }
            m_Terraform = run;
        }

        [System.Runtime.CompilerServices.MethodImpl(System.Runtime.CompilerServices.MethodImplOptions.NoInlining)]
        void PumpTerraform()
        {
            var r = m_Terraform;
            if (r == null) return;
            try
            {
                if (r.Done < r.Frames)
                {
                    foreach (var b in r.Brushes) m_Terrain.ApplyBrush(r.Type, ToolUtils.GetBounds(b), b, r.Tex);
                    r.Done++; return;
                }
                if (r.Done < r.Frames + 30) { r.Done++; return; }        // let the height readback catch up before reporting
                var after = new List<object>();
                for (int i = 0; i < r.Pts.Count && i < 60; i++)
                    after.Add(new[] { (float)Math.Round(r.Pts[i].x, 1), (float)Math.Round(r.Pts[i].y, 1), (float)Math.Round(r.Before[i], 2),
                                      (float)Math.Round(SampleHeight(r.Pts[i].x, r.Pts[i].y), 2), (float)Math.Round(r.Brushes[i].m_Target.y, 2) });
                m_Terraform = null;
                r.Cmd.Complete(new Dictionary<string, object> { ["tool"] = r.Tool, ["brush"] = r.BrushName, ["points"] = r.Brushes.Count, ["frames"] = r.Frames,
                                                                ["applied"] = true, ["heights"] = after, ["heights_columns"] = "x, z, before, after, target",
                                                                ["note"] = "terrain edits are not in the MCP undo stack" });
            }
            catch (Exception e) { m_Terraform = null; r.Cmd.Fail("terraform failed: " + e.Message); }
        }
    }
}
