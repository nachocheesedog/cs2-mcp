// Executes bridge commands on the game's main thread (MainLoop phase). Queries/controls answer immediately;
// world edits (roads, zoning, bulldozing, replacing, undo) are turned into DefJobs and run by McpToolSystem.
using System;
using System.Collections.Generic;
using Colossal.Mathematics;
using Game;
using Game.Buildings;
using Game.City;
using Game.Common;
using Game.Net;
using Game.Prefabs;
using Game.Rendering;
using Game.SceneFlow;
using Game.Simulation;
using Game.Tools;
using Game.Zones;
using Unity.Collections;
using Unity.Entities;
using Unity.Mathematics;
using Transform = Game.Objects.Transform;

namespace CS2MCP
{
    public partial class McpBridgeSystem : GameSystemBase
    {
        PrefabSystem m_Prefabs;
        TerrainSystem m_Terrain;
        CitySystem m_City;
        SimulationSystem m_Sim;
        CameraUpdateSystem m_Camera;
        McpToolSystem m_Tool;
        EntityQuery m_NetPrefabQuery, m_ZonePrefabQuery, m_EdgeQuery, m_NodeQuery, m_BuildingQuery;

        protected override void OnCreate()
        {
            base.OnCreate();
            m_Prefabs = World.GetOrCreateSystemManaged<PrefabSystem>();
            m_Terrain = World.GetOrCreateSystemManaged<TerrainSystem>();
            m_City = World.GetOrCreateSystemManaged<CitySystem>();
            m_Sim = World.GetOrCreateSystemManaged<SimulationSystem>();
            m_Camera = World.GetOrCreateSystemManaged<CameraUpdateSystem>();
            m_Tool = World.GetOrCreateSystemManaged<McpToolSystem>();
            m_NetPrefabQuery = GetEntityQuery(ComponentType.ReadOnly<PrefabData>(), ComponentType.ReadOnly<NetGeometryData>());
            m_ZonePrefabQuery = GetEntityQuery(ComponentType.ReadOnly<PrefabData>(), ComponentType.ReadOnly<ZoneData>());
            m_EdgeQuery = Live(ComponentType.ReadOnly<Edge>(), ComponentType.ReadOnly<Curve>(), ComponentType.ReadOnly<PrefabRef>());
            m_NodeQuery = Live(ComponentType.ReadOnly<Node>());
            m_BuildingQuery = Live(ComponentType.ReadOnly<Building>(), ComponentType.ReadOnly<Transform>(), ComponentType.ReadOnly<PrefabRef>());
            CreatePerception();
            CreateDistricts();
            EnsureSaveHook();
            Log.Info("McpBridgeSystem created");
        }

        // ------------------------------------------------------------------ city load boundary (v0.33)
        // The world (and this system) outlives every city. Per-city state is cleared here: the undo stack (its entries
        // rebuild roads / retarget edges by position and would edit whichever city is loaded now - the test save is a copy of
        // the main city), deferred actions, a running terraform, an MCP tool job. Exceptions must not escape: the game
        // disables a system whose preload/loaded hook throws.
        static int s_LoadGeneration;
        protected override void OnGamePreload(Colossal.Serialization.Entities.Purpose purpose, GameMode mode)
        {
            base.OnGamePreload(purpose, mode);
            try
            {
                s_LoadGeneration++;
                int undo = m_Undo.Count;
                m_Undo.Clear(); m_Deferred.Clear();
                s_LoadedSave = null; s_LoadPurpose = purpose.ToString();
                var tr = m_Terraform; m_Terraform = null;
                tr?.Cmd.Fail("a city load started - terraform run abandoned");
                if (m_Tool.Busy) m_Tool.ForceReset("a city load started", restoreTool: false);
                Log.Info($"game preload ({purpose}, {mode}): cleared {undo} undo entries, generation {s_LoadGeneration}");
            }
            catch (Exception e) { Log.Info("OnGamePreload: " + e); }
        }

