# AI-gallery reference renders: the asset ALONE on a transparent background, no render stamps (no paths), auto-framed
# from its bounding box. An image model then builds a city around it without redrawing the asset.
#   blender -b <proj>.blend -P render_ai_refs.py -- <out_dir> <Coll1,Coll2,...> <front_x> <front_y>
# front = the street side in BLENDER coordinates (e.g. 0 -1 when the street is at -Y, 0 1 for +Y, 1 0 for +X).
# Tall assets (height > footprint) get portrait shots incl. a street-level look-up; wide ones get landscape shots.
# Afterwards run `python gallery_tool.py strip <out_dir>` (Blender still writes EXIF).
import bpy, math, os, sys
from mathutils import Vector
exec(open(os.path.expanduser(r"~\.claude\skills\blender-mcp-architecture\helpers\lbt_helpers.py")).read())
a = sys.argv[sys.argv.index("--") + 1:]
out, colls, front = a[0], a[1].split(","), Vector((float(a[2]), float(a[3]), 0.0)).normalized()
os.makedirs(out, exist_ok=True)
scn = bpy.context.scene
keep = set(colls)                                                # render only the asset's collections (+ parents/children)
for c in bpy.data.collections:
    if any(k in [x.name for x in c.children_recursive] for k in colls): keep.add(c.name)
for n in colls: keep |= {x.name for x in bpy.data.collections[n].children_recursive}
for c in bpy.data.collections: c.hide_render = c.name not in keep
objs = [o for n in colls for o in bpy.data.collections[n].all_objects if o.type == 'MESH' and not o.hide_render]
pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
lo = Vector([min(p[i] for p in pts) for i in range(3)]); hi = Vector([max(p[i] for p in pts) for i in range(3)])
ctr, size = (lo + hi) / 2, hi - lo
H, R = size.z, max(size.x, size.y) / 2
for at in dir(scn.render):
    if at.startswith("use_stamp"): setattr(scn.render, at, False)
scn.render.film_transparent = True
scn.render.image_settings.file_format = 'PNG'; scn.render.image_settings.color_mode = 'RGBA'
cam = bpy.data.objects.get("AIREF_Cam") or bpy.data.objects.new("AIREF_Cam", bpy.data.cameras.new("AIREF_Cam"))
if cam.name not in scn.collection.objects: scn.collection.objects.link(cam)
scn.camera = cam; cam.data.clip_end = 20000; cam.data.sensor_fit = 'AUTO'; cam.data.sensor_width = 36

def rot(v, deg):
    r = math.radians(deg); return Vector((v.x * math.cos(r) - v.y * math.sin(r), v.x * math.sin(r) + v.y * math.cos(r), 0))

def shot(name, az, elev_deg, lens, res, fill, sun_el, sun_off, target_z=None, dist=None, cam_z=None):
    """sun_off = sun direction relative to the camera's side (deg); 0 = sun behind the camera, +-50 = raking light."""
    w, h = res; tall_fit = max(H, 2 * R * (w / h if h > w else 1)) if h >= w else max(H * w / h, 2 * R)
    fov = 2 * math.atan(18 / lens)                                # AUTO fit: 36 mm across the larger dimension
    d = dist or (tall_fit / 2 / fill) / math.tan(fov / 2) + R
    tz = ctr.z if target_z is None else target_z
    dirv = rot(front, az); e = math.radians(elev_deg)
    loc = Vector((ctr.x, ctr.y, tz)) + dirv * d * math.cos(e) + Vector((0, 0, d * math.sin(e)))
    if cam_z is not None: loc.z = cam_z
    cam.location = loc; cam.data.lens = lens
    cam.rotation_euler = (Vector((ctr.x, ctr.y, tz)) - loc).to_track_quat('-Z', 'Y').to_euler()
    sv = rot(dirv, sun_off)                                         # lbt physical_sky: dir to sun = (sin A, -cos A)
    physical_sky(scn.name, elev_deg=sun_el, az_deg=math.degrees(math.atan2(sv.x, -sv.y)))
    scn.render.resolution_x, scn.render.resolution_y, scn.render.resolution_percentage = w, h, 100
    scn.render.filepath = os.path.join(out, name + ".png"); bpy.ops.render.render(write_still=True)
    return scn.render.filepath

tall = H > 2 * R
P, L = (1024, 1536), (1536, 1024)
done = []
if tall:
    done += [shot("ref_three_quarter", -35, 6, 80, P, 0.85, 30, -45),
             shot("ref_golden", 35, 4, 80, P, 0.85, 7, 55),
             shot("ref_aerial", -40, 40, 35, L, 0.8, 38, -40),
             shot("ref_street_up", -25, 0, 14, P, 1.0, 34, -40, target_z=lo.z + H * 0.45, dist=R * 1.6, cam_z=lo.z + 1.7)]
else:
    done += [shot("ref_three_quarter", -35, 25, 50, L, 0.8, 32, -45),
             shot("ref_golden", 35, 20, 50, L, 0.8, 7, 55),
             shot("ref_aerial", -40, 55, 35, L, 0.8, 45, -40),
             shot("ref_street", -15, 3, 24, L, 0.9, 30, -40, target_z=lo.z + H * 0.35, cam_z=lo.z + 1.7)]
bpy.data.objects.remove(cam, do_unlink=True)
print("AIREFS", {"tall": tall, "size": [round(v, 1) for v in size], "files": done})
