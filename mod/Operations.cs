// World-editing operations for McpBridgeSystem: place (straight/curved) roads, bulldoze roads/buildings, replace road
// types, paint/clear zoning, and an undo stack. Every operation becomes a DefJob run by McpToolSystem (preview ->
// error check -> apply). Definitions mirror the game's own tools (see ref\ decompiled NetToolSystem / BulldozeToolSystem /
// ZoneToolSystem).
using System;
using System.Collections.Generic;
using Colossal.Mathematics;
using Game.Buildings;
using Game.Common;
using Game.Net;
using Game.Prefabs;
using Game.Tools;
using Game.Zones;
using Unity.Collections;
using Unity.Entities;
using Unity.Mathematics;
using Transform = Game.Objects.Transform;

namespace CS2MCP
{
    public partial class McpBridgeSystem
    {
        sealed class UndoEntry
        {
            public int Id; public string Kind, Desc;
            public int Gen; public string Save;                                                  // v0.33: the city load it belongs to
            public List<Entity> Created = new List<Entity>();                                   // place_roads -> delete these
            public List<(Entity prefab, Bezier4x3 curve)> CreatedCurves = new List<(Entity, Bezier4x3)>(); // same edges by shape: finds them again after a rebuild/split gave them new entities
            public List<(Entity prefab, Bezier4x3 curve)> Deleted = new List<(Entity, Bezier4x3)>();   // bulldoze -> rebuild
            public int BuildingsDeleted;
            public List<(float3 mid, Entity oldPrefab)> Replaced = new List<(float3, Entity)>();       // replace -> swap back
            public List<(Entity block, int index, ZoneType zone)> Cells = new List<(Entity, int, ZoneType)>(); // zone -> restore
            public List<(Entity edge, byte flags)> RoadFlags = new List<(Entity, byte)>();                        // road_align -> restore (v0.36)
            public List<Entity> Districts = new List<Entity>();                                               // create_district -> delete
            public List<(List<float2> outline, string name)> DistrictShapes = new List<(List<float2>, string)>(); // delete_district -> recreate
        }
        readonly List<UndoEntry> m_Undo = new List<UndoEntry>();
        Unity.Mathematics.Random m_Rng = new Unity.Mathematics.Random(0x51ED2701u);
        const int kUndoCap = 50;

        int m_NextUndoId = 1;
        void PushUndo(UndoEntry u)
        {
            u.Id = m_NextUndoId++; u.Gen = s_LoadGeneration; u.Save = s_LoadedSave;
            m_Undo.Add(u); if (m_Undo.Count > kUndoCap) m_Undo.RemoveAt(0);
        }

        object History()
        {
            var l = new List<object>();
            for (int i = m_Undo.Count - 1; i >= 0; i--)
            {
                var u = m_Undo[i];
                l.Add(new Dictionary<string, object>
                {
                    ["id"] = u.Id, ["kind"] = u.Kind, ["description"] = u.Desc, ["save"] = u.Save, ["created_edges"] = u.Created.Count, ["deleted_roads"] = u.Deleted.Count,
                    ["deleted_buildings"] = u.BuildingsDeleted, ["replaced"] = u.Replaced.Count, ["zone_cells"] = u.Cells.Count,
                });
            }
            return new Dictionary<string, object> { ["undo_depth"] = m_Undo.Count, ["entries_newest_first"] = l };
        }

        // ================================================================== place (straight / curved)
        sealed class PlaceSeg { public Entity Prefab; public float2 A, B; public float2? C1, C2; public Bezier4x3? Exact; public float2 Elev; }
        struct End { public float3 Pos; public Entity Entity; public float Split; public string Snapped; public float Elev; }

        DefJob BuildPlace(Dictionary<string, object> p, bool recordUndo)
        {
            var segs = Json.List(p, "segments") ?? throw new ArgumentException("segments: [{start:[x,z], end:[x,z], type?, control?:[x,z], controls?:[[x,z],[x,z]]}] required");
            string defType = Json.Str(p, "type", null);
            var prefabs = RoadPrefabMap(anyNet: true);
            var list = new List<PlaceSeg>();
            foreach (var o in segs)
            {
                var s = (Dictionary<string, object>)o;
                string type = Json.Str(s, "type", defType) ?? throw new ArgumentException("each segment needs a type (or give a default 'type')");
                if (!prefabs.TryGetValue(type, out var prefab)) throw new ArgumentException($"unknown road type '{type}' (use list_road_types)");
                var seg = new PlaceSeg { Prefab = prefab, A = XZ(s["start"]), B = XZ(s["end"]) };
                if (s.TryGetValue("elevation", out var ev) && ev != null)    // metres above ground: number or [start, end]
                    seg.Elev = ev is List<object> el ? new float2((float)(double)el[0], (float)(double)el[1]) : new float2((float)(double)ev);
                if (s.TryGetValue("controls", out var cs) && cs is List<object> cl && cl.Count == 2) { seg.C1 = XZ(cl[0]); seg.C2 = XZ(cl[1]); }
                else if (s.TryGetValue("control", out var q) && q != null)
                {   // quadratic control point -> equivalent cubic
                    var Q = XZ(q); seg.C1 = seg.A + (Q - seg.A) * (2f / 3f); seg.C2 = seg.B + (Q - seg.B) * (2f / 3f);
                }
                list.Add(seg);
            }
            var job = MakePlaceJob(list, Json.Bool(p, "dry_run", false), Json.Bool(p, "allow_errors", false), recordUndo,
                                   (float)Json.Num(p, "snap_tolerance", 2.0), $"place {list.Count} road segment(s)", null, Json.Bool(p, "no_intersections", false));
            job.Hold = Json.Bool(p, "hold", false);
            return job;
        }

