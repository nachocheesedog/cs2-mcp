# Generic helpers for building architecture in Blender 5.x via MCP (data API + bmesh only).
# Usage inside execute_blender_code:
#   exec(open(os.path.expanduser(r"~\.claude\skills\blender-mcp-architecture\helpers\lbt_helpers.py")).read())
#   PREFIX = "MYP"   # optional: collection prefix (default "LBT")
# Then: C = coll("Walls", clear=True); bm = bmesh.new(); add_box(bm, ...); mesh_obj("MYP_Wall", bm, [mat], C)
import bpy, bmesh, math, os
from mathutils import Vector, Matrix

try: PREFIX
except NameError: PREFIX = "LBT"

# ---------------------------------------------------------------- collections / objects
def coll(name, clear=False, scene="Scene"):
    """Top-level collection '<PREFIX>_<name>' in the main scene; clear=True deletes its objects (+ orphaned data)."""
    full = f"{PREFIX}_{name}"
    c = bpy.data.collections.get(full)
    if c is None:
        c = bpy.data.collections.new(full); bpy.data.scenes[scene].collection.children.link(c)
    if clear:
        for o in list(c.objects):
            data = o.data; bpy.data.objects.remove(o, do_unlink=True)
            if data is not None and data.users == 0:
                for store in (bpy.data.meshes, bpy.data.curves):
                    if data.name in store and store[data.name] == data: store.remove(data); break
    return c

def mesh_obj(name, bm, mats, c):
    me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free()
    for m in mats: me.materials.append(m)
    o = bpy.data.objects.new(name, me); c.objects.link(o); return o

def inst(name, mesh, c, loc, rot_z=0.0, scale=None):
    """Linked-data instance (shares mesh) — the cheap way to repeat modules."""
    o = bpy.data.objects.new(name, mesh); c.objects.link(o)
    o.location = loc; o.rotation_euler = (0, 0, rot_z)
    if scale: o.scale = scale
    return o

def add_bevel(o, width=0.01, segs=1, angle=35):
    md = o.modifiers.new("Bevel", 'BEVEL'); md.width = width; md.segments = segs
    try: md.limit_method = 'ANGLE'; md.angle_limit = math.radians(angle)
    except Exception: pass
    return md

def array_x(o, count, step):
    md = o.modifiers.new("Array", 'ARRAY'); md.count = count; md.use_relative_offset = False
    md.use_constant_offset = True; md.constant_offset_displace = (step, 0, 0); return md

# ---------------------------------------------------------------- bmesh primitives (all return created verts via tag)
def tag(res, mi):
    vs = res['verts'] if isinstance(res, dict) else res
    for f in {f for v in vs for f in v.link_faces}: f.material_index = mi

def add_box(bm, x0, x1, y0, y1, z0, z1, mi=0):
    r = bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Translation(((x0+x1)/2, (y0+y1)/2, (z0+z1)/2)) @ Matrix.Diagonal((x1-x0, y1-y0, z1-z0, 1)))
    tag(r, mi); return r

def add_cyl(bm, r, depth, mtx, segs=16, mi=0, r2=None):
    res = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segs, radius1=r, radius2=r if r2 is None else r2, depth=depth, matrix=mtx)
    tag(res, mi); return res

def cyl_between(bm, p0, p1, r, sides=8, mi=0, r2=None):
    p0, p1 = Vector(p0), Vector(p1); d = p1 - p0
    mtx = Matrix.Translation((p0 + p1) / 2) @ d.to_track_quat('Z', 'Y').to_matrix().to_4x4()
    return add_cyl(bm, r, d.length, mtx, segs=sides, mi=mi, r2=r2)

def beam(bm, p0, p1, w, d, ztop, mi=0):
    """Rectangular beam between 2D points p0->p1, width w, depth d, top at ztop."""
    p0, p1 = Vector((p0[0], p0[1], 0)), Vector((p1[0], p1[1], 0)); L = (p1 - p0).length
    if L < 0.05: return
    ang = math.atan2(p1.y - p0.y, p1.x - p0.x); mid = (p0 + p1) / 2
    tag(bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Translation((mid.x, mid.y, ztop - d / 2)) @ Matrix.Rotation(ang, 4, 'Z') @ Matrix.Diagonal((L, w, d, 1))), mi)

