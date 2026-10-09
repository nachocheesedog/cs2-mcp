# Configure recipes (cs2_asset_configure)

`cs2_asset_configure name=<prefab> [copy_from] [remove_components] [add_components] [set] [lot_width lot_depth access_type]
[icon title description locale] [describe] [save=true]`

- **`copy_from`** clones a vanilla prefab's gameplay components. It skips `ObjectSubObjects/Areas/Lanes/Nets`,
  `NetSubObjects`, `AreaSubObjects`, `EffectSource`, `ObsoleteIdentifiers` and `SpawnableObject`, because those belong to
  the template's own geometry.
- **`set`** takes `{"Component.m_Field": value}`, or `{"m_Field": value}` for fields on the prefab itself. Values can be:
  - numbers, bools or enum names
  - a prefab name, for prefab references
  - a list, for arrays
  - null, to clear a field
- **`describe=true`** dumps every component's public fields. Use it before guessing field names.
- Changes are saved to the asset by default. Configuration survives `preset=None` re-imports.

## Residential signature building, always available (Liberty House)
```json
{"name": "NA_LibertyHouse", "copy_from": "NA_ResidentialHighSignature01",
 "remove_components": ["Unlockable", "BuildingTerraformOverride"], "add_components": ["BuildingProperties"],
 "set": {"m_LotWidth": 4, "m_LotDepth": 8, "BuildingProperties.m_ResidentialProperties": 440,
         "UIObject.m_Icon": null, "UIObject.m_Priority": 210, "SignatureBuilding.m_UnlockEventImage": null}}
```
- Households: roughly 1 per 80–100 m² of residential floor area. Liberty House used 440 for about 22 residential floors.
- Other `SignatureBuilding` fields: `m_ZoneType`, `m_XPReward`, `m_UnlockEventImage`. The `UIObject.m_Group` for
  residential signatures is `SignaturesResidential`.
- A signature building can be placed once per city.
- **Gotcha:** `cs2_asset_import preset=SignatureBuilding` adds a `BuildingProperties` whose
  defaults include `m_AllowedManufactured: ["Software"]` and `m_SpaceMultiplier: 6`. That quietly lets an office
  company rent the building. Always set `"BuildingProperties.m_AllowedManufactured": []` (and `m_AllowedSold: []` for
  pure residential). Check with `describe_components=["BuildingProperties"]`.

## Mixed use / hotel (tenant restricted by what it sells)
Vanilla residential and mixed buildings have NO `BuildingProperties`; they inherit the zone's `ZoneProperties`.
- `NA Residential High`: residential 6, sold none.
- `NA Residential Mixed`: residential 2, space 2.5, sold = ConvenienceFood, Food, Meals, Paper, Furniture, Vehicles,
  Plastics, Electronics, Chemicals, Pharmaceuticals, Beverages, Textiles, Entertainment, Recreation.

Vanilla hotels (`NA_CommercialHotel01_*`) are commercial with `m_AllowedSold: ["Lodging"]` and space multiplier 5.
So a signature with households PLUS a hotel is:
```json
{"set": {"SignatureBuilding.m_ZoneType": "NA Residential Mixed", "BuildingProperties.m_ResidentialProperties": 416,
         "BuildingProperties.m_AllowedSold": ["Lodging"], "BuildingProperties.m_AllowedManufactured": [],
         "BuildingProperties.m_SpaceMultiplier": 6}}
```
The list restricts which ONE commercial company moves in:
- `["Lodging"]` gives a hotel.
- `["Meals"]` gives restaurants.
- `["Entertainment", "Recreation"]` gives leisure.

`describe` works on zone prefabs too (mod ≥ 0.23: `NA Residential Mixed`).

