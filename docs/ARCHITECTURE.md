# Architecture

```
MCP client ──stdio──▶ server/cs2_mcp.py ──HTTP 127.0.0.1:17654──▶ Bridge.cs (listener thread)
                                                                   │ queue
                                                                   ▼
                                                        McpBridgeSystem (game main loop)
                                                          ├─ reads: ECS queries, systems
                                                          └─ edits ─▶ McpToolSystem (ToolUpdate phase)
                                                                      definitions → game preview → errors → apply / hold
```

## The mod (`mod/`)

This is a Cities: Skylines II code mod (`IMod`) built as `CS2MCP.dll` for `net472`. It compiles directly against
`Cities2_Data\Managed\*.dll`. It uses no Burst jobs, `SystemAPI` or `IJobEntity` code generation, so the official
modding toolchain is not needed.

| File | Role |
|---|---|
| `Mod.cs` | Registers the two systems and starts the bridge. |
| `Bridge.cs` | `HttpListener` on `127.0.0.1:17654`. `GET /health` is answered on the listener thread; `POST /rpc` is validated (no `Origin` / `Sec-Fetch-*`, JSON only, 16 MB, depth 64), queued, and the request thread waits up to `timeout_s` for the main thread. Holds `Version`. |
| `McpBridgeSystem.cs` | Runs in `SystemUpdatePhase.MainLoop`. Drains the queue and dispatches each `method` (a big `switch`). Pumps long jobs (imports, terraforming, screenshots) across frames. Owns the undo stack and the load/save hooks. |
| `McpToolSystem.cs` | A custom `ToolBaseSystem` that edits the world the way the built-in tools do. It writes `CreationDefinition`s, lets the game build a `Temp` preview, counts errors and collects their reasons from notification icons, then applies, holds or clears. A watchdog resets orphaned jobs. |
| `Operations.cs` | Road placement (curves, elevation, crossing splits, in-batch T junctions), bulldoze, replace, zone (batched), undo. |
| `Perception.cs` | `road_network`, `zone_blocks`, `terrain_grid`. |
| `Buildings.cs`, `Parks.cs`, `Terraform.cs`, `Areas.cs`, `Districts.cs` | Placing buildings, objects, terrain brushes, areas and districts. |
| `Coverage.cs`, `Transit.cs`, `Traffic.cs`, `Demand.cs`, `Budget.cs` | City statistics read the same way the game's info views compute them. |
| `AssetImport.cs`, `PrefabClone.cs`, `Overrides.cs`, `MeshExport.cs`, `TextureExport.cs`, `MeshInfo.cs`, `Obsolete.cs` | Asset import and configuration, prefab tools, mesh and texture export. |
| `Screenshot.cs`, `Photo.cs` | Screenshots (normal, or clean through Photo Mode's capture path) and Photo Mode properties. |
| `Session.cs` | Saves, loading, main menu, quit. |
| `Json.cs` | A small dependency-free JSON reader and writer. |

Logs go to `%USERPROFILE%\AppData\LocalLow\Colossal Order\Cities Skylines II\Logs\CS2MCP.log`.

### Bridge protocol

```http
POST /rpc HTTP/1.1
Host: 127.0.0.1:17654
Content-Type: application/json

{"method": "place_roads", "params": {"segments": [...], "dry_run": true, "timeout_s": 60}}
```

The reply is `{"result": {...}}` or `{"error": "message"}`. Every method the mod understands is reachable from the MCP
client through `cs2_raw {method, params}`, and from a shell through `server/bridge_call.py <method> '<json>' [timeout_s]`
(use `@file.json` for long payloads).

### Edit lifecycle

1. The server sends an edit (`place_roads`, `zone`, `place_building`…).
2. `McpToolSystem` takes over the active tool slot, writes definitions, and waits for the game to generate the preview.
3. It reads error notifications on the `Temp` entities and returns them as `error_reasons` and `error_locations`.
4. `dry_run` → the preview is cleared. `hold` → the preview stays until `preview_commit` / `preview_cancel`.
   Otherwise → the edit is applied if there are no errors, and an undo entry is pushed.
5. The player's previous tool and selected prefab are restored.

## The server (`server/`)

`cs2_mcp.py` implements MCP over stdio (JSON-RPC, protocol `2024-11-05`) with no SDK dependency. `TOOLS` is a list of
`(name, description, input_schema, handler)` tuples. `HANDLERS` maps each name to its handler.

The server watches `server/*.py`. When a file changes it reloads every module in the folder and sends
`notifications/tools/list_changed`, so tools can be edited without restarting the client.

| Module | Role |
|---|---|
| `cs2_mcp.py` | Protocol, bridge client (`rpc()` retries when the player switches tools mid-edit), tool registry, core tools. |
| `survey.py` | The world model built from `road_network` / `zone_blocks` / `terrain_grid`, the `cs2_map` renderer (Pillow), and the `cs2_inspect` checks. |
| `geom.py` | Frames, Béziers, offsets. |
| `pave.py` | District grids, rim roads, connect options, plan build and removal. |
| `sites.py` | Where a new district can go (v0.46): one survey of the city, dead ends and empty street sides as anchors, the clear way in, free land per stretch, a dry run against city limits and money; the lattice that joins the grid to the anchor. |
| `zoning.py` | Zoning forecast (depth lost, dead gaps, shallow frontage), `cs2_zoning_check`. |
| `align.py` | Zone-crack detection and the road half-alignment planner. |
| `tools2.py` … `tools16.py` | Feature groups added over time: paths and pairs (2), ramps and extending lines (3), waterfronts (4), zoning and city health (5), services (6), transit (7), parks and objects (8), service sites (9), interchange design (10, 11), ribs (12), road snapshots, profiles and junction detail (13), highway forks (14), waterfront backs and bulk upgrades (15), one-call districts and batches (16 — it gets this module's planner and builders through `_district_api()`, resolved at call time so hot reload keeps working). |
| `assets.py`, `fbx_raw.py` | Offline FBX and texture validation, import orchestration, `_meta.json` and icon application. |
| `gamesession.py` | Restarting the game through Steam, waiting for the menu, loading a save. |

## Adding a tool

1. **Mod side** (only if you need new game access): add a `case "my_method":` in `McpBridgeSystem`, implement it in a
   file for its feature area, and bump `Bridge.Version`. Read-only queries run directly. Edits must go through
   `McpToolSystem` (definitions + preview) or an end-frame command buffer. Never change ECS structure in the middle of
   a frame.
2. `dotnet build -c Debug` to compile-check. Then close the game and run `dotnet build -c Release` to deploy.
3. **Server side:** write `def t_my_tool(a): return rpc("my_method", {...})` (or a planner in a `tools*.py` module) and
   add a `("cs2_my_tool", description, schema, t_my_tool)` entry to `TOOLS`. Gate on the mod version if older mods
   lack the method.
4. `python tools/gen_tools_doc.py` to regenerate [TOOLS.md](TOOLS.md), and add an entry to the changelog.

Rules of thumb that came out of real crashes are in [KNOWN_ISSUES.md](KNOWN_ISSUES.md).
