# Render orthographic albedo + emissive elevations (S, E, N, W, Top) of NA_LibertyHouse for the LOD texture atlases.
# Private scene + material override that outputs the atlas BaseColor / Emissive as flat emission (Standard view,
# black world) -> unlit texture data, not shaded renders.
import bpy, os, json
from mathutils import Vector
OUTD = os.path.expanduser(r"~\blender\liberty-house\cs2_build\lod_src"); os.makedirs(OUTD, exist_ok=True)
NAME = "NA_LibertyHouse"; PPM = 8.8                 # S+E+N+W+Top widths (3x32 + 2x64 m = 224 m) must fit 2048 px
ob = bpy.data.objects[NAME]
obw = bpy.data.objects.get(NAME + "_Win")          # window sub-mesh (rendered with the atlas glass strip)
xs = [v.co.x for v in ob.data.vertices]; ys = [v.co.y for v in ob.data.vertices]; zs = [v.co.z for v in ob.data.vertices]
bb = dict(x0=min(xs), x1=max(xs), y0=min(ys), y1=max(ys), z0=0.0, z1=max(zs))
sc = bpy.data.scenes.get("LH_LODBake") or bpy.data.scenes.new("LH_LODBake")
for o in list(sc.collection.objects): sc.collection.objects.unlink(o)
sc.collection.objects.link(ob)
if obw: sc.collection.objects.link(obw)
w = bpy.data.worlds.get("LH_Black") or bpy.data.worlds.new("LH_Black"); w.color = (0, 0, 0)
try: w.use_nodes = False
except Exception: pass
sc.world = w
sc.render.engine = bpy.data.scenes["Scene"].render.engine
sc.view_settings.view_transform = 'Standard'; sc.view_settings.look = 'None'; sc.view_settings.exposure = 0.0
sc.render.film_transparent = True; sc.render.image_settings.file_format = 'PNG'; sc.render.image_settings.color_mode = 'RGBA'
try: sc.eevee.taa_render_samples = 16
except Exception: pass
def override(kind):
    m = bpy.data.materials.get("LH_Override_" + kind) or bpy.data.materials.new("LH_Override_" + kind)
    try: m.use_nodes = True
    except Exception: pass
    nt = m.node_tree; nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial"); em = nt.nodes.new("ShaderNodeEmission"); tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.get(f"{NAME}_{kind}.png"); nt.links.new(tex.outputs["Color"], em.inputs["Color"])
    uv = nt.nodes.new("ShaderNodeUVMap"); uv.uv_map = "UVMap"; nt.links.new(uv.outputs[0], tex.inputs[0])
    nt.links.new(em.outputs[0], out.inputs[0]); return m
cam_d = bpy.data.cameras.get("LH_LODCam") or bpy.data.cameras.new("LH_LODCam"); cam_d.type = 'ORTHO'; cam_d.clip_end = 5000
cam = bpy.data.objects.get("LH_LODCam") or bpy.data.objects.new("LH_LODCam", cam_d)
if cam.name not in sc.collection.objects: sc.collection.objects.link(cam)
sc.camera = cam
H = bb["z1"]; Wx = bb["x1"] - bb["x0"]; Wy = bb["y1"] - bb["y0"]
cxm, cym = (bb["x0"] + bb["x1"]) / 2, (bb["y0"] + bb["y1"]) / 2
VIEWS = {"S": ((cxm, bb["y0"] - 500, H / 2), (0, 1, 0), Wx, H), "N": ((cxm, bb["y1"] + 500, H / 2), (0, -1, 0), Wx, H),
         "E": ((bb["x1"] + 500, cym, H / 2), (-1, 0, 0), Wy, H), "W": ((bb["x0"] - 500, cym, H / 2), (1, 0, 0), Wy, H),
         "T": ((cxm, cym, H + 500), (0, 0, -1), Wx, Wy)}
info = {"bbox": bb, "views": {}}
for kind in ("BaseColor", "Emissive"):
    sc.view_layers[0].material_override = override(kind)
    for v, (loc, d, wm, hm) in VIEWS.items():
        cam.location = loc
        cam.rotation_euler = Vector(d).to_track_quat('-Z', 'Y').to_euler()
        if v == "T": cam.rotation_euler = (0, 0, 0)
        cam_d.ortho_scale = max(wm, hm)
        sc.render.resolution_x = max(8, round(wm * PPM)); sc.render.resolution_y = max(8, round(hm * PPM)); sc.render.resolution_percentage = 100
        sc.render.filepath = os.path.join(OUTD, f"{v}_{kind}.png")
        bpy.ops.render.render(write_still=True, scene=sc.name)
        info["views"][v] = {"w_m": wm, "h_m": hm, "px": [sc.render.resolution_x, sc.render.resolution_y]}
sc.view_layers[0].material_override = None
sc.collection.objects.unlink(ob)
if obw: sc.collection.objects.unlink(obw)
json.dump(info, open(os.path.join(OUTD, "views.json"), "w"), indent=1)
result = {"views": {k: v["px"] for k, v in info["views"].items()}, "bbox": {k: round(v, 2) for k, v in bb.items()}}