        protected override void OnGameLoaded(Colossal.Serialization.Entities.Context serializationContext)
        {
            base.OnGameLoaded(serializationContext);
            try
            {
                s_LoadedSave = SaveNameOf(serializationContext.instigatorGuid);
                Log.Info($"game loaded ({serializationContext.purpose}): save_name={s_LoadedSave ?? "(none)"}");
            }
            catch (Exception e) { s_LoadedSave = null; Log.Info("OnGameLoaded: " + e); }
            // v0.39: the game's upkeep cache aborts on a prefab with two Money upkeep entries -> fill in what it skipped (ModeCache.cs)
            try { var mc = SyncModeCache(); if (mc["added"] is int n && n > 0) Log.Info("OnGameLoaded mode cache: " + Json.Write(mc)); }
            catch (Exception e) { Log.Info("OnGameLoaded mode cache: " + e); }
        }

        // commands that stay allowed while a save is loading (everything else would run against a half-deserialized world)
        static readonly HashSet<string> kAllowedWhileLoading = new HashSet<string>
            { "ping", "list_saves", "history", "quit_game", "list_road_types", "list_zone_types", "prefab_overrides" };
        static bool GameLoading => GameManager.instance != null && GameManager.instance.isGameLoading;

        // a pump that throws (e.g. a game member renamed by an update) is logged once instead of throwing out of OnUpdate
        // every frame (Critical dialog + paused simulation each frame)
        readonly HashSet<string> m_PumpErrors = new HashSet<string>();
        void PumpFailed(string pump, Exception e)
        {
            if (m_PumpErrors.Add(pump + e.GetType().Name)) Log.Info($"{pump} pump failed: {e}");
        }

        EntityQuery Live(params ComponentType[] all) => GetEntityQuery(new EntityQueryDesc
        {
            All = all, None = new[] { ComponentType.ReadOnly<Deleted>(), ComponentType.ReadOnly<Temp>(), ComponentType.ReadOnly<Owner>() }   // Owner = sub-nets inside buildings/lots
        });

        // work that must run in this system's own update (not inside the tool system's apply stage), e.g. policy events
        readonly List<Action> m_Deferred = new List<Action>();

        protected override void OnUpdate()
        {
            try { PumpScreenshot(); }
            catch (Exception e) { PumpFailed("screenshot", e); var s = m_Shot; m_Shot = null; if (s != null) { try { RestoreAfterShot(s); } catch { } s.Cmd.Fail("screenshot failed: " + e.Message); } }
            try { PumpTerraform(); }
            catch (Exception e) { PumpFailed("terraform", e); var r = m_Terraform; m_Terraform = null; r?.Cmd.Fail("terraform failed: " + e.Message); }
            try { PumpImport(); }
            catch (Exception e) { PumpFailed("import", e); var j = m_Import; m_Import = null; j?.Cmd.Fail("asset import failed: " + e.Message); }
            try { PumpOverrides(); } catch (Exception e) { PumpFailed("overrides", e); }
            try { PumpBrands(); } catch (Exception e) { PumpFailed("brands", e); }
            try { m_Tool.Watchdog(); } catch (Exception e) { PumpFailed("watchdog", e); }
            if (m_Deferred.Count > 0)
            {
                var run = new List<Action>(m_Deferred); m_Deferred.Clear();
                foreach (var act in run) { try { act(); } catch (Exception e) { Log.Info("deferred action failed: " + e); } }
            }
            int budget = 16;
            while (budget-- > 0 && Bridge.Inbox.TryDequeue(out var cmd))
            {
                if (!cmd.TryStart()) { Log.Info($"{cmd.Method}: dropped (its caller timed out before the game got to it)"); continue; }
                try
                {
                    if (GameLoading && !kAllowedWhileLoading.Contains(cmd.Method))
                        cmd.Fail("the game is loading a save - retry once status shows loading=false and city_loaded=true");
                    else
                    {
                        // written (and flushed) before the call runs, so a crash leaves the culprit as the last ">" line without a "<"
                        bool quiet = cmd.Method == "status";
                        var t0 = DateTime.UtcNow;
                        if (!quiet) Log.Info("> " + cmd.Method + CallSummary(cmd.Params));
                        Dispatch(cmd);
                        if (!quiet) Log.Info($"< {cmd.Method} {(DateTime.UtcNow - t0).TotalMilliseconds:0} ms");
                    }
                }
                catch (Exception e) { Log.Info($"{cmd.Method} failed: {e}"); cmd.Fail(e.GetType().Name + ": " + e.Message); }
            }
        }

