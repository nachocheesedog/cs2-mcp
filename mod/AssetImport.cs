// Asset pipeline tools: import an FBX/PNG asset folder exactly like the Editor's Asset Importer does, then configure
// and save the resulting prefab - no editor GUI needed.
//   asset_import    {directory, project_root?, preset?, template?, seed?, lot_width?, lot_depth?, activate_tool?,
//                    window_light?, window_light_lod?, window_color? (v0.34)}
//                   -> runs Game.UI.Editor.AssetImportPanel.ImportAssets (public static; the Import button's code path)
//                      as a job pumped from OnUpdate; completes when the async import finishes (can take minutes:
//                      texture compression). Reports the created prefab(s) + error/warning lines from the import logs.
//   asset_configure {name, lot_width?, lot_depth?, access_type?, save?} -> edit + save an existing building prefab.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using Colossal.Logging;
using Game.Prefabs;
using Game.Tools;
using Game.UI.Editor;

namespace CS2MCP
{
    public partial class McpBridgeSystem
    {
        sealed class ImportJob
        {
            public Command Cmd;
            public Task<AssetImportPanel.PrefabFactory> Task;
            public string Root, Dir;
            public AssetImportPanel.Preset Preset;
            public int LotW, LotD;
            public bool ActivateTool;
            public DateTime Start;
            public Dictionary<string, long> LogStart;
            public List<string> WindowOverrides = new List<string>();      // v0.34: SettingsOverrides keys to Clear() afterwards
        }
        ImportJob m_Import;
        static readonly string[] kImportLogs = { "AssetPipeline.log", "CS2MCP.Import.log", "Editor.log" };
        static string LogDir => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "AppData", "LocalLow", "Colossal Order", "Cities Skylines II", "Logs");

        static string NormDir(string d) => Path.GetFullPath(d).TrimEnd('\\', '/');

