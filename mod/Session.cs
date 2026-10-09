// Game session control (v0.17): list_saves, load_save (by save name or city name, like the menu's Continue/Load
// buttons: MapMetadataSystem + GameModeSystem from the save, then GameManager.Load), main_menu (GameManager.MainMenu)
// and quit_game (GameManager.QuitGame). Lets an agent run the restart -> import -> load loop without the player.
using System;
using System.Collections.Generic;
using System.Linq;
using Colossal;
using Colossal.IO.AssetDatabase;
using Colossal.PSI.Common;
using Game;
using Game.Assets;
using Game.Prefabs.Modes;
using Game.SceneFlow;
using Game.UI;

namespace CS2MCP
{
    public partial class McpBridgeSystem
    {
        // the save file the city came from. city_name alone can't tell copies of one city apart (the test save is a copy of
        // the main city). v0.33: GameManager.onGameSaveLoad fires only for SAVES (ref GameManager.Save), so a load - from the
        // menu or load_save - is picked up in OnGameLoaded from the load context (its instigator = the save's metadata id),
        // and OnGamePreload clears it so a stale name is never reported.
        static string s_LoadedSave, s_LoadPurpose; static bool s_SaveHooked;
        static void EnsureSaveHook()
        {
            if (s_SaveHooked || GameManager.instance == null) return;
            GameManager.instance.onGameSaveLoad += (saveName, preview, start, success) => { if (!start && success) s_LoadedSave = saveName; };
            s_SaveHooked = true;
        }

        static string SaveNameOf(Colossal.Hash128 instigator)
        {
            if (instigator == Colossal.Hash128.Empty) return null;                 // new city from a map, or no asset
            return AssetDatabase.global.TryGetAsset(instigator, out var asset) && asset is SaveGameMetadata m && m.target != null ? m.target.displayName : null;
        }

        // CS2MCP.Session errors must not pop the game's modal error dialog (which pauses the player's simulation) or upload
        // the log to the crash reporter: the failure is already reported to the RPC caller / in CS2MCP.log
        static Colossal.Logging.ILog s_SessionLog;
        static Colossal.Logging.ILog SessionLog
        {
            get
            {
                if (s_SessionLog == null) s_SessionLog = QuietLogger("CS2MCP.Session");
                return s_SessionLog;
            }
        }
        internal static Colossal.Logging.ILog QuietLogger(string name)
        {
            var log = Colossal.Logging.LogManager.GetLogger(name);
            log.showsErrorsInUI = false;
            log.disableBacktrace = true;
            return log;
        }

        static List<SaveGameMetadata> AllSaves() =>
            AssetDatabase.global.GetAssets(default(SearchFilter<SaveGameMetadata>))
                .Where(m => m != null && m.target != null).ToList();

        object ListSaves(Dictionary<string, object> p)
        {
            int limit = (int)Json.Num(p, "limit", 30);
            var saves = AllSaves().OrderByDescending(m => m.target.lastModified).Take(limit)
                .Select(m => (object)new Dictionary<string, object> {
                    { "name", m.target.displayName }, { "city", m.target.cityName },
                    { "modified", m.target.lastModified.ToString("yyyy-MM-dd HH:mm") }, { "autosave", m.target.autoSave },
                    { "population", m.target.population }, { "valid", m.isValidSaveGame } }).ToList();
            return new Dictionary<string, object> { { "count", saves.Count }, { "saves", saves } };
        }

        object LoadSave(Dictionary<string, object> p)
        {
            string name = Json.Str(p, "name", null) ?? throw new ArgumentException("name required (save name or city name)");
            var gm = GameManager.instance;
            if (gm.isGameLoading) throw new InvalidOperationException("the game is already loading");
            var all = AllSaves().Where(m => m.isValidSaveGame).OrderByDescending(m => m.target.lastModified).ToList();
            var save = all.FirstOrDefault(m => string.Equals(m.target.displayName, name, StringComparison.OrdinalIgnoreCase))
                       ?? all.FirstOrDefault(m => !m.target.autoSave && string.Equals(m.target.cityName, name, StringComparison.OrdinalIgnoreCase));
            if (save == null) throw new ArgumentException("no save named '" + name + "' (see list_saves)");
            var t = save.target; EnsureSaveHook();                          // save_name is set by OnGameLoaded once the load succeeded
            TaskManager.instance.EnqueueTask("SaveLoadGame", async delegate
            {
                try
                {
                    var map = World.GetOrCreateSystemManaged<MapMetadataSystem>();
                    map.mapName = t.mapName; map.prefabReferences = t.prefabReferences;
                    World.GetOrCreateSystemManaged<GameModeSystem>().overrideMode = t.gameMode;
                    PlatformManager.instance.achievementsEnabled = !t.isReadonly;
                    await gm.Load(GameMode.Game, Colossal.Serialization.Entities.Purpose.LoadGame, save);
                }
                catch (OperationCanceledException) { }
                catch (Exception e) { Log.Info("session task failed: " + e); SessionLog.Error(e); }
            }, 1);
            return new Dictionary<string, object> { { "loading", t.displayName }, { "city", t.cityName },
                { "modified", t.lastModified.ToString("yyyy-MM-dd HH:mm") }, { "note", "poll status until city_loaded" } };
        }

        object MainMenu()
        {
            var gm = GameManager.instance;
            if (gm.isGameLoading) throw new InvalidOperationException("the game is loading");
            TaskManager.instance.EnqueueTask("SaveLoadGame", async delegate
            {
                try { await gm.MainMenu(); } catch (Exception e) { Log.Info("session task failed: " + e); SessionLog.Error(e); }
            }, 1);
            return new Dictionary<string, object> { { "main_menu", "requested (unsaved progress is lost)" } };
        }

        object QuitGame()
        {
            GameManager.QuitGame();
            return new Dictionary<string, object> { { "quit", "requested" } };
        }
    }
}
