# Game internals behind the tools

The CS2MCP mod (this repo's `mod/`, C# net472, compiled against the game's managed DLLs) calls the same
code the Asset Editor's buttons call. It exposes an HTTP bridge at `127.0.0.1:17654/rpc`, and commands are pumped in
`McpBridgeSystem.OnUpdate`. The server lives in the repo's `server/`: `cs2_mcp.py` holds the tool definitions
and `assets.py` does validate, import and meta handling.

## Code-modding guidelines (for work on the CS2MCP mod)
Condensed from the C:S Modding Discord knowledgebase + the community "CS2 Modding Instructions". The highlights
that matter for the CS2MCP mod:
- C# 9 with Mod.props' target framework (net48); keep `.pubxml` in sync after toolchain updates.
- Prefer ECS systems over Harmony (Lib.Harmony 2.2.2 with a unique ID if unavoidable).
- Dispose Native collections by lifetime: Temp / TempJob at the end of OnUpdate or `Dispose(jobHandle)`; Persistent in
  OnDestroy.
- OnUpdate runs every frame, even in the main menu, so keep the bridge pump cheap.
- Log through the mod logger; settings go in ModsSettings/; never redistribute decompiled game code.
- The upgrade panel reads `Assets.UPGRADE_NAME/UPGRADE_DESCRIPTION[<name>]` for ServiceUpgrade prefabs (mod 0.31
  writes them).

Also load the `cs2-modding@csmodding` plugin skills (if installed) (cs2-modding, cs2-modding-setup, cs2-mod-project,
cs2-modding-ui) before changing mod code: they are verified against the decompiled game (1.6.2f1).

## Decompiling more
```bash
export DOTNET_ROLL_FORWARD=Major
M="/c/Program Files (x86)/Steam/steamapps/common/Cities Skylines II/Cities2_Data/Managed"
~/.dotnet/tools/ilspycmd.exe -p -o <scratch>/gamefull "$M/Game.dll"                       # whole project, ~30 s, 4300 files
~/.dotnet/tools/ilspycmd.exe -t Colossal.Localization.LocalizationManager "$M/Colossal.Localization.dll"   # one type
~/.dotnet/tools/ilspycmd.exe -l c "$M/Colossal.UI.dll"                                     # list classes
```
Then grep. The editor UI code (`Game.UI.Editor.*`) shows how every Editor action is done in code.

## Import
- `Game.UI.Editor.AssetImportPanel.ImportAssets(projectRoot, dir, seed, Preset, template, World, ILog, overrides)` is
  public static and returns `Task<PrefabFactory>`. The factory exposes `.prefabs` and `.rootPrefabs` (prefab, source).
- `Preset`: None, Building, SignatureBuilding, CityServiceBuilding, SpawnableBuilding, BuildingExtension,
  CityServiceExtension, StaticObject, LocalizedStaticObject, AgingTree, SimplePlant, ExistingPrefab (needs a template).
- `AssetImportPipeline.IsArtRootPath` string-matches the directory against the root, so pass normalized full paths.
- The object prefab is named after the root mesh file name. Imported data persists in
  `%USERPROFILE%\AppData\LocalLow\Colossal Order\Cities Skylines II\ImportedData\<Name>\` and loads with every city.
- Logs are in `...\Cities Skylines II\Logs\`: `AssetPipeline.log`, `CS2MCP.Import.log`, `Editor.log`.
- The FBX importer applies a fixed scale of 0.01 (expects cm) and ignores `UnitScaleFactor`.
- `WindowsPostProcessor` (the "Windows" template, matched on the file pattern `^.*(?:_Win|_Wim|_Wif|_Wio)(\.fbx)$`):
  each island becomes a window, windows group into rooms per 3 m floor, and rooms light at random at night.

## Prefabs
- `PrefabID` includes the asset hash, so `(type, name)` lookups miss imported assets. The mod scans the private
  `PrefabSystem.m_Prefabs` list by name.
- The mod edits prefabs with:
  - `PrefabBase.AddComponent(Type)`, `AddOrGetComponent<T>()`, `AddComponentFrom(ComponentBase)`, `Remove(Type)`
  - `PrefabSystem.UpdatePrefab(prefab)`
  - `prefab.asset.MarkDirty(); prefab.asset.Save(force: true)`
- `BuildingPrefab.m_LotWidth / m_LotDepth / m_AccessType`; `BuildingProperties.m_ResidentialProperties` = households.
- Company jobs (office/industrial/commercial), from `Game.Simulation.CompanyUtils`:
  `ceil(maxWorkersPerCell × lot cells × (1 + 0.5 × level) × m_SpaceMultiplier)`. Apartment size =
  `m_SpaceMultiplier × lot cells / m_ResidentialProperties` (`PropertyUtils`).

## Build-menu icon
- `ImageSystem.GetThumbnail(prefab)` uses `UIObject.m_Icon` (through `UIExtensions.ResolveUri`) when it is set; otherwise
  it uses a live `thumbnail://ThumbnailCamera/...` render.
- The Editor's icon picker (`Game.UI.Editor.UIIconField`) copies the PNG into `AssetDatabase.user` as an `ImageAsset`,
  then sets `m_Icon = Colossal.UI.UIExtensions.ToGlobalUri(imageAsset)` (in Colossal.UI.dll). The mod does the same, in
  the prefab's own subPath.

## Name + description
- Keys are `Assets.NAME[<prefab name>]` and `Assets.DESCRIPTION[<prefab name>]`. Upgrades use `Assets.UPGRADE_NAME/...`;
  service and menu prefabs use `Services.NAME`.
- The Editor (`InspectorPanelSystem.SaveLocalization`) deletes the prefab's LocaleAssets (same subPath), builds a
  `LocaleData(localeId, entries, indexCounts)` per locale, and does
  `database.AddAsset<LocaleAsset>(AssetDataPath.Create(subPath, assetName + "_" + localeId))`, then `SetData`, then `Save()`.
- `LocalizationManager` (Colossal.Localization.dll) loads every LocaleAsset at startup. For a live update, use
  `AddSource(localeId, new MemorySource(dict))` / `RemoveSource`.

## Publishing (not yet a tool)
- `Game.UI.Menu.PdxAssetUploadHandle.BeginSubmit / FinalizeSubmit`, using the in-game Paradox account. Always confirm
  with the user first.
- Before publishing, check that the icon ImageAsset and the LocaleAssets are packaged with the asset.
- Decompiled (`Game.UI.Menu.PdxAssetUploadHandle` + `AssetUploadPanelUISystem`):
  - `new PdxAssetUploadHandle(mainAsset, params AssetData[] extra)` runs a dependency collector, so one mod can pack
    several prefabs (e.g. the two towers of a twin-tower pair).
  - `modInfo` fields: m_DisplayName, m_ShortDescription, m_LongDescription and m_UserModVersion (all required),
    m_Visibility (Public / Unlisted / Private), m_Tags (auto type tags + picked), m_ModDependencies, m_ExternalLinks.
    m_RecommendedGameVersion is set automatically.
  - Previews: SetPreview(thumbnail) and AddScreenshot(ImageAsset).
  - `BeginSubmit(keepIntermediateFiles)` copies the files into a new upload folder. `FinalizeSubmit()` then calls
    Publish, or UpdateExisting when `updateExisting` is set.
  - `SyncPlatformData()` loads the available tags and the author's existing mods (used for updates).
  - Main-menu sharing only covers saves and maps (StandaloneAssetUploadPanelUISystem). Prefab assets are shared from
    the Editor's panel.