## Other starting points
Check the exact template names first. Use `cs2_list_building_types`, or `describe` a candidate.
- **Office / commercial signature**:
  1. Find the template's real name first with `cs2_list_building_types filter=Signature`, then filter by `Office` /
     `Commercial`. Names follow the residential pattern (`NA_ResidentialHighSignature01`), but look them up; don't guess.
  2. Run `describe=true` on the template to see its `BuildingProperties` and `SignatureBuilding` values.
  3. `copy_from` it, `remove_components=["Unlockable","BuildingTerraformOverride"]`, clear `UIObject.m_Icon` +
     `SignatureBuilding.m_UnlockEventImage`, and set the lot.
  - **There is no "workplaces" field.** The job count is computed per company (decompiled
    `CompanyUtils.GetIndustrialAndOfficeFittingWorkers`; commercial uses `GetCommercialMaxFittingWorkers`, same shape):
    `workers = ceil(m_MaxWorkersPerCell × lotW × lotD × (1 + 0.5 × level) × BuildingProperties.m_SpaceMultiplier)`.
    - `m_MaxWorkersPerCell` comes from the company type (office process), not the building.
    - `level` = 1 unless the building is a growable (`SpawnableBuildingData`).
  - To scale the jobs for a tall tower, set `BuildingProperties.m_SpaceMultiplier`. Start from the template's value
    (describe), multiply it by the ratio of floor area you want, place the building, and read the real job count with
    `cs2_building_status` / `cs2_inspect`. Adjust from there.
  - `BuildingProperties.m_AllowedSold` / `m_AllowedManufactured` decide which company types (e.g. software, financial,
    media) can move in. Keep the template's values unless the user wants a specific tenant type.
  - Households stay 0 for a pure office (`m_ResidentialProperties`). A mixed-use tower can set both.
- **City service** (e.g. a train station): import with `preset=CityServiceBuilding` or `template=<vanilla station>`.
  Stations need tracks and platforms as sub-nets, which the pipeline does not generate yet (planned: a layout JSON from
  Blender). Placing buildings with sub-nets can crash the game; `cs2_place_building` refuses them unless
  `allow_subnets=true`, after saving.
- **Static prop / landmark with no simulation**: `preset=StaticObject`.
- **Growable (zoned) building**: `preset=SpawnableBuilding`. The level and zone come from the name (`_L#`) and the
  components.

## Build-menu info (mod ≥ 0.14, imported/user assets only)
- `icon=<png/jpg path>`: stored as an ImageAsset next to the prefab (`ImportedData/<Name>/<Name>_Icon_<hash>.png`).
  `UIObject.m_Icon` is then set to its `assetdb://` URI. A changed picture gets a new hash, so there is no stale cache.
- `title=`, `description=`: stored as LocaleAssets `Assets.NAME[<name>]` and `Assets.DESCRIPTION[<name>]` next to the
  prefab (en-US by default, `locale=` for others). They are also added live as a memory source, so the UI updates
  without a restart. Existing entries for other locales are kept.
- `cs2_asset_import` applies all three automatically from `<project_root>_build/<Name>_meta.json` + `<Name>_Icon.png`.

## Transit stations: pedestrians, stops, and upgrades
These were learned the hard way on a large custom station. Read vanilla prefabs before inventing anything:
`cs2_asset_configure name=TrainStation01 describe=true`. The output is large, so save it and summarise it with a script.

**Pedestrian space: copy the VANILLA pattern (TrainStation01).** Don't build a web of Invisible Pedestrian Paths.
- **Hangaround Area** sub-areas cover each platform, full length, with node y at platform height. Waiting passengers
  stand there, and each one is also a spawn location for the stops (HangaroundArea adds SpawnLocationData).
- **Walking Area** sub-areas (a NavigationArea) are polygons for free movement: plaza, concourse, galleries, bridges.
  Each node carries its own y. A polygon can't have a hole, so a ring is built as 4 rectangles.
- **Invisible Pedestrian Path** is only for LEVEL CHANGES (stairs, escalators, overpasses), with its ends inside the
  areas.
- **Pedestrian Access Location** markers go on the lot edge where a street meets a walking area. Vanilla has 40.
- A "Walking Area" without an access location is unreachable.
- Stops: see the stop pattern in `station_layout.py`. An **Integrated Passenger Train Stop** sits 2.5 m from the track
  centre, and an **Integrated Subway Stop - Middle** 1.5 m from the track centre, 1 m up. Use ONE stop per track side;
  trains are long.