def tube(bm, pts, r, sides=6, mi=0, cap=True):
    """Sweep a circle along a polyline (rails, handrails, hood moulds, wires)."""
    pts = [Vector(p) for p in pts]; rings = []
    for i, p in enumerate(pts):
        t = (pts[1] - pts[0]) if i == 0 else (pts[-1] - pts[-2]) if i == len(pts) - 1 else (pts[i + 1] - pts[i - 1])
        t.normalize(); up = Vector((0, 0, 1)) if abs(t.z) < 0.9 else Vector((1, 0, 0))
        u = t.cross(up).normalized(); w = u.cross(t).normalized()
        rings.append([bm.verts.new(p + r * (math.cos(2 * math.pi * k / sides) * u + math.sin(2 * math.pi * k / sides) * w)) for k in range(sides)])
    for a, b in zip(rings, rings[1:]):
        for k in range(sides):
            bm.faces.new((a[k], a[(k + 1) % sides], b[(k + 1) % sides], b[k])).material_index = mi
    if cap and sides > 2:
        bm.faces.new(list(reversed(rings[0]))).material_index = mi; bm.faces.new(rings[-1]).material_index = mi
    return rings

def text_mesh(name, body, size, mat_, c, extrude=0.0):
    """Mesh from a text curve (data API). Lies in XY facing +Z: rotate X by pi/2 to face -Y."""
    cu = bpy.data.curves.new(name + "_tmp", 'FONT'); cu.body = body; cu.size = size
    cu.align_x = 'CENTER'; cu.align_y = 'CENTER'; cu.extrude = extrude
    tmp = bpy.data.objects.new(name + "_tmpobj", cu); c.objects.link(tmp)
    me = bpy.data.meshes.new_from_object(tmp.evaluated_get(bpy.context.evaluated_depsgraph_get())); me.name = name
    bpy.data.objects.remove(tmp, do_unlink=True); bpy.data.curves.remove(cu)
    me.materials.clear(); me.materials.append(mat_); return me

# ---------------------------------------------------------------- wall with arched openings (no booleans)
def arch_curve(w, spring, kind, n=10):
    """(x,z) points from left springer over the head to the right springer. kind: pointed|round|segment|flat."""
    if kind == 'pointed':
        r = w * 0.85; cxl = -w / 2 + r; ang_end = math.acos(-cxl / r)
        left = [(cxl + r * math.cos(math.pi - (math.pi - ang_end) * k / n), spring + r * math.sin(math.pi - (math.pi - ang_end) * k / n)) for k in range(n + 1)]
        return left + [(-x, z) for x, z in reversed(left[:-1])]
    if kind == 'round':
        r = w / 2; return [(r * math.cos(math.pi * (1 - k / (2 * n))), spring + r * math.sin(math.pi * (1 - k / (2 * n)))) for k in range(2 * n + 1)]
    if kind == 'segment':
        r = w * 0.8; h = r - math.sqrt(r * r - (w / 2) ** 2); a0 = math.asin((w / 2) / r)
        return [(r * math.sin(-a0 + a0 * k / n), spring - (r - h) + r * math.cos(-a0 + a0 * k / n)) for k in range(2 * n + 1)]
    return [(-w / 2, spring), (w / 2, spring)]

def wall(bm, W, H, openings, depth=0.45, mi=0, rev=0, sill_mi=0, glass_mi=None):
    """Wall x in [-W/2,W/2], z in [0,H], front at y=0 facing -Y. openings = [(xc, w, sill, spring, kind)].
    Builds piers, sill strips, spandrels above each head, reveals to y=depth and (optionally) a glass ngon there."""
    ops = sorted(openings, key=lambda o: o[0]); xs = [-W / 2]
    for xc, w, *_ in ops: xs += [xc - w / 2, xc + w / 2]
    xs.append(W / 2)
    def q(pts, m):
        bm.faces.new([bm.verts.new(p) for p in pts]).material_index = m
    for i in range(0, len(xs), 2):
        a, b = xs[i], xs[i + 1]
        if b - a > 1e-4: q([(a, 0, 0), (b, 0, 0), (b, 0, H), (a, 0, H)], mi)
    for xc, w, sill, spr, kind in ops:
        ac = [(xc + x, z) for x, z in arch_curve(w, spr, kind)]
        if sill > 1e-4: q([(xc - w / 2, 0, 0), (xc + w / 2, 0, 0), (xc + w / 2, 0, sill), (xc - w / 2, 0, sill)], mi)
        for (x0, z0), (x1, z1) in zip(ac, ac[1:]): q([(x0, 0, z0), (x1, 0, z1), (x1, 0, H), (x0, 0, H)], mi)
        outline = [(xc - w / 2, sill)] + ac + [(xc + w / 2, sill)]
        for (x0, z0), (x1, z1) in zip(outline, outline[1:]): q([(x0, 0, z0), (x0, depth, z0), (x1, depth, z1), (x1, 0, z1)], rev)
        if sill > 1e-4: q([(xc + w / 2, 0, sill), (xc + w / 2, depth, sill), (xc - w / 2, depth, sill), (xc - w / 2, 0, sill)], sill_mi)
        if glass_mi is not None: q([(x, depth, z) for x, z in outline], glass_mi)