        DefJob MakePlaceJob(List<PlaceSeg> segs, bool dryRun, bool allowErrors, bool recordUndo, float tol, string desc, Action onApplied = null, bool noIntersections = false)
        {
            // snap to / cross only roads and tracks: pipes and power lines lie under/over everything and must not grab ends
            var edges = new List<(Entity e, Bezier4x3 c)>();
            var before = new HashSet<Entity>(); var roadNodes = new HashSet<Entity>();
            using (var es = m_EdgeQuery.ToEntityArray(Allocator.Temp))
                foreach (var e in es)
                {
                    before.Add(e);
                    if (!IsRoadOrTrack(e)) continue;
                    edges.Add((e, EntityManager.GetComponentData<Curve>(e).m_Bezier));
                    var ee = EntityManager.GetComponentData<Edge>(e); roadNodes.Add(ee.m_Start); roadNodes.Add(ee.m_End);
                }
            var nodes = new List<(Entity e, float3 pos)>();
            foreach (var n in roadNodes)
                if (EntityManager.Exists(n) && EntityManager.HasComponent<Node>(n)) nodes.Add((n, EntityManager.GetComponentData<Node>(n).m_Position));

            // 1) base curves with snapped ends
            var items = new List<(Entity prefab, Bezier4x3 curve, End a, End b)>();
            int snappedNodes = 0, snappedRoads = 0, curved = 0;
            var nearMiss = new List<object>();
            foreach (var s in segs)
            {
                var a = Snap(s.Exact.HasValue ? s.Exact.Value.a.xz : s.A, tol, nodes, edges);
                var b = Snap(s.Exact.HasValue ? s.Exact.Value.d.xz : s.B, tol, nodes, edges);
                if (s.Exact.HasValue)
                {   // rebuilding a known curve (undo): keep its original height, e.g. bridges
                    var ex = s.Exact.Value;
                    if (a.Entity == Entity.Null && ex.a.y - a.Pos.y > 0.5f) { a.Elev = ex.a.y - a.Pos.y; a.Pos.y = ex.a.y; }
                    if (b.Entity == Entity.Null && ex.d.y - b.Pos.y > 0.5f) { b.Elev = ex.d.y - b.Pos.y; b.Pos.y = ex.d.y; }
                }
                if (a.Entity == Entity.Null && s.Elev.x != 0) { a.Pos.y += s.Elev.x; a.Elev = s.Elev.x; }
                if (b.Entity == Entity.Null && s.Elev.y != 0) { b.Pos.y += s.Elev.y; b.Elev = s.Elev.y; }
                if (a.Entity != Entity.Null) a.Elev = ElevationAt(a);
                if (b.Entity != Entity.Null) b.Elev = ElevationAt(b);
                foreach (var e in new[] { a, b })
                {
                    if (e.Snapped == "node") snappedNodes++; else if (e.Snapped == "edge") snappedRoads++;
                    else if (nearMiss.Count < 40)
                    {   // a free end that lands close to an existing node usually meant to join it (stale coordinates)
                        float best = 15f; float3 bp = default;
                        foreach (var (_, np) in nodes) { float d = math.distance(np.xz, e.Pos.xz); if (d < best) { best = d; bp = np; } }
                        if (best < 15f && best > tol)
                            nearMiss.Add(new Dictionary<string, object> { ["end"] = new[] { (float)Math.Round(e.Pos.x, 1), (float)Math.Round(e.Pos.z, 1) },
                                                                          ["nearest_node"] = new[] { (float)Math.Round(bp.x, 2), (float)Math.Round(bp.z, 2) }, ["gap_m"] = (float)Math.Round(best, 1) });
                    }
                }
                Bezier4x3 curve;
                if (s.Exact.HasValue)
                {
                    curve = s.Exact.Value; curve.a = a.Pos; curve.d = b.Pos;
                    if (math.distance(MathUtils.Position(curve, 0.5f), (curve.a + curve.d) / 2) > 0.3f) curved++;
                }
                else if (s.C1.HasValue)
                {
                    curve = new Bezier4x3(a.Pos, new float3(s.C1.Value.x, math.lerp(a.Pos.y, b.Pos.y, 1f / 3f), s.C1.Value.y),
                                          new float3(s.C2.Value.x, math.lerp(a.Pos.y, b.Pos.y, 2f / 3f), s.C2.Value.y), b.Pos);
                    curved++;
                }
                else curve = NetUtils.StraightCurve(a.Pos, b.Pos);
                if (MathUtils.Length(curve) < 1f) throw new ArgumentException("segment shorter than 1 m");
                items.Add((s.Prefab, curve, a, b));
            }
            // 2) crossings: with existing roads (join the road there) and between new roads in this batch (shared node)
            var splits = new List<List<(float t, End e)>>();
            foreach (var _ in items) splits.Add(new List<(float, End)>());
            int crossExisting = 0, crossNew = 0, gradeSeparated = 0, teeNew = 0;
            // 2a) ends of new roads: merge coincident unsnapped ends, and join an end that lands on another new road's
            //     interior (T junction) by splitting that road there with the same shared point
            if (!noIntersections)
            {
                var shared = new List<End>();
                End Canon(End e)
                {
                    foreach (var q in shared) if (math.distance(q.Pos.xz, e.Pos.xz) < 0.6f && math.abs(q.Pos.y - e.Pos.y) < 2f) return q;
                    shared.Add(e); return e;
                }
                for (int i = 0; i < items.Count; i++)
                {
                    var it = items[i];
                    for (int side = 0; side < 2; side++)
                    {
                        var e = side == 0 ? it.a : it.b;
                        if (e.Entity != Entity.Null) continue;
                        for (int j = 0; j < items.Count; j++)
                        {
                            if (j == i) continue;
                            var cj = items[j].curve;
                            float d = MathUtils.Distance(cj.xz, e.Pos.xz, out float t);
                            if (d > tol || t <= 0.01f || t >= 0.99f) continue;
                            var p = MathUtils.Position(cj, t);
                            if (math.abs(p.y - e.Pos.y) > 4f) continue;           // passes over/under
                            e.Pos = p; e.Snapped = "tee"; e.Elev = math.lerp(items[j].a.Elev, items[j].b.Elev, t);
                            splits[j].Add((t, e)); teeNew++;
                            break;
                        }
                        e = Canon(e);
                        if (side == 0) it.a = e; else it.b = e;
                    }
                    if (math.distance(it.curve.a, it.a.Pos) > 0.01f || math.distance(it.curve.d, it.b.Pos) > 0.01f)
                    {   // move the curve ends onto the shared points, keeping the handles' offsets
                        var c = it.curve; var da = it.a.Pos - c.a; var dd = it.b.Pos - c.d;
                        c.a += da; c.b += da; c.c += dd; c.d += dd; it.curve = c;
                    }
                    items[i] = it;
                }
            }
            if (!noIntersections)
            {
                for (int i = 0; i < items.Count; i++)
                {
                    var ci = items[i].curve; var bi = MathUtils.Bounds(ci.xz);
                    foreach (var ed in edges)
                    {
                        if (ed.e == items[i].a.Entity || ed.e == items[i].b.Entity) continue;
                        if (!MathUtils.Intersect(bi, MathUtils.Bounds(ed.c.xz))) continue;
                        if (!MathUtils.Intersect(ci.xz, ed.c.xz, out float2 tt, 6)) continue;
                        if (tt.x <= 0.01f || tt.x >= 0.99f) continue;
                        if (math.abs(MathUtils.Position(ci, tt.x).y - MathUtils.Position(ed.c, tt.y).y) > 4f) { gradeSeparated++; continue; }   // passes over/under
                        var edge = EntityManager.GetComponentData<Edge>(ed.e);
                        End x;
                        if (tt.y < 0.02f || tt.y > 0.98f)
                        {
                            var n = tt.y < 0.5f ? edge.m_Start : edge.m_End;
                            x = new End { Entity = n, Pos = EntityManager.GetComponentData<Node>(n).m_Position, Snapped = "node" };
                        }
                        else x = new End { Entity = ed.e, Split = tt.y, Pos = MathUtils.Position(ed.c, tt.y), Snapped = "edge" };
                        x.Elev = ElevationAt(x);
                        splits[i].Add((tt.x, x)); crossExisting++;
                    }
                }
                for (int i = 0; i < items.Count; i++)
                    for (int j = i + 1; j < items.Count; j++)
                    {
                        var ci = items[i].curve; var cj = items[j].curve;
                        if (!MathUtils.Intersect(MathUtils.Bounds(ci.xz), MathUtils.Bounds(cj.xz))) continue;
                        if (!MathUtils.Intersect(ci.xz, cj.xz, out float2 tt, 6)) continue;
                        if (tt.x <= 0.01f || tt.x >= 0.99f || tt.y <= 0.01f || tt.y >= 0.99f) continue;
                        var p = MathUtils.Position(ci, tt.x);
                        if (math.abs(p.y - MathUtils.Position(cj, tt.y).y) > 4f) { gradeSeparated++; continue; }
                        float ground = SampleHeight(p.x, p.z);
                        float elev = math.lerp(items[i].a.Elev, items[i].b.Elev, tt.x);
                        var x = new End { Entity = Entity.Null, Pos = new float3(p.x, elev != 0 ? p.y : ground, p.z), Snapped = "cross", Elev = elev != 0 ? p.y - ground : 0 };
                        splits[i].Add((tt.x, x)); splits[j].Add((tt.y, x)); crossNew++;
                    }
            }
            // 3) cut each curve into pieces at its crossings
            var courses = new List<(Entity prefab, NetCourse course)>();
            for (int i = 0; i < items.Count; i++)
            {
                var (prefab, curve, a, b) = items[i];
                var pts = new List<(float t, End e)> { (0f, a) };
                splits[i].Sort((u, v) => u.t.CompareTo(v.t));
                foreach (var sp in splits[i]) if (sp.t - pts[pts.Count - 1].t > 0.005f) pts.Add(sp);
                pts.Add((1f, b));
                for (int k = 0; k + 1 < pts.Count; k++)
                {
                    var piece = MathUtils.Cut(curve, new float2(pts[k].t, pts[k + 1].t));
                    piece.a = pts[k].e.Pos; piece.d = pts[k + 1].e.Pos;
                    if (MathUtils.Length(piece) < 1f) continue;
                    courses.Add((prefab, new NetCourse
                    {
                        m_Curve = piece,
                        m_StartPosition = CoursePosOf(piece, pts[k].e, 0f, CoursePosFlags.IsFirst),
                        m_EndPosition = CoursePosOf(piece, pts[k + 1].e, 1f, CoursePosFlags.IsLast),
                        m_Length = MathUtils.Length(piece), m_FixedIndex = -1,
                    }));
                }
            }
            var job = new DefJob { Kind = "place_roads", DryRun = dryRun, AllowErrors = allowErrors, Definitions = courses.Count };
            job.Info["segments"] = segs.Count; job.Info["curved_segments"] = curved; job.Info["pieces"] = courses.Count;
            job.Info["snapped_ends"] = new Dictionary<string, object> { ["to_nodes"] = snappedNodes, ["to_roads"] = snappedRoads };
            if (nearMiss.Count > 0) job.Info["near_miss_ends"] = nearMiss;   // free ends within 15 m of an existing node that did not snap
            job.Info["intersections"] = new Dictionary<string, object> { ["with_existing_roads"] = crossExisting, ["between_new_roads"] = crossNew, ["new_t_junctions"] = teeNew, ["grade_separated"] = gradeSeparated };
            job.Write = cb =>
            {
                foreach (var (prefab, course) in courses)
                {
                    var e = cb.CreateEntity();
                    cb.AddComponent(e, new CreationDefinition { m_Prefab = prefab, m_RandomSeed = m_Rng.NextInt(), m_Flags = CreationFlags.SubElevation });
                    cb.AddComponent(e, default(Updated));
                    cb.AddComponent(e, course);
                }
            };
            job.OnApplied = summary =>
            {   // new edges that lie on the placed curves = what we built (excludes split pieces of pre-existing roads)
                var created = new List<Entity>();
                using (var es = m_EdgeQuery.ToEntityArray(Allocator.Temp))
                    foreach (var e in es)
                    {
                        if (before.Contains(e)) continue;
                        var c = EntityManager.GetComponentData<Curve>(e).m_Bezier;
                        if (OnAny(c.a, courses) && OnAny(c.d, courses) && OnAny(MathUtils.Position(c, 0.5f), courses)) created.Add(e);
                    }
                summary["created_edges"] = created.Count;
                summary["created_ids"] = created.GetRange(0, Math.Min(created.Count, 200)).ConvertAll(Id);
                if (recordUndo)
                {
                    var ue = new UndoEntry { Kind = "place_roads", Desc = desc, Created = created };
                    foreach (var e in created) ue.CreatedCurves.Add((EntityManager.GetComponentData<PrefabRef>(e).m_Prefab, EntityManager.GetComponentData<Curve>(e).m_Bezier));
                    PushUndo(ue); summary["undo_id"] = ue.Id;
                }
                onApplied?.Invoke();
                return summary;
            };
            return job;
        }

