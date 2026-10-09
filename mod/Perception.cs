// Read-only views of the city for planning: the road graph (curves, nodes, widths, elevation, traffic), zone blocks with
// their cells, and a terrain/water height grid. These only report facts; the MCP client does the judging.
using System;
using System.Collections.Generic;
using System.Text;
using Colossal.Mathematics;
using Game.Common;
using Game.Net;
using Game.Prefabs;
using Game.Simulation;
using Game.Tools;
using Game.Zones;
using Unity.Collections;
using Unity.Entities;
using Unity.Mathematics;

namespace CS2MCP
{
    public partial class McpBridgeSystem
    {
        WaterSystem m_Water;
        EntityQuery m_AllBlockQuery;

        void CreatePerception()
        {
            m_Water = World.GetOrCreateSystemManaged<WaterSystem>();
            // zone blocks are owned by their road edge, so they must not use Live() (which drops owned entities)
            m_AllBlockQuery = GetEntityQuery(new EntityQueryDesc
            {
                All = new[] { ComponentType.ReadOnly<Block>(), ComponentType.ReadOnly<Cell>() },
                None = new[] { ComponentType.ReadOnly<Deleted>(), ComponentType.ReadOnly<Temp>() },
            });
        }

        static float[] P3(float3 v) => new[] { (float)Math.Round(v.x, 2), (float)Math.Round(v.y, 2), (float)Math.Round(v.z, 2) };

        bool LiveEdge(Entity e) => EntityManager.Exists(e) && !EntityManager.HasComponent<Deleted>(e) && !EntityManager.HasComponent<Temp>(e);

        // ------------------------------------------------------------------ road graph
        object RoadNetwork(Dictionary<string, object> p)
        {
            var box = BoxOf(p); int limit = (int)Json.Num(p, "limit", 3000); bool lanes = Json.Bool(p, "lanes", false);
            var bounds = new Bounds2(new float2(box.x0, box.z0), new float2(box.x1, box.z1));
            var edges = new List<object>(); var nodeSet = new HashSet<Entity>(); int total = 0;
            var widthCache = new Dictionary<Entity, float>();
            using (var ents = m_EdgeQuery.ToEntityArray(Allocator.Temp))
                foreach (var e in ents)
                {
                    var c = EntityManager.GetComponentData<Curve>(e);
                    if (!MathUtils.Intersect(MathUtils.Bounds(c.m_Bezier.xz), bounds)) continue;
                    total++;
                    if (edges.Count >= limit) continue;
                    var edge = EntityManager.GetComponentData<Edge>(e);
                    var prefab = EntityManager.GetComponentData<PrefabRef>(e).m_Prefab;
                    if (!widthCache.TryGetValue(prefab, out float w))
                        widthCache[prefab] = w = EntityManager.HasComponent<NetGeometryData>(prefab) ? EntityManager.GetComponentData<NetGeometryData>(prefab).m_DefaultWidth : 0f;
                    var b = c.m_Bezier;
                    var d = new Dictionary<string, object>
                    {
                        ["id"] = Id(e), ["type"] = m_Prefabs.GetPrefabName(prefab), ["width"] = w, ["length"] = Math.Round(c.m_Length, 1),
                        ["curve"] = new object[] { P3(b.a), P3(b.b), P3(b.c), P3(b.d) },
                        ["start_node"] = Id(edge.m_Start), ["end_node"] = Id(edge.m_End),
                    };
                    if (EntityManager.HasComponent<Elevation>(e)) { var el = EntityManager.GetComponentData<Elevation>(e).m_Elevation; d["elevation"] = new[] { el.x, el.y }; }
                    if (lanes)
                    {   // v0.27: car lanes start->end ("fwd") and end->start ("back"); one-way when one side is 0
                        var (fw, bk, sp) = EdgeLanes(e);
                        d["lanes"] = new[] { fw, bk }; d["speed_limit"] = Math.Round(sp, 1);
                    }
                    if (EntityManager.HasComponent<Road>(e))
                    {   // 1 = free flow, lower = slower than the speed limit (what the game's traffic overlay shows)
                        var f = NetUtils.GetTrafficFlowSpeed(EntityManager.GetComponentData<Road>(e));
                        d["flow"] = Math.Round(math.csum(f) / 4f, 3);
                        var rf = EntityManager.GetComponentData<Road>(e).m_Flags;   // v0.36: zone-cell half alignment per end
                        d["half"] = new[] { (rf & Game.Net.RoadFlags.StartHalfAligned) != 0, (rf & Game.Net.RoadFlags.EndHalfAligned) != 0 };
                    }
                    edges.Add(d); nodeSet.Add(edge.m_Start); nodeSet.Add(edge.m_End);
                }
            var nodes = new List<object>();
            foreach (var n in nodeSet)
            {
                if (!EntityManager.Exists(n)) continue;
                int deg = 0, owned = 0;
                if (EntityManager.HasBuffer<ConnectedEdge>(n))
                    foreach (var ce in EntityManager.GetBuffer<ConnectedEdge>(n, true))
                    {
                        if (!LiveEdge(ce.m_Edge) || !EntityManager.HasComponent<Edge>(ce.m_Edge)) continue;
                        var ed = EntityManager.GetComponentData<Edge>(ce.m_Edge);
                        if (ed.m_Start != n && ed.m_End != n) continue;             // middle connections (lots) are not junction arms
                        if (EntityManager.HasComponent<Owner>(ce.m_Edge)) owned++; else deg++;
                    }
                var nd = new Dictionary<string, object> { ["id"] = Id(n), ["pos"] = P3(EntityManager.GetComponentData<Node>(n).m_Position), ["degree"] = deg };
                if (owned > 0) nd["owned_arms"] = owned;
                if (EntityManager.HasComponent<Game.Net.OutsideConnection>(n)) nd["outside_connection"] = true;
                nodes.Add(nd);
            }
            return new Dictionary<string, object> { ["edges_in_area"] = total, ["returned"] = edges.Count, ["edges"] = edges, ["nodes"] = nodes };
        }

