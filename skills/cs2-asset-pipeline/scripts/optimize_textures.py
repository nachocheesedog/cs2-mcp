# VT-safe game-texture diet + packaging gate for CS2 asset folders (system Python + Pillow + numpy).
#   python optimize_textures.py <asset dir> [...] [--dry]     share LOD maps that are exact copies of LOD0's
#   python optimize_textures.py <asset dir> [...] --check     VT gate only: exit 1 if any material would be dropped
#
# THE VT RULE (decompiled SurfaceAsset.Save(vt:true), game 1.6.2f1): when the game BUILDS A PACKAGE (Share),
# each material's textures go into the streaming atlas only if EVERY texture is >= the VT tile (512 px) and all
# textures on one stack have the same size. Otherwise the material is silently left out. A package whose materials
# are ALL left out gets an EMPTY StreamingData~/TS512_MB3.MidMips, and an empty cache makes every subscriber's game
# crash at start (FATAL "Can't test if a material is a duplicate with registered materials",
# AtlasMaterialsGrouper.IsDuplicate: m_RegisteredMaterials.Count == 0). A published vehicle pack shipped like that (64 px
# constant maps, 2048/1024 mix).
# So: never shrink a map below its material's size, never below 512, and keep every map of one material the same size.
# Local (unpackaged) imports do not show the problem - it appears only in the published package.
#
# What this script does (lossless): a LOD map (<N>_LOD1_X.png ...) that is pixel-identical AND the same size as LOD0's
# <N>_X.png is removed and mapped in settings.json "sharedAssets" (Colossal's own mechanism; the VT packager handles
# textures referenced by several surfaces). Nothing is resized or dropped. Originals go to
# <project>/cs2_build/_tex_full/<asset>/ first. Re-runnable; earlier shares whose file is still absent are kept.
import os, re, sys, json, shutil
import numpy as np
from PIL import Image

DRY, CHECK = "--dry" in sys.argv, "--check" in sys.argv
dirs = [a for a in sys.argv[1:] if not a.startswith("--")]
if not dirs: raise SystemExit("usage: optimize_textures.py <asset dir> ... [--dry | --check]")
VT_TILE = 512
MIB = 1048576


def cost(w):                                                   # stored BC7 bytes with mips (16 B per 4x4 block)
    t = 0
    while True:
        t += ((w + 3) // 4) ** 2 * 16
        if w <= 1: return t
        w //= 2


def scan(d):
    name = os.path.basename(d)
    pat = re.compile(re.escape(name) + r"(_LOD(\d+))?_([A-Za-z0-9]+)\.png$")
    maps = {}
    for f in os.listdir(d):
        m = pat.match(f)
        if m: maps[(int(m.group(2) or 0), m.group(3))] = f
    sj = os.path.join(d, "settings.json")
    shared = json.load(open(sj)).get("sharedAssets", {}) if os.path.exists(sj) else {}
    return name, maps, shared


def vt_check(d):
    """Effective maps per LOD material (own file or shared target) -> problems that would drop it from the VT atlas."""
    name, maps, shared = scan(d)
    lods = sorted({k[0] for k in maps} | {int(m.group(1) or 0) for m in (re.match(re.escape(name) + r"(?:_LOD(\d+))?_", f) for f in shared) if m})
    problems = []
    for lod in lods:
        pre = f"{name}_LOD{lod}_" if lod else f"{name}_"
        eff = {}
        for (l, kind), f in maps.items():
            if l == lod and not re.fullmatch(r"Emissive\d+", kind): eff[kind] = f
        for f, t in shared.items():
            if f.startswith(pre) and not (lod == 0 and "_LOD" in f):
                kind = f[len(pre):-4]
                if "_" not in kind and not re.fullmatch(r"Emissive\d+", kind): eff.setdefault(kind, t)
        sizes = {}
        for kind, f in eff.items():
            p = os.path.join(d, f)
            if not os.path.exists(p): problems.append(f"LOD{lod} {kind}: shared target {f} missing"); continue
            sizes[kind] = Image.open(p).size
        small = {k: s for k, s in sizes.items() if min(s) < VT_TILE}
        if small: problems.append(f"LOD{lod}: below {VT_TILE} px {small}")
        if len(set(sizes.values())) > 1: problems.append(f"LOD{lod}: mixed sizes {sizes}")
    return name, problems


def optimize(d):
    d = os.path.abspath(d); name, maps, old = scan(d)
    proj = os.path.dirname(os.path.dirname(d))
    if not maps: return f"{name}: no maps"
    before = sum(cost(Image.open(os.path.join(d, f)).size[0]) for f in maps.values())
    shared, drop = {}, []
    for (lod, kind), f in maps.items():
        base = (0, kind)
        if lod == 0 or base not in maps or re.fullmatch(r"Emissive\d+", kind): continue
        a, b = Image.open(os.path.join(d, f)), Image.open(os.path.join(d, maps[base]))
        if a.size == b.size and np.array_equal(np.asarray(a.convert("RGBA")), np.asarray(b.convert("RGBA"))):
            drop.append((lod, kind)); shared[f] = maps[base]
    for f, t in old.items():
        if f not in maps.values() and os.path.exists(os.path.join(d, t)): shared.setdefault(f, t)
    after = sum(cost(Image.open(os.path.join(d, maps[k])).size[0]) for k in maps if k not in drop)
    msg = f"{name}: {len(maps)} maps -> {len(maps) - len(drop)} stored ({len(shared)} shared); est. {before / MIB:.1f} -> {after / MIB:.1f} MB"
    if DRY or not drop: return msg + (" [dry run]" if DRY else " [nothing to share]")
    bk = os.path.join(proj, "cs2_build", "_tex_full", name); os.makedirs(bk, exist_ok=True)
    for k in drop: shutil.copy2(os.path.join(d, maps[k]), bk)
    for k in drop: os.remove(os.path.join(d, maps[k]))
    json.dump({"sharedAssets": dict(sorted(shared.items()))}, open(os.path.join(d, "settings.json"), "w"), indent=1)
    return msg + f" (removed copies in {bk})"


bad = 0
for d in dirs:
    if not CHECK: print(optimize(d))
    n, probs = vt_check(os.path.abspath(d))
    print(f"VT_CHECK {n}: " + ("OK" if not probs else "FAIL - " + "; ".join(probs)))
    bad += bool(probs)
print("OPTIMIZE_DONE" if not CHECK else "CHECK_DONE", f"({bad} failing)")
sys.exit(1 if (CHECK and bad) else 0)