        // short description of a call's size for the crash trail: flags and list lengths, never the full payload
        static string CallSummary(Dictionary<string, object> p)
        {
            if (p == null || p.Count == 0) return "";
            var sb = new System.Text.StringBuilder();
            foreach (var kv in p)
            {
                if (sb.Length > 160) { sb.Append(" ..."); break; }
                sb.Append(' ').Append(kv.Key).Append('=');
                if (kv.Value is System.Collections.ICollection c) sb.Append('[').Append(c.Count).Append(']');
                else if (kv.Value is string str) sb.Append(str.Length > 24 ? str.Substring(0, 24) + "~" : str);
                else sb.Append(kv.Value);
            }
            return sb.ToString();
        }

        bool CityLoaded => m_City.City != Entity.Null && EntityManager.Exists(m_City.City);
        void RequireCity() { if (!CityLoaded) throw new InvalidOperationException("no city loaded - load or start a city first"); }

        void Dispatch(Command cmd)
        {
            var p = cmd.Params;
            switch (cmd.Method)
            {
                case "ping": cmd.Complete(Ping()); return;
                case "list_saves": cmd.Complete(ListSaves(p)); return;
                case "load_save": cmd.Complete(LoadSave(p)); return;
                case "main_menu": cmd.Complete(MainMenu()); return;
                case "quit_game": cmd.Complete(QuitGame()); return;
                case "mesh_materials": cmd.Complete(MeshMaterials(p)); return;
                case "texture_export": cmd.Complete(TextureExport(p)); return;
                case "mesh_export": cmd.Complete(MeshExport(p)); return;
                case "city_info": RequireCity(); cmd.Complete(CityInfo()); return;
                case "camera": cmd.Complete(CameraInfo()); return;
                case "camera_goto": RequireCity(); cmd.Complete(CameraGoto(p)); return;
                case "photo_mode": RequireCity(); cmd.Complete(PhotoMode(p)); return;
                case "set_speed": RequireCity(); cmd.Complete(SetSpeed(p)); return;
                case "terrain_height": RequireCity(); cmd.Complete(TerrainHeights(p)); return;
                case "list_road_types": cmd.Complete(ListRoadTypes(Json.Str(p, "filter", ""), Json.Str(p, "kind", "all"))); return;
                case "list_building_types": RequireCity(); cmd.Complete(ListBuildingTypes(p)); return;
                case "place_building": RequireCity(); m_Tool.Submit(cmd, BuildPlaceBuilding(p, recordUndo: true)); return;
                case "building_nets": RequireCity(); cmd.Complete(BuildingNets(p)); return;
                case "building_status": RequireCity(); cmd.Complete(BuildingStatus(p)); return;
                case "service_access": RequireCity(); cmd.Complete(ServiceAccess(p)); return;
                case "path_test": RequireCity(); cmd.Complete(PathTest(p)); return;
                case "path_result": RequireCity(); cmd.Complete(PathResult(p)); return;
                case "demand": RequireCity(); cmd.Complete(Demand(p)); return;
                case "coverage": RequireCity(); cmd.Complete(Coverage(p)); return;
                case "transit": RequireCity(); cmd.Complete(Transit(p)); return;
                case "movement": RequireCity(); cmd.Complete(Movement(p)); return;
                case "site_movements": RequireCity(); cmd.Complete(SiteMovements(p)); return;
                case "edge_lanes": RequireCity(); cmd.Complete(EdgeLaneDetail(p)); return;
                case "node_detail": RequireCity(); cmd.Complete(NodeDetail(p)); return;
                case "commute": RequireCity(); cmd.Complete(Commute(p)); return;
                case "service_budget": RequireCity(); cmd.Complete(ServiceBudget(p)); return;
                case "attractiveness": RequireCity(); cmd.Complete(Attractiveness(p)); return;
                case "list_object_types": RequireCity(); cmd.Complete(ListObjectTypes(p)); return;
                case "place_objects": RequireCity(); m_Tool.Submit(cmd, BuildPlaceObjects(p)); return;
                case "obsolete_prefabs": RequireCity(); cmd.Complete(ObsoletePrefabs(p)); return;
                case "menu_assets": RequireCity(); cmd.Complete(MenuAssets(p)); return;
                case "terraform": RequireCity(); StartTerraform(cmd, p); return;
                case "areas": RequireCity(); cmd.Complete(Areas(p)); return;
                case "prefab_components": RequireCity(); cmd.Complete(PrefabComponents(p)); return;
                case "delete_areas": RequireCity(); m_Tool.Submit(cmd, BuildDeleteAreas(p)); return;
                case "list_zone_types": cmd.Complete(ListZoneTypes()); return;
                case "query_roads": RequireCity(); cmd.Complete(QueryRoads(p)); return;
                case "query_buildings": RequireCity(); cmd.Complete(QueryBuildings(p)); return;
                case "history": cmd.Complete(History()); return;
                case "place_roads": RequireCity(); m_Tool.Submit(cmd, BuildPlace(p, recordUndo: true)); return;
                case "bulldoze": RequireCity(); m_Tool.Submit(cmd, BuildBulldoze(p, recordUndo: true)); return;
                case "replace_roads": RequireCity(); m_Tool.Submit(cmd, BuildReplace(p, recordUndo: true)); return;
                case "zone": RequireCity(); m_Tool.Submit(cmd, BuildZone(p, recordUndo: true)); return;
                case "road_align": RequireCity(); cmd.Complete(RoadAlign(p)); return;
                case "undo": RequireCity(); Undo(cmd); return;
                case "screenshot": StartScreenshot(cmd); return;
                case "road_network": RequireCity(); cmd.Complete(RoadNetwork(p)); return;
                case "zone_blocks": RequireCity(); cmd.Complete(ZoneBlocks(p)); return;
                case "terrain_grid": RequireCity(); cmd.Complete(TerrainGrid(p)); return;
                case "obstacles": RequireCity(); cmd.Complete(Obstacles(p)); return;
                case "preview_commit": RequireCity(); m_Tool.CommitHeld(cmd); return;
                case "preview_cancel": RequireCity(); m_Tool.CancelHeld(cmd); return;
                case "tool_reset": cmd.Complete(m_Tool.ForceReset(Json.Str(p, "reason", "tool_reset requested"))); return;
                case "list_districts": RequireCity(); cmd.Complete(ListDistricts(p)); return;
                case "list_district_policies": RequireCity(); cmd.Complete(ListDistrictPolicies()); return;
                case "create_district": RequireCity(); m_Tool.Submit(cmd, BuildDistrict(p, recordUndo: true)); return;
                case "delete_district": RequireCity(); m_Tool.Submit(cmd, BuildDeleteDistrict(p, recordUndo: true)); return;
                case "rename_district": RequireCity(); cmd.Complete(RenameDistrict(p)); return;
                case "set_district_policy": RequireCity(); cmd.Complete(SetDistrictPolicy(p)); return;
                case "asset_import": StartAssetImport(cmd, p); return;
                case "asset_configure": cmd.Complete(WithModeCache(AssetConfigure(p))); return;
                case "prefab_clone": cmd.Complete(WithModeCache(PrefabClone(p))); return;
                case "prefab_entity": cmd.Complete(PrefabEntity(p)); return;
                case "prefab_overrides": cmd.Complete(PrefabOverrides()); return;
                case "prefab_create": cmd.Complete(WithModeCache(PrefabCreate(p))); return;
                case "mode_cache_sync": cmd.Complete(SyncModeCache()); return;
                case "brand_info": cmd.Complete(BrandInfo(p)); return;
                case "brand_bind": cmd.Complete(BrandBind(p)); return;
                case "brand_bindings": cmd.Complete(BrandBindings()); return;
                default: cmd.Fail("unknown method: " + cmd.Method); return;
            }
        }

