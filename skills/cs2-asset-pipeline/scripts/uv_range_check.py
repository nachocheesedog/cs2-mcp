# Pre-publish UV check: CS2 imports textures with CLAMP wrap, so any UV outside [0, 1] smears in game.
#   "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b -P uv_range_check.py -- <asset folder>
# Prints one UVR line per FBX; every line must say "outside 0". Also reports unused (loose) vertices, which trip a
# vertex-remap bug in the CS2 FBX importer.
import bpy, glob, os, sys
folder = sys.argv[sys.argv.index("--") + 1]
bad = 0
for f in sorted(glob.glob(os.path.join(folder, "*.fbx"))):
    bpy.ops.wm.read_factory_settings(use_empty=True); bpy.ops.import_scene.fbx(filepath=f)
    for o in bpy.data.objects:
        if o.type != 'MESH': continue
        me = o.data; used = {v for p in me.polygons for v in p.vertices}; loose = len(me.vertices) - len(used)
        if not me.uv_layers: print("UVR", os.path.basename(f), "NO UVS"); bad += 1; continue
        uv = me.uv_layers[0].data
        out = sum(1 for d in uv if not (-1e-4 <= d.uv[0] <= 1.0001 and -1e-4 <= d.uv[1] <= 1.0001))
        bad += bool(out or loose)
        print("UVR", os.path.basename(f), "outside", out, "loose_verts", loose)
print("UVR_RESULT", "OK" if not bad else f"{bad} PROBLEM FILE(S)")
