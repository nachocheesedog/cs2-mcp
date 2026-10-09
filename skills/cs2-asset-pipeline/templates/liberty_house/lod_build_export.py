# Build LOD1 (massing boxes, far < 50% of LOD0) and LOD2 (<= 500 tris) with UVs projected from the S/E/N/W/Top
# elevation atlas, give each mesh one material named like the mesh, and export all three FBX files
# (1 unit = 1 m, pivot = lot centre at ground, object name == file name) into cs2\NA_LibertyHouse.
# Run after: make_atlas.py (system py) -> export_build.py -> lod_render.py -> compose_lod_atlas.py (system py).
import os
exec(open(os.path.expanduser(r"~\blender\liberty-house\scripts\export_build.py")).read())   # (re)builds LOD0 + cs2_material
import json
LL = json.load(open(os.path.join(CS2_BUILD, "lod_layout.json"))); RECT = LL["rects"]; BB = LL["bbox"]; NA = LL["size"]
main = bpy.data.objects[NAME]
C = coll("Export")
TY0, TY1 = TWR_Y

def project_uv(bm):
    uvl = bm.loops.layers.uv.new("UVMap"); bm.normal_update()
    Wx, Wy, H = BB["x1"] - BB["x0"], BB["y1"] - BB["y0"], BB["z1"]
    for f in bm.faces:
        n = f.normal
        if abs(n.z) > 0.7: view = "T"
        elif abs(n.x) > abs(n.y): view = "E" if n.x > 0 else "W"
        else: view = "N" if n.y > 0 else "S"
        x0, y0, w, h = RECT[view]
        for lp in f.loops:
            p = lp.vert.co
            if view == "S": u, v = (p.x - BB["x0"]) / Wx, p.z / H
            elif view == "N": u, v = (BB["x1"] - p.x) / Wx, p.z / H
            elif view == "E": u, v = (p.y - BB["y0"]) / Wy, p.z / H
            elif view == "W": u, v = (BB["y1"] - p.y) / Wy, p.z / H
            else: u, v = (p.x - BB["x0"]) / Wx, (p.y - BB["y0"]) / Wy
            u, v = min(max(u, 0.0), 1.0), min(max(v, 0.0), 1.0)
            lp[uvl].uv = ((x0 + u * w) / NA, 1.0 - (y0 + (1.0 - v) * h) / NA)

def finish(name, bm, lod):
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.001)
    rot180(bm)                                        # same 180 deg turn as LOD0 (street end = front)
    project_uv(bm)
    mat = cs2_material(name, os.path.join(CS2, NAME), f"{NAME}_{lod}")
    for f in bm.faces: f.material_index = 0
    old = bpy.data.objects.get(name)
    if old: bpy.data.objects.remove(old, do_unlink=True)
    if name in bpy.data.meshes: bpy.data.meshes.remove(bpy.data.meshes[name])
    return mesh_obj(name, bm, [mat], C)

PX, (PY0, PY1) = POD_X, POD_Y
# ---- LOD1: podium, transfer band, white slab, dark street-end block, pavilion, cap, mech
bm = bmesh.new()
add_box(bm, -16.0, 16.0, -32.0, 32.0, -0.05, 0.08)                      # plaza (lot base)
add_box(bm, -PX, PX, PY0, PY1, 0.0, POD_TOP + 0.4)
add_box(bm, XW - 0.6, XE + 0.6, TY0 - 0.6, TY1 + 0.6, TWR_BASE, Z0)
add_box(bm, XW, XE, TY0, TY1 - NOTCH, Z0, TWR_TOP + 1.0)
add_box(bm, XW + SLOT, XE - SLOT, TY1 - NOTCH, TY1, Z0, AMEN_TOP)
add_box(bm, XW + 1.5, XE - 1.5, -6.0, TY1 - NOTCH, TWR_TOP, AMEN_TOP)
add_box(bm, XW - 0.6, XE + 0.6, -7.5, TY1 + 0.5, AMEN_TOP, AMEN_TOP + CAP)
add_box(bm, -6.0, 6.0, 2.0, 18.0, AMEN_TOP + CAP, TOP)
lod1 = finish(f"{NAME}_LOD1", bm, "LOD1")
# ---- LOD2: podium + tower
bm = bmesh.new()
add_box(bm, -16.0, 16.0, -32.0, 32.0, -0.05, 0.08)
add_box(bm, -PX, PX, PY0, PY1, 0.0, POD_TOP + 0.4)
add_box(bm, XW, XE, TY0, TY1, POD_TOP + 0.4, AMEN_TOP + CAP)
lod2 = finish(f"{NAME}_LOD2", bm, "LOD2")

def export(ob, extra=()):
    sc = bpy.data.scenes.get("LH_FBX") or bpy.data.scenes.new("LH_FBX")
    for o in list(sc.collection.objects): sc.collection.objects.unlink(o)
    for x in (ob, *extra): sc.collection.objects.link(x)
    path = os.path.join(CS2, NAME, ob.name + ".fbx")
    with bpy.context.temp_override(scene=sc, view_layer=sc.view_layers[0]):
        # CS2's FBXImporter applies a fixed scale 0.01 (expects centimetres) and ignores the file's unit. FBX_SCALE_NONE +
        # global_scale 1 writes raw vertices in cm (UnitScaleFactor 1) -> 1 Blender metre = 1 game metre.
        # (FBX_SCALE_UNITS wrote metres and the building imported at 1/100 size - invisible.)
        bpy.ops.export_scene.fbx(filepath=path, use_selection=False, object_types={'MESH'}, apply_unit_scale=True, global_scale=1.0,
                                 apply_scale_options='FBX_SCALE_NONE', axis_forward='-Z', axis_up='Y', bake_space_transform=True,
                                 use_mesh_modifiers=True, mesh_smooth_type='FACE', add_leaf_bones=False, path_mode='STRIP',
                                 embed_textures=False, use_custom_props=False)
    for x in (ob, *extra): sc.collection.objects.unlink(x)
    return path
# the window sub-mesh is its OWN file: the importer matches "^.*_Win.fbx$" on file names (not object names) to
# apply the Windows/interior-map template; it has no material (CS2 rule for sub-meshes)
paths = [export(main), export(bpy.data.objects[NAME + "_Win"]), export(lod1), export(lod2)]
for o in (lod1, lod2): o.hide_set(True); o.hide_render = True
t0, t1, t2 = tri_count([main]), tri_count([lod1]), tri_count([lod2])
result = {"win_tris": tri_count([bpy.data.objects[NAME + "_Win"]]), "tris": {"LOD0": t0, "LOD1": t1, "LOD2": t2, "LOD1_ratio": round(t1 / t0, 4)}, "fbx": [os.path.basename(p) for p in paths]}