        // ------------------------------------------------------------------ status / controls
        Dictionary<string, object> Ping()
        {
            var gm = GameManager.instance; EnsureSaveHook();
            return new Dictionary<string, object>
            {
                ["mod_version"] = Bridge.Version, ["game_mode"] = gm != null ? gm.gameMode.ToString() : "unknown",
                ["loading"] = gm != null && gm.isGameLoading, ["city_loaded"] = CityLoaded, ["tool_busy"] = m_Tool.Busy, ["preview_held"] = m_Tool.Held,
                ["undo_depth"] = m_Undo.Count, ["import_running"] = m_Import != null,
                // v0.17: which city is loaded, so sessions can check before editing (city name + the last save file)
                ["city_name"] = CityLoaded ? World.GetOrCreateSystemManaged<Game.City.CityConfigurationSystem>().cityName : null,
                // v0.33: set from the load itself (menu loads included) and by saves; null = unknown (new city, or load failed)
                ["save_name"] = CityLoaded ? s_LoadedSave : null,   // the save file this city was loaded from / last saved as
                ["load_purpose"] = s_LoadPurpose, ["load_generation"] = s_LoadGeneration,
            };
        }

        Dictionary<string, object> CityInfo()
        {
            var d = new Dictionary<string, object> { ["money"] = m_City.moneyAmount, ["xp"] = m_City.XP, ["speed"] = m_Sim.selectedSpeed };
            var city = m_City.City;
            if (EntityManager.HasComponent<Population>(city)) d["population"] = EntityManager.GetComponentData<Population>(city).m_Population;
            if (EntityManager.HasComponent<PlayerMoney>(city)) d["unlimited_money"] = EntityManager.GetComponentData<PlayerMoney>(city).m_Unlimited;
            d["road_edges"] = m_EdgeQuery.CalculateEntityCount();
            d["buildings"] = m_BuildingQuery.CalculateEntityCount();
            return d;
        }