        void StartAssetImport(Command cmd, Dictionary<string, object> p)
        {
            if (m_Import != null) { cmd.Fail("an asset import is already running (started " + m_Import.Start.ToString("HH:mm:ss") + ")"); return; }
            string dir = Json.Str(p, "directory", null) ?? throw new ArgumentException("directory (the asset folder, e.g. ...\\cs2\\NA_MyAsset) is required");
            dir = NormDir(dir);
            if (!Directory.Exists(dir)) throw new ArgumentException("directory not found: " + dir);
            string root = Json.Str(p, "project_root", null);
            root = root != null ? NormDir(root) : Path.GetDirectoryName(dir);                 // default: the folder's parent
            if (!dir.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                throw new ArgumentException($"directory must be inside project_root ({root})");
            string presetName = Json.Str(p, "preset", "Building");
            if (!Enum.TryParse(presetName, true, out AssetImportPanel.Preset preset))
                throw new ArgumentException("preset must be one of: " + string.Join(", ", Enum.GetNames(typeof(AssetImportPanel.Preset))));
            PrefabBase template = null;
            string tname = Json.Str(p, "template", null);
            if (!string.IsNullOrEmpty(tname))
            {
                template = FindPrefabByName(tname) ?? throw new ArgumentException("template prefab not found: " + tname);
                if (!p.ContainsKey("preset")) preset = AssetImportPanel.Preset.ExistingPrefab;
            }
            if (preset == AssetImportPanel.Preset.ExistingPrefab && template == null) throw new ArgumentException("preset ExistingPrefab needs template");
            var job = new ImportJob
            {
                Cmd = cmd, Root = root, Dir = dir, Preset = preset, Start = DateTime.Now,
                LotW = (int)Json.Num(p, "lot_width", 0), LotD = (int)Json.Num(p, "lot_depth", 0),
                ActivateTool = Json.Bool(p, "activate_tool", false), LogStart = new Dictionary<string, long>(),
            };
            foreach (var f in kImportLogs)
            {
                var path = Path.Combine(LogDir, f);
                job.LogStart[f] = File.Exists(path) ? new FileInfo(path).Length : 0;
            }
            Log.Info($"asset_import start: root={root} dir={dir} preset={preset} template={template?.name}");
            var log = QuietLogger("CS2MCP.Import");          // v0.33: importer errors go to the caller, not a modal dialog
            Game.AssetPipeline.AssetImportPipeline.SettingsOverrides.Snapshot overrides = null;
            try { overrides = WindowOverrides(p, dir, job); }
            catch { ClearWindowOverrides(job); throw; }
            try { job.Task = AssetImportPanel.ImportAssets(root, dir, Json.Str(p, "seed", null), preset, template, World, log, overrides); }
            catch { ClearWindowOverrides(job); throw; }
            m_Import = job;
        }

        // v0.34: the Editor import panel's per-file window settings ("Interior Light Intensity", window colour) for every
        // window FBX (*_Win / _Wim / _Wif / _Wio, any LOD) in the folder: window_light 0..1 (game default 0.549, the importer
        // saturates it), window_color [r, g, b] linear (default dark blue-grey), window_light_lod = a separate intensity for
        // the _LOD1/_LOD2 window files (far LODs read dim). Same mechanism as AssetImportPanel.BuildWindowWidgets:
        // AssetImportPipeline.SettingsOverrides keyed by the source path, snapshotted into ImportAssets, cleared afterwards.
        Game.AssetPipeline.AssetImportPipeline.SettingsOverrides.Snapshot WindowOverrides(Dictionary<string, object> p, string dir, ImportJob job)
        {
            bool hasLight = p.ContainsKey("window_light"), hasLod = p.ContainsKey("window_light_lod"), hasColor = p.ContainsKey("window_color");
            if (!hasLight && !hasLod && !hasColor) return null;
            float light = (float)Json.Num(p, "window_light", -1), lightLod = (float)Json.Num(p, "window_light_lod", -1);
            float[] rgb = null;
            if (hasColor && p["window_color"] is List<object> cl && cl.Count >= 3)
                rgb = new[] { Convert.ToSingle(cl[0]), Convert.ToSingle(cl[1]), Convert.ToSingle(cl[2]) };
            var wt = typeof(Colossal.AssetPipeline.PostProcessors.InteriorMapping.WindowsPostProcessor);
            var sType = wt.GetNestedType("PostProcessSettings");
            var baseSettings = (Colossal.AssetPipeline.Importers.ISettings)sType.GetMethod("GetDefault").Invoke(null, null);
            foreach (var f in Directory.GetFiles(dir, "*.fbx"))
            {
                string stem = Path.GetFileNameWithoutExtension(f);
                if (!(stem.EndsWith("_Win") || stem.EndsWith("_Wim") || stem.EndsWith("_Wif") || stem.EndsWith("_Wio"))) continue;
                bool lod = stem.Contains("_LOD1") || stem.Contains("_LOD2") || stem.Contains("_LOD3");
                float li = lod && lightLod >= 0 ? lightLod : light;
                foreach (var key in new[] { f.Replace('\\', '/'), f })                        // the pipeline keys by asset.path
                {
                    if (!Game.AssetPipeline.AssetImportPipeline.SettingsOverrides.TryGetOrCreateSettings(key, baseSettings, out var boxed)) continue;
                    job.WindowOverrides.Add(key);
                    foreach (var fieldName in new[] { "colorNormal", "colorFrosted", "colorOpaque" })
                    {
                        var fi = sType.GetField(fieldName); var c = fi.GetValue(boxed);
                        var ct = c.GetType();
                        if (li >= 0) ct.GetField("lightIntensity").SetValue(c, li);
                        if (rgb != null) { ct.GetField("r").SetValue(c, rgb[0]); ct.GetField("g").SetValue(c, rgb[1]); ct.GetField("b").SetValue(c, rgb[2]); }
                        fi.SetValue(boxed, c);
                    }
                }
            }
            Log.Info($"asset_import window overrides: {job.WindowOverrides.Count} keys light={light} lod={lightLod}");
            return Game.AssetPipeline.AssetImportPipeline.SettingsOverrides.CreateSnapshot();
        }

        void ClearWindowOverrides(ImportJob j)
        {
            if (j == null) return;
            foreach (var k in j.WindowOverrides)
                try { Game.AssetPipeline.AssetImportPipeline.SettingsOverrides.Clear(k); } catch { }
            j.WindowOverrides.Clear();
        }

        [System.Runtime.CompilerServices.MethodImpl(System.Runtime.CompilerServices.MethodImplOptions.NoInlining)]
        void PumpImport()
        {
            var j = m_Import;
            if (j == null) return;
            if (!j.Task.IsCompleted)
            {
                if ((DateTime.Now - j.Start).TotalMinutes > 30) { m_Import = null; ClearWindowOverrides(j); j.Cmd.Fail("asset import did not finish within 30 minutes"); }
                return;
            }
            m_Import = null;
            ClearWindowOverrides(j);                                                          // also on faulted imports
            try { j.Cmd.Complete(WithModeCache(FinishImport(j))); }
            catch (Exception e) { Log.Info("asset_import finish failed: " + e); j.Cmd.Fail(e.GetType().Name + ": " + e.Message); }
        }

        object FinishImport(ImportJob j)
        {
            var d = new Dictionary<string, object>
            {
                ["project_root"] = j.Root, ["directory"] = j.Dir, ["preset"] = j.Preset.ToString(),
                ["seconds"] = Math.Round((DateTime.Now - j.Start).TotalSeconds, 1),
            };
            if (j.Task.IsFaulted) d["exception"] = j.Task.Exception?.GetBaseException().ToString();
            var factory = j.Task.IsFaulted ? null : j.Task.Result;
            var created = new List<object>();
            if (factory != null)
            {
                d["render_prefabs"] = factory.prefabs.Count;
                d["root_meshes"] = factory.rootPrefabs.Select(r => (object)new Dictionary<string, object> { ["prefab"] = r.prefab?.name, ["source"] = r.source }).ToList();
                foreach (var (prefab, source) in factory.rootPrefabs)
                {
                    string fileName = Path.GetFileName(source);                                 // ImportAssets names the object prefab after this
                    var obj = FindPrefabByName(fileName, preferObject: true);
                    if (obj == null) continue;
                    var info = PrefabSummary(obj);
                    if (obj is BuildingPrefab bp && (j.LotW > 0 || j.LotD > 0))
                    {
                        if (j.LotW > 0) bp.m_LotWidth = j.LotW;
                        if (j.LotD > 0) bp.m_LotDepth = j.LotD;
                        m_Prefabs.UpdatePrefab(bp);
                        info["saved"] = SavePrefab(bp);
                        info["lot"] = new[] { bp.m_LotWidth, bp.m_LotDepth };
                    }
                    created.Add(info);
                }
            }
            d["created"] = created;
            d["ok"] = factory != null && created.Count > 0 && !j.Task.IsFaulted;
            if (!j.ActivateTool)
            {   // ImportAssets activates the placement tool with the new prefab (editor behaviour) - drop back to the default tool.
                // v0.33: only when that object tool is what is active - never over another session's running/held MCP operation
                // or a tool the player picked since
                try
                {
                    var ts = World.GetOrCreateSystemManaged<ToolSystem>();
                    if (!m_Tool.Busy && ts.activeTool is ObjectToolSystem) ts.activeTool = World.GetOrCreateSystemManaged<DefaultToolSystem>();
                }
                catch (Exception e) { Log.Info("tool reset failed: " + e.Message); }
            }
            d["log"] = ImportLogTail(j);
            Log.Info($"asset_import done: ok={d["ok"]} created={created.Count} in {d["seconds"]}s");
            return d;
        }

        List<object> ImportLogTail(ImportJob j)
        {
            var lines = new List<object>();
            foreach (var f in kImportLogs)
            {
                var path = Path.Combine(LogDir, f);
                if (!File.Exists(path)) continue;
                try
                {
                    using (var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                    {
                        long from = j.LogStart.TryGetValue(f, out var s) && s <= fs.Length ? s : 0;
                        fs.Seek(from, SeekOrigin.Begin);
                        using (var r = new StreamReader(fs))
                        {
                            string line;
                            while ((line = r.ReadLine()) != null && lines.Count < 60)
                            {
                                if (line.IndexOf("ERROR", StringComparison.OrdinalIgnoreCase) >= 0 || line.IndexOf("WARN", StringComparison.OrdinalIgnoreCase) >= 0
                                    || line.IndexOf("Exception", StringComparison.Ordinal) >= 0 || line.IndexOf("Completed", StringComparison.Ordinal) >= 0
                                    || line.IndexOf("Root prefab", StringComparison.Ordinal) >= 0)
                                    lines.Add(f + ": " + (line.Length > 400 ? line.Substring(0, 400) : line));
                            }
                        }
                    }
                }
                catch (Exception e) { lines.Add(f + ": (unreadable: " + e.Message + ")"); }
            }
            return lines;
        }

        // ------------------------------------------------------------------ configure / helpers
        object AssetConfigure(Dictionary<string, object> p)
        {
            string name = Json.Str(p, "name", null) ?? throw new ArgumentException("name required");
            // objects first; else any prefab type (0.23: mesh RenderPrefabs, e.g. "Taxi01 Mesh" -> ProceduralAnimationProperties bones)
            var prefab = FindPrefabByName(name, preferObject: true) ?? FindPrefabByName(name) ?? throw new ArgumentException("prefab not found: " + name);
            // v0.33: edits only on user (imported) assets. Game / mod-pack prefabs stay read-only (describe, and reading them
            // as a copy_from SOURCE, are fine) unless allow_vanilla=true - and even then the change is runtime-only, never saved.
            bool userAsset = IsUserAsset(prefab), allowVanilla = Json.Bool(p, "allow_vanilla", false);
            if (!userAsset && !allowVanilla && kEditKeys.Any(p.ContainsKey))
                throw new ArgumentException($"{name} is not a user (imported) asset - asset_configure only edits your own assets. Its " +
                    "regenerated prefab would affect every city this session and could not be saved. Use describe=true to read it; " +
                    "pass allow_vanilla=true only for a deliberate runtime-only test (lost on restart), ideally at the main menu.");
            var changed = new List<object>();
            if (prefab is BuildingPrefab bp)
            {
                if (p.ContainsKey("lot_width")) { bp.m_LotWidth = (int)Json.Num(p, "lot_width", bp.m_LotWidth); changed.Add("lot_width"); }
                if (p.ContainsKey("lot_depth")) { bp.m_LotDepth = (int)Json.Num(p, "lot_depth", bp.m_LotDepth); changed.Add("lot_depth"); }
                string acc = Json.Str(p, "access_type", null);
                if (acc != null)
                {
                    if (!Enum.TryParse(acc, true, out Game.Prefabs.BuildingAccessType at))
                        throw new ArgumentException("access_type must be one of: " + string.Join(", ", Enum.GetNames(typeof(Game.Prefabs.BuildingAccessType))));
                    bp.m_AccessType = at; changed.Add("access_type");
                }
            }
            else if (p.ContainsKey("lot_width") || p.ContainsKey("lot_depth") || p.ContainsKey("access_type"))
                throw new ArgumentException($"{name} is a {prefab.GetType().Name}, not a BuildingPrefab");
            // copy_from: clone another prefab's gameplay components (like the importer's ExistingPrefab preset: no sub-objects,
            // sub-areas/lanes/nets or effects, which belong to the template's own geometry)
            string copyFrom = Json.Str(p, "copy_from", null);
            if (copyFrom != null)
            {
                var src = FindPrefabByName(copyFrom, preferObject: true) ?? FindPrefabByName(copyFrom) ?? throw new ArgumentException("copy_from prefab not found: " + copyFrom);
                // copy_components: only these component types (e.g. ["EmissiveProperties"] from a vanilla vehicle mesh - brings
                // along what JSON can't express, like the turn-signal AnimationCurves); an explicit list overrides kCopySkip
                var only = (Json.List(p, "copy_components") ?? new List<object>()).Select(o => o as string).ToList();
                foreach (var c in src.components)
                {
                    if (c == null) continue;
                    if (only.Count > 0 ? !only.Contains(c.GetType().Name) : kCopySkip.Contains(c.GetType().Name)) continue;
                    var existing = prefab.components.FirstOrDefault(x => x != null && x.GetType() == c.GetType());
                    if (existing != null) prefab.Remove(c.GetType());
                    prefab.AddComponentFrom(c);
                    changed.Add("+" + c.GetType().Name);
                }
            }
            foreach (var o in Json.List(p, "remove_components") ?? new List<object>())
            {
                var t = FindComponentType(o as string);
                if (prefab.components.Any(x => x != null && x.GetType() == t)) { prefab.Remove(t); changed.Add("-" + t.Name); }
            }
            foreach (var o in Json.List(p, "add_components") ?? new List<object>())
            {
                var t = FindComponentType(o as string);
                if (!prefab.components.Any(x => x != null && x.GetType() == t))
                {
                    var added = prefab.AddComponent(t); changed.Add("+" + t.Name);
                    // a fresh component has null arrays (e.g. ObjectSubObjects.m_SubObjects) and the game's GetDependencies
                    // loops over them unchecked -> NullReferenceException on UpdatePrefab (2026-09-24). Start them empty.
                    foreach (var fi in t.GetFields(System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Instance))
                        if (fi.FieldType.IsArray && fi.GetValue(added) == null) fi.SetValue(added, Array.CreateInstance(fi.FieldType.GetElementType(), 0));
                }
            }
            // set: {"Component.m_Field": value} or {"m_Field": value} (on the prefab itself, e.g. BuildingPrefab.m_LotWidth)
            if (p.TryGetValue("set", out var setObj) && setObj is Dictionary<string, object> sets)
            {
                foreach (var kv in sets)
                {
                    int dot = kv.Key.LastIndexOf('.');
                    string comp = dot > 0 ? kv.Key.Substring(0, dot) : null, field = dot > 0 ? kv.Key.Substring(dot + 1) : kv.Key;
                    var target = ComponentTarget(prefab, comp);
                    var fi = target.GetType().GetField(field, System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Instance)
                             ?? throw new ArgumentException($"{target.GetType().Name} has no public field {field}");
                    fi.SetValue(target, ConvertValue(kv.Value, fi.FieldType));
                    changed.Add(kv.Key);
                }
            }
            // icon: a PNG/JPG stored as an ImageAsset next to the prefab (like the Editor's icon picker) -> UIObject.m_Icon
            string iconFile = Json.Str(p, "icon", null);
            string iconUri = null;
            if (iconFile != null)
            {
                iconUri = ImportIcon(prefab, iconFile);
                prefab.AddOrGetComponent<UIObject>().m_Icon = iconUri;
                changed.Add("icon");
            }
            // title / description: LocaleAssets next to the prefab (the Editor's "Localized name/description"), live-applied
            Dictionary<string, object> locInfo = null;
            string title = Json.Str(p, "title", null), description = Json.Str(p, "description", null);
            if (title != null || description != null)
            {
                locInfo = SaveLocalization(prefab, title, description, Json.Str(p, "locale", null));
                changed.Add("localization");
            }
            if (changed.Count > 0) m_Prefabs.UpdatePrefab(prefab);
            var info = PrefabSummary(prefab);
            if (iconUri != null) info["icon"] = iconUri;
            if (locInfo != null) info["localization"] = locInfo;
            if (Json.Bool(p, "describe", false))
            {
                var only = (Json.List(p, "describe_components") ?? new List<object>()).Select(o => o as string).ToList();
                var desc = new Dictionary<string, object>();
                if (only.Count == 0 || only.Contains("prefab") || only.Contains(prefab.GetType().Name)) desc[prefab.GetType().Name] = DescribeFields(prefab);
                foreach (var c in prefab.components)
                    if (c != null && (only.Count == 0 || only.Contains(c.GetType().Name))) desc[c.GetType().Name] = DescribeFields(c);
                info["fields"] = desc;
            }
            info["changed"] = changed;
            if (changed.Count > 0 && Json.Bool(p, "save", true))
            {
                if (userAsset) info["saved"] = SavePrefab(prefab);
                else { info["saved"] = false; info["save_skipped"] = "not a user asset - runtime-only change, gone after a restart"; }
            }
            return info;
        }

        // parameters of asset_configure that change the prefab (describe / describe_components / save only read)
        static readonly string[] kEditKeys = { "lot_width", "lot_depth", "access_type", "copy_from", "remove_components", "add_components",
                                               "set", "icon", "title", "description" };

        static bool IsUserAsset(PrefabBase prefab) => prefab.asset != null && prefab.asset.database == Colossal.IO.AssetDatabase.AssetDatabase.user;

        static string AssetSubPath(PrefabBase prefab)
        {
            if (!IsUserAsset(prefab))
                throw new ArgumentException(prefab.name + " is not a user (imported) asset - icon/title/description can only be set on those");
            return prefab.asset.subPath;
        }

        string ImportIcon(PrefabBase prefab, string file)
        {
            if (!File.Exists(file)) throw new ArgumentException("icon file not found: " + file);
            string ext = Path.GetExtension(file).ToLowerInvariant();
            if (ext != ".png" && ext != ".jpg" && ext != ".jpeg") throw new ArgumentException("icon must be .png or .jpg");
            var db = Colossal.IO.AssetDatabase.AssetDatabase.user;
            string sub = AssetSubPath(prefab), stem = prefab.name + "_Icon";
            // replace any previous icon of this prefab (content-hashed names, so a changed picture never hits a stale cache)
            foreach (var old in Colossal.IO.AssetDatabase.AssetDatabase.global.GetAssets(
                         Colossal.IO.AssetDatabase.SearchFilter<Colossal.IO.AssetDatabase.ImageAsset>.ByCondition(a => a.subPath == sub && a.name.StartsWith(stem))).ToList())
                db.DeleteAsset(old);
            byte[] bytes = File.ReadAllBytes(file);
            string hash;
            using (var md5 = System.Security.Cryptography.MD5.Create())
                hash = BitConverter.ToString(md5.ComputeHash(bytes), 0, 4).Replace("-", "").ToLowerInvariant();
            var path = Colossal.IO.AssetDatabase.AssetDataPath.Create(sub, stem + "_" + hash + ext, true);
            using (var img = db.AddAsset<Colossal.IO.AssetDatabase.ImageAsset>(path))
            {
                using (var w = img.GetWriteStream()) w.Write(bytes, 0, bytes.Length);
                return Colossal.UI.UIExtensions.ToGlobalUri(img);
            }
        }

        static readonly Dictionary<string, (string, Colossal.Localization.MemorySource)> s_LiveLocale = new Dictionary<string, (string, Colossal.Localization.MemorySource)>();

        Dictionary<string, object> SaveLocalization(PrefabBase prefab, string title, string description, string localeId)
        {
            var db = Colossal.IO.AssetDatabase.AssetDatabase.user;
            var lm = Game.SceneFlow.GameManager.instance.localizationManager;
            localeId = localeId ?? lm.fallbackLocaleId;                        // en-US
            string sub = AssetSubPath(prefab), nameKey = "Assets.NAME[" + prefab.name + "]", descKey = "Assets.DESCRIPTION[" + prefab.name + "]";
            // merge with what is already saved for this prefab (all locales), then rewrite the locale assets like the Editor does
            var perLocale = new Dictionary<string, Dictionary<string, string>>();
            foreach (var la in Colossal.IO.AssetDatabase.AssetDatabase.global.GetAssets(
                         Colossal.IO.AssetDatabase.SearchFilter<Colossal.IO.AssetDatabase.LocaleAsset>.ByCondition(a => a.subPath == sub)).ToList())
            {
                try { perLocale[la.localeId] = new Dictionary<string, string>(la.data.entries); } catch { }
                db.DeleteAsset(la);
            }
            if (!perLocale.TryGetValue(localeId, out var entries)) perLocale[localeId] = entries = new Dictionary<string, string>();
            if (title != null) entries[nameKey] = title;
            if (description != null) entries[descKey] = description;
            if (!entries.ContainsKey(nameKey)) entries[nameKey] = prefab.name;
            // upgrades (ServiceUpgrade) are titled/described in the upgrade panel by their own keys
            // (decompiled PrefabUISystem.GetTitleAndDescription: Assets.UPGRADE_NAME / Assets.UPGRADE_DESCRIPTION)
            if (prefab.Has<Game.Prefabs.ServiceUpgrade>())
            {
                entries["Assets.UPGRADE_NAME[" + prefab.name + "]"] = entries[nameKey];
                if (entries.TryGetValue(descKey, out var ud)) entries["Assets.UPGRADE_DESCRIPTION[" + prefab.name + "]"] = ud;
            }
            foreach (var kv in perLocale)
            {
                var asset = db.AddAsset<Colossal.IO.AssetDatabase.LocaleAsset>(Colossal.IO.AssetDatabase.AssetDataPath.Create(sub, prefab.asset.name + "_" + kv.Key));
                asset.SetData(new Colossal.IO.AssetDatabase.LocaleData(kv.Key, kv.Value, new Dictionary<string, int>()),
                              lm.LocaleIdToSystemLanguage(kv.Key), lm.GetLocalizedName(kv.Key));
                asset.Save();
            }
            // live: a memory source on top so the UI shows it now (the saved LocaleAsset is what loads next session)
            if (s_LiveLocale.TryGetValue(prefab.name, out var prev)) lm.RemoveSource(prev.Item1, prev.Item2);
            var src = new Colossal.Localization.MemorySource(new Dictionary<string, string>(entries));
            lm.AddSource(localeId, src);
            s_LiveLocale[prefab.name] = (localeId, src);
            return new Dictionary<string, object> { ["locale"] = localeId, ["name"] = entries[nameKey],
                ["description_chars"] = entries.TryGetValue(descKey, out var d) ? d.Length : 0, ["locales_saved"] = perLocale.Count };
        }

        bool SavePrefab(PrefabBase prefab)
        {
            try
            {
                if (!IsUserAsset(prefab)) { Log.Info($"prefab save refused: {prefab.name} is not a user asset"); return false; }   // never force-save game/pack assets
                prefab.asset.MarkDirty();
                prefab.asset.Save(force: true);
                return true;
            }
            catch (Exception e) { Log.Info("prefab save failed: " + e); return false; }
        }

        Dictionary<string, object> PrefabSummary(PrefabBase prefab)
        {
            var d = new Dictionary<string, object> { ["name"] = prefab.name, ["type"] = prefab.GetType().Name };
            if (prefab is BuildingPrefab bp) { d["lot"] = new[] { bp.m_LotWidth, bp.m_LotDepth }; d["access_type"] = bp.m_AccessType.ToString(); }
            if (prefab is ObjectGeometryPrefab og && og.m_Meshes != null)
                d["meshes"] = og.m_Meshes.Select(m => (object)(m.m_Mesh != null ? m.m_Mesh.name : null)).ToList();
            d["components"] = prefab.components.Select(c => (object)c.GetType().Name).ToList();
            d["has_entity"] = m_Prefabs.TryGetEntity(prefab, out _);
            d["asset_path"] = prefab.asset?.path.ToString();
            return d;
        }

        // PrefabID includes the asset hash, so a (type, name) PrefabID never matches imported/user assets. Scan the
        // PrefabSystem's own list instead (private m_Prefabs; includes prefabs added this frame, before their entity exists).
        static System.Reflection.FieldInfo s_PrefabListField;
        IEnumerable<PrefabBase> AllPrefabs()
        {
            if (s_PrefabListField == null)
                s_PrefabListField = typeof(PrefabSystem).GetField("m_Prefabs", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
            return s_PrefabListField?.GetValue(m_Prefabs) as IEnumerable<PrefabBase> ?? Enumerable.Empty<PrefabBase>();
        }

        PrefabBase FindPrefabByName(string name, bool preferObject = false)
        {
            PrefabBase any = null;
            foreach (var pb in AllPrefabs())
            {
                if (pb == null || pb.name != name) continue;
                if (pb is ObjectPrefab) return pb;                          // buildings, static objects, extensions first
                if (any == null) any = pb;
            }
            return preferObject ? null : any;
        }

        // ------------------------------------------------------------------ generic prefab editing (reflection)
        static readonly HashSet<string> kCopySkip = new HashSet<string> { "ObjectSubObjects", "ObjectSubAreas", "ObjectSubLanes",
            "ObjectSubNets", "NetSubObjects", "AreaSubObjects", "EffectSource", "ObsoleteIdentifiers", "SpawnableObject" };

        static Type FindComponentType(string name)
        {
            var t = typeof(PrefabBase).Assembly.GetTypes().FirstOrDefault(x => x.Name == name && typeof(ComponentBase).IsAssignableFrom(x) && !x.IsAbstract);
            return t ?? throw new ArgumentException("unknown prefab component type: " + name);
        }

        object ComponentTarget(PrefabBase prefab, string comp)
        {
            if (comp == null || comp == "prefab" || comp == prefab.GetType().Name) return prefab;
            var c = prefab.components.FirstOrDefault(x => x != null && x.GetType().Name == comp);
            return c ?? throw new ArgumentException($"{prefab.name} has no {comp} component (add it with add_components)");
        }

        object ConvertValue(object v, Type t)
        {
            if (v == null) return null;
            if (t.IsEnum)
            {
                if (v is string s) return Enum.Parse(t, s, true);
                return Enum.ToObject(t, Convert.ToInt64(v));
            }
            if (typeof(PrefabBase).IsAssignableFrom(t))
            {
                string pname = v as string ?? throw new ArgumentException("prefab references are given by name");
                // names are not unique across prefab types (e.g. "TransportationTrain" is a FeaturePrefab AND a UI group):
                // prefer a prefab of the field's own type
                var pb = AllPrefabs().FirstOrDefault(x => x != null && x.name == pname && t.IsInstanceOfType(x)) ?? FindPrefabByName(pname);
                if (pb == null) throw new ArgumentException("prefab not found: " + v);
                if (!t.IsInstanceOfType(pb)) throw new ArgumentException($"{v} is a {pb.GetType().Name}, field needs {t.Name}");
                return pb;
            }
            if (t.IsArray && v is List<object> list)
            {
                var et = t.GetElementType(); var arr = Array.CreateInstance(et, list.Count);
                for (int i = 0; i < list.Count; i++) arr.SetValue(ConvertValue(list[i], et), i);
                return arr;
            }
            if (t.IsGenericType && t.GetGenericTypeDefinition() == typeof(List<>) && v is List<object> glist)
            {
                var et = t.GetGenericArguments()[0]; var res = (System.Collections.IList)Activator.CreateInstance(t);
                foreach (var x in glist) res.Add(ConvertValue(x, et));
                return res;
            }
            if (t == typeof(string)) return v.ToString();
            if (t == typeof(bool)) return Convert.ToBoolean(v);
            if (t == typeof(Unity.Mathematics.quaternion))
            {   // [x,y,z,w] or {"yaw_deg": d} (rotation about +Y, the game's up axis)
                if (v is Dictionary<string, object> qd && qd.ContainsKey("yaw_deg"))
                    return Unity.Mathematics.quaternion.RotateY(Unity.Mathematics.math.radians((float)Json.Num(qd, "yaw_deg", 0)));
                if (v is List<object> ql && ql.Count == 4)
                    return new Unity.Mathematics.quaternion(Convert.ToSingle(ql[0]), Convert.ToSingle(ql[1]), Convert.ToSingle(ql[2]), Convert.ToSingle(ql[3]));
            }
            // structured values (sub-net / sub-object infos, Bezier curves, float3, int2 ...): a JSON object sets public fields
            // by name; a JSON list fills the fields positionally (so float3 = [x,y,z], int2 = [a,b], Bezier4x3 = [a,b,c,d])
            if (!t.IsPrimitive && !typeof(UnityEngine.Object).IsAssignableFrom(t) && (v is Dictionary<string, object> || v is List<object>))
            {
                object inst = t.IsValueType ? Activator.CreateInstance(t)
                    : (t.GetConstructor(Type.EmptyTypes) != null ? Activator.CreateInstance(t) : System.Runtime.Serialization.FormatterServices.GetUninitializedObject(t));
                var fields = t.GetFields(System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Instance);
                if (v is Dictionary<string, object> dict)
                {
                    foreach (var kv in dict)
                    {
                        var fi = fields.FirstOrDefault(f => f.Name == kv.Key) ?? throw new ArgumentException($"{t.Name} has no public field {kv.Key} (fields: {string.Join(", ", fields.Select(f => f.Name))})");
                        fi.SetValue(inst, ConvertValue(kv.Value, fi.FieldType));
                    }
                }
                else
                {
                    var l = (List<object>)v;
                    if (l.Count > fields.Length) throw new ArgumentException($"{t.Name} takes at most {fields.Length} values ({string.Join(", ", fields.Select(f => f.Name))})");
                    for (int i = 0; i < l.Count; i++) fields[i].SetValue(inst, ConvertValue(l[i], fields[i].FieldType));
                }
                return inst;
            }
            return Convert.ChangeType(v, t, System.Globalization.CultureInfo.InvariantCulture);
        }

        // describe: prefab references by name, nested plain objects/structs (sub-net infos, Bezier curves, float3 ...) expanded
        // to their public fields, arrays up to kDescribeMaxItems entries.
        const int kDescribeMaxItems = 400;
        static object DescribeValue(object v, int depth = 0)
        {
            switch (v)
            {
                case null: return null;
                case PrefabBase pb: return pb.name;
                case UnityEngine.Object uo: return uo.name;
                case string s: return s;
                case Enum e: return e.ToString();
                case float f: return Math.Round(f, 4);
                case double dd: return Math.Round(dd, 4);
                case bool _: case int _: case uint _: case long _: case short _: case byte _: return v;
                case System.Collections.IList a when depth < 4:
                    { var outl = new List<object>(); int n = 0; foreach (var x in a) { if (n++ >= kDescribeMaxItems) { outl.Add("..."); break; } outl.Add(DescribeValue(x, depth + 1)); } return outl; }
                default:
                    if (depth >= 5) return v.GetType().Name;
                    var fs = v.GetType().GetFields(System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Instance);
                    if (fs.Length == 0) return v.ToString();
                    var d = new Dictionary<string, object>();
                    foreach (var fi in fs) { try { d[fi.Name] = DescribeValue(fi.GetValue(v), depth + 1); } catch { d[fi.Name] = "?"; } }
                    return d;
            }
        }

        static Dictionary<string, object> DescribeFields(object target)
        {
            var d = new Dictionary<string, object>();
            foreach (var f in target.GetType().GetFields(System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Instance))
            {
                if (f.Name == "components" || f.Name == "name" || f.Name == "active") continue;
                try { d[f.Name] = DescribeValue(f.GetValue(target)); } catch { d[f.Name] = "?"; }
            }
            return d;
        }
    }
}
