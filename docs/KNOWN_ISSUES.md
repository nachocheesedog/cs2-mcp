# Known issues and limitations

## Crash paths found so far (the tools guard against them)

- **Buildings with built-in networks** (stations, harbours, some service buildings): placing them, and even
  previewing them, has crashed the game. `cs2_place_building` refuses such prefabs unless you pass
  `allow_subnets: true`. Save first when you use it.
- **Fence *nets* as a custom building's sub-nets** crash even a preview. Use fence *prop* sub-objects instead.
- **Mass demolition:** removing hundreds of buildings, then roads right away, crashed the game once. Work in this
  order: dezone → buildings in batches of about 40 → roads in batches of 10–15, with a few seconds of simulation
  between batches. Skip edges whose nodes belong to buildings or stations.
- **Configuring a prefab that already has instances in the city** can crash the game when the change alters its
  component set (the ECS archetype). Configure imported assets before you place them.

## Limitations

- **Windows only** as tested. The build assumes a Steam install. `cs2_restart_game` launches the game through Steam.
- **Game updates can break the mod.** It uses internal game APIs. When a member is renamed, the affected feature
  logs one error and stops working, and the rest keep going. Report it with your game version.
- **Some edits cannot be undone:** terraforming, district policy changes and asset imports. The undo stack (50 deep,
  in memory) is cleared whenever a save loads.
- **Batches are all or nothing.** One refused segment makes the game refuse the whole batch. `skip_failing` (on
  `cs2_place_roads`) bisects with dry runs, drops the bad segments and reports them.
- **Zoning regrows fast.** With high demand, frontage fills within a minute or two. Build every road of a district
  before you zone it, or the new buildings will block later roads.
- **Overlap checks ignore height** in a few inspection heuristics, so an overpass can be reported as a near miss.
- **Anarchy-style mods** hide the game's placement errors. Keep them off while the MCP edits, or you may get
  overlapping, unconnected roads.

## Untested or lightly tested

- Grade-separated crossings built with `elevation`.
- `cs2_prefab_clone` and the window-light overrides on `cs2_asset_import`.
- `cs2_mesh_export` on every kind of prefab (it has been tested on vehicles and buildings).
- Clients other than Claude Code.