        object SetSpeed(Dictionary<string, object> p)
        {
            float s = (float)Json.Num(p, "speed", 1);
            if (s < 0 || s > 3) throw new ArgumentException("speed must be 0 (pause) .. 3");
            m_Sim.selectedSpeed = s;
            return new Dictionary<string, object> { ["speed"] = m_Sim.selectedSpeed };
        }

        public float SampleHeight(float x, float z)
        {
            var data = m_Terrain.GetHeightData(true);
            return TerrainUtils.SampleHeight(ref data, new float3(x, 0, z));
        }

        Dictionary<string, object> CameraInfo()
        {
            var cam = UnityEngine.Camera.main;
            if (cam == null) throw new InvalidOperationException("no main camera");
            var p = cam.transform.position; var f = cam.transform.forward;
            var d = new Dictionary<string, object> { ["position"] = new[] { p.x, p.y, p.z }, ["forward"] = new[] { f.x, f.y, f.z } };
            var ctl = m_Camera.gamePlayController;
            if (ctl != null) { d["pivot"] = new[] { ctl.pivot.x, ctl.pivot.y, ctl.pivot.z }; d["zoom"] = ctl.zoom; d["yaw"] = ctl.angle.x; d["pitch"] = ctl.angle.y; }
            if (CityLoaded && f.y < -0.01f)
            {   // march the view ray to the terrain -> ground point at the centre of the screen
                var pos = new float3(p.x, p.y, p.z); var dir = math.normalize(new float3(f.x, f.y, f.z));
                for (int i = 0; i < 4000; i++)
                {
                    pos += dir * 5f;
                    float h = SampleHeight(pos.x, pos.z);
                    if (pos.y <= h) { d["ground_point"] = new[] { pos.x, h, pos.z }; break; }
                }
            }
            return d;
        }

        object CameraGoto(Dictionary<string, object> p)
        {
            var ctl = m_Camera.gamePlayController ?? throw new InvalidOperationException("gameplay camera not available");
            float x = (float)Json.Num(p, "x", ctl.pivot.x), z = (float)Json.Num(p, "z", ctl.pivot.z);
            ctl.pivot = new UnityEngine.Vector3(x, SampleHeight(x, z), z);
            if (p.ContainsKey("zoom")) ctl.zoom = (float)Json.Num(p, "zoom", ctl.zoom);
            if (p.ContainsKey("yaw") || p.ContainsKey("pitch"))                  // mod 0.30: view angle (deg), angle = (yaw, pitch)
                ctl.angle = new float2((float)Json.Num(p, "yaw", ctl.angle.x), (float)Json.Num(p, "pitch", ctl.angle.y));
            return new Dictionary<string, object> { ["pivot"] = new[] { ctl.pivot.x, ctl.pivot.y, ctl.pivot.z }, ["zoom"] = ctl.zoom,
                                                    ["yaw"] = ctl.angle.x, ["pitch"] = ctl.angle.y };
        }