        // ------------------------------------------------------------------ zone blocks
        // Each block is a strip of 8 m cells along a road: size = [cells along the road, cells deep]. cells is a string in
        // index order (row y = depth from the road, then x): '#' hidden (blocked by roads/other blocks), '.' visible + empty,
        // a letter = zone (see legend), upper case = a building stands on it.
        object ZoneBlocks(Dictionary<string, object> p)
        {
            var box = BoxOf(p); int limit = (int)Json.Num(p, "limit", 4000);
            var bounds = new Bounds2(new float2(box.x0, box.z0), new float2(box.x1, box.z1));
            const string alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!$%&*+-=<>@^~";
            var legend = new Dictionary<int, char>(); var legendOut = new Dictionary<string, object>();
            foreach (var kv in ZonePrefabMap())
            {
                int idx = EntityManager.GetComponentData<ZoneData>(kv.Value).m_ZoneType.m_Index;
                if (idx == 0 || legend.ContainsKey(idx) || legend.Count >= alphabet.Length) continue;
                char ch = alphabet[legend.Count]; legend[idx] = ch; legendOut[ch.ToString()] = kv.Key;
            }
            var list = new List<object>(); int total = 0;
            var sb = new StringBuilder(); var sbBuilt = new StringBuilder();
            using (var blocks = m_AllBlockQuery.ToEntityArray(Allocator.Temp))
                foreach (var blk in blocks)
                {
                    var block = EntityManager.GetComponentData<Block>(blk);
                    if (!MathUtils.Intersect(ZoneUtils.CalculateBounds(block), bounds)) continue;
                    total++;
                    if (list.Count >= limit) continue;
                    var cells = EntityManager.GetBuffer<Cell>(blk, true);
                    sb.Clear(); sbBuilt.Clear();
                    for (int i = 0; i < cells.Length; i++)
                    {
                        var cell = cells[i];
                        sbBuilt.Append((cell.m_State & CellFlags.Occupied) != 0 ? '1' : '0');
                        if ((cell.m_State & CellFlags.Visible) == 0) { sb.Append('#'); continue; }
                        sb.Append(cell.m_Zone.m_Index == 0 ? '.' : (legend.TryGetValue(cell.m_Zone.m_Index, out var lc) ? lc : '?'));
                    }
                    var d = new Dictionary<string, object>
                    {
                        ["id"] = Id(blk), ["pos"] = P3(block.m_Position), ["dir"] = new[] { block.m_Direction.x, block.m_Direction.y },
                        ["size"] = new[] { block.m_Size.x, block.m_Size.y }, ["cells"] = sb.ToString(), ["built"] = sbBuilt.ToString(),
                    };
                    if (EntityManager.HasComponent<Owner>(blk)) d["road"] = Id(EntityManager.GetComponentData<Owner>(blk).m_Owner);
                    list.Add(d);
                }
            return new Dictionary<string, object>
            {
                ["blocks_in_area"] = total, ["returned"] = list.Count, ["legend"] = legendOut, ["blocks"] = list,
                ["cell_codes"] = "cells: '#' hidden, '.' visible unzoned, other chars = zone (legend), '?' = zone not in legend; built: '1' = a building stands on the cell",
                ["geometry"] = "cell (x,y) at index y*size.x+x; centre = pos + dir*4*(size.y-2y-1) + right*4*(size.x-2x-1), right = (dir.y, -dir.x). y=0 is the row along the road (dir points from the block toward its road)",
            };
        }

