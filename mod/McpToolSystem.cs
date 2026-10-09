// A custom tool that applies batches of creation definitions exactly like the built-in tools do: a job writes
// CreationDefinition entities (+ NetCourse / Zoning / ObjectDefinition), the game's generate systems turn them into a
// Temp preview, the tool inspects the preview (Temp + Error), then sets applyMode = Apply so the apply systems make it
// permanent (or Clear to discard). Afterwards it lets the job record results/undo data and hands back to the default tool.
using System;
using System.Collections.Generic;
using Game.Common;
using Game.Net;
using Game.Prefabs;
using Game.Tools;
using Unity.Entities;
using Unity.Jobs;

namespace CS2MCP
{
    public sealed class DefJob
    {
        public string Kind;
        public bool DryRun, AllowErrors, Hold;                                                // Hold: keep the preview on screen until commit/cancel
        public int Definitions;
        public Action<EntityCommandBuffer> Write;                                            // writes the definitions
        public Func<Dictionary<string, object>, Dictionary<string, object>> OnApplied;        // after apply: results + undo
        public Dictionary<string, object> Info = new Dictionary<string, object>();
    }

    public partial class McpToolSystem : ToolBaseSystem
    {
        public override string toolID => "CS2MCP Tool";
        public bool Busy => m_Job != null;
        public bool Held => m_Job != null && m_Stage == 3;

        ToolSystem m_Tools;
        DefaultToolSystem m_Default;
        ToolOutputBarrier m_Barrier;
        EntityQuery m_DefQuery, m_TempEdgeQuery, m_TempErrorQuery, m_TempWarningQuery, m_IconQuery;
        PrefabSystem m_PrefabSys;
        DefJob m_Job; Command m_Cmd; int m_Stage, m_Frames;
        Dictionary<string, object> m_Summary;
        Command m_Pending; bool m_PendingCommit, m_PendingForce; string m_LastAbort;
        // v0.29: the player's own tool and what it had selected (a road, a building, a zone brush...) - put back when we finish,
        // so an MCP operation borrows the active-tool slot for a few frames instead of clearing the player's selection
        ToolBaseSystem m_PrevTool; PrefabBase m_PrevPrefab;

        protected override void OnCreate()
        {
            base.OnCreate();
            m_Tools = World.GetOrCreateSystemManaged<ToolSystem>();
            m_Default = World.GetOrCreateSystemManaged<DefaultToolSystem>();
            m_Barrier = World.GetOrCreateSystemManaged<ToolOutputBarrier>();
            // v0.33: the game's own sweep query, {CreationDefinition} EXCLUDING Updated = last frame's definitions. It used to
            // be {CreationDefinition, Updated}, which at ToolUpdate matches the simulation's pending definitions (zone spawns,
            // finished constructions' sub-nets/areas, emitted via EndFrameBarrier) and destroyed them before Modification1.
            m_DefQuery = GetDefinitionQuery();
            m_TempEdgeQuery = GetEntityQuery(ComponentType.ReadOnly<Temp>(), ComponentType.ReadOnly<Edge>());
            m_TempErrorQuery = GetEntityQuery(ComponentType.ReadOnly<Temp>(), ComponentType.ReadOnly<Error>());
            m_TempWarningQuery = GetEntityQuery(ComponentType.ReadOnly<Temp>(), ComponentType.ReadOnly<Warning>());
            m_IconQuery = GetEntityQuery(ComponentType.ReadOnly<Game.Notifications.Icon>(), ComponentType.ReadOnly<PrefabRef>(), ComponentType.ReadOnly<Owner>());
            m_PrefabSys = World.GetOrCreateSystemManaged<PrefabSystem>();
            Log.Info("McpToolSystem created");
        }

        public override PrefabBase GetPrefab() => null;
        public override bool TrySetPrefab(PrefabBase prefab) => false;

        public void Submit(Command cmd, DefJob job)
        {
            if (m_Job != null) { cmd.Fail(Held ? "a preview is being held - commit or cancel it first" : "tool busy with a previous operation"); return; }
            if (job.Definitions == 0) { cmd.Complete(new Dictionary<string, object> { ["kind"] = job.Kind, ["applied"] = false, ["reason"] = "nothing to do" }); return; }
            m_Job = job; m_Cmd = cmd; m_Stage = 0; m_Frames = 0; m_LastAbort = null;
            if (m_Tools.activeTool != this)
            {
                m_PrevTool = m_Tools.activeTool;
                try { m_PrevPrefab = m_PrevTool?.GetPrefab(); } catch { m_PrevPrefab = null; }
            }
            m_Tools.activeTool = this;
        }

        void Finish(object result, string error = null, bool restoreTool = true)
        {
            if (m_Cmd != null) { if (error != null) m_Cmd.Fail(error); else m_Cmd.Complete(result); }
            m_Job = null; m_Cmd = null; m_Stage = 0; m_Summary = null; m_Pending = null;
            if (restoreTool && m_Tools.activeTool == this) RestorePlayerTool();
        }