        bool IsRoadOrTrack(Entity e) => EntityManager.HasComponent<Road>(e) || EntityManager.HasComponent<Game.Net.TrackLane>(e)
            || EntityManager.HasComponent<Game.Net.TrainTrack>(e) || EntityManager.HasComponent<Game.Net.TramTrack>(e) || EntityManager.HasComponent<Game.Net.SubwayTrack>(e);

        float ElevationAt(End e)
        {   // elevation of an existing node / point on an existing edge (0 on the ground)
            if (e.Entity == Entity.Null || !EntityManager.HasComponent<Elevation>(e.Entity)) return 0f;
            var el = EntityManager.GetComponentData<Elevation>(e.Entity).m_Elevation;
            return EntityManager.HasComponent<Edge>(e.Entity) ? math.lerp(el.x, el.y, e.Split) : math.cmax(el);
        }

        static bool OnAny(float3 pt, List<(Entity prefab, NetCourse course)> courses)
        {
            foreach (var (_, c) in courses) if (MathUtils.Distance(c.m_Curve.xz, pt.xz, out _) < 2.5f) return true;
            return false;
        }

        End Snap(float2 xz, float tol, List<(Entity e, float3 pos)> nodes, List<(Entity e, Bezier4x3 c)> edges)
        {
            var end = new End { Pos = new float3(xz.x, SampleHeight(xz.x, xz.y), xz.y), Entity = Entity.Null };
            float best = tol;
            foreach (var n in nodes)
            {
                float d = math.distance(n.pos.xz, xz);
                if (d <= best) { best = d; end.Entity = n.e; end.Split = 0f; end.Pos = n.pos; }
            }
            if (end.Entity != Entity.Null) { end.Snapped = "node"; return end; }
            best = tol;
            foreach (var ed in edges)
            {
                float d = MathUtils.Distance(ed.c.xz, xz, out float t);
                if (d <= best && t > 0.001f && t < 0.999f) { best = d; end.Entity = ed.e; end.Split = t; end.Pos = MathUtils.Position(ed.c, t); }
            }
            if (end.Entity != Entity.Null) end.Snapped = "edge";
            return end;
        }