        // ------------------------------------------------------------------ terrain + water grid
        object TerrainGrid(Dictionary<string, object> p)
        {
            float x0 = (float)Json.Num(p, "xmin", -1000), x1 = (float)Json.Num(p, "xmax", 1000);
            float z0 = (float)Json.Num(p, "zmin", -1000), z1 = (float)Json.Num(p, "zmax", 1000);
            float step = (float)Json.Num(p, "step", 32);
            int nx = (int)math.floor((x1 - x0) / step) + 1, nz = (int)math.floor((z1 - z0) / step) + 1;
            if (step < 4 || nx < 2 || nz < 2 || (long)nx * nz > 90000) throw new ArgumentException("grid must be 2..90000 samples with step >= 4 (raise step or shrink the area)");
            var th = m_Terrain.GetHeightData(true);
            bool water = m_Water != null && m_Water.Loaded;
            WaterSurfaceData<SurfaceWater> ws = default;
            if (water) { ws = m_Water.GetSurfaceData(out var deps); deps.Complete(); }
            var heights = new List<object>(nz); var depths = new List<object>(nz);
            float hmin = float.MaxValue, hmax = float.MinValue; int wet = 0;
            for (int j = 0; j < nz; j++)
            {
                var hr = new float[nx]; var dr = new float[nx];
                for (int i = 0; i < nx; i++)
                {
                    var pos = new float3(x0 + i * step, 0, z0 + j * step);
                    float h = TerrainUtils.SampleHeight(ref th, pos), dpt = 0;
                    if (water) dpt = WaterUtils.SampleDepth(ref ws, pos);
                    hr[i] = (float)Math.Round(h, 1); dr[i] = (float)Math.Round(math.max(0, dpt), 1);
                    hmin = math.min(hmin, h); hmax = math.max(hmax, h); if (dpt > 0.5f) wet++;
                }
                heights.Add(hr); depths.Add(dr);
            }
            if (water) m_Water.AddSurfaceReader(default);
            return new Dictionary<string, object>
            {
                ["origin"] = new[] { x0, z0 }, ["step"] = step, ["nx"] = nx, ["nz"] = nz, ["rows"] = "heights[j][i] at (xmin + i*step, zmin + j*step)",
                ["heights"] = heights, ["water_depth"] = water ? (object)depths : null, ["height_range"] = new[] { hmin, hmax },
                ["water_samples_over_0_5m"] = wet,
            };
        }
    }
}
