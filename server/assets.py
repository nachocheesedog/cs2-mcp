"""Asset pipeline tools: validate an exported asset folder offline, import it into the running game through the mod
(the Editor Asset Importer's own code path), configure the prefab. Standard library only.

Hard-won CS2 importer rules (2026-09-23, Liberty House) - the validator checks the ones it can:
- FBX in CENTIMETRES: the FBXImporter applies a fixed scale 0.01 and ignores UnitScaleFactor AND node transforms. Blender: export
  with apply_scale_options='FBX_SCALE_NONE', global_scale=1, apply_unit_scale=True, bake_space_transform=True. Blender's dialog
  default (bake off) writes metre vertices + a x100 node scale; FBX_SCALE_UNITS writes metres + UnitScaleFactor 100. Both import
  at 1/100 size (lot shows, building invisible). Verified 2026-09-24 with a 10 m cube: default -> extent 10, ours -> 1000.
- Main mesh: ONE material named exactly like the mesh/file. Sub-meshes have NO material.
- Windows = a SEPARATE file <Asset>_Win.fbx (also _Wim/_Wif/_Wio): the "Windows" template + WindowsPostProcessor match the
  FILE name ("^.*_Win.fbx$"), not object names. Every island (connected piece) = one window; the importer groups them into
  rooms per floor (floorHeight 3 m) and lights rooms at random at NIGHT. Object names inside the main FBX are ignored.
- _Base (file suffix) = base surface with repeating textures (*_Base_BaseColor ...) - not yet used here.
- The Emissive map glows day AND night (no EmissiveProperties set up by the importer); keep it ~0.02 linear or 0.
- Building front (road side): the game put the road at the Blender -Y end for Liberty House - the street end must face -Y.
- Re-import with preset "None" refreshes meshes/textures in place and keeps the prefab config, BUT fails with "being used
  by another process" once the game has loaded the building's .Geometry: quit to the main menu (or restart) first.
- Build-menu picture + description: every asset gets <project_root>_build/<Name>_meta.json {"title", "description"} and
  <Name>_Icon.png (square, transparent background) - NOT inside the asset folder (the importer would treat the PNG as a
  texture). import_asset applies them after the import (mod asset_configure icon/title/description); without them the asset
  shows no picture and no description in game.
- Import from a loaded city or with preset None: at the main menu the Find It mod crashes in ActivatePrefabTool when a
  preset creates a new object prefab.
"""
import json, os, re, struct, zlib

TEX_SUFFIXES = ("BaseColor", "Normal", "MaskMap", "ControlMask", "Emissive")
REQUIRED_TEX = ("BaseColor", "Normal", "MaskMap")
SUBMESH_SUFFIXES = ("_Win", "_Wim", "_Wif", "_Wio", "_Gls", "_Glf", "_Glm", "_Gra", "_Veg", "_Wat", "_Was", "_Clt", "_Base")
INTERIOR_SUFFIXES = ("_Win", "_Wim", "_Wif", "_Wio")
RPC = None