        static CoursePos CoursePosOf(Bezier4x3 curve, End end, float delta, CoursePosFlags ends) => new CoursePos
        {
            m_Entity = end.Entity, m_SplitPosition = end.Split, m_Position = end.Pos,
            m_Rotation = NetUtils.GetNodeRotation(MathUtils.Tangent(curve, delta)),
            m_Elevation = new float2(end.Elev), m_CourseDelta = delta, m_ParentMesh = -1,
            m_Flags = ends | CoursePosFlags.IsLeft | CoursePosFlags.IsRight,
        };

        // course describing an existing edge (used by bulldoze + replace, as in BulldozeToolSystem.AddEntity)
        NetCourse CourseOfEdge(Entity e)
        {
            var edge = EntityManager.GetComponentData<Edge>(e);
            var c = EntityManager.GetComponentData<Curve>(e).m_Bezier;
            return new NetCourse
            {
                m_Curve = c, m_Length = MathUtils.Length(c), m_FixedIndex = -1,
                m_StartPosition = new CoursePos { m_Entity = edge.m_Start, m_Position = c.a, m_Rotation = NetUtils.GetNodeRotation(MathUtils.StartTangent(c)), m_CourseDelta = 0f, m_ParentMesh = -1, m_Flags = CoursePosFlags.IsFirst },
                m_EndPosition = new CoursePos { m_Entity = edge.m_End, m_Position = c.d, m_Rotation = NetUtils.GetNodeRotation(MathUtils.EndTangent(c)), m_CourseDelta = 1f, m_ParentMesh = -1, m_Flags = CoursePosFlags.IsLast },
            };
        }

        // ================================================================== bulldoze
        (List<Entity> roads, List<Entity> buildings) ResolveTargets(Dictionary<string, object> p, bool allowBuildings)
        {
            var roads = new List<Entity>(); var buildings = new List<Entity>();
            var ids = Json.List(p, "ids");
            if (ids != null)
                foreach (var o in ids)
                {
                    var e = ParseId((string)o);
                    if (!EntityManager.Exists(e) || EntityManager.HasComponent<Deleted>(e) || EntityManager.HasComponent<Owner>(e)) continue;   // never touch sub-nets owned by buildings
                    if (EntityManager.HasComponent<Edge>(e)) roads.Add(e);
                    else if (allowBuildings && EntityManager.HasComponent<Building>(e)) buildings.Add(e);
                }
            if (HasBox(p))
            {
                var box = BoxOf(p);
                if (Json.Bool(p, "roads", true))
                    using (var es = m_EdgeQuery.ToEntityArray(Allocator.Temp))
                        foreach (var e in es)
                            if (box.In(MathUtils.Position(EntityManager.GetComponentData<Curve>(e).m_Bezier, 0.5f)) && !roads.Contains(e)) roads.Add(e);
                if (allowBuildings && Json.Bool(p, "buildings", false))
                    using (var bs = m_BuildingQuery.ToEntityArray(Allocator.Temp))
                        foreach (var b in bs)
                            if (box.In(EntityManager.GetComponentData<Transform>(b).m_Position) && !buildings.Contains(b)) buildings.Add(b);
            }
            return (roads, buildings);
        }

        DefJob BuildBulldoze(Dictionary<string, object> p, bool recordUndo)
        {
            var (roads, buildings) = ResolveTargets(p, allowBuildings: true);
            var job = MakeBulldozeJob(roads, buildings, Json.Bool(p, "dry_run", false), Json.Bool(p, "allow_errors", true), recordUndo,
                                      $"bulldoze {roads.Count} road(s), {buildings.Count} building(s)");
            job.Hold = Json.Bool(p, "hold", false);
            return job;
        }

        DefJob MakeBulldozeJob(List<Entity> roads, List<Entity> buildings, bool dryRun, bool allowErrors, bool recordUndo, string desc, Action onApplied = null)
        {
            var roadDefs = new List<(Entity e, NetCourse course)>();
            var undo = new UndoEntry { Kind = "bulldoze", Desc = desc, BuildingsDeleted = buildings.Count };
            foreach (var e in roads)
            {
                roadDefs.Add((e, CourseOfEdge(e)));
                undo.Deleted.Add((EntityManager.GetComponentData<PrefabRef>(e).m_Prefab, EntityManager.GetComponentData<Curve>(e).m_Bezier));
            }
            // v0.44: a building is deleted the way BulldozeToolSystem.AddEntity does it - the building itself, then (with an
            // OwnerDefinition) every sub-net node / edge (recursive), every sub-area (+ buildings inside areas) and every
            // installed upgrade. 0.43 deleted only the building: big assets left their elevated sub-nets (pillars) and
            // sub-objects behind as orphans
            var del = new BulldozeSet();
            foreach (var b in buildings) CollectObjectDelete(del, b, default(OwnerDefinition));
            var job = new DefJob { Kind = "bulldoze", DryRun = dryRun, AllowErrors = allowErrors, Definitions = roadDefs.Count + del.Count };
            job.Info["roads"] = roadDefs.Count; job.Info["buildings"] = buildings.Count;
            if (del.Count > buildings.Count) { job.Info["sub_nets"] = del.Nets.Count; job.Info["sub_areas"] = del.Areas.Count; job.Info["upgrades"] = del.Objects.Count - buildings.Count; }
            job.Write = cb =>
            {
                foreach (var (e, course) in roadDefs)
                {
                    var d = cb.CreateEntity();
                    cb.AddComponent(d, default(Updated));
                    cb.AddComponent(d, course);
                    cb.AddComponent(d, new CreationDefinition { m_Original = e, m_Flags = CreationFlags.Delete });
                }
                WriteBulldozeSet(cb, del);
            };
            job.OnApplied = summary =>
            {
                if (recordUndo) { PushUndo(undo); summary["undo_id"] = undo.Id; }
                if (buildings.Count > 0) summary["note"] = "undo rebuilds bulldozed roads only; buildings cannot be restored";
                onApplied?.Invoke();
                return summary;
            };
            return job;
        }

