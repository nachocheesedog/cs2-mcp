using System;
using System.Collections.Generic;
using Game.Net;
using Unity.Entities;

namespace CS2MCP
{
    // v0.36: zone-cell alignment of existing roads. The game's own road tool sets Road.StartHalfAligned / EndHalfAligned
    // (from CoursePosFlags.HalfAlign when it snaps to the zone grid); BlockSystem.CreateBlocks then shifts that end's
    // cell line by 4 m so a street's cells line up with the rows of the road it meets (Medium = 3 cells wide -> its rows
    // start 12 m out, a 4 m phase offset otherwise). Roads placed through the MCP never had the flags -> a 4 m crack at
    // every avenue/side-street seam. road_align sets them on existing edges; the edges are tagged Updated so the game
    // rebuilds their zone blocks (cells that move lose their zoning; the server re-zones them).
    public partial class McpBridgeSystem
    {
        object RoadAlign(Dictionary<string, object> p)
        {
            var list = Json.List(p, "edges") ?? throw new ArgumentException("edges: [{id, start_half, end_half}] required");
            bool dry = Json.Bool(p, "dry_run", false);
            var plan = new List<(Entity e, byte oldF, byte newF)>();
            var skipped = new List<object>();
            foreach (var o in list)
            {
                if (!(o is Dictionary<string, object> a)) throw new ArgumentException("edges: each item is {id, start_half, end_half}");
                var id = Json.Str(a, "id", null) ?? throw new ArgumentException("edges[].id required");
                var e = ParseId(id);
                if (!EntityManager.Exists(e) || !EntityManager.HasComponent<Road>(e) || !EntityManager.HasComponent<Edge>(e))
                { skipped.Add(new Dictionary<string, object> { ["id"] = id, ["reason"] = "not a road edge (ids change on every load)" }); continue; }
                var r = EntityManager.GetComponentData<Road>(e);
                byte oldF = (byte)r.m_Flags, newF = oldF;
                void Set(string key, Game.Net.RoadFlags bit)
                {
                    if (!a.ContainsKey(key)) return;
                    if (Json.Bool(a, key, false)) newF |= (byte)bit; else newF &= (byte)~(byte)bit;
                }
                Set("start_half", Game.Net.RoadFlags.StartHalfAligned);
                Set("end_half", Game.Net.RoadFlags.EndHalfAligned);
                if (newF != oldF) plan.Add((e, oldF, newF));
            }
            var outp = new Dictionary<string, object> { ["edges_to_change"] = plan.Count, ["skipped"] = skipped, ["dry_run"] = dry };
            if (dry || plan.Count == 0) return outp;
            var undo = new UndoEntry { Kind = "road_align", Desc = $"road_align {plan.Count} edges" };
            // flags written now; Updated through the end-frame barrier (v0.33 rule) so next frame's BlockSystem sees it
            var tags = World.GetOrCreateSystemManaged<Game.EndFrameBarrier>().CreateCommandBuffer();
            foreach (var (e, oldF, newF) in plan)
            {
                var r = EntityManager.GetComponentData<Road>(e);
                r.m_Flags = (Game.Net.RoadFlags)newF;
                EntityManager.SetComponentData(e, r);
                tags.AddComponent<Game.Common.Updated>(e);
                undo.RoadFlags.Add((e, oldF));
            }
            PushUndo(undo);
            outp["applied"] = true; outp["undo_id"] = undo.Id;
            return outp;
        }

        void UndoRoadAlign(UndoEntry u)
        {
            var tags = World.GetOrCreateSystemManaged<Game.EndFrameBarrier>().CreateCommandBuffer();
            foreach (var (e, oldF) in u.RoadFlags)
            {
                if (!EntityManager.Exists(e) || !EntityManager.HasComponent<Road>(e)) continue;
                var r = EntityManager.GetComponentData<Road>(e);
                r.m_Flags = (Game.Net.RoadFlags)oldF;
                EntityManager.SetComponentData(e, r);
                tags.AddComponent<Game.Common.Updated>(e);
            }
        }
    }
}