        // v0.33: the HTTP caller gave up (timeout) before the edit was applied -> discard the preview instead of applying
        // an edit nobody is waiting for (a retry would otherwise apply it twice)
        bool CallerGone => m_Cmd != null && m_Cmd.Abandoned;

        void RestorePlayerTool()
        {
            var tool = m_PrevTool; var prefab = m_PrevPrefab;
            m_PrevTool = null; m_PrevPrefab = null;
            if (tool == null || tool == this) { m_Tools.activeTool = m_Default; return; }
            try
            {
                m_Tools.activeTool = tool;
                if (prefab != null && tool.GetPrefab() != prefab) tool.TrySetPrefab(prefab);
            }
            catch (Exception e) { Log.Info("restore player tool: " + e.Message); m_Tools.activeTool = m_Default; }
        }

        // Watchdog (0.25): OnUpdate only runs while this tool is the ToolSystem's active tool, so a job whose tool got
        // displaced without OnStopRunning (or never started) sits forever and every later command reports "tool busy".
        // McpBridgeSystem calls Watchdog() every frame; after ~120 frames of being orphaned the job is dropped.
        int m_OrphanFrames;
        [System.Runtime.CompilerServices.MethodImpl(System.Runtime.CompilerServices.MethodImplOptions.NoInlining)]
        public void Watchdog()
        {
            if (m_Job == null || Held || m_Tools.activeTool == this) { m_OrphanFrames = 0; return; }
            if (++m_OrphanFrames > 120) ForceReset("the MCP tool lost the active-tool slot mid-operation (watchdog)");
        }
        // v0.33: no EntityManager.DestroyEntity here any more - this runs from MainLoop, where it also hit other tools'
        // current definitions. Our leftover definitions lose Updated at Cleanup, and whichever tool runs next sweeps them
        // with the same GetDefinitionQuery (DefaultToolSystem does it every frame). restoreTool=false during a city load,
        // where ToolSystem itself resets the active tool.
        public Dictionary<string, object> ForceReset(string why, bool restoreTool = true)
        {
            var info = new Dictionary<string, object> { ["was_busy"] = m_Job != null, ["was_held"] = Held, ["kind"] = m_Job?.Kind, ["stage"] = m_Stage };
            m_OrphanFrames = 0;
            if (m_Job == null) return info;
            m_LastAbort = why; Log.Info("tool reset: " + why);
            m_Pending?.Fail(why);
            if (!restoreTool) { m_PrevTool = null; m_PrevPrefab = null; }
            Finish(null, why, restoreTool);
            return info;
        }

        public void CommitHeld(Command cmd) => Resolve(cmd, true);
        public void CancelHeld(Command cmd) => Resolve(cmd, false);
        void Resolve(Command cmd, bool commit)
        {
            if (!Held) { cmd.Fail("no preview is being held" + (m_LastAbort != null ? $" (the last one was dropped: {m_LastAbort})" : "")); return; }
            if (m_Pending != null) { cmd.Fail("already committing/cancelling"); return; }
            m_Pending = cmd; m_PendingCommit = commit; m_PendingForce = Json.Bool(cmd.Params, "allow_errors", m_Job.AllowErrors);
        }

        // another tool was picked while we were running (e.g. the player clicked a tool): drop our work cleanly
        protected override void OnStopRunning()
        {
            if (m_Job != null)
            {
                m_LastAbort = Held ? "another tool was selected while the preview was held" : "another tool was selected mid-operation";
                m_Pending?.Fail(m_LastAbort);
                Finish(null, m_LastAbort);
            }
            base.OnStopRunning();
        }