**Shops / "Station Services" = a SERVICE UPGRADE, not a commercial building.**
- Vanilla `TrainStation01 Services` and `TrainStation03 Services` are `BuildingExtensionPrefab`s with:
  - `ServiceUpgrade.m_Buildings = [parent]`
  - `m_ExternalLot: false` (the mesh sits inside the parent's lot)
  - `TransportStation.m_ComfortFactor +0.3`, `Workplace 4`, `ServiceConsumption`
  - `CompanyObject {m_SelectCompany: true, [Commercial_Bar, FoodStore, LiquorStore, Restaurant]}`. This is cosmetic:
    it only picks which company props show. It is NOT a trading business.
- The naming pattern is `<Station> Services`. `list_building_types` does NOT list extensions, so probe the names with
  `cs2_asset_configure name="<X> Services" save=false`.
- **Comfort:** `TransportStationData.Combine` ADDS the upgrades to the station, and `TransportStationAISystem` sets
  `saturate(sum × efficiency)`. The maximum is 1.0, so station 0.5 + services 0.5 = the maximum.
- Import an upgrade with `preset=BuildingExtension` (or template=`TrainStation03 Services`). Then set
  `ServiceUpgrade.m_Buildings` to your station, plus the comfort.
- A real trading shop would need a separate commercial (e.g. signature) building on its own lot.

## Company brand signs on shops (verified in game)
Vanilla "Brand Graphics" props show the logo art of whichever company rents the building: they carry
`SpawnableBrandDummy` (the concrete ones) or are `PlaceholderObject` randoms. Put them in `ObjectSubObjects` with
`m_ParentMesh 0`, identity rotation on a +z-facing (street) facade, position ON the wall face. `m_Probability` 100 to
always show (vanilla uses 60). Sizes (ObjectGeometryData m_Size, x = along the facade):
- `SignNeonSmall01/02` 2.49 x 0.34, `SignNeonMedium01/02` 4.99 x 0.68, `SignNeonLarge01/02`: flat, wall-attached.
- `SignSidewaySmall01/02` + `SignSidewaySmallRandom01` project 1.56 m (0.37 tall); Medium 2.54 m (1.50 tall); Large: blade signs.
- `SignFrontwaySmall/Medium/Large01/02` + `...Random01` (2.7 / 4.9 / 9.6 m wide, ~1.2-1.8 deep, not wall-attached).
Vanilla shop modules: `NA_MixedStorefront01_L3_1x1` (BuildingExtension: Neon + 2 Sideway + wall light) and
`EU_MixedStorefront_1x1_Random_01` (placeholder) can be dropped in as ground sub-objects (parent -1) as mod packs do.
Mixed zones: "NA Residential Mixed" (2x4..6x4 blocks), "NA Mixed Medium Row" (1-wide rows), plus EU/FR/JP/USNE variants.
Mixed buildings need no extra component - the zone gives the commercial ground floor. Name lookups
(cs2_asset_configure describe / prefab_entity) work at the MAIN MENU; list_* tools need a loaded city.
Mod packs (.cok) are zips of JSON prefabs (refs as `$fstrref:"UnityGUID:..."`); describe the prefab in game to resolve names.

## Per-building colour variation (ColorProperties)
Verified on a growable townhouse pack.
- Tint = MULTIPLY of BaseColor in ControlMask-painted areas (R, G, B = colour channels 0/1/2; A is not read). R may be
  partial; G/B must be hard 0/1. It can only darken, so paint the tinted atlas strips a neutral grey (keep the detail).
- Put `ColorProperties` on the render prefab `<Name> Mesh` only (LOD meshes fall back to it; their textures still need the
  same mask). `m_ColorVariations` = list of `{"m_Colors": [[r,g,b,1],[...],[...]], "m_VariationGroup": "Roof"}` (always
  4 numbers, colours sRGB = srgb(lin(target)/lin(base))), `m_VariationGroups` = `[{"m_Name":"Roof","m_Probability":100,
  "m_MeshSyncMode":"SameIndex"}]` so a building's several meshes (house + rear additions) pick the SAME set,
  `m_VariationRanges` [2,6,8]. Never an empty group name.
- Picked per building from its saved PseudoRandomSeed (stable across loads). Buildings already in a save stay untinted until
  they regrow - choose a neutral base that still looks right untinted (e.g. roof grey sRGB 165).
