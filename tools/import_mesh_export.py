"""Load a CS2MCP `mesh_export` (cs2_mesh_export, mod 0.37) into Blender.

Usage
  In a running Blender (e.g. through the Blender MCP):
      exec(open(r"<repo>\\tools\\import_mesh_export.py").read())
      load_export(r"<out_dir>\\<prefab>_mesh_export.json")            # LOD0 of every mesh of the prefab
      load_export(r"<out_dir>", prefab="<prefab>", lods=(0, 1, 2))      # out_dir + prefab name works too
  Headless:
      blender -b --python tools\\import_mesh_export.py -- <manifest.json | out_dir> [--prefab NAME] [--lods 0,1]
                  [--no-placement] [--no-bones] [--save out.blend]

Axis conversion (documented once, used everywhere below)
  The OBJ holds RAW game coordinates: the game's (Unity) local mesh frame, metres, LEFT-handed:
      +x = the object's right, +y = up, +z = forward (vehicle front).
  Our Blender convention: Z up, a vehicle faces -Y, and game +x = Blender -X. So
      Blender (X, Y, Z) = C * game (x, y, z) with C = [[-1, 0, 0], [0, 0, -1], [0, 1, 0]]  ->  (-x, -z, +y)
  det(C) = -1 (left-handed -> right-handed is a mirror), so every triangle's winding is reversed to keep faces
  pointing outward; normals map with C as well (C is orthogonal). A placement rotation R (game quaternion) becomes
  C R C^T (a proper rotation), a placement position p becomes C p. Vehicle origins sit on the ground in the game, so
  an exported vehicle stands on z = 0 facing -Y (drop_to_ground=True forces min z = 0 anyway).

What you get
  Collection "<prefab> (CS2)" with one object per render mesh and LOD, named "<mesh>_LOD<n>". LOD0 is visible, other
  LODs hidden. Vertex order is kept 1:1 with the game (seams are split vertices, not welded - weld a COPY if needed).
  Per-submesh materials "CS2_<surface>_<index>", custom split normals, UV0, vertex groups per bone from the skin
  weights, and (bones=True) an empty per bone at its mesh-space rest position (wheels, bogies, doors...).
  Custom properties on each object: cs2_prefab, cs2_mesh, cs2_render_prefab, cs2_lod, cs2_sidecar.
"""
import json
import os
import sys

import bpy
from mathutils import Matrix, Quaternion, Vector

C_GAME_TO_BLENDER = Matrix(((-1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0)))


def g2b(p):
    """game (x, y, z) -> Blender (-x, -z, y)"""
    return (-p[0], -p[2], p[1])


def _read_obj(path):
    """Parse the OBJ written by mesh_export (v / vt / vn / g / usemtl / f with identical v/vt/vn indices)."""
    verts, uvs, nrms, faces, face_mat, mats = [], [], [], [], [], []
    cur = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line or line[0] == "#":
                continue
            tag, _, rest = line.partition(" ")
            if tag == "v":
                x, y, z = rest.split()[:3]
                verts.append((float(x), float(y), float(z)))
            elif tag == "vt":
                u, v = rest.split()[:2]
                uvs.append((float(u), float(v)))
            elif tag == "vn":
                x, y, z = rest.split()[:3]
                nrms.append((float(x), float(y), float(z)))
            elif tag == "usemtl":
                name = rest.strip()
                if name not in mats:
                    mats.append(name)
                cur = mats.index(name)
            elif tag == "f":
                faces.append(tuple(int(c.split("/")[0]) - 1 for c in rest.split()))
                face_mat.append(cur)
    return verts, uvs, nrms, faces, face_mat, mats


def _material(name):
    mname = "CS2_" + name
    m = bpy.data.materials.get(mname)
    if m is None:
        m = bpy.data.materials.new(mname)
    return m


def build_object(obj_path, name, sidecar=None, collection=None, bones=True):
    """Build one Blender object from an exported OBJ (+ its sidecar dict), converted to Blender axes."""
    verts, uvs, nrms, faces, face_mat, mats = _read_obj(obj_path)
    bverts = [g2b(v) for v in verts]
    keep = [i for i, f in enumerate(faces) if len(set(f)) == len(f)]       # drop degenerate triangles (repeated index)
    dropped = len(faces) - len(keep)
    # reverse winding: (a, b, c) -> (a, c, b); the mirror in C_GAME_TO_BLENDER flips orientation
    bfaces = [(faces[i][0],) + tuple(reversed(faces[i][1:])) for i in keep]
    me = bpy.data.meshes.new(name)
    me.from_pydata(bverts, [], bfaces)
    for mn in mats:
        me.materials.append(_material(mn))
    me.polygons.foreach_set("material_index", [face_mat[i] for i in keep])
    loop_vi = [vi for f in bfaces for vi in f]                            # loops follow face corner order
    if uvs and len(uvs) == len(verts):
        layer = me.uv_layers.new(name="UV0")
        layer.data.foreach_set("uv", [c for vi in loop_vi for c in uvs[vi]])
    me.update()
    if nrms and len(nrms) == len(verts):
        if hasattr(me, "use_auto_smooth"):                                # Blender < 4.1
            me.use_auto_smooth = True
        me.normals_split_custom_set([g2b(nrms[vi]) for vi in loop_vi])
    ob = bpy.data.objects.new(name, me)
    (collection or bpy.context.scene.collection).objects.link(ob)
    ob["cs2_obj"] = obj_path
    ob["cs2_degenerate_faces_dropped"] = dropped
    if sidecar:
        ob["cs2_prefab"] = sidecar.get("prefab") or ""
        ob["cs2_mesh"] = sidecar.get("mesh") or ""
        ob["cs2_render_prefab"] = sidecar.get("render_prefab") or ""
        ob["cs2_lod"] = int(sidecar.get("lod", 0))
        ob["cs2_sidecar"] = sidecar.get("json") or ""
        bone_list = sidecar.get("bones") or []
        skin = sidecar.get("skin")
        if skin and bone_list:
            inf = int(skin["influences"])
            idx, w = skin["indices"], skin["weights"]
            groups = {}
            for v in range(len(verts)):
                for k in range(inf):
                    wt = w[v * inf + k]
                    if wt <= 0:
                        continue
                    b = idx[v * inf + k]
                    groups.setdefault(b, {}).setdefault(wt, []).append(v)
            for b, by_w in sorted(groups.items()):
                bname = bone_list[b]["name"] if 0 <= b < len(bone_list) else "bone_%d" % b
                vg = ob.vertex_groups.new(name=bname)
                for wt, vs in by_w.items():
                    vg.add(vs, float(wt), "REPLACE")
        if bones and bone_list:
            for bi in bone_list:
                pos = bi.get("mesh_space_position")
                if pos is None:
                    continue
                e = bpy.data.objects.new("%s_bone_%s" % (name, bi["name"]), None)
                e.empty_display_type = "PLAIN_AXES"
                e.empty_display_size = 0.15
                e.location = g2b(pos)
                e["cs2_bone_type"] = bi.get("type") or ""
                e["cs2_bone_index"] = int(bi["index"])
                (collection or bpy.context.scene.collection).objects.link(e)
                e.parent = ob
    return ob