        object TerrainHeights(Dictionary<string, object> p)
        {
            var pts = Json.List(p, "points") ?? throw new ArgumentException("points: [[x,z],...] required");
            var outp = new List<object>();
            foreach (var o in pts) { var xz = XZ(o); outp.Add(new[] { xz.x, SampleHeight(xz.x, xz.y), xz.y }); }
            return new Dictionary<string, object> { ["points"] = outp };
        }

        // ------------------------------------------------------------------ catalogues
        // roads + all track types (incl. modded); anyNet adds every other drawable net: pathways, fences, pipes, power lines...
        public Dictionary<string, Entity> RoadPrefabMap(bool anyNet = false)
        {
            var map = new Dictionary<string, Entity>();
            using (var ents = m_NetPrefabQuery.ToEntityArray(Allocator.Temp))
                foreach (var e in ents)
                    if (m_Prefabs.TryGetPrefab<PrefabBase>(e, out var pb) && (pb is RoadPrefab || pb is TrackPrefab || anyNet && pb is NetGeometryPrefab)) map[pb.name] = e;
            return map;
        }

        string NetKind(Entity prefab)
        {
            if (!m_Prefabs.TryGetPrefab<PrefabBase>(prefab, out var pb)) return "other";
            if (pb is TrackPrefab) return "track";
            if (pb is RoadPrefab) return "road";
            if (pb is PathwayPrefab) return "pathway";
            if (pb is FencePrefab) return "fence";
            if (pb is PipelinePrefab || pb is PowerLinePrefab) return "utility";
            var n = pb.GetType().Name; return n.EndsWith("Prefab") ? n.Substring(0, n.Length - 6).ToLowerInvariant() : n.ToLowerInvariant();
        }

        public Dictionary<string, Entity> ZonePrefabMap()
        {
            var map = new Dictionary<string, Entity>();
            using (var ents = m_ZonePrefabQuery.ToEntityArray(Allocator.Temp))
                foreach (var e in ents)
                    if (m_Prefabs.TryGetPrefab<PrefabBase>(e, out var pb)) map[pb.name] = e;
            return map;
        }

        object ListRoadTypes(string filter, string kind = "all")
        {
            var list = new List<object>();
            bool any = kind != "road" && kind != "track";     // "all" (default) lists every drawable net; "road"/"track"/"pathway"/"fence"/"utility" filter
            foreach (var kv in RoadPrefabMap(any))
            {
                if (filter.Length > 0 && kv.Key.IndexOf(filter, StringComparison.OrdinalIgnoreCase) < 0) continue;
                var g = EntityManager.GetComponentData<NetGeometryData>(kv.Value);
                bool track = EntityManager.HasComponent<TrackData>(kv.Value);
                string k = NetKind(kv.Value);
                if (kind != "all" && kind != "any" && k != kind) continue;
                var d = new Dictionary<string, object> { ["name"] = kv.Key, ["width"] = g.m_DefaultWidth, ["max_slope"] = (float)Math.Round(g.m_MaxSlopeSteepness, 3), ["kind"] = k };
                if (track) { var td = EntityManager.GetComponentData<TrackData>(kv.Value); d["track_type"] = td.m_TrackType.ToString(); d["speed_limit"] = Math.Round(td.m_SpeedLimit, 1); }
                list.Add(d);
            }
            list.Sort((a, b) => string.CompareOrdinal((string)((Dictionary<string, object>)a)["name"], (string)((Dictionary<string, object>)b)["name"]));
            return new Dictionary<string, object> { ["count"] = list.Count, ["road_types"] = list };
        }

        object ListZoneTypes()
        {
            var names = new List<string>(ZonePrefabMap().Keys); names.Sort(StringComparer.Ordinal);
            return new Dictionary<string, object> { ["count"] = names.Count, ["zone_types"] = names, ["dezone"] = "use zone_type \"none\"" };
        }