# ---------------------------------------------------------------- node-material builder (Principled stays the output)
def _out(node, name):
    for s in node.outputs:
        if s.name == name or s.identifier == name: return s
    raise KeyError(name)

class MB:
    def __init__(self, name, base=(0.5, 0.5, 0.5), rough=0.7, metal=0.0, alpha=1.0, emit=0.0, emit_col=None):
        old = bpy.data.materials.get(name)
        if old is not None: bpy.data.materials.remove(old)   # NOTE: empties slots on meshes still using it -> rebuild them
        m = bpy.data.materials.new(name)
        try: m.use_nodes = True
        except Exception: pass
        self.m, self.nt = m, m.node_tree; self.n, self.l = self.nt.nodes, self.nt.links
        self.b = self.n.get("Principled BSDF")
        m.diffuse_color = (*base, alpha)
        for k, v in (("Base Color", (*base, 1)), ("Roughness", rough), ("Metallic", metal), ("Alpha", alpha)):
            self.b.inputs[k].default_value = v
        if emit:
            self.b.inputs["Emission Color"].default_value = (*(emit_col or base), 1); self.b.inputs["Emission Strength"].default_value = emit
        if alpha < 1:
            for attr, val in (("surface_render_method", 'BLENDED'), ("blend_method", 'BLEND'), ("use_backface_culling", False)):
                try: setattr(m, attr, val)
                except Exception: pass
    def node(self, t, **props):
        nd = self.n.new(t)
        for k, v in props.items(): setattr(nd, k, v)
        return nd
    def link(self, a, b): self.l.new(a, b)
    def coord(self, scale=(1, 1, 1), rot=(0, 0, 0), kind="Object"):
        """Mapping applies scale -> rotate -> translate. Walls in local XZ: rot=(pi/2,0,0)."""
        tc = self.node("ShaderNodeTexCoord"); mp = self.node("ShaderNodeMapping")
        mp.inputs["Scale"].default_value = scale; mp.inputs["Rotation"].default_value = rot
        self.link(_out(tc, kind), mp.inputs["Vector"]); return _out(mp, "Vector")
    def ramp(self, fac, stops):
        r = self.node("ShaderNodeValToRGB"); cr = r.color_ramp
        while len(cr.elements) < len(stops): cr.elements.new(0.5)
        for e, (p, c) in zip(cr.elements, stops): e.position = p; e.color = (*c, 1) if len(c) == 3 else c
        self.link(fac, r.inputs["Fac"]); return _out(r, "Color")
    def noise(self, vec, scale=5.0, detail=4.0, rough=0.55):
        nz = self.node("ShaderNodeTexNoise")
        for k, v in (("Scale", scale), ("Detail", detail), ("Roughness", rough)): nz.inputs[k].default_value = v
        self.link(vec, nz.inputs["Vector"]); return nz
    def voronoi(self, vec, scale=10.0):
        v = self.node("ShaderNodeTexVoronoi"); v.inputs["Scale"].default_value = scale
        self.link(vec, v.inputs["Vector"]); return v
    def brick(self, vec, c1, c2, mortar, mortar_size=0.009, width=0.225, height=0.075, offset=0.5, bias=0.0, smooth=0.1):
        """Fac output = mortar mask (1 at mortar). Color1/Color2 + bias = per-brick random -> use for per-pane effects."""
        b = self.node("ShaderNodeTexBrick"); b.offset = offset
        for k, v in (("Color1", (*c1, 1)), ("Color2", (*c2, 1)), ("Mortar", (*mortar, 1)), ("Scale", 1.0), ("Mortar Size", mortar_size),
                     ("Mortar Smooth", smooth), ("Bias", bias), ("Brick Width", width), ("Row Height", height)):
            b.inputs[k].default_value = v
        self.link(vec, b.inputs["Vector"]); return b
    def mix(self, fac, a, b, blend='MIX'):
        mx = self.node("ShaderNodeMix"); mx.data_type = 'RGBA'
        try: mx.blend_type = blend
        except Exception: pass
        f = next(s for s in mx.inputs if s.identifier == "Factor_Float")
        if isinstance(fac, (int, float)): f.default_value = fac
        else: self.link(fac, f)
        for ident, val in (("A_Color", a), ("B_Color", b)):
            s = next(s for s in mx.inputs if s.identifier == ident)
            if isinstance(val, tuple): s.default_value = (*val, 1) if len(val) == 3 else val
            else: self.link(val, s)
        return _out(mx, "Result_Color")          # !!! three outputs are all NAMED "Result" — use the identifier
    def math(self, op, a, b=0.0):
        mn = self.node("ShaderNodeMath", operation=op)
        for i, v in enumerate((a, b)):
            if isinstance(v, (int, float)): mn.inputs[i].default_value = v
            else: self.link(v, mn.inputs[i])
        return mn.outputs[0]
    def sepxyz(self, vec):
        s = self.node("ShaderNodeSeparateXYZ"); self.link(vec, s.inputs[0]); return s
    def bump(self, height, strength=0.3, distance=0.02):
        bp = self.node("ShaderNodeBump"); bp.inputs["Strength"].default_value = strength; bp.inputs["Distance"].default_value = distance
        self.link(height, bp.inputs["Height"]); self.link(_out(bp, "Normal"), self.b.inputs["Normal"]); return bp
    def base(self, s): self.link(s, self.b.inputs["Base Color"])
    def rough(self, s): self.link(s, self.b.inputs["Roughness"])
    def emission(self, s, strength):
        self.link(s, self.b.inputs["Emission Color"]); self.b.inputs["Emission Strength"].default_value = strength

