# One-call districts, batches and fast mode (v0.46)

Laying a district out street by street, then zoning it, then placing services costs one model round trip per step,
each with its own preview, camera move, screenshot and check. On a large district that adds up to dozens of calls,
and the assistant tends to spend them polishing details. These tools move the loop into the server:

| Tool | What it does |
|---|---|
| `cs2_build_district` | outline → street grid → zero-crack flags → services → zoning → game district, in one call |
| `cs2_remove_district` | takes such a district back out (exact undo, or by shape) |
| `cs2_batch` | runs a list of tool calls inside the server: one round trip for a known sequence |
| `cs2_settings fast_mode` | builders skip the camera move, the post-build check and the dry-run forecast unless asked |

Everything is server-side Python on top of the existing bridge methods: no mod rebuild is needed. Zero-crack flags need
mod 0.36 or newer and batched zoning 0.35 or newer; with an older mod the build skips the flags and zones quad by quad.

## `cs2_build_district`

### What happens, in order

1. **Outline.** `polygon`, a box, or `center` + `size` (+ `size_z`, `angle_deg`). The default is an 800 m square
   around the camera's ground point.
2. **Street plan.** The same planner as `cs2_district_grid`: lined up with the parallel streets nearby (phase fitted so
   the spacing to them is an exact pitch), spacing = road width + 96 m so every block gets full 6-cell zoning on both
   sides, clipped to buildable land (no water, cliffs, highways, existing zoning or lots), junctions square.
   Connectors may leave the outline by up to `connect_beyond_m` (default 250) to join the streets around it. Pieces
   that join nothing are dropped unless `connected_only=false`. With no straight streets nearby to line up with, the
   grid takes the angle of the outline's longest edge.
3. **Pause.** The simulation is paused for the rest of the build, so nothing grows into the way of the next step.
   The old speed comes back at the end, also when something fails.
4. **One preview of the whole plan.** If the game's preview reports a money error, the build stops there instead of
   bisecting every refused street.
5. **Streets.** Snapped onto the zone lattice, built in chunks outward from the seed. Streets the game refuses are
   skipped and reported.
6. **Zero-crack flags** on the new streets (`align`, default true).
7. **Services** (`services`, optional), before zoning so growables can't take their lots: free lots of each type along
   the new streets, the first near the centre of the outline (or as far as possible from existing buildings of that
   type), the rest spread out (`min_gap_m`, default 300). Each one is built for real; a refused lot falls through to
   the next best.
8. **Zoning** of the new streets' frontage, only empty cells: arterials with the arterial styles, the other streets
   with the local styles, a list rotating street by street. Arterial styles go first so they take the corners.
9. **Game district** for the outline, with `name` and `policies` (`district`, default true).
10. **Check:** dead ends inside the outline — `fix_these` lists the ones a refused street left; the others are stubs
    at the outline, room to grow — road islands, zoned and unzoned cells.
11. **Report:** streets built / refused, buildings, zoning per style, the check, money spent, the history entries it
    made, and the call that takes it back. `map=true` adds one map of the result.

### Presets

`preset` picks road types, block length and zone families. Every value can be overridden.

| preset | local streets | arterials | arterial every | block length | arterial zoning | local zoning |
|---|---|---|---|---|---|---|
| `suburb` | Small Road | Medium Road | 4th line | 22 cells | commercial low | residential low |
| `residential` (default) | Small Road | Medium Road | 4th line | 22 cells | residential medium, mixed | residential row |
| `downtown` | Medium Road | Large Road | 3rd line | 16 cells | commercial high, mixed | residential high, office high |
| `office` | Medium Road | Large Road | 3rd line | 16 cells | commercial high | office high, office low |
| `industrial` | Medium Road | Large Road | 3rd line | 28 cells | industrial | industrial |

A preset road type the game lacks falls back (Large → Medium → Small); a type you name yourself must exist.
`arterial_type: "none"` builds no arterials.

### Zoning

`zoning` is one of:

- left out — the preset's styles;
- `false` — streets only;
- a zone type name or a family — every new street;
- a list — rotated street by street;
- `{"arterial": ..., "local": ...}` — per role, each a name, a family or a list.