        // ------------------------------------------------------------------ queries
        struct Box { public float x0, x1, z0, z1; public bool In(float3 q) => q.x >= x0 && q.x <= x1 && q.z >= z0 && q.z <= z1; }
        static Box BoxOf(Dictionary<string, object> p) => new Box
        {
            x0 = (float)Json.Num(p, "xmin", -1e9), x1 = (float)Json.Num(p, "xmax", 1e9), z0 = (float)Json.Num(p, "zmin", -1e9), z1 = (float)Json.Num(p, "zmax", 1e9)
        };
        static bool HasBox(Dictionary<string, object> p) => p.ContainsKey("xmin") || p.ContainsKey("xmax") || p.ContainsKey("zmin") || p.ContainsKey("zmax");

        object QueryRoads(Dictionary<string, object> p)
        {
            var box = BoxOf(p); int limit = (int)Json.Num(p, "limit", 500), total = 0;
            var list = new List<object>();
            using (var ents = m_EdgeQuery.ToEntityArray(Allocator.Temp))
                foreach (var e in ents)
                {
                    var cv = EntityManager.GetComponentData<Curve>(e);
                    if (!box.In(cv.m_Bezier.a) && !box.In(cv.m_Bezier.d)) continue;
                    total++;
                    if (list.Count >= limit) continue;
                    var c = cv.m_Bezier;
                    list.Add(new Dictionary<string, object>
                    {
                        ["id"] = Id(e), ["type"] = m_Prefabs.GetPrefabName(EntityManager.GetComponentData<PrefabRef>(e).m_Prefab),
                        ["start"] = new[] { c.a.x, c.a.y, c.a.z }, ["end"] = new[] { c.d.x, c.d.y, c.d.z }, ["length"] = cv.m_Length,
                        ["curved"] = math.distance(MathUtils.Position(c, 0.5f), (c.a + c.d) / 2) > 0.5f,
                    });
                }
            return new Dictionary<string, object> { ["total_in_area"] = total, ["returned"] = list.Count, ["roads"] = list };
        }

        object QueryBuildings(Dictionary<string, object> p)
        {
            var box = BoxOf(p); int limit = (int)Json.Num(p, "limit", 200), total = 0;
            var list = new List<object>();
            using (var ents = m_BuildingQuery.ToEntityArray(Allocator.Temp))
                foreach (var e in ents)
                {
                    var t = EntityManager.GetComponentData<Transform>(e);
                    if (!box.In(t.m_Position)) continue;
                    total++;
                    if (list.Count >= limit) continue;
                    list.Add(new Dictionary<string, object>
                    {
                        ["id"] = Id(e), ["type"] = m_Prefabs.GetPrefabName(EntityManager.GetComponentData<PrefabRef>(e).m_Prefab),
                        ["position"] = new[] { t.m_Position.x, t.m_Position.y, t.m_Position.z },
                        ["forward"] = Fwd(t.m_Rotation), ["lot"] = Lot(EntityManager.GetComponentData<PrefabRef>(e).m_Prefab),
                    });
                }
            return new Dictionary<string, object> { ["total_in_area"] = total, ["returned"] = list.Count, ["buildings"] = list };
        }

        float[] Fwd(quaternion q) { var f = math.mul(q, new float3(0, 0, 1)); return new[] { (float)Math.Round(f.x, 3), (float)Math.Round(f.z, 3) }; }
        int[] Lot(Entity prefab)
        {   // lot size in 8 m cells [width, depth] (the lot is centred on position, depth along forward)
            if (!EntityManager.HasComponent<BuildingData>(prefab)) return null;
            var l = EntityManager.GetComponentData<BuildingData>(prefab).m_LotSize; return new[] { l.x, l.y };
        }

        // ------------------------------------------------------------------ helpers
        public static string Id(Entity e) => $"{e.Index}:{e.Version}";
        public static Entity ParseId(string s)
        {
            var parts = s.Split(':');
            if (parts.Length != 2) throw new ArgumentException($"bad id '{s}' (expected index:version)");
            return new Entity { Index = int.Parse(parts[0]), Version = int.Parse(parts[1]) };
        }
        static float2 XZ(object o)
        {
            var l = o as List<object> ?? throw new ArgumentException("expected [x, z]");
            return new float2((float)(double)l[0], (float)(double)l[1]);
        }
    }
}