def brick_wall_mat(name, c1=(0.34, 0.075, 0.04), c2=(0.26, 0.055, 0.03), mortar=(0.40, 0.38, 0.34), soot=0.4):
    """Weathered brick for walls modelled in local XZ: bond + fine grain + large patches + rain streaks + bump."""
    mb = MB(name, c1, rough=0.86); v = mb.coord(rot=(math.pi / 2, 0, 0))
    br = mb.brick(v, c1, c2, mortar, bias=-0.1, smooth=0.2); fine = mb.noise(v, scale=35.0, detail=8)
    col = mb.mix(mb.math('MULTIPLY', _out(fine, "Fac"), 0.3), _out(br, "Color"), (0.12, 0.08, 0.06), blend='MULTIPLY')
    patch = mb.noise(mb.coord(scale=(0.15, 0.15, 0.15), rot=(math.pi / 2, 0, 0)), scale=1.0, detail=2)
    col = mb.mix(mb.math('MULTIPLY', _out(patch, "Fac"), 0.45), col, (0.6, 0.32, 0.2), blend='OVERLAY')
    streak = mb.noise(mb.coord(scale=(2.5, 1.0, 0.08), rot=(math.pi / 2, 0, 0)), scale=1.0, detail=3)
    col = mb.mix(mb.math('MULTIPLY', mb.math('GREATER_THAN', _out(streak, "Fac"), 0.6), soot), col, (0.1, 0.08, 0.07), blend='MULTIPLY')
    mb.base(col); mb.bump(mb.math('ADD', mb.math('SUBTRACT', 1.0, _out(br, "Fac")), mb.math('MULTIPLY', _out(fine, "Fac"), 0.3)), 0.7, 0.012)
    return mb.m

# ---------------------------------------------------------------- stats + isolated QA renders
def tri_count(objs):
    dg = bpy.context.evaluated_depsgraph_get(); n = 0
    for o in objs:
        if o.type not in {'MESH', 'CURVE'}: continue
        try:
            me = o.evaluated_get(dg).to_mesh(); n += sum(len(p.vertices) - 2 for p in me.polygons); o.evaluated_get(dg).to_mesh_clear()
        except Exception: pass
    return n