Families: `residential low`, `residential medium`, `residential row`, `residential high`, `residential lowrent`,
`mixed`, `commercial low`, `commercial high`, `office low`, `office high`, `office`, `industrial`. A family resolves to
one of this game's zone types (`cs2_list_zone_types`), preferring the region theme: `theme` (`NA`, `EU`, `UK`...) or,
by default, the theme most of the zoned cells around the outline use. A family the game lacks falls back (row →
medium → low, high commercial → low commercial, …). Exact zone type names and name fragments (`"CHI Row"`) work too.

### Preview first, or build straight away

```json
{"polygon": [[1200, -300], [2000, -300], [2000, 400], [1200, 400]], "preset": "residential", "preview": true, "name": "Riverside"}
```

returns a map with the drafted streets and the zoning forecast, plus a `plan_id`. Then

```json
{"plan_id": "district_20261016_101500", "services": [{"type": "MedicalClinic01"}, {"type": "ElementarySchool01"}]}
```

builds exactly that draft. Arguments given at build time (name, services, zoning, policies...) override the ones the
draft was made with. Without `preview` the first call builds straight away.

### Services

`services = [{type, count?, min_gap_m?, lot_m?, allow_subnets?}]` or plain type names. Names come from
`cs2_list_building_types`. Buildings with built-in nets (most vanilla police stations, clinics with paths, parks,
crematoria) are refused unless that entry has `allow_subnets: true` — the bridge's crash guard. Save first when you use
it.

## `cs2_remove_district`

`plan_id` (default: the newest district built by `cs2_build_district`).

- **Exact undo** when the build's own history entries are still the newest ones of this city load: each is undone
  newest first with `expect_id`, so nothing else is ever undone.
- **By shape** otherwise (or `by_shape=true`): its streets (found by shape, so pieces the game split are included;
  their zoning goes with them), its service buildings (by type and position) and its district (by name).
- `clear_buildings=true` first bulldozes the buildings that grew inside the outline since, in batches of 40 with the
  simulation paused.
- `dry_run=true` says which way it would go and what it would remove.

The undo stack is 50 entries deep and cleared on every load; a district usually takes 5–15 entries.

## `cs2_batch`

```json
{"calls": [
  {"tool": "cs2_rename_district", "args": {"name": "Riverside", "new_name": "Riverside East"}},
  {"tool": "cs2_zone", "args": {"zone_type": "NA Commercial Low", "center": [1500, 100], "width": 96, "depth": 48}},
  {"tool": "cs2_place_building", "args": {"type": "MedicalClinic01", "x": 1610, "z": 40, "facing": [0, 1]}}
], "pause": true}
```

Calls run in order; `stop_on_error` (default true) stops at the first failure. `fast` (default true) sets
`follow_camera` and `check` to false for the tools that have them, unless a call's own args say otherwise. Results come
back shortened: images dropped (counted), each result cut at `max_chars` (default 1500). `cs2_batch`,
`cs2_restart_game`, `cs2_main_menu` and `cs2_load_save` are not allowed inside a batch.

## Fast mode

`cs2_settings fast_mode=true` (kept in `server/settings.json`) changes three defaults for every builder:

| default | normal | fast mode |
|---|---|---|
| move the camera to each change (`follow_camera`) | yes | no |
| junction check after each build (`check`) | yes | no |
| zoning forecast on dry runs (`zoning_check`) | yes | no |

A call can still ask for any of them. Turn it off again for careful detail work.

## Prompts that use it

- *"Build a residential district in the empty land north of the river, between x 1200–2000 and z −300–400, with a
  clinic and a primary school. Call it Riverside."*
- *"Preview a downtown district around the camera, 1.2 km wide. If the forecast is good, build it."*
- *"Remove the district you just built."*
- *"Turn fast mode on for this session."*

## Limits

- The street plan is a grid. Curved waterfronts, ribs off a spine and interchanges still go through the detailed
  tools (`cs2_rim_road`, `tools12.ribs`, `cs2_design_path`…); the skill `cs2-city-building` describes them.
- Services are placed by spacing, not by demand: a new district has no residents yet. Once people live there,
  `cs2_service_sites` places by real demand.
- The build pauses the simulation and borrows the player's tool for each operation, like every MCP edit. The final
  speed is the one from before the call.
