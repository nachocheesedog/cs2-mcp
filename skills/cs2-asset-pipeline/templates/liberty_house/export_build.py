# Build the CS2 LOD0 meshes from LH_Podium + LH_Tower (incl. the warm accent glow):
#   NA_LibertyHouse      main mesh, ONE material named like the mesh, UV-mapped onto the atlas strips
#                        (cs2_build\atlas_layout.json from make_atlas.py)
#   NA_LibertyHouse_Win  window sub-mesh, NO material: CS2's importer interior-maps every island of it (_Win suffix):
#                        parallax rooms, lit at random at NIGHT only. Every outward-facing glass pane is re-emitted as its
#                        own island, cut per floor and per ~3 m bay (one island = one window; rooms group per floor).
# The asset is rotated 180 deg about Z (the dark north entrance end must face the street; CS2 puts the road at the
# Blender -Y end). Pivot = lot centre at ground (z = 0). Collection LH_Export.
import os
exec(open(os.path.expanduser(r"~\blender\liberty-house\scripts\lh_common.py")).read())
import json
ROOT = os.path.expanduser(r"~\blender\liberty-house")
CS2 = os.path.join(ROOT, "cs2"); CS2_BUILD = os.path.join(ROOT, "cs2_build")
NAME = "NA_LibertyHouse"; WIN = NAME + "_Win"
L = json.load(open(os.path.join(CS2_BUILD, "atlas_layout.json"))); S = L["strips"]; NPX = L["size"]; TILE = L["tile_m"]
C = coll("Export", clear=True)
ROT180 = True
WIN_BAY = 3.0          # target window island width (m)

MAT2STRIP = {"LH_White": "white", "LH_Glass": "glass", "LH_Mullion": "mullion", "LH_GreyAlu": "grey_alu",
             "LH_BalGlass": "bal_glass", "LH_Spandrel": "spandrel", "LH_Green": "green", "LH_Paver": "paver",
             "LH_Water": "water", "LH_CWFrame": "cw_frame", "LH_Reveal": "reveal", "LH_Accent": "accent", "LH_Plaza": "plaza"}
FLOOR_Z = sorted(set([GF + k * POD_FL for k in range(N_POD + 1)] + [Z0 + k * TYP for k in range(N_TWR + 1)] + [AMEN_TOP]))

def gather(coll_names):
    """Main bmesh (tagged with strips) + window bmesh (separate islands, tagged 'glass' for the LOD bake)."""
    bm = bmesh.new(); lay = bm.faces.layers.int.new("strip")
    bw = bmesh.new(); lw = bw.faces.layers.int.new("strip")
    names = list(S.keys()); dg = bpy.context.evaluated_depsgraph_get(); stats = {"windows": 0, "inward_glass_dropped": 0}
    def new_face(b, l, vs, strip, ref_n):
        try: nf = b.faces.new(vs)
        except ValueError: return
        nf[l] = names.index(strip)
        if ref_n is not None:
            nf.normal_update()
            if nf.normal.dot(ref_n) < 0: nf.normal_flip()
    for cn in coll_names:
        for o in bpy.data.collections[cn].objects:
            if o.type != 'MESH': continue
            ev = o.evaluated_get(dg); me = ev.to_mesh()
            mats = [ms.material.name if ms.material else "" for ms in ev.material_slots]
            tmp = bmesh.new(); tmp.from_mesh(me); tmp.transform(o.matrix_world); ev.to_mesh_clear()
            tmp.normal_update(); vmap = {}
            for f in tmp.faces:
                mname = mats[f.material_index] if f.material_index < len(mats) else ""
                strip = MAT2STRIP.get(mname, "mullion"); n = f.normal.copy()
                if abs(n.z) > 0.7 and strip in ("glass", "spandrel", "mullion", "cw_frame", "reveal", "bal_glass", "grey_alu"):
                    strip = "roof"
                if strip == "glass" and abs(n.z) < 0.3 and len(f.verts) == 4:
                    cs = [v.co for v in f.verts]
                    ax = 0 if abs(n.x) > abs(n.y) else 1; ua = 1 - ax
                    plane = sum(c[ax] for c in cs) / 4
                    u0, u1 = min(c[ua] for c in cs), max(c[ua] for c in cs)
                    z0, z1 = min(c.z for c in cs), max(c.z for c in cs)
                    if u1 - u0 < 0.5 or z1 - z0 < 0.5:                       # thin edge of a glass box -> main mesh
                        strip = "spandrel"
                    elif n[ax] * plane < 0:                                  # back face of a glass pane (faces inward)
                        stats["inward_glass_dropped"] += 1; continue
                    else:
                        cuts = [z0] + [z for z in FLOOR_Z if z0 + 0.05 < z < z1 - 0.05] + [z1]
                        nb = max(1, round((u1 - u0) / WIN_BAY)); us = [u0 + (u1 - u0) * i / nb for i in range(nb + 1)]
                        def P(u, z):
                            p = [0.0, 0.0, z]; p[ax] = plane; p[ua] = u; return bw.verts.new(p)
                        for za, zb in zip(cuts, cuts[1:]):
                            for ub0, ub1 in zip(us, us[1:]):
                                new_face(bw, lw, [P(ub0, za), P(ub1, za), P(ub1, zb), P(ub0, zb)], "glass", n); stats["windows"] += 1
                        continue
                vs = []
                for v in f.verts:
                    if v.index not in vmap: vmap[v.index] = bm.verts.new(v.co)
                    vs.append(vmap[v.index])
                new_face(bm, lay, vs, strip, None)
            tmp.free()
    return bm, lay, bw, lw, names, stats