def placement_matrix(pl):
    """ObjectGeometryPrefab.m_Meshes placement (game frame) -> Blender 4x4 (C R C^T rotation, C p translation)."""
    x, y, z, w = pl["rotation_xyzw"]
    r = C_GAME_TO_BLENDER @ Quaternion((w, x, y, z)).to_matrix() @ C_GAME_TO_BLENDER.transposed()
    m = r.to_4x4()
    m.translation = Vector(g2b(pl["position"]))
    return m


def _find_manifest(path, prefab):
    if os.path.isfile(path):
        return path
    if prefab:
        cand = os.path.join(path, "".join("_" if c in '<>:"/\\|?*' else c for c in prefab).strip() + "_mesh_export.json")
        if os.path.isfile(cand):
            return cand
    found = [f for f in os.listdir(path) if f.endswith("_mesh_export.json")]
    if len(found) == 1:
        return os.path.join(path, found[0])
    raise FileNotFoundError("give the manifest path or prefab= (manifests here: %s)" % found)


def load_export(path, prefab=None, lods=(0,), placement=True, bones=True, drop_to_ground=False, collection_name=None):
    """Load a mesh_export manifest (or out_dir + prefab) into a new collection. lods=None loads every exported LOD.
    Returns the list of created mesh objects."""
    manifest = _find_manifest(path, prefab)
    base = os.path.dirname(manifest)
    with open(manifest, encoding="utf-8") as fh:
        man = json.load(fh)
    coll = bpy.data.collections.new(collection_name or "%s (CS2)" % man["prefab"])
    bpy.context.scene.collection.children.link(coll)
    created = []
    for mesh in man["meshes"]:
        pls = mesh.get("placements") or [None]
        for lod in mesh["lods"]:
            if lods is not None and lod["lod"] not in lods:
                continue
            if lod.get("error") or not lod.get("obj"):
                print("skip %s LOD%s: %s" % (mesh["mesh"], lod["lod"], lod.get("error")))
                continue
            obj_path = os.path.join(base, os.path.basename(lod["obj"]))       # folder may have moved
            side_path = os.path.join(base, os.path.basename(lod["json"]))
            sidecar = None
            if os.path.isfile(side_path):
                with open(side_path, encoding="utf-8") as fh:
                    sidecar = json.load(fh)
            for pi, pl in enumerate(pls):
                name = "%s_LOD%d" % (mesh["file_base"], lod["lod"]) + ("" if len(pls) == 1 else "_p%d" % pi)
                ob = build_object(obj_path, name, sidecar, coll, bones)
                if placement and pl:
                    ob.matrix_world = placement_matrix(pl)
                if lod["lod"] != min(l["lod"] for l in mesh["lods"] if lods is None or l["lod"] in lods):
                    ob.hide_set(True)                                          # only the finest loaded LOD shows
                    ob.hide_render = True
                created.append(ob)
    if drop_to_ground and created:
        bpy.context.view_layer.update()
        zmin = min((ob.matrix_world @ Vector(c)).z for ob in created for c in ob.bound_box)
        for ob in created:
            if ob.parent is None:
                ob.location.z -= zmin
    for ob in created:
        bpy.context.view_layer.update()
        zs = [(ob.matrix_world @ Vector(c)) for c in ob.bound_box]
        mn = Vector((min(v.x for v in zs), min(v.y for v in zs), min(v.z for v in zs)))
        mx = Vector((max(v.x for v in zs), max(v.y for v in zs), max(v.z for v in zs)))
        print("%s: %d verts, %d faces, size %.3f x %.3f x %.3f m, min z %.3f"
              % (ob.name, len(ob.data.vertices), len(ob.data.polygons), *(mx - mn), mn.z))
    return created


def _main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--prefab")
    ap.add_argument("--lods", default="0", help="comma list or 'all'")
    ap.add_argument("--no-placement", action="store_true")
    ap.add_argument("--no-bones", action="store_true")
    ap.add_argument("--ground", action="store_true")
    ap.add_argument("--save")
    a = ap.parse_args(argv)
    lods = None if a.lods == "all" else tuple(int(x) for x in a.lods.split(","))
    load_export(a.path, a.prefab, lods, not a.no_placement, not a.no_bones, a.ground)
    if a.save:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.save))


if __name__ == "__main__" and "--" in sys.argv:
    _main()
