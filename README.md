# CS2 MCP

An [MCP](https://modelcontextprotocol.io) server plus a companion code mod that let an AI assistant such as
Claude Code see and build in a running **Cities: Skylines II** city.

The assistant can read the city: roads, zoning, terrain, water, traffic, services, transit and demand. It can take
screenshots and render top-down maps so it can check its own work. It can build and fix things through the game's own
tools: roads, bridges, ramps, zoning, districts, buildings, trees and props. Every edit can be previewed, is checked
for the same errors the game shows the player, and can be undone. There is also a Blender → game asset pipeline that
imports custom buildings without opening the Editor.

```
MCP client (Claude Code, …)
        │  stdio (MCP)
        ▼
server/cs2_mcp.py ──── HTTP, 127.0.0.1:17654 only ────▶ CS2MCP mod (inside the game)
 129 tools: planning, geometry,                          runs each command on the game's main
 maps, checks                                            thread; edits go through a custom tool
                                                         (preview → error check → apply → undo)
```

> **Status: experimental hobby project.** It works well day to day, but it drives a live game through internal APIs.
> Save your city before large edits. Test big operations in a copy of your save first. Not affiliated with Colossal
> Order or Paradox Interactive.

## Contents

- [What it can do](#what-it-can-do)
- [Requirements](#requirements)
- [Install](#install)
- [First steps](#first-steps)
- [Safety model](#safety-model)
- [Documentation](#documentation)
- [Skills (agent playbooks)](#skills-agent-playbooks)
- [Troubleshooting](#troubleshooting)
- [Uninstall](#uninstall)
- [Development](#development)
- [Credits](#credits)
- [License](#license)

## What it can do

| Area | Examples |
|---|---|
| **Look** | `cs2_screenshot` (optionally clean, via Photo Mode), `cs2_map` (top-down render of terrain, water, zoning, lots, roads by type or traffic, with a coordinate grid and overlays), `cs2_tour`, `cs2_inspect` (dead ends, near misses, sharp junctions, islands, congestion, unused land) |
| **Roads** | straight and curved roads, grids, rings, arcs, one-way pairs, smoothed paths with elevation profiles, ramps split exactly on a carriageway, piers, quays along a shoreline, parallel back streets, trimming, replacing, and grade and clearance design for interchanges |
| **Zoning** | cell-exact zoning and rezoning, rule-based zoning palettes, batched zoning in one undo step, a zoning forecast (lost depth, dead gaps), zone-crack detection and a road half-alignment fix |
| **City health** | demand with its factors, building warnings per district, service coverage (health, fire, police, parks, education, deathcare…), budgets, transit ridership, foot traffic, commute flows, site finders for parks, services and stations |
| **Buildings and objects** | place any building with an exact lot centre and facing, install service upgrades, place trees and props along a shoreline or road or scattered in an area, terraform |
| **Districts** | create, rename and delete districts, set their policies |
| **Assets** | validate a Blender export, import it into the running game, configure it (copy settings from a vanilla building, lot size, households, icon, name, description), clone prefabs, export game meshes and textures back to Blender |
| **Session** | list and load saves, return to the main menu, restart the game, change the simulation speed |

The full list with parameters is in **[docs/TOOLS.md](docs/TOOLS.md)**.

## Requirements

- **Windows** with Cities: Skylines II (Steam). The build and the game-restart tool assume Windows. Other platforms are
  untested.
- **.NET SDK 6 or newer** to build the mod. It targets `net472` and compiles directly against the game's own DLLs, so
  you need no Unity install and no modding toolchain.
- **Python 3.10+** for the server, with `numpy`, `scipy`, `Pillow` and `contourpy`
  (`pip install -r server/requirements.txt`).
- An MCP client. The instructions below use [Claude Code](https://claude.com/claude-code). Any stdio MCP client should
  work.

## Install

### 1. Build and install the mod

Close the game first. The game loads mods only at startup, and the build overwrites the DLL.

```bash
git clone https://github.com/nachocheesedog/cs2-mcp.git
cd cs2-mcp/mod
dotnet build -c Release
```

A Release build copies `CS2MCP.dll` to
`%USERPROFILE%\AppData\LocalLow\Colossal Order\Cities Skylines II\Mods\CS2MCP\`. A Debug build only compiles.

If the game is not in the default Steam folder, pass the path to its `Managed` folder:

```bash
dotnet build -c Release -p:CS2Managed="D:\SteamLibrary\steamapps\common\Cities Skylines II\Cities2_Data\Managed"
```

Start the game. Local mods in that folder load automatically. The game's log folder then contains `CS2MCP.log`, whose
first line is `CS2MCP <version> loading`.

### 2. Install the server's Python packages

```bash
pip install -r server/requirements.txt
```

### 3. Register the server with your MCP client

Claude Code:

```bash
claude mcp add cs2 -- python C:\path\to\cs2-mcp\server\cs2_mcp.py
```

Other clients: register a stdio server whose command is `python <repo>/server/cs2_mcp.py`. See
[examples/mcp.json](examples/mcp.json).

### 4. Check the connection

With the game running, ask your assistant to run `cs2_status`. It should report the mod version, the game mode and,
once a city is loaded, its money, population and road count.

## First steps

Try these prompts once a city is loaded:

- *"Take a screenshot of where the camera is, then draw a map of the 1 km around it with zoning and traffic."*
- *"Check this district for dead ends, near-miss junctions and unused land."*
- *"Dry-run a 4 × 3 grid of small roads here with 96 m zoning depth between streets, and show me the preview."*
- *"Which districts are short on healthcare coverage? Suggest sites for a clinic."*

Good habits:

- **Dry run first.** Every editing tool takes `dry_run: true`. The game builds its normal preview, and the tool reports
  what would fail and why (for example `{"Overlap Existing": 2}` with locations), without building anything.
- **Hold and look.** `hold: true` keeps the preview in the game so you can take a screenshot before calling
  `cs2_preview_commit` or `cs2_preview_cancel`.
- **Undo is precise.** Each edit returns an `undo_id`. Call `cs2_undo` with `expect_id` so the tool never undoes the
  wrong thing.
- **Build roads first, then zone.** With high demand, zoned frontage fills within a minute or two, and the new
  buildings block any road you add later. [docs/ROAD_LAYOUT.md](docs/ROAD_LAYOUT.md) has the rules for layouts that
  zone cleanly.

## Safety model

The bridge is a local control port into your game, so it is locked down:

- **Local only.** The HTTP listener binds `127.0.0.1:17654` and never an external interface.
- **No browser access.** Any request carrying an `Origin` or `Sec-Fetch-*` header is refused (403), and the body must
  be `application/json` (415 otherwise). A web page therefore cannot drive the game through your browser (CSRF).
  Bodies are capped at 16 MB and JSON nesting at 64 levels.
- **Timed-out commands never run later.** A command still queued when its caller times out is dropped. A command that
  had already started is marked abandoned, and its preview is discarded instead of applied.
- **No edits while a save loads.** During loading only status, catalogue, history and quit calls answer.
- **Undo belongs to one city load.** The stack is cleared when a save loads, so an undo can never touch a different
  save.
- **Risky prefabs need explicit opt-in.** Buildings with built-in networks (stations, some service buildings) can crash
  the game even in a preview. `cs2_place_building` refuses them unless you pass `allow_subnets: true`.
- **Vanilla assets stay untouched.** `cs2_asset_configure` edits and saves only your own imported assets. Changes to
  vanilla prefabs (`allow_vanilla`) apply to the running game only and are never saved.
- **File writes are fenced.** `cs2_screenshot save_png` writes only under your temp folder, the game's Screenshots
  folder, `%USERPROFILE%\cs2-mcp`, `%USERPROFILE%\blender`, and any folder listed in
  `…\ModsData\CS2MCP\save_png_roots.txt`.
- **No telemetry.** Neither the mod nor the server sends anything off your machine.

Some operations cannot be undone: terraforming, district policy changes and asset imports. Saving before large jobs is
still the best protection.

## Documentation

| Document | What it covers |
|---|---|
| [docs/TOOLS.md](docs/TOOLS.md) | every tool and its parameters (generated from the server) |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | how the mod and server fit together, the bridge protocol, adding a tool |
| [docs/ROAD_LAYOUT.md](docs/ROAD_LAYOUT.md) | how the game zones along roads, and layout rules that avoid gaps and cracks |
| [docs/ASSET_PIPELINE.md](docs/ASSET_PIPELINE.md) | Blender → game import without the Editor, and the importer pitfalls |
| [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md) | crash paths found so far, limitations, untested areas |
| [CHANGELOG.md](CHANGELOG.md) | version history |

## Skills (agent playbooks)

The tools say *what* the assistant can do; the [`skills/`](skills) folder says *how to do it well*. These are
[Claude Code](https://claude.com/claude-code) skills: Markdown playbooks plus helper scripts,
written up from many sessions of building real cities and assets with this MCP. Each lesson is recorded as a rule
with its symptom, cause and fix, so the assistant does not repeat a mistake.

| Skill | Use it for |
|---|---|
| [`cs2-city-building`](skills/cs2-city-building/SKILL.md) | road layouts that zone perfectly, highway junctions and ramps, district rebuilds, services (deathcare, healthcare), working safely in a live save |
| [`cs2-asset-pipeline`](skills/cs2-asset-pipeline/SKILL.md) | a real building from research → Blender model → trim-sheet atlas, LODs, night windows → import, configure, icon, placement, with every importer pitfall found so far |
| [`blender-mcp-architecture`](skills/blender-mcp-architecture/SKILL.md) | modelling detailed buildings in Blender through the Blender MCP: bmesh helpers, procedural materials, progress renders |
| [`cs2-publish`](skills/cs2-publish/SKILL.md) | publishing to Paradox Mods: readiness and texture gates, privacy audit, gallery images, listing text, updates |

Install by copying the folders you want into `~/.claude/skills/` (Windows: `%USERPROFILE%\.claude\skills\`):

```bash
cp -r skills/* ~/.claude/skills/
```

Claude Code picks them up in the next session and loads one automatically when a task matches it (for example *"recreate
this tower as a CS2 asset"* loads `cs2-asset-pipeline`). The asset skills expect the
Blender MCP server to be connected for modelling steps; the scripts
assume Blender projects under `~/blender/<project>` and can be pointed elsewhere by editing the path at the top.

The skills are opinionated: they ask before restarting the game, loading a save, importing an asset or publishing, and
they prefer a test copy of your save for big edits. Adapt the rules to your own workflow.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `cannot reach the CS2MCP mod at http://127.0.0.1:17654` | The game is not running, the mod did not load, or another program holds the port. Check `CS2MCP.log` in the game's `Logs` folder. |
| The build cannot find `Game.dll` | Pass `-p:CS2Managed=...` (see [Install](#1-build-and-install-the-mod)). |
| New mod code has no effect | The game loads the DLL only at startup. Restart it after a Release build. |
| `another tool was selected` | You switched tools in the game during an edit. The server retries for about 20 seconds, then gives up. |
| A tool is stuck busy | `cs2_tool_reset`. A watchdog also resets orphaned jobs automatically. |
| An edit fails with `Overlap Existing` | The new road lands too close to an existing junction, road end or building. Run `cs2_map` or a held preview plus `cs2_screenshot` to see the red outline, then move the end at least about 40 m away or aim at an existing node. |
| New tools do not appear in the client | The server hot-reloads `server/*.py` and sends `tools/list_changed`. Some clients still need the MCP server reconnected. |

## Uninstall

Delete `%USERPROFILE%\AppData\LocalLow\Colossal Order\Cities Skylines II\Mods\CS2MCP\`. To keep it but disable it,
rename the folder to `.CS2MCP`: the game skips folders that start with a dot. Then remove the MCP server from your
client (`claude mcp remove cs2`).

Files the mod may leave behind in the game's data folder: `ModsData\CS2MCP\` (prefab overrides, extra screenshot
roots) and `ImportedData\<Asset>` for any assets you imported.

## Development

```
mod/        C# code mod (net472). Bridge.cs = HTTP listener + version; McpBridgeSystem.cs = command dispatch on the main
            thread; McpToolSystem.cs = the editing tool; Operations.cs = place / bulldoze / zone / undo; one file per
            feature area after that.
server/     MCP server (stdio). cs2_mcp.py = protocol, tool registry, hot reload; tools*.py, pave.py, zoning.py,
            align.py, survey.py = planners, geometry, map renderer; assets.py = asset validation/import.
tools/      import_mesh_export.py (game mesh -> Blender), gen_tools_doc.py (regenerates docs/TOOLS.md).
docs/       reference docs.
```

The offline tests use a synthetic town, so they need no game:

```bash
cd server
python test_grid.py && python test_v02.py && python test_survey.py && python test_district.py
```

After you add or change a tool, regenerate the reference with `python tools/gen_tools_doc.py`.
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#adding-a-tool) walks through adding a tool end to end.

Issues and pull requests are welcome. Please include your game version and the relevant lines of `CS2MCP.log`.

## Credits

The asset skills stand on work by the **Cities: Skylines Modding** Discord community. Their knowledgebase guides on
LODs, window shaders, window layout and Blender pipelines — by **Feindbold**, **kjmci**, **Macwelshman**, **TheOcter**,
**cbudd** and the other knowledgebase authors — are summarised, with full credit per guide, in
[`skills/cs2-asset-pipeline/references/community_guidelines.md`](skills/cs2-asset-pipeline/references/community_guidelines.md).
The official [Asset Pipeline: Buildings](https://cs2.paradoxwikis.com/Asset_Pipeline:_Buildings) wiki page by Paradox
Interactive / Colossal Order and the wiki's contributors is summarised in
[`wiki_buildings.md`](skills/cs2-asset-pipeline/references/wiki_buildings.md). Please read the originals; they have far
more detail, images and tools than these notes.

## License

[MIT](LICENSE). Cities: Skylines II is a trademark of Paradox Interactive. This project is not affiliated with or
endorsed by Paradox Interactive or Colossal Order. It ships no game code or assets. The mod compiles against the DLLs
of your own game install.