def uv_map(b, l, names):
    uvl = b.loops.layers.uv.new("UVMap"); b.normal_update()
    for f in b.faces:
        st = S[names[f[l]]]; y0, h, mode = st["y0"], st["h"], st["mode"]
        n = f.normal; zs = [v.co.z for v in f.verts]; zmin, zmax = min(zs), max(zs)
        ys = [v.co.y for v in f.verts]; ymin, ymax = min(ys), max(ys)
        vertical = abs(n.z) < 0.5
        t = Vector((-n.y, n.x, 0.0))
        t = t.normalized() if (vertical and t.length > 1e-6) else Vector((1, 0, 0))
        shift = 0.0
        if mode == "floor":                         # decorrelate storeys: shift the pane pattern per floor (whole panes)
            k = int(round(zmin / 0.5)); shift = ((k * 7919) % 11) * 1.5
        for lp in f.loops:
            p = lp.vert.co
            um = (p.dot(t) if vertical else p.x) + shift
            if mode == "floor" and vertical and zmax - zmin > 1e-3:
                vpx = y0 + h - (p.z - zmin) / (zmax - zmin) * h
            elif mode == "ground" and not vertical and n.z > 0 and ymax - ymin > 1e-3:
                vpx = y0 + h - (p.y - ymin) / (ymax - ymin) * h      # each 4 m plaza band spans the strip
            else:
                vpx = y0 + h / 2
            lp[uvl].uv = (um / TILE, 1.0 - vpx / NPX)

def rot180(b):
    if ROT180: bmesh.ops.rotate(b, verts=b.verts, cent=(0, 0, 0), matrix=Matrix.Rotation(math.pi, 3, 'Z'))

bm, lay, bw, lw, names, wstats = gather(["LH_Podium", "LH_Tower"])
bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.001)          # main only: window islands must stay separate
uv_map(bm, lay, names); uv_map(bw, lw, names)
rot180(bm); rot180(bw)
xs = [v.co.x for v in bm.verts]; ys = [v.co.y for v in bm.verts]; zs = [v.co.z for v in bm.verts]
bbox = {"x": (round(min(xs), 2), round(max(xs), 2)), "y": (round(min(ys), 2), round(max(ys), 2)), "z": (round(min(zs), 2), round(max(zs), 2))}

def cs2_material(name, folder, prefix):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    try: m.use_nodes = True
    except Exception: pass
    nt = m.node_tree; nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial"); b = nt.nodes.new("ShaderNodeBsdfPrincipled"); nt.links.new(b.outputs[0], out.inputs[0])
    def img(tag, colour):
        p = os.path.join(folder, f"{prefix}_{tag}.png")
        im = bpy.data.images.load(p, check_existing=True); im.reload()
        im.colorspace_settings.name = 'sRGB' if colour else 'Non-Color'
        n = nt.nodes.new("ShaderNodeTexImage"); n.image = im; n.interpolation = 'Cubic'; return n
    bc, em, mm, nm = img("BaseColor", True), img("Emissive", True), img("MaskMap", False), img("Normal", False)
    nt.links.new(bc.outputs["Color"], b.inputs["Base Color"])
    nt.links.new(em.outputs["Color"], b.inputs["Emission Color"]); b.inputs["Emission Strength"].default_value = 1.0
    sep = nt.nodes.new("ShaderNodeSeparateColor"); nt.links.new(mm.outputs["Color"], sep.inputs[0])
    nt.links.new(sep.outputs[0], b.inputs["Metallic"])
    inv = nt.nodes.new("ShaderNodeMath"); inv.operation = 'SUBTRACT'; inv.inputs[0].default_value = 1.0
    nt.links.new(mm.outputs["Alpha"], inv.inputs[1]); nt.links.new(inv.outputs[0], b.inputs["Roughness"])
    nmap = nt.nodes.new("ShaderNodeNormalMap"); nt.links.new(nm.outputs["Color"], nmap.inputs["Color"]); nt.links.new(nmap.outputs[0], b.inputs["Normal"])
    return m
MAT = cs2_material(NAME, os.path.join(CS2, NAME), NAME)
for f in bm.faces: f.material_index = 0
for nm_ in (NAME, WIN):
    old = bpy.data.objects.get(nm_)
    if old: bpy.data.objects.remove(old, do_unlink=True)
    if nm_ in bpy.data.meshes: bpy.data.meshes.remove(bpy.data.meshes[nm_])
o = mesh_obj(NAME, bm, [MAT], C)
ow = mesh_obj(WIN, bw, [], C)                                         # no material (CS2 rule for window sub-meshes)
result = {"tris": tri_count([o]), "win_tris": tri_count([ow]), "verts": len(o.data.vertices), "bbox": bbox, **wstats}