PNG_SIG = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
_PNG_KEEP = {b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND", b"sRGB", b"gAMA", b"cHRM"}

def _clean_png(path):
    """Metadata-free copy of a PNG for anything that gets packaged and published: Blender writes the .blend path
    (with the Windows user name) plus EXIF into renders. Keeps only the image chunks; returns the new path (or the input
    if it is not a PNG)."""
    import tempfile
    try:
        d = open(path, "rb").read()
    except OSError:
        return path
    if d[:8] != PNG_SIG:
        return path
    out, i = [d[:8]], 8
    while i + 8 <= len(d):
        n, t = struct.unpack(">I4s", d[i:i + 8]); end = i + 12 + n
        if t in _PNG_KEEP: out.append(d[i:end])
        i = end
    dst = os.path.join(tempfile.gettempdir(), "cs2mcp_clean_" + os.path.basename(path))
    with open(dst, "wb") as f: f.write(b"".join(out))
    return dst

def _png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n": return None
    return struct.unpack(">II", head[16:24])


def _fbx_read(path):
    """Minimal binary FBX reader: version, UnitScaleFactor, raw vertex extents (file units), model/material names."""
    data = open(path, "rb").read()
    if data[:20] != b"Kaydara FBX Binary  ": return {"format": "ascii-or-unknown"}
    ver = struct.unpack("<I", data[23:27])[0]; wide = ver >= 7500
    out = {"format": "binary", "version": ver, "unit_scale": None, "models": [], "materials": [], "extent": None, "node_scales": []}
    mins, maxs = [1e30] * 3, [-1e30] * 3

    def node(off):
        if wide: end, nprops, plen = struct.unpack("<QQQ", data[off:off + 24]); off += 24
        else: end, nprops, plen = struct.unpack("<III", data[off:off + 12]); off += 12
        nlen = data[off]; name = data[off + 1:off + 1 + nlen]; off += 1 + nlen
        if end == 0: return None
        props, p = [], off
        for _ in range(nprops):
            t = chr(data[p]); p += 1
            if t in "YCIFDL":
                fmt, sz = {"Y": ("<h", 2), "C": ("?", 1), "I": ("<i", 4), "F": ("<f", 4), "D": ("<d", 8), "L": ("<q", 8)}[t]
                props.append(struct.unpack(fmt, data[p:p + sz])[0]); p += sz
            elif t in "fdlib":
                n, enc, clen = struct.unpack("<III", data[p:p + 12]); p += 12
                raw = data[p:p + clen]; p += clen
                if name == b"Vertices":
                    if enc: raw = zlib.decompress(raw)
                    props.append(struct.unpack("<%d%s" % (n, "d" if t == "d" else "f"), raw))
                else: props.append(None)
            elif t in "SR":
                n = struct.unpack("<I", data[p:p + 4])[0]; p += 4
                props.append(data[p:p + n]); p += n
        if name == b"P" and props and props[0] == b"UnitScaleFactor": out["unit_scale"] = props[-1]
        if name == b"P" and props and props[0] == b"Lcl Scaling" and len(props) >= 3:
            out["node_scales"].append(tuple(round(float(x), 4) for x in props[-3:]))
        if name in (b"Model", b"Material") and len(props) > 1 and isinstance(props[1], bytes):
            nm = props[1].split(b"\x00")[0].decode(errors="replace")
            (out["models"] if name == b"Model" else out["materials"]).append(nm)
        if name == b"Vertices" and props and props[0]:
            v = props[0]
            for k in range(3):
                c = v[k::3]; mins[k] = min(mins[k], min(c)); maxs[k] = max(maxs[k], max(c))
        c = off + plen
        while c < end:
            r = node(c)
            if r is None: break
            c = r
        return end

    off = 27
    while off < len(data) - 200:
        r = node(off)
        if r is None: break
        off = r
    if maxs[0] > -1e29: out["extent"] = [round(maxs[k] - mins[k], 2) for k in range(3)]
    return out


def _parse_name(stem):
    """Mirror of Colossal.AssetPipeline.AssetUtils.ParseName's segment order: Theme_Name[_L#][_WxD][_Module][_LOD#][_Material]..."""
    parts = stem.split("_")
    out = {"theme": parts[0] if len(parts) > 1 else "", "name": parts[1] if len(parts) > 1 else parts[0], "lod": 0, "lot": None,
           "level": None, "rest": []}
    for seg in parts[2:]:
        if re.fullmatch(r"L\d", seg): out["level"] = int(seg[1])
        elif re.fullmatch(r"\dx\d", seg): out["lot"] = seg
        elif re.fullmatch(r"LOD\d", seg): out["lod"] = int(seg[3])
        else: out["rest"].append(seg)
    return out


def _meta(directory, a=None):
    """Build-menu metadata for an asset: explicit args win, else <project_root>_build/<Name>_meta.json + <Name>_Icon.png."""
    a = a or {}
    d = os.path.abspath(directory); name = os.path.basename(d.rstrip(r"\/")); build = os.path.dirname(d) + "_build"
    path = a.get("meta") or os.path.join(build, name + "_meta.json")
    m = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f: m = json.load(f)
    icon = a.get("icon") or m.get("icon") or os.path.join(build, name + "_Icon.png")
    if icon and not os.path.isabs(icon): icon = os.path.join(os.path.dirname(path), icon)
    return {"meta_file": path if os.path.isfile(path) else None, "title": a.get("title") or m.get("title"),
            "description": a.get("description") or m.get("description"), "icon": icon if icon and os.path.isfile(icon) else None}


def validate(a):
    """Offline pre-flight for a CS2 asset folder (the importer's 'asset folder' inside a project root)."""
    d = os.path.abspath(a["directory"])
    if not os.path.isdir(d): raise ValueError(f"directory not found: {d}")
    name = os.path.basename(d.rstrip("\\/"))
    files = sorted(os.listdir(d))
    fbx = [f for f in files if f.lower().endswith(".fbx")]
    png = [f for f in files if f.lower().endswith(".png")]
    other = [f for f in files if f not in fbx and f not in png]
    problems, warnings, meshes = [], [], []
    if not fbx: problems.append("no .fbx files")
    if name + ".fbx" not in fbx: problems.append(f"main mesh {name}.fbx missing (the folder name must equal the LOD0 file name)")
    for f in fbx:
        stem = f[:-4]; pn = _parse_name(stem)
        sub = next((s for s in SUBMESH_SUFFIXES if stem.endswith(s)), None)
        try: info = _fbx_read(os.path.join(d, f))
        except Exception as e: info = {"format": "unreadable", "error": str(e)}
        m = {"file": f, "lod": pn["lod"], "submesh": sub, **{k: info.get(k) for k in ("format", "version", "unit_scale", "extent", "models", "materials")}}
        if not stem.startswith(name): problems.append(f"{f}: does not start with the asset name {name}")
        if info.get("format") == "binary" and info["version"] < 7400: problems.append(f"{f}: FBX {info['version']} is too old")
        us, ext = info.get("unit_scale"), info.get("extent")
        if us is not None and abs(us - 1.0) > 1e-6:
            problems.append(f"{f}: UnitScaleFactor {us} - CS2 ignores it and scales raw vertices by 0.01; export in cm "
                            "(Blender: apply_scale_options='FBX_SCALE_NONE', global_scale=1)")
        elif any(any(abs(c - 1.0) > 1e-3 for c in sc) for sc in info.get("node_scales") or []):
            problems.append(f"{f}: object transform not baked (node scale {next(sc for sc in info['node_scales'] if any(abs(c - 1.0) > 1e-3 for c in sc))}) - CS2 ignores node transforms, so the raw "
                            "vertices (metres) come in at 1/100 size. Blender: bake_space_transform=True with FBX_SCALE_NONE, global_scale 1 "
                            "(Blender's dialog default leaves it off)")
        elif ext and max(ext) < 300:
            warnings.append(f"{f}: raw extent {ext} file units = {[round(e / 100, 2) for e in ext]} m in game - is it meant to be that small?")
        if sub is None:
            for suf in TEX_SUFFIXES:
                if os.path.exists(os.path.join(d, f"{stem}_{suf}.png")): m.setdefault("textures", []).append(suf)
            missing = [s for s in REQUIRED_TEX if s not in m.get("textures", [])]
            if missing: problems.append(f"{f}: missing textures {missing}")
            mats = info.get("materials") or []
            if len(mats) != 1: problems.append(f"{f}: main mesh needs exactly one material (has {len(mats)})")
            elif mats[0] != stem: warnings.append(f"{f}: material '{mats[0]}' should be named like the mesh '{stem}'")
            for mdl in info.get("models") or []:
                if any(mdl.endswith(s) for s in INTERIOR_SUFFIXES):
                    problems.append(f"{f}: contains object '{mdl}' - window sub-meshes must be their own file ({mdl}.fbx); "
                                    "the importer matches _Win on file names only")
        else:
            if info.get("materials"): warnings.append(f"{f}: sub-mesh has materials {info['materials']} - sub-meshes should have none")
        meshes.append(m)
    lods = sorted({m["lod"] for m in meshes if m["submesh"] is None})
    if lods and lods != list(range(len(lods))): warnings.append(f"LOD numbers are not contiguous: {lods}")
    if not any(m["submesh"] in INTERIOR_SUFFIXES for m in meshes):
        warnings.append(f"no {name}_Win.fbx - windows will not light up at night (baked emissive glows day and night)")
    meta = _meta(d)
    for k, why in (("title", "no build-menu name"), ("description", "no build-menu description"), ("icon", "no build-menu picture")):
        if not meta[k]: warnings.append(f"{why}: add {k} to {os.path.dirname(d)}_build/{name}_meta.json"
                                         + (f" / render {name}_Icon.png there" if k == "icon" else ""))
    for f in png:
        sz = _png_size(os.path.join(d, f))
        if not sz: problems.append(f"{f}: not a PNG"); continue
        w, h = sz
        if w != h or w & (w - 1) or not 512 <= w <= 8192:
            warnings.append(f"{f}: {w}x{h} - CS2 wants square power-of-two textures (512-4096)")
        stem = f[:-4]
        if not any(stem.endswith("_" + s) for s in TEX_SUFFIXES): warnings.append(f"{f}: unknown texture suffix")
    if other: warnings.append(f"non-asset files in the asset folder: {other}")
    root = os.path.dirname(d)
    stray = [x for x in os.listdir(root) if os.path.isfile(os.path.join(root, x))]
    if stray: warnings.append(f"project root {root} has loose files ({len(stray)}); keep it to asset folders only")
    return {"asset": name, "directory": d, "project_root": root, "ok": not problems, "build_menu": meta, "problems": problems, "warnings": warnings,
            "meshes": meshes, "textures": len(png)}


def import_asset(a):
    v = validate(a)
    if not v["ok"] and not a.get("force"):
        return {"imported": False, "reason": "validation failed (pass force=true to try anyway)", "validation": v}
    q = {"directory": v["directory"], "timeout_s": a.get("timeout_s", 1200)}
    for k in ("project_root", "preset", "template", "seed", "lot_width", "lot_depth", "activate_tool",
              "window_light", "window_light_lod", "window_color"):
        if k in a: q[k] = a[k]
    res = RPC("asset_import", q, timeout=q["timeout_s"] + 30)
    res["validation_warnings"] = v["warnings"]
    # the game's pipeline logs errors but still reports "Completed ... Errors 0" per group - surface them honestly
    errs = [l for l in res.get("log", []) if "[ERROR]" in l or "Exception" in l]
    if errs:
        res["ok"] = False; res["errors"] = errs
        if any("being used by another process" in l for l in errs):
            res["hint"] = ("the game holds this asset's .Geometry file (it is loaded in the city): quit to the main menu "
                           "(or restart the game), re-import with preset 'None', then load the city again")
        elif any("KeyNotFoundException" in l and "FindIt" in l for l in errs):
            res["hint"] = ("the Find It mod crashed when the importer activated the placement tool (main menu); the asset "
                           "itself was saved - load a city, or re-import with preset 'None'")
    meta = _meta(v["directory"], a)
    if res.get("ok") and (meta["icon"] or meta["title"] or meta["description"]):
        q = {"name": v["asset"]}
        for k in ("icon", "title", "description"):
            if meta[k]: q[k] = _clean_png(meta[k]).replace("\\", "/") if k == "icon" else meta[k]
        try:
            r = RPC("asset_configure", q)
            res["build_menu"] = {k: r.get(k) for k in ("icon", "localization", "saved")}
        except Exception as e:
            res["build_menu"] = {"error": str(e)}; res["ok"] = False
    elif res.get("ok"):
        res["build_menu"] = "missing: no title/description/icon - add <project_root>_build/<Name>_meta.json + <Name>_Icon.png"
    return res


def configure(a):
    q = {k: a[k] for k in ("name", "lot_width", "lot_depth", "access_type", "save", "set", "add_components",
                           "remove_components", "copy_from", "copy_components", "describe", "icon", "title", "description", "locale", "describe_components", "allow_vanilla") if k in a}
    if q.get("icon"): q["icon"] = _clean_png(q["icon"])
    return RPC("asset_configure", q)