        // ================================================================== building delete = BulldozeToolSystem.AddEntity
        sealed class BulldozeSet
        {
            public readonly List<(Entity e, ObjectDefinition obj, OwnerDefinition owner)> Objects = new List<(Entity, ObjectDefinition, OwnerDefinition)>();
            public readonly List<(Entity e, NetCourse course, OwnerDefinition owner)> Nets = new List<(Entity, NetCourse, OwnerDefinition)>();
            public readonly List<(Entity e, CreationFlags flags, OwnerDefinition owner, Game.Areas.Node[] nodes, Game.Tools.LocalNodeCache[] cache)> Areas =
                new List<(Entity, CreationFlags, OwnerDefinition, Game.Areas.Node[], Game.Tools.LocalNodeCache[])>();
            public readonly HashSet<Entity> Seen = new HashSet<Entity>();
            public int Count => Objects.Count + Nets.Count + Areas.Count;
        }

        // an object (building / upgrade) + its sub-nets, sub-areas and installed upgrades; owner = the parent's definition
        void CollectObjectDelete(BulldozeSet set, Entity e, OwnerDefinition owner)
        {
            if (!set.Seen.Add(e) || !EntityManager.HasComponent<Transform>(e)) return;
            var t = EntityManager.GetComponentData<Transform>(e);
            var od = new ObjectDefinition { m_Position = t.m_Position, m_Rotation = t.m_Rotation, m_ParentMesh = -1, m_Probability = 100, m_PrefabSubIndex = -1 };
            if (EntityManager.HasComponent<Game.Objects.Elevation>(e))
            {
                var el = EntityManager.GetComponentData<Game.Objects.Elevation>(e);
                od.m_Elevation = el.m_Elevation; od.m_ParentMesh = Game.Objects.ObjectUtils.GetSubParentMesh(el.m_Flags);
            }
            if (EntityManager.HasComponent<LocalTransformCache>(e))
            {
                var lc = EntityManager.GetComponentData<LocalTransformCache>(e);
                od.m_LocalPosition = lc.m_Position; od.m_LocalRotation = lc.m_Rotation; od.m_ParentMesh = lc.m_ParentMesh;
                od.m_GroupIndex = lc.m_GroupIndex; od.m_Probability = lc.m_Probability; od.m_PrefabSubIndex = lc.m_PrefabSubIndex;
            }
            else if (owner.m_Prefab != Entity.Null)
            {
                var lt = Game.Objects.ObjectUtils.WorldToLocal(Game.Objects.ObjectUtils.InverseTransform(new Transform(owner.m_Position, owner.m_Rotation)), t);
                od.m_LocalPosition = lt.m_Position; od.m_LocalRotation = lt.m_Rotation;
            }
            else { od.m_LocalPosition = t.m_Position; od.m_LocalRotation = t.m_Rotation; }
            set.Objects.Add((e, od, owner));
            var mine = new OwnerDefinition { m_Prefab = EntityManager.GetComponentData<PrefabRef>(e).m_Prefab, m_Position = t.m_Position, m_Rotation = t.m_Rotation };
            if (EntityManager.HasBuffer<Game.Net.SubNet>(e)) CollectSubNets(set, e, mine);
            if (EntityManager.HasBuffer<Game.Areas.SubArea>(e))
            {
                var areas = EntityManager.GetBuffer<Game.Areas.SubArea>(e, true);
                var list = new List<Entity>(); for (int i = 0; i < areas.Length; i++) list.Add(areas[i].m_Area);
                foreach (var a in list)
                {
                    if (!set.Seen.Add(a) || !EntityManager.HasBuffer<Game.Areas.Node>(a)) continue;
                    var flags = CreationFlags.Delete; if (!EntityManager.HasComponent<Game.Areas.Lot>(a)) flags |= CreationFlags.Hidden;
                    var nodes = EntityManager.GetBuffer<Game.Areas.Node>(a, true).AsNativeArray().ToArray();
                    var cache = EntityManager.HasBuffer<Game.Tools.LocalNodeCache>(a) ? EntityManager.GetBuffer<Game.Tools.LocalNodeCache>(a, true).AsNativeArray().ToArray() : null;
                    set.Areas.Add((a, flags, mine, nodes, cache));
                    if (EntityManager.HasBuffer<Game.Objects.SubObject>(a))                   // buildings standing in the area
                    {
                        var subs = EntityManager.GetBuffer<Game.Objects.SubObject>(a, true); var bl = new List<Entity>();
                        for (int k = 0; k < subs.Length; k++) if (EntityManager.HasComponent<Building>(subs[k].m_SubObject)) bl.Add(subs[k].m_SubObject);
                        foreach (var sb in bl) CollectObjectDelete(set, sb, default(OwnerDefinition));
                    }
                }
            }
            if (owner.m_Prefab == Entity.Null && EntityManager.HasBuffer<InstalledUpgrade>(e))     // top-level: its upgrades go too
            {
                var ups = EntityManager.GetBuffer<InstalledUpgrade>(e, true); var ul = new List<Entity>();
                for (int i = 0; i < ups.Length; i++) ul.Add(ups[i].m_Upgrade);
                foreach (var u in ul) CollectObjectDelete(set, u, mine);
            }
        }

        // BulldozeToolSystem.AddSubNets: lone nodes (no owned edge starts / ends there) as zero-length courses, edges as their
        // curve (game-style CoursePos: no flags), recursing into sub-nets of sub-nets
        void CollectSubNets(BulldozeSet set, Entity owner, OwnerDefinition od)
        {
            var buf = EntityManager.GetBuffer<Game.Net.SubNet>(owner, true); var list = new List<Entity>();
            for (int i = 0; i < buf.Length; i++) list.Add(buf[i].m_SubNet);
            foreach (var sn in list)
            {
                if (EntityManager.HasComponent<Game.Net.Node>(sn))
                {
                    if (HasOwnedEdgeAt(sn, owner) || !set.Seen.Add(sn)) continue;
                    var n = EntityManager.GetComponentData<Game.Net.Node>(sn);
                    set.Nets.Add((sn, new NetCourse
                    {
                        m_Curve = new Bezier4x3(n.m_Position, n.m_Position, n.m_Position, n.m_Position), m_Length = 0f, m_FixedIndex = -1,
                        m_StartPosition = new CoursePos { m_Entity = sn, m_Position = n.m_Position, m_Rotation = n.m_Rotation, m_CourseDelta = 0f },
                        m_EndPosition = new CoursePos { m_Entity = sn, m_Position = n.m_Position, m_Rotation = n.m_Rotation, m_CourseDelta = 1f },
                    }, od));
                }
                else if (EntityManager.HasComponent<Edge>(sn))
                {
                    var edge = EntityManager.GetComponentData<Edge>(sn);
                    if (set.Seen.Contains(edge.m_Start) || set.Seen.Contains(edge.m_End) || !set.Seen.Add(sn)) continue;
                    var c = EntityManager.GetComponentData<Curve>(sn).m_Bezier;
                    var course = new NetCourse
                    {
                        m_Curve = c, m_Length = MathUtils.Length(c), m_FixedIndex = -1,
                        m_StartPosition = new CoursePos { m_Entity = edge.m_Start, m_Position = c.a, m_Rotation = NetUtils.GetNodeRotation(MathUtils.StartTangent(c)), m_CourseDelta = 0f },
                        m_EndPosition = new CoursePos { m_Entity = edge.m_End, m_Position = c.d, m_Rotation = NetUtils.GetNodeRotation(MathUtils.EndTangent(c)), m_CourseDelta = 1f },
                    };
                    set.Nets.Add((sn, course, od));
                    if (EntityManager.HasBuffer<Game.Net.SubNet>(sn))
                        CollectSubNets(set, sn, new OwnerDefinition { m_Prefab = EntityManager.GetComponentData<PrefabRef>(sn).m_Prefab, m_Position = c.a, m_Rotation = new float4(c.d, 0f) });
                }
            }
        }

