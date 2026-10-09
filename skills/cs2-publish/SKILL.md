---
name: cs2-publish
description: Publishing a finished Cities Skylines II (CS2) custom asset to Paradox Mods, end to end and the same way every time. It covers the readiness gate, the technical checks (UV clamp, loose vertices), the privacy audit (no user name, paths or image metadata), the AI gallery (reference renders, a ChatGPT prompt, then a clean gallery JPG and square thumbnail), the listing text, the in-game Share/Upload steps, and updates. Use it whenever the user wants to publish, share, upload, list or update an asset on Paradox Mods, or asks for thumbnails, gallery or screenshot images, or a description for a mod, even if they name only one of those steps.
---

# CS2 asset → Paradox Mods

A proven route, used for buildings, stations with upgrade companions, vehicle packs and growable packs. Run every stage for every asset, in order, and report each gate's result to the user. **The user presses
Submit**, never Claude, and only after an explicit yes for that specific upload.

Companion skill: **cs2-asset-pipeline** covers modelling, export, import and configure. Its scripts `uv_range_check.py`
and `publish_audit.py` live in `~\.claude\skills\cs2-asset-pipeline\scripts\`. This skill's own scripts are in
`scripts\`.

## The loop (what the user experiences)
Keep it frictionless:
1. **Claude runs Stages 0–2** (readiness, UV and loose-vertex check, privacy audit) and reports a one-line result for
   each gate.
2. **Claude shows the reference renders IN the chat** (e.g. SendUserFile with `display: "render"`). Post ONE
   ready-to-paste ChatGPT prompt that makes every image in sequence, thumbnail last.
3. **The user runs ChatGPT and pastes the images back into this chat.** Pasted images land in the session's
   `images\` folder (e.g. `%TEMP%\claude\<session>\images\<n>.webp`); the image source path is shown in the message.
4. **Claude checks each image is faithful** (and the physics: no above-ground rail yard for an underground station,
   and so on). Then `gallery_tool.py make/extra` puts clean files straight into the game's Screenshots folder, and
   the audit runs. If the game is running, Claude says a restart is needed so the picker sees them.
5. **Claude posts the complete upload sheet in chat, in Upload-panel order:**
   - the Share → Build Package steps (with the companions to add)
   - a table of thumbnail, update checkbox, version, screenshots, tags, visibility and links
   - copy blocks for the name, short description, long description and changelog
   - the expected harmless errors
   - the same sheet saved as `<Name>_listing.md`
   Between steps 2 and 4, Claude also takes the **in-game screenshots** (Stage 3b) after asking to borrow the camera.
6. **The user uploads and presses Submit.** Claude confirms afterwards via the Stage 6 checks and records the result.

## Project rules
- **Privacy:** nothing in the upload may identify the user except their Paradox screen name. That means no Windows
  user name, PC name, e-mail, real name or file paths, and no image metadata (EXIF, PNG text, XMP, C2PA).
- **No backtracking:** find problems BEFORE the upload. Typical post-publish bugs (smeared textures from out-of-range
  UVs, a file path baked into the icon metadata) are all caught by the gates below.
- **One gallery image + one thumbnail** is the default, both made with ChatGPT image generation from Claude's
  reference renders. The user runs ChatGPT; Claude writes the prompt and processes the result.
- **Asset only.** Keep listings and images strictly about the asset.

## Stage 0 — readiness gate
- The asset's final version is exported, validated (`cs2_asset_validate`: 0 problems) and **imported into the game**.
- The user has seen it in game and is happy with it.
- Re-imports use `preset=None` at the main menu. If the geometry is locked, the game needs a restart.
- **Companions:** list every prefab that must ship with the asset, such as upgrades (e.g. a `<Name>Services`
  companion with its `<Name>` station) or props. They go into the same package through Build Package → "Add Asset".

## Stage 0b — save compatibility (the game handles it; VERIFY, don't build)
Saves reference a prefab by `PrefabID` = type + name + hash. A local asset's hash is its asset GUID; a subscribed
asset's hash comes from the Paradox Mods platform ID. The two never match, so switching from a local copy to the
subscription could, in principle, orphan placed buildings.

**Verified:** the Share packager ADDS `ObsoleteIdentifiers` automatically, listing the local ID (type/name/GUID) to
every packaged prefab, companions included. `PrefabSystem` registers those as aliases. Placed copies of several
published assets (+ companions) in a real city resolved to the subscribed `.cok` with 0 missing assets. Nothing to add
by hand.

**Switch-over procedure (after publishing):**
1. Close the game.
2. Move `ImportedData\<Name>\` plus the loose `<Name>_*` files (and companions) to a backup folder outside the game's
   data folders.
3. Subscribe and add the mod to the active playset.
4. Load the real city PAUSED and don't save. Run the step-5 checks straight away, then unpause (speed 1) or quit:
   autosaves written while a freshly loaded city sits paused crash on load (ClimateSystem.ApplyWeatherEffects,
   seen repeatedly; see cs2-asset-pipeline troubleshooting.md).
5. Run `cs2_missing_assets` (our prefabs must not be listed) and `cs2_query_buildings` (the placed copies still have
   their real type names). `cs2_asset_configure describe` shows `asset_path` under `.cache\Mods\pdx_mods\<mod id>`.
6. Only then save normally. Keep the backup. Never restore it while subscribed: that would load two copies.

## Stage 1 — technical checks (all must pass)
- **VT gate (a package with an empty VT cache crashes every subscriber's game at start):**
  - Before the final import: `python ~/.claude/skills/cs2-asset-pipeline/scripts/optimize_textures.py <proj>/cs2/<Name> --check`
    must print `VT_CHECK ... OK` for every asset: each material's maps all the same size and >= 512 px.
  - After Build Package, BEFORE Submit: `python ~/.claude/skills/cs2-publish/scripts/package_vt_check.py
    "%LocalLow%\Colossal Order\Cities Skylines II\.packages"`. It must say OK with registered materials > 0. 0 means the
    package crashes every subscriber's game at start.
```bash
"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b -P ~/.claude/skills/cs2-asset-pipeline/scripts/uv_range_check.py -- <proj>/cs2/<Name>
```
- Every FBX must report `outside 0 loose_verts 0`, ending in `UVR_RESULT OK`.
- **Why UVs matter:** CS2 imports textures CLAMPED (`DefaultTextureImporter`). Any UV outside [0, 1] smears into
  streaks in game, while Blender still looks fine.
  - **Fix:** slice faces at whole U values and shift them back into [0, 1] in the exporter (a `wrap_u()` post-pass in
    the project's export script).
- **Why loose vertices matter:** they trip a vertex-remap bug in the game's FBX importer.
- **In-game check** (daylight, close up): tiled textures show their pattern on every face, the icon shows in the build
  menu, windows light up at night, and every LOD looks right.
- **Service-access gate** (any building people live or work in): `cs2_path_test id=<placed building> mode=hearse` and
  `mode=ambulance` must both return found=true for every building type/layout in the upload (mod >= 0.42). Growable
  packs with door-side access markers can block hearses + ambulances - see cs2-asset-pipeline
  troubleshooting "Dead waiting for hearse".

## Stage 2 — privacy audit
```bash
python ~/.claude/skills/cs2-asset-pipeline/scripts/publish_audit.py <Name> "<game>/Screenshots" <proj>/cs2_build
```
- It must print `0 with identifying data in what gets uploaded`.
- `SOURCE (not uploaded)` lines are expected: FBX headers and Blender intermediates carry paths, but they never leave
  the PC.
- **Icons:**
  - The icon is the classic leak: Blender writes the `.blend` path and EXIF into every render.
  - `cs2_asset_import` and `cs2_asset_configure` hand the game a metadata-stripped copy (`_clean_png`), and
    `render_icon()` turns the stamps off.
  - An icon imported by an older tool version may still be dirty. Re-apply it with
    `cs2_asset_configure name=<Name> icon=<cs2_build>/<Name>_Icon.png`, then re-run the audit.
- **Descriptions** must not contain real names, local paths or tool names.

## Stage 3 — gallery (one gallery image + one thumbnail)
1. **Reference renders.** Render the asset alone on a transparent background, auto-framed, lit from the camera's side,
   with no stamps:
   ```bash
   "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b <proj>.blend -P ~/.claude/skills/cs2-publish/scripts/render_ai_refs.py -- <proj>/renders/ai_refs <Coll1,Coll2> <front_x> <front_y>
   python ~/.claude/skills/cs2-publish/scripts/gallery_tool.py strip <proj>/renders/ai_refs
   python ~/.claude/skills/cs2-publish/scripts/gallery_tool.py sheet <proj>/renders/ai_refs <scratch>/refs.jpg
   ```
   - The collections are the asset's SOURCE collections, never `*_Export`, LOD or presentation ones.
   - `front` is the street side in Blender coordinates: `0 1` (street at +Y), `0 -1` (street at -Y), `1 0` (+X).
   - A tall asset gets `ref_three_quarter`, `ref_golden`, `ref_aerial` and `ref_street_up` in portrait. A wide asset
     gets landscape `ref_street` instead of the look-up.
   - Look at the contact sheet before sending anything. Check the whole asset is in frame and lit, not a black
     backlit silhouette.
   - Show the refs to the user IN the chat (e.g. SendUserFile with `display: "render"`).
2. **Prompt.** Use `references/prompt_template.md`, which has one multi-image prompt that makes every image in
   sequence, including a square thumbnail. Fill in the building-specific keep-list (its signature features) and the
   setting (the real city/neighbourhood, or "a dense, realistic downtown").
3. **Process the user's pick.** The downloads usually land as `.webp` in the chat's images folder. Then:
   ```bash
   python ~/.claude/skills/cs2-publish/scripts/gallery_tool.py make <gallery image> <ShortName> <proj>/cs2_build --cx <asset centre 0..1> [--thumb <square image>]
   ```
   - This writes `<ShortName>_Gallery.jpg` (full frame) and `<ShortName>_THUMBNAIL.jpg` (1024², under 2 MB, no
     metadata) to cs2_build AND the game's Screenshots folder, which is the only folder the upload picker lists.
   - Without `--thumb`, the thumbnail is a square crop around `--cx`. Show it to the user.
   - Put extra gallery shots in with `gallery_tool.py extra <img> <Short>_Gallery_<n>_<label> <build>` (10 max).
   - **The picker only sees files that were in the Screenshots folder when the game STARTED.** Files added while it
     runs never show up: no `.cid` gets written, and there is no rescan. So make the gallery files BEFORE the user
     launches the game, or restart it afterwards. .jpg is fine; the upload re-saves everything as JPG anyway.
   - **Naming:** the thumbnail is always `<Short>_THUMBNAIL.jpg`, in caps, so it's easy to spot in the picker.
   - Windows file names are case-insensitive: `X_Thumbnail.jpg` IS `X_THUMBNAIL.jpg`. Never move or delete an "old"
     thumbnail by name after making a new one.
   - Re-run the Stage 2 audit on the Screenshots folder.
4. **Faithfulness check.** Compare the AI image with the reference: same silhouette, signature features and colours.
   If the model redrew the building, have the user regenerate it with the fix-up line from the template. Suggest one
   real in-game screenshot as an extra, so players see what they actually get.

### Pack galleries: "row on a street" lineups
For growable packs, LINEUPS - buildings side by side along one street, labels on the kerb, a level 1-5 progression
strip - read better than per-building grid tiles. Render the SHIPPED FBX + atlas, not the flat Blender preview
materials (a lineup render script plus a sheet script that stacks strips; grid catalog renders as an alternative).
Gotchas: in-game ColorProperties tints are sRGB - convert to linear before using them in a Blender Mix node (else roofs
render ~2x too light); frame orthographic cameras on real mesh VERTICES, not bound_box corners (the back-top corner of
a deep lot sits far above the roof -> big empty band); extend the ground far so no edge shows.
Gallery limit is 10 images: mix ~4 lineups with ~6 in-game shots.

## Stage 3b — in-game screenshots (every listing gets real game shots)
Players want to see what they actually get, so the gallery also carries 4–6 **clean, graded in-game shots** next to the
AI image. They must look like proper game screenshots: no UI, no notification icons, no overlays or road names, no
selection outline.
- **Tools (mod ≥ 0.30):**
  - `cs2_camera_goto x z zoom yaw pitch`: frame the shot. The camera orbits a ground pivot: `zoom` = distance (m),
    `pitch` = look-down angle, `yaw` = heading (degrees). Read the current values back with `cs2_camera`.
  - `cs2_photo_mode`: the game's own Photo Mode properties by id. No arguments lists them (value, min, max); `set`
    applies them; `reset=true` restores every default. **Always reset at the end** so the player's view is normal again.
  - `cs2_screenshot clean=true save_png=<proj>/renders/ingame/<n>_<label>.png max_width=1600`: the Photo Mode capture
    path (RenderingSystem.hideOverlay + UI view off, `frames` wait for TAA), restored afterwards. `supersize=2` for a
    sharper master.
- **The camera belongs to the user.** Ask before a photo session; it moves their view. Pause the simulation (`cs2_set_speed
  0`) so cars and pedestrians hold still. Afterwards, reset photo mode and restore speed (the camera can stay).
- **Shot list** (landscape, the asset filling ~60–70 % of the frame):
  1. **Hero:** 3/4 view from the street side, pitch 20–30°, golden hour (`Time of Day` ≈ 7.5 or 18.5).
  2. **Aerial:** pitch 45–55°, midday, the whole lot + neighbours for context.
  3. **Street level:** pitch 5–12°, close to the frontage, the signature ground floor.
  4. **Detail:** the asset's signature feature up close (terraces, crown, canopy...).
  5. **Night:** `Time of Day` ≈ 21–22, lit windows; lift `ColorAdjustments.postExposure` +0.3…0.6.
  6. Optional: the far LOD from across the city, or a before/after.
- **Grading that worked** (subtle; judge every frame and back off if it looks fake): `ColorAdjustments.contrast`
  +8…15, `ColorAdjustments.saturation` +5…12, `WhiteBalance.temperature` +5…10 for golden hour (−5 for a crisp blue
  midday), `Vignette.intensity` 0.15–0.25, `Bloom.intensity` a touch at night. Depth of field only for street and detail
  shots, never the hero or aerial (it hides the asset).
- **Faithful:** in-game shots must show the asset exactly as it ships. Don't pick an angle that hides a known flaw; fix
  the flaw instead.
- **Into the upload:** `gallery_tool.py strip <dir>` (ScreenCapture PNGs carry no metadata, but strip anyway), then
  `gallery_tool.py extra <png> <Short>_Gallery_<n>_<label> <proj>/cs2_build` for each keeper. As with Stage 3, the
  picker only lists files that were there when the game STARTED, so the game needs a restart before the upload.
- Show every keeper to the user IN the chat and let them pick.

## Stage 4 — listing text
Write `<proj>/cs2_build/<Name>_listing.md` from `references/listing_template.md`:
- Name: 60 characters max. Short description: 200 max. Long description: markdown-free plain text.
- Tags: up to 10, easier to edit on the website later.
- Visibility: Unlisted first if the user wants to test the subscribed copy; Public otherwise.
- Version: 1.0.0, or +1 for an update, with a changelog.

Keep it factual: the real building, architect and year, what's modelled, the gameplay (zone, households, lot,
upgrades), and "no mods or DLC required" when true.

- **Say what's included**, the way a player would want to know it. For stations: tracks and stops per level with
  depths, the concourse, what's at street level, and what each upgrade adds.
- **Verify the facts against the IMPORTED prefab, not just the export.** The ImportedData `.Prefab` is JSON;
  `ObjectSubNets` entries are net GUIDs plus Bezier curves. Count them per net and per depth.
- **Get the build-menu group** with `cs2_asset_configure name=<Name> describe=true describe_components=["UIObject"]
  save=false`. For example, `TransportationTrain` means Transportation > Train.
- **Wide assets with hidden parts** (underground tracks, pits): say so explicitly in the ChatGPT prompt ("ALL tracks
  underground, NO rail yards at street level"). Otherwise the model invents an above-ground rail yard.

## Stage 5 — upload (the user clicks; Claude guides)
Official guide: https://cs2.paradoxwikis.com/Assets:_Package,_Share_and_Upload
1. The user is signed in to their Paradox account in game. Without it, Share is greyed out.
2. Editor → **Workspace** (put the asset there from the Asset Browser) → the **⋯** menu next to its name → **Share**.
   Right-click only toggles a favourite.
3. **Build Package**: check the dependency list (only the asset's own parts plus its companions; add companions with
   "Add Asset"), then **Build Package**. The game may freeze for a moment.
4. **Upload Asset** panel: thumbnail (the grey box → `<Short>_THUMBNAIL`), name, short and long description,
   screenshots (+ → `<Short>_Gallery`), tags, visibility. Paste from the listing file.
   **Max 2 MB per image, and the game re-encodes on upload**, so a 1.3 MB 2560x1440 JPG still failed. gallery_tool
   writes 1920x1080 at quality 85 (~150-650 KB). **HARD LIMIT: at most 10 screenshots.** The panel lets you pick more,
   but the backend then rejects the whole Submit (`NERR_201: BACKEND_ERROR` / `InvalidInput` / `screenshotNames: Must
   have 10 or less elements`, seen in Player.log). Screenshot file names must end in jpg/jpeg/png and be at most 128
   characters. After a failed Submit, check Player.log for `[UI] [ERROR]` lines, fix, and submit again.
5. The user presses **Submit**.

**Big packs: "Add Asset" is O(n²) and freezes the game for a long time.** Decompiled: every added asset calls
`PdxAssetUploadHandle.AddAdditionalAsset` -> `DependencyCollector.AddAsset(refresh: true)` -> a full `Refresh()` that
re-walks every root's dependencies, plus `RefreshAssetList/RefreshDependencyLists`. Select-all on a ~1,550-item
growable pack kept the game "Not Responding" (one core at 100 %, ~9 GB) for 30+ min. It is working, not hung: tell the
user not to click, and watch `(Get-Process Cities2).Responding`. Never add ZONES as roots: a zone root makes the
collector Load() every prefab in the game to find its buildings (reverse-dependency scan). A better approach would be a
mod tool that builds the handle with `DependencyCollector.AddAssets(all)` (one refresh). Keep packs lean: clones for an
extra zone cost little download but square the add time.

Harmless errors while the Upload panel opens: `InvalidOperationException: Invalid binary data stream...` and/or
`InvalidCastException` in `PrefabAsset.Load`, reached through `ModTags.GetTags` / `SyncPlatformData`.
- The game tries to read the packaged prefabs to suggest type tags. It can't read our prefabs (they're saved as JSON),
  so the load returns null and it skips the suggestions.
- The upload still works. The user adds the tags by hand.

Claude cannot type into the game UI: there is no computer use and no upload tool. It gives the text in copy blocks.

## Stage 6 — after the upload
- An `ArgumentNullException` in `PackageHelper.DeletePackage` after Submit is **harmless**. The publish had already
  finished; it's the cleanup that crashed.
  - It is a generic game bug: it has named an icon on one upload and a LOD .Geometry on another.
  - The temporary `.cok` in `.packages` still gets removed. Check that folder afterwards: a leftover `.cok` of an asset
    also in ImportedData means two copies get loaded.
- Confirm it in game under Paradox Mods → **ME**. The public author page and search don't show Unlisted/Private mods,
  or mods still being processed.
- Subscribe on a test playset and check the icon, textures and gameplay.
- Only after that, move the asset's local files out of `ImportedData`, so the local and subscribed copies don't
  collide.
- Record the result in the asset's project notes: version, visibility, date, and any leftover to-dos.

## Stage 7 — updates (proven route; follow it step by step)
Official guide: cs2.paradoxwikis.com/Assets:_Package,_Share_and_Upload (update section). This variant differs in
one point: while updating, the author is NOT subscribed to the mod (see "Why").

**A. Prepare**
1. Final files on disk. Gates: `optimize_textures.py <proj>/cs2/<Name> --check` (VT gate) = OK, Stage 1 UV check,
   Stage 2 privacy audit.
2. When updating an asset you also run locally: UNSUBSCRIBE from the mod on mods.paradoxplaza.com BEFORE launching. Merely switching it off in the
   playset is not enough: it is re-downloaded at every start.
3. If the game was running the subscription (after a Stage 0b switch-over), then with the game CLOSED:
   - move `.cache\Mods\pdx_mods\<mod id>_*` out (park it, don't delete);
   - move the local copy back from the Stage 0b backup folder into ImportedData. The configured prefab has to be
     there: a preset=None import keeps its configuration.
4. Start the game. Player.log must have no `[FATAL]` and no `Duplicate prefab ID ... pdx_mods/<mod id>` for this mod.
5. At the MAIN MENU: `cs2_asset_import preset=None` for every asset of the package, then re-apply what a re-import
   resets (vehicle lights / configure_cars, LodProperties.m_Bias ...). Only one import runs at a time (the mod runs
   one import); if another agent/session drives the same game, coordinate the menu slot with it.

**B. Share**
6. Editor -> Workspace -> main asset -> ⋯ -> Share -> Build Package. Add every companion with Add Asset (a depot doesn't
   pull in its vehicles or upgrades; a building doesn't pull in its extensions). Ignore `Prop_*` entries from Find It.
7. Package gate BEFORE Submit: `python scripts/package_vt_check.py "%LocalLow%\Colossal Order\Cities Skylines II\.packages"`
   must say OK: registered materials > 0, and every textured .Surface has a VTSurface.
8. Upload panel: tick **Update Previously Shared Asset**. The game code (AssetUploadPanelUISystem) shows either
   - a DROPDOWN "<mod id> - <name>" (the author's mods fetched): pick it, and name / descriptions / tags PREFILL from
     the platform (SetInfoFromExisting); or
   - a TEXT BOX "Existing asset ID" (the author list not fetched): type the id and paste every field by hand
     (nothing prefills).
   Never submit with the id empty: that path publishes a NEW mod instead of an update.
9. Version = the next WHOLE number after the mod page's "v.N" (1 -> 2; not 1.0.1 / 1.1.0). A changelog is required.
10. Thumbnail + screenshots do NOT prefill while unsubscribed (they come from the local download). Re-pick them.
    - To reproduce the previous set exactly: the earlier submit left its images in
      `.cache\Mods\local\<guid>\.metadata\` (preview.jpg + screenshotN.jpg, in order). Match them to the Screenshots
      folder with a small-thumbnail pixel diff (exact matches diff ~0.1).
    - Max 10 screenshots. The picker only lists files that were in Screenshots when the game STARTED.
11. Submit. The ArgumentNullException in PackageHelper.DeletePackage afterwards is harmless (the cleanup failing).

**C. Verify**
12. On mods.paradoxplaza.com/mods/<mod id>/Windows (JS page: read it with a browser tool):
    - LAST UPDATED and SIZE changed;
    - ALL VERSIONS lists v.N;
    - the author page still has the same number of mods (no duplicate);
    - `.packages` is empty again.
13. Later (optional): switch the game to the subscription (Stage 0b). Subscribe, then with the game
    closed the local copy moves to the backup folder. Only ONE copy may be loaded.

**Why / failure modes seen**
- Start crash FATAL "Can't test if a material is a duplicate with registered materials"
  (AtlasMaterialsGrouper.IsDuplicate <- TextureStreamingSystem.Initialize) = a mounted package whose VT MidMips cache is
  EMPTY. A package with 64 px / mixed-size textures has every material dropped from VT when packaged. That is the
  cause, not duplicate copies (those only give WARNs). Recovery: game closed, park `pdx_mods\<mod id>_*`, unsubscribe.
- Subscribing in game while it runs auto-enables the mod and hot-loads it next to the local copy ("Duplicate prefab
  ID ... assetdb://paradoxmods", "Failed to enable/disable Mod '<mod id>'"). An update submitted from that state becomes
  a new mod.
- Run Stages 1-2 again for every update.