def qa_render(tag_name, collections, cam_loc, target, lens=35, res=(1280, 720), out_dir=None, main="Scene", sun_name=None):
    """Render a private scene QA_<tag> (linked collections + own camera) without touching the main scene's camera.
    Mirrors EEVEE + colour-management settings from the main scene. Delete QA_* scenes before saving the file."""
    out_dir = out_dir or os.path.join(os.path.dirname(bpy.data.filepath) or os.getcwd(), "qa"); os.makedirs(out_dir, exist_ok=True)
    sname = "QA_" + tag_name; s = bpy.data.scenes.get(sname) or bpy.data.scenes.new(sname)
    for ch in list(s.collection.children): s.collection.children.unlink(ch)
    for o in list(s.collection.objects): s.collection.objects.unlink(o)
    for cn in collections:
        c = bpy.data.collections.get(cn)
        if c: s.collection.children.link(c)
    m = bpy.data.scenes[main]; s.world = m.world; s.render.engine = m.render.engine
    for p in m.eevee.bl_rna.properties:
        if p.is_readonly or p.type in {'POINTER', 'COLLECTION'}: continue
        try: setattr(s.eevee, p.identifier, getattr(m.eevee, p.identifier))
        except Exception: pass
    for a in ("view_transform", "look", "exposure", "gamma"):
        try: setattr(s.view_settings, a, getattr(m.view_settings, a))
        except Exception: pass
    s.render.resolution_x, s.render.resolution_y, s.render.resolution_percentage = res[0], res[1], 100
    cd = bpy.data.cameras.get("QA_Cam_" + tag_name) or bpy.data.cameras.new("QA_Cam_" + tag_name); cd.lens = lens; cd.clip_end = 5000
    cam = bpy.data.objects.get("QA_Cam_" + tag_name) or bpy.data.objects.new("QA_Cam_" + tag_name, cd)
    if cam.name not in s.collection.objects: s.collection.objects.link(cam)
    cam.location = cam_loc; cam.rotation_euler = (Vector(target) - Vector(cam_loc)).to_track_quat('-Z', 'Y').to_euler(); s.camera = cam
    sun = bpy.data.objects.get(sun_name) if sun_name else next((o for o in m.collection.all_objects if o.type == 'LIGHT' and o.data.type == 'SUN'), None)
    if sun and sun.name not in s.collection.objects: s.collection.objects.link(sun)
    path = os.path.join(out_dir, f"{tag_name}.png"); s.render.filepath = path
    bpy.ops.render.render(write_still=True, scene=sname); return path