        bool HasOwnedEdgeAt(Entity node, Entity owner)
        {
            if (!EntityManager.HasBuffer<ConnectedEdge>(node)) return false;
            var ce = EntityManager.GetBuffer<ConnectedEdge>(node, true);
            for (int i = 0; i < ce.Length; i++)
            {
                var e = ce[i].m_Edge; var ed = EntityManager.GetComponentData<Edge>(e);
                if ((ed.m_Start == node || ed.m_End == node) && EntityManager.HasComponent<Owner>(e) && EntityManager.GetComponentData<Owner>(e).m_Owner == owner) return true;
            }
            return false;
        }

        static void WriteBulldozeSet(EntityCommandBuffer cb, BulldozeSet set)
        {
            foreach (var (e, obj, owner) in set.Objects)
            {
                var d = cb.CreateEntity(); cb.AddComponent(d, default(Updated));
                if (owner.m_Prefab != Entity.Null) cb.AddComponent(d, owner);
                cb.AddComponent(d, obj);
                cb.AddComponent(d, new CreationDefinition { m_Original = e, m_Flags = CreationFlags.Delete });
            }
            foreach (var (e, course, owner) in set.Nets)
            {
                var d = cb.CreateEntity(); cb.AddComponent(d, default(Updated));
                cb.AddComponent(d, new CreationDefinition { m_Original = e, m_Flags = CreationFlags.Delete });
                if (owner.m_Prefab != Entity.Null) cb.AddComponent(d, owner);
                cb.AddComponent(d, course);
            }
            foreach (var (e, flags, owner, nodes, cache) in set.Areas)
            {
                var d = cb.CreateEntity();
                cb.AddComponent(d, new CreationDefinition { m_Original = e, m_Flags = flags });
                cb.AddComponent(d, default(Updated));
                if (owner.m_Prefab != Entity.Null) cb.AddComponent(d, owner);
                var nb = cb.AddBuffer<Game.Areas.Node>(d); foreach (var n in nodes) nb.Add(n);
                if (cache != null) { var cbuf = cb.AddBuffer<Game.Tools.LocalNodeCache>(d); foreach (var c in cache) cbuf.Add(c); }
            }
        }

        // ================================================================== replace road type (upgrade/downgrade in place)
        DefJob BuildReplace(Dictionary<string, object> p, bool recordUndo)
        {
            string type = Json.Str(p, "type", null) ?? throw new ArgumentException("type (new road type) required");
            if (!RoadPrefabMap().TryGetValue(type, out var prefab)) throw new ArgumentException($"unknown road type '{type}'");
            var (roads, _) = ResolveTargets(p, allowBuildings: false);
            var targets = new List<(Entity e, Entity prefab)>();
            foreach (var r in roads) targets.Add((r, prefab));
            var job = MakeReplaceJob(targets, Json.Bool(p, "dry_run", false), Json.Bool(p, "allow_errors", false), recordUndo, $"replace {roads.Count} road(s) with {type}");
            job.Hold = Json.Bool(p, "hold", false);
            return job;
        }

        DefJob MakeReplaceJob(List<(Entity e, Entity prefab)> targets, bool dryRun, bool allowErrors, bool recordUndo, string desc, Action onApplied = null)
        {
            var defs = new List<(Entity e, Entity prefab, NetCourse course)>();
            var undo = new UndoEntry { Kind = "replace_roads", Desc = desc };
            foreach (var (e, prefab) in targets)
            {
                var course = CourseOfEdge(e);
                defs.Add((e, prefab, course));
                undo.Replaced.Add((MathUtils.Position(course.m_Curve, 0.5f), EntityManager.GetComponentData<PrefabRef>(e).m_Prefab));
            }
            var job = new DefJob { Kind = "replace_roads", DryRun = dryRun, AllowErrors = allowErrors, Definitions = defs.Count };
            job.Info["roads"] = defs.Count;
            job.Write = cb =>
            {
                foreach (var (e, prefab, course) in defs)
                {
                    var d = cb.CreateEntity();
                    cb.AddComponent(d, new CreationDefinition { m_Original = e, m_Prefab = prefab, m_RandomSeed = m_Rng.NextInt(), m_Flags = CreationFlags.Align | CreationFlags.SubElevation });
                    cb.AddComponent(d, default(Updated));
                    cb.AddComponent(d, course);
                }
            };
            job.OnApplied = summary => { if (recordUndo) { PushUndo(undo); summary["undo_id"] = undo.Id; } onApplied?.Invoke(); return summary; };
            return job;
        }