        protected override JobHandle OnUpdate(JobHandle inputDeps)
        {
            if (m_Job == null)
            {
                applyMode = ApplyMode.None;
                if (m_Tools.activeTool == this) RestorePlayerTool();
                return inputDeps;
            }
            m_Frames++;
            if (m_Stage != 3 && m_Frames > 600)
            {
                applyMode = ApplyMode.Clear; inputDeps = DestroyDefinitions(m_DefQuery, m_Barrier, inputDeps);
                Finish(null, "operation timed out in the tool"); return inputDeps;
            }
            if ((m_Stage == 0 || m_Stage == 1) && CallerGone)
            {
                applyMode = ApplyMode.Clear; inputDeps = DestroyDefinitions(m_DefQuery, m_Barrier, inputDeps);
                Log.Info($"{m_Job.Kind}: caller timed out before apply - preview discarded");
                Finish(null, "the caller timed out before the edit was applied - preview discarded, nothing changed"); return inputDeps;
            }
            try
            {
                switch (m_Stage)
                {
                    case 0:   // write definitions -> the game builds a Temp preview over the next frames
                        inputDeps = DestroyDefinitions(m_DefQuery, m_Barrier, inputDeps);
                        m_Job.Write(m_Barrier.CreateCommandBuffer());
                        applyMode = ApplyMode.None;
                        m_Stage = 1; m_Frames = 0;
                        return inputDeps;
                    case 1:   // inspect the preview
                        applyMode = ApplyMode.None;
                        if (m_Frames < 3) return inputDeps;
                        int errors = m_TempErrorQuery.CalculateEntityCount();
                        m_Summary = new Dictionary<string, object>(m_Job.Info)
                        {
                            ["kind"] = m_Job.Kind, ["definitions"] = m_Job.Definitions, ["preview_edges"] = m_TempEdgeQuery.CalculateEntityCount(),
                            ["errors"] = errors, ["warnings"] = m_TempWarningQuery.CalculateEntityCount(),
                        };
                        if (errors > 0) { m_Summary["error_reasons"] = ErrorReasons(out var where); m_Summary["error_locations"] = where; }
                        if (m_Job.Hold && !m_Job.DryRun)
                        {   // leave the Temp preview on screen; the client commits or cancels it later
                            m_Summary["held"] = true; m_Summary["applied"] = false;
                            m_Summary["next"] = "look at it (screenshot), then preview_commit or preview_cancel";
                            m_Cmd.Complete(new Dictionary<string, object>(m_Summary)); m_Cmd = null;
                            m_Stage = 3; m_Frames = 0;
                            return inputDeps;
                        }
                        if (m_Job.DryRun || (errors > 0 && !m_Job.AllowErrors))
                        {
                            applyMode = ApplyMode.Clear;
                            inputDeps = DestroyDefinitions(m_DefQuery, m_Barrier, inputDeps);
                            m_Summary["applied"] = false;
                            m_Summary["reason"] = m_Job.DryRun ? "dry_run" : "preview has errors (overlaps, too steep, not enough money...) - pass allow_errors=true to force";
                            Finish(m_Summary);
                            return inputDeps;
                        }
                        applyMode = ApplyMode.Apply;          // apply systems make the Temp preview permanent this frame
                        inputDeps = DestroyDefinitions(m_DefQuery, m_Barrier, inputDeps);
                        m_Stage = 2; m_Frames = 0;
                        return inputDeps;
                    case 3:   // holding the preview
                        applyMode = ApplyMode.None;
                        if (m_Pending == null) return inputDeps;
                        if (m_Pending.Abandoned) { m_Pending = null; return inputDeps; }   // its caller timed out: keep holding, apply nothing
                        m_Cmd = m_Pending; m_Pending = null;
                        int errs = m_TempErrorQuery.CalculateEntityCount();
                        if (!m_PendingCommit || (errs > 0 && !m_PendingForce))
                        {
                            applyMode = ApplyMode.Clear;
                            inputDeps = DestroyDefinitions(m_DefQuery, m_Barrier, inputDeps);
                            Finish(new Dictionary<string, object>
                            {
                                ["kind"] = m_Job.Kind, ["applied"] = false, ["errors"] = errs,
                                ["reason"] = m_PendingCommit ? "preview has errors - commit with allow_errors=true to force" : "cancelled",
                            });
                            return inputDeps;
                        }
                        m_Summary["errors"] = errs; m_Summary.Remove("held"); m_Summary.Remove("next");
                        applyMode = ApplyMode.Apply;
                        inputDeps = DestroyDefinitions(m_DefQuery, m_Barrier, inputDeps);
                        m_Stage = 2; m_Frames = 0;
                        return inputDeps;
                    default:  // applied: give the result entities a couple of frames to settle, then report + record undo
                        applyMode = ApplyMode.None;
                        if (m_Frames < 3) return inputDeps;
                        m_Summary["applied"] = true;
                        var final = m_Job.OnApplied != null ? m_Job.OnApplied(m_Summary) : m_Summary;
                        Finish(final);
                        return inputDeps;
                }
            }
            catch (Exception e)
            {
                Log.Info("tool error: " + e);
                applyMode = ApplyMode.Clear; inputDeps = DestroyDefinitions(m_DefQuery, m_Barrier, inputDeps);
                Finish(null, "tool error: " + e.Message);
                return inputDeps;
            }
        }

        // names of the notification icons (TightCurve, OverlapExisting, SteepSlope, ...) attached to the Temp preview
        // counts per error type, plus where each one is (first 200) so a caller can drop just the offending pieces
        Dictionary<string, object> ErrorReasons(out List<object> where)
        {
            var counts = new Dictionary<string, object>(); where = new List<object>();
            using (var icons = m_IconQuery.ToEntityArray(Unity.Collections.Allocator.Temp))
                foreach (var ic in icons)
                {
                    var owner = EntityManager.GetComponentData<Owner>(ic).m_Owner;
                    if (!EntityManager.HasComponent<Temp>(ic) && !(EntityManager.Exists(owner) && EntityManager.HasComponent<Temp>(owner))) continue;
                    string name = m_PrefabSys.GetPrefabName(EntityManager.GetComponentData<PrefabRef>(ic).m_Prefab);
                    counts[name] = (counts.TryGetValue(name, out var c) ? (int)c : 0) + 1;
                    if (where.Count < 200)
                    {
                        var at = EntityManager.GetComponentData<Game.Notifications.Icon>(ic).m_Location;
                        where.Add(new Dictionary<string, object> { ["reason"] = name, ["pos"] = new[] { (float)Math.Round(at.x, 1), (float)Math.Round(at.z, 1) } });
                    }
                }
            return counts;
        }
    }
}