def render_icon(collections, out_path, size=512, az_deg=-35.0, elev_deg=18.0, lens=50, margin=1.06, main="Scene", sun_name=None,
                extra=()):
    """Build-menu icon for a CS2 asset: square PNG with a TRANSPARENT background, 3/4 view from the front (front = -Y,
    az_deg rotates toward +X), camera auto-fitted to the collections' bounding box. Uses a private ICON scene (removed
    afterwards), so the main scene/camera are untouched. Mirrors EEVEE, world and colour management from `main`.
    extra: collections rendered too but NOT used for the fit (e.g. long piers allowed to run off the frame)."""
    import math as _m
    m = bpy.data.scenes[main]; s = bpy.data.scenes.new("ICON_tmp")
    objs = []
    for cn in collections:
        c = bpy.data.collections.get(cn)
        if c: s.collection.children.link(c); objs += [o for o in c.all_objects if o.type == 'MESH' and not o.hide_render]
    for cn in extra:
        c = bpy.data.collections.get(cn)
        if c and c.name not in s.collection.children: s.collection.children.link(c)
    if not objs: raise ValueError("render_icon: no mesh objects in %r" % (collections,))
    s.world = m.world; s.render.engine = m.render.engine
    for p in m.eevee.bl_rna.properties:
        if p.is_readonly or p.type in {'POINTER', 'COLLECTION'}: continue
        try: setattr(s.eevee, p.identifier, getattr(m.eevee, p.identifier))
        except Exception: pass
    for a in ("view_transform", "look", "exposure", "gamma"):
        try: setattr(s.view_settings, a, getattr(m.view_settings, a))
        except Exception: pass
    s.render.film_transparent = True
    s.render.resolution_x = s.render.resolution_y = size; s.render.resolution_percentage = 100
    s.render.image_settings.file_format = 'PNG'; s.render.image_settings.color_mode = 'RGBA'
    for a in dir(s.render):                                     # no metadata in published images: Blender writes
        if a.startswith('use_stamp'): setattr(s.render, a, False)   # the .blend path (with the user name) into the PNG
    pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    lo = Vector([min(p[i] for p in pts) for i in range(3)]); hi = Vector([max(p[i] for p in pts) for i in range(3)])
    ctr = (lo + hi) / 2
    A, E = _m.radians(az_deg), _m.radians(elev_deg)
    back = Vector((_m.sin(A) * _m.cos(E), -_m.cos(A) * _m.cos(E), _m.sin(E)))      # camera sits at ctr + back * dist
    cd = bpy.data.cameras.new("ICON_cam"); cd.lens = lens; cd.clip_end = 10000; cd.sensor_fit = 'HORIZONTAL'
    cam = bpy.data.objects.new("ICON_cam", cd); s.collection.objects.link(cam); s.camera = cam
    cam.rotation_euler = (-back).to_track_quat('-Z', 'Y').to_euler()
    R = cam.rotation_euler.to_matrix(); r, u, f = R.col[0], R.col[1], -R.col[2]
    t = _m.tan(cd.angle / 2) / margin
    dist = max(max(abs((p - ctr).dot(r)), abs((p - ctr).dot(u))) / t - (p - ctr).dot(f) for p in pts)
    cam.location = ctr + back * dist
    sun = bpy.data.objects.get(sun_name) if sun_name else next((o for o in m.collection.all_objects if o.type == 'LIGHT' and o.data.type == 'SUN'), None)
    if sun: s.collection.objects.link(sun)
    os.makedirs(os.path.dirname(out_path), exist_ok=True); s.render.filepath = out_path
    bpy.ops.render.render(write_still=True, scene=s.name)
    bpy.data.scenes.remove(s); bpy.data.objects.remove(cam); bpy.data.cameras.remove(cd)
    return out_path

def physical_sky(scene="Scene", elev_deg=38.0, az_deg=-35.0, strength=0.22, sun_energy=4.2, exposure=-0.45):
    """Sky Texture world + matching sun lamp + AgX contrast + EEVEE raytracing (values that looked right in 5.2)."""
    scn = bpy.data.scenes[scene]; E, A = math.radians(elev_deg), math.radians(az_deg)
    w = bpy.data.worlds.get("Sky") or bpy.data.worlds.new("Sky"); scn.world = w
    try: w.use_nodes = True
    except Exception: pass
    nt = w.node_tree; nt.nodes.clear()
    out, bg, sky = nt.nodes.new("ShaderNodeOutputWorld"), nt.nodes.new("ShaderNodeBackground"), nt.nodes.new("ShaderNodeTexSky")
    for st in ('MULTIPLE_SCATTERING', 'NISHITA', 'HOSEK_WILKIE'):
        try: sky.sky_type = st; break
        except Exception: pass
    for a, v in (("sun_elevation", E), ("sun_rotation", math.pi / 2 + A), ("altitude", 30.0), ("dust_density", 0.8), ("ozone_density", 1.2), ("sun_intensity", 0.4)):
        try: setattr(sky, a, v)
        except Exception: pass
    nt.links.new(sky.outputs[0], bg.inputs[0]); nt.links.new(bg.outputs[0], out.inputs[0]); bg.inputs[1].default_value = strength
    sun = next((o for o in scn.collection.all_objects if o.type == 'LIGHT' and o.data.type == 'SUN'), None)
    if sun is None:
        sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", 'SUN')); scn.collection.objects.link(sun)
    sun.data.energy = sun_energy; sun.data.color = (1.0, 0.94, 0.86); sun.data.angle = math.radians(0.8)
    d = Vector((math.cos(E) * math.sin(A), -math.cos(E) * math.cos(A), math.sin(E)))
    sun.rotation_mode = 'QUATERNION'; sun.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(d)
    for a, v in (("use_raytracing", True), ("ray_tracing_method", 'SCREEN'), ("use_shadows", True), ("taa_render_samples", 96), ("use_fast_gi", True)):
        try: setattr(scn.eevee, a, v)
        except Exception: pass
    for a, v in (("view_transform", 'AgX'), ("look", 'AgX - Medium High Contrast'), ("exposure", exposure)):
        try: setattr(scn.view_settings, a, v)
        except Exception: pass