        // ================================================================== zoning (marquee over any quad)
        // v0.35: `areas: [{corners, zone_type?, overwrite?}, ...]` zones many quads in ONE DefJob (one preview/apply, one
        // tool-slot borrow, one undo entry) - per-quad calls made a 400-lot rezone take minutes and flicker the player's tool.
        // The single `corners` + `zone_type` form still works. Overlapping quads with different targets are refused (the
        // order same-frame Zoning definitions apply in is undefined); undo records each cell's original zone once.
        DefJob BuildZone(Dictionary<string, object> p, bool recordUndo)
        {
            var zones = ZonePrefabMap();
            bool overwriteDefault = Json.Bool(p, "overwrite", true);
            var specs = new List<(Quad2 q2, Entity prefab, ZoneType target, bool dezone, bool overwrite, string type)>();
            void AddSpec(List<object> corners, string type, bool overwrite)
            {
                if (corners == null || corners.Count != 4) throw new ArgumentException("corners: 4 [x,z] points (in order around the area) required");
                if (type == null) throw new ArgumentException("zone_type required (a name from list_zone_types, or \"none\" to dezone)");
                bool dz = type.Equals("none", StringComparison.OrdinalIgnoreCase);
                Entity prefab;
                if (dz) { if (zones.Count == 0) throw new InvalidOperationException("no zone prefabs"); prefab = new List<Entity>(zones.Values)[0]; }
                else if (!zones.TryGetValue(type, out prefab)) throw new ArgumentException($"unknown zone type '{type}' (use list_zone_types)");
                var target = dz ? ZoneType.None : EntityManager.GetComponentData<ZoneData>(prefab).m_ZoneType;
                specs.Add((new Quad2(XZ(corners[0]), XZ(corners[1]), XZ(corners[2]), XZ(corners[3])), prefab, target, dz, overwrite, dz ? "none" : type));
            }
            var areas = Json.List(p, "areas");
            if (areas != null)
            {
                if (areas.Count == 0) throw new ArgumentException("areas: empty list");
                if (areas.Count > 1000) throw new ArgumentException("areas: at most 1000 quads per call (split the batch)");
                string defType = Json.Str(p, "zone_type", null);
                foreach (var o in areas)
                {
                    if (!(o is Dictionary<string, object> a)) throw new ArgumentException("areas: each item is {corners, zone_type?, overwrite?}");
                    AddSpec(Json.List(a, "corners"), Json.Str(a, "zone_type", defType), Json.Bool(a, "overwrite", overwriteDefault));
                }
            }
            else AddSpec(Json.List(p, "corners"), Json.Str(p, "zone_type", null), overwriteDefault);

            // snapshot the cells this will change (for undo + a precise count): one pass over the blocks for all quads
            var types = new List<string>(); foreach (var s in specs) if (!types.Contains(s.type)) types.Add(s.type);
            var undo = new UndoEntry { Kind = "zone", Desc = specs.Count == 1 ? $"{(specs[0].dezone ? "dezone" : "zone " + specs[0].type)} area"
                                                                               : $"zone {specs.Count} areas ({string.Join(", ", types)})" };
            var qbs = new Bounds2[specs.Count];
            var all = MathUtils.Bounds(specs[0].q2);
            for (int i = 0; i < specs.Count; i++) { qbs[i] = MathUtils.Bounds(specs[i].q2); all |= qbs[i]; }
            var claimed = new Dictionary<(Entity, int), int>();                  // cell -> first area that covers it
            var hits = new List<int>();
            using (var blocks = m_AllBlockQuery.ToEntityArray(Allocator.Temp))
                foreach (var blk in blocks)
                {
                    var block = EntityManager.GetComponentData<Block>(blk);
                    var bb = ZoneUtils.CalculateBounds(block);
                    if (!MathUtils.Intersect(bb, all)) continue;
                    hits.Clear();
                    for (int i = 0; i < specs.Count; i++) if (MathUtils.Intersect(bb, qbs[i])) hits.Add(i);
                    if (hits.Count == 0) continue;
                    var cells = EntityManager.GetBuffer<Cell>(blk, true);
                    for (int y = 0; y < block.m_Size.y; y++)
                        for (int x = 0; x < block.m_Size.x; x++)
                        {
                            int idx = y * block.m_Size.x + x;
                            if (idx >= cells.Length) continue;
                            var cell = cells[idx];
                            if ((cell.m_State & CellFlags.Visible) == 0) continue;     // the game only zones visible cells
                            var cp = ZoneUtils.GetCellPosition(block, new int2(x, y)).xz;
                            foreach (int i in hits)
                            {
                                var s = specs[i];
                                if (!MathUtils.Intersect(s.q2, cp)) continue;
                                if (claimed.TryGetValue((blk, idx), out int j))
                                {
                                    if (specs[j].type != s.type || specs[j].overwrite != s.overwrite)
                                        throw new ArgumentException($"areas {j} and {i} overlap with different zone_type/overwrite near ({cp.x:F0}, {cp.y:F0}) - split them or make them agree");
                                    continue;
                                }
                                claimed[(blk, idx)] = i;
                                bool changes = s.dezone ? !cell.m_Zone.Equals(ZoneType.None)
                                                        : (s.overwrite || cell.m_Zone.Equals(ZoneType.None)) && !cell.m_Zone.Equals(s.target);
                                if (changes) undo.Cells.Add((blk, idx, cell.m_Zone));
                            }
                        }
                }
            var job = new DefJob { Kind = "zone", DryRun = Json.Bool(p, "dry_run", false), AllowErrors = true, Definitions = specs.Count, Hold = Json.Bool(p, "hold", false) };
            job.Info["zone_type"] = specs.Count == 1 ? (object)specs[0].type : types;
            job.Info["cells_to_change"] = undo.Cells.Count;
            if (specs.Count == 1) job.Info["overwrite"] = specs[0].overwrite; else job.Info["areas"] = specs.Count;
            var lifted = new List<Quad3>(specs.Count);
            foreach (var s in specs) lifted.Add(new Quad3(Lift(s.q2.a), Lift(s.q2.b), Lift(s.q2.c), Lift(s.q2.d)));
            job.Write = cb =>
            {
                for (int i = 0; i < specs.Count; i++)
                {
                    var s = specs[i];
                    var flags = ZoningFlags.Marquee | (s.dezone ? ZoningFlags.Dezone | ZoningFlags.Overwrite : ZoningFlags.Zone | (s.overwrite ? ZoningFlags.Overwrite : 0));
                    var d = cb.CreateEntity();
                    cb.AddComponent(d, new CreationDefinition { m_Prefab = s.prefab });
                    cb.AddComponent(d, default(Updated));
                    cb.AddComponent(d, new Zoning { m_Flags = flags, m_Position = lifted[i] });
                }
            };
            job.OnApplied = summary => { if (recordUndo && undo.Cells.Count > 0) { PushUndo(undo); summary["undo_id"] = undo.Id; } return summary; };
            return job;
        }

        float3 Lift(float2 xz) => new float3(xz.x, SampleHeight(xz.x, xz.y), xz.y);

        // ================================================================== undo
        void Undo(Command cmd)
        {
            if (m_Undo.Count == 0) { cmd.Complete(new Dictionary<string, object> { ["undone"] = false, ["reason"] = "nothing to undo" }); return; }
            var u = m_Undo[m_Undo.Count - 1];
            // v0.33: entries replay by position (rebuild roads, retarget edges within 1.5 m) - never into another city load.
            // OnGamePreload clears the stack; this is the second guard.
            if (u.Gen != s_LoadGeneration)
            {
                int n = m_Undo.Count; m_Undo.Clear();
                cmd.Fail($"undo refused: #{u.Id} ({u.Desc}) was recorded in an earlier city load (save '{u.Save ?? "?"}'); cleared {n} stale entries");
                return;
            }
            int expect = (int)Json.Num(cmd.Params, "expect_id", -1);
            if (expect >= 0 && expect != u.Id) { cmd.Fail($"undo refused: the newest change is #{u.Id} ({u.Desc}), not #{expect}"); return; }
            void Pop() => m_Undo.Remove(u);
            switch (u.Kind)
            {
                case "place_roads":
                {
                    bool IsLive(Entity e) => EntityManager.Exists(e) && !EntityManager.HasComponent<Deleted>(e) && EntityManager.HasComponent<Edge>(e);
                    var alive = u.Created.FindAll(IsLive);
                    // edges that died since (bulldozed + rebuilt by another undo, replaced, split by a new junction) come back
                    // as new entities: find them by shape - same prefab, both ends and midpoint on a recorded curve
                    var lost = new List<(Entity prefab, Bezier4x3 curve)>();
                    for (int i = 0; i < u.Created.Count && i < u.CreatedCurves.Count; i++) if (!IsLive(u.Created[i])) lost.Add(u.CreatedCurves[i]);
                    if (lost.Count > 0)
                        using (var es = m_EdgeQuery.ToEntityArray(Allocator.Temp))
                            foreach (var e in es)
                            {
                                if (alive.Contains(e)) continue;
                                var c = EntityManager.GetComponentData<Curve>(e).m_Bezier; var pf = EntityManager.GetComponentData<PrefabRef>(e).m_Prefab;
                                foreach (var (prefab, curve) in lost)
                                    if (pf == prefab && MathUtils.Distance(curve.xz, c.a.xz, out _) < 1.5f && MathUtils.Distance(curve.xz, c.d.xz, out _) < 1.5f
                                        && MathUtils.Distance(curve.xz, MathUtils.Position(c, 0.5f).xz, out _) < 1.5f) { alive.Add(e); break; }
                            }
                    if (alive.Count == 0) { Pop(); cmd.Complete(new Dictionary<string, object> { ["undone"] = true, ["note"] = "roads were already gone" }); return; }
                    var job = MakeBulldozeJob(alive, new List<Entity>(), false, true, false, "undo: " + u.Desc, Pop);
                    job.Info["undo_of"] = u.Desc; m_Tool.Submit(cmd, job); return;
                }
                case "place_building":
                {
                    var alive = u.Created.FindAll(e => EntityManager.Exists(e) && !EntityManager.HasComponent<Deleted>(e));
                    if (alive.Count == 0) { Pop(); cmd.Complete(new Dictionary<string, object> { ["undone"] = true, ["note"] = "building was already gone" }); return; }
                    var job = MakeBulldozeJob(new List<Entity>(), alive, false, true, false, "undo: " + u.Desc, Pop);
                    job.Info["undo_of"] = u.Desc; m_Tool.Submit(cmd, job); return;
                }
                case "bulldoze":
                {
                    if (u.Deleted.Count == 0) { Pop(); cmd.Complete(new Dictionary<string, object> { ["undone"] = true, ["note"] = "only buildings were bulldozed; they cannot be restored" }); return; }
                    var segs = u.Deleted.ConvertAll(d => new PlaceSeg { Prefab = d.prefab, Exact = d.curve });
                    var job = MakePlaceJob(segs, false, true, false, 1.0f, "undo: " + u.Desc, Pop, noIntersections: true);
                    job.Info["undo_of"] = u.Desc; m_Tool.Submit(cmd, job); return;
                }
                case "replace_roads":
                {
                    var targets = new List<(Entity, Entity)>();
                    using (var es = m_EdgeQuery.ToEntityArray(Allocator.Temp))
                        foreach (var (mid, oldPrefab) in u.Replaced)
                            foreach (var e in es)
                                if (MathUtils.Distance(EntityManager.GetComponentData<Curve>(e).m_Bezier.xz, mid.xz, out _) < 1.5f) { targets.Add((e, oldPrefab)); break; }
                    if (targets.Count == 0) { Pop(); cmd.Complete(new Dictionary<string, object> { ["undone"] = true, ["note"] = "roads no longer exist" }); return; }
                    var job = MakeReplaceJob(targets, false, true, false, "undo: " + u.Desc, Pop);
                    job.Info["undo_of"] = u.Desc; m_Tool.Submit(cmd, job); return;
                }
                case "road_align":
                {
                    UndoRoadAlign(u); Pop();
                    cmd.Complete(new Dictionary<string, object> { ["undone"] = true, ["undo_of"] = u.Desc, ["edges_restored"] = u.RoadFlags.Count });
                    return;
                }
                case "zone":
                {
                    int restored = 0; var touched = new HashSet<Entity>();
                    foreach (var (blk, idx, zone) in u.Cells)
                    {
                        if (!EntityManager.Exists(blk) || !EntityManager.HasBuffer<Cell>(blk)) continue;
                        var buf = EntityManager.GetBuffer<Cell>(blk);
                        if (idx >= buf.Length) continue;
                        var c = buf[idx]; c.m_Zone = zone; buf[idx] = c; restored++; touched.Add(blk);
                    }
                    // v0.33: Updated through the end-frame barrier, so next frame's zone update (Modification phases) sees it -
                    // added directly here, at the tail of MainLoop, it was stripped at Cleanup before anything read it
                    if (touched.Count > 0)
                    {
                        var tags = World.GetOrCreateSystemManaged<Game.EndFrameBarrier>().CreateCommandBuffer();
                        foreach (var blk in touched) tags.AddComponent<Updated>(blk);
                    }
                    Pop();
                    cmd.Complete(new Dictionary<string, object> { ["undone"] = true, ["undo_of"] = u.Desc, ["cells_restored"] = restored });
                    return;
                }
                case "create_district":
                {
                    var d = u.Districts.Count > 0 ? u.Districts[0] : Entity.Null;
                    if (!LiveDistricts().Contains(d)) { Pop(); cmd.Complete(new Dictionary<string, object> { ["undone"] = true, ["note"] = "district was already gone" }); return; }
                    var job = MakeDeleteDistrictJob(d, false, false, Pop);
                    job.Info["undo_of"] = u.Desc; m_Tool.Submit(cmd, job); return;
                }
                case "delete_district":
                {
                    var (outline, name) = u.DistrictShapes[0];
                    var p = new Dictionary<string, object> { ["name"] = name,
                        ["polygon"] = outline.ConvertAll(q => (object)new List<object> { (double)q.x, (double)q.y }) };
                    var job = BuildDistrict(p, false, Pop);
                    job.Info["undo_of"] = u.Desc; job.Info["note"] = "outline and name restored; re-apply policies if it had any";
                    m_Tool.Submit(cmd, job); return;
                }
            }
            cmd.Fail("unknown undo entry " + u.Kind);
        }
    }
}
