# GAME CLOSED. Point every surface of a pack (name prefix) at the textures of ONE freshly imported asset instead of re-importing all 400
# meshes for a texture-only change (a full re-import of a big pack takes far longer). A .Surface stores each
# texture as <slot name> 0xFF <16-byte GUID>. Main surfaces take the reference asset's main-surface GUIDs, _LOD1/_LOD2 surfaces
# its LOD1 surface GUIDs (the 512 px palette); _Win / _Gls surfaces are left alone. Then run dedupe_textures.py <prefix>.
#   python texture_swap.py <reference asset name, e.g. NA_PKHouseA_L1_1x2> [--prefix NA_PK] [--dry]
import os, re, glob, sys, shutil
DB = os.path.join(os.environ["USERPROFILE"], r"AppData\LocalLow\Colossal Order\Cities Skylines II\ImportedData")
BK = os.path.join(os.environ["USERPROFILE"], r"blender\_cs2_asset_backups\_texture_swap_backup")
ref = sys.argv[1]; dry = "--dry" in sys.argv
PFX = sys.argv[sys.argv.index("--prefix") + 1] if "--prefix" in sys.argv else ref[:5]
SLOT = re.compile(rb"_(BaseColorMap|ControlMask|MaskMap|NormalMap)\xff(.{16})", re.S)
def slots(path): return {m.group(1): m.group(2) for m in SLOT.finditer(open(path, "rb").read())}
def one(pattern):
    fs = [f for f in glob.glob(os.path.join(DB, pattern)) if not re.search(r"_(Win|Gls)_", os.path.basename(f))]
    fs.sort(key=os.path.getmtime); return fs[-1]                              # the newest import of the reference
BK_DEDUPE = os.path.join(os.environ["USERPROFILE"], r"blender\_cs2_asset_backups\_texture_dedupe_backup")
def guid_of(tex):
    h = open(tex + ".cid").read().strip(); return bytes(int(h[i + 1] + h[i], 16) for i in range(0, 32, 2))
def newest_tex(prefix, tag):
    """Newest <prefix>_<tag>_<hash>.Texture in ImportedData or the dedupe backup (moved back into ImportedData)."""
    fs = [f for d in (DB, BK_DEDUPE) for f in glob.glob(os.path.join(d, f"{prefix}_{tag}_[0-9a-f]*.Texture"))]
    f = max(fs, key=os.path.getmtime)
    if not f.startswith(DB):
        if not dry:
            for g in (f, f + ".cid"): shutil.move(g, os.path.join(DB, os.path.basename(g)))
        f = os.path.join(DB, os.path.basename(f)) if not dry else f
    return f
# Lesson: a re-imported asset's SURFACE may still point at stale (restored) textures, so take the reference
# asset's newest TEXTURE FILES directly (the latest import), never its surface.
TAG = {b"BaseColorMap": "BaseColor", b"ControlMask": "ControlMask", b"MaskMap": "MaskMap", b"NormalMap": "Normal"}
main_files = {k: newest_tex(ref, t) for k, t in TAG.items()}; lod_files = {k: newest_tex(ref + "_LOD1", t) for k, t in TAG.items()}
for k, f in list(main_files.items()) + list(lod_files.items()): print("  ref", k.decode(), os.path.basename(f), os.path.getsize(f))
main_ref = {k: guid_of(f) for k, f in main_files.items()}; lod_ref = {k: guid_of(f) for k, f in lod_files.items()}
# LOD2 may use its own (baked) texture set: take the reference's newest LOD2 files; if they are the palette too, same thing
try:
    lod2_files = {k: newest_tex(ref + "_LOD2", t) for k, t in TAG.items()}; lod2_ref = {k: guid_of(f) for k, f in lod2_files.items()}
    for k, f in lod2_files.items(): print("  ref LOD2", k.decode(), os.path.basename(f), os.path.getsize(f))
except ValueError:
    lod2_ref = lod_ref
assert all(os.path.getsize(f) > 1000000 for f in main_files.values()) and all(os.path.getsize(f) < 1000000 for f in lod_files.values())
os.makedirs(BK, exist_ok=True)
changed = same = 0
for s in glob.glob(os.path.join(DB, PFX + "*.Surface")) + glob.glob(os.path.join(DB, PFX + "*", "*.Surface")):
    n = os.path.basename(s)
    if re.search(r"_(Win|Gls)_", n): continue
    want = lod2_ref if "_LOD2_" in n else lod_ref if "_LOD1_" in n else main_ref
    b = open(s, "rb").read()
    nb = SLOT.sub(lambda m: b"_" + m.group(1) + b"\xff" + want[m.group(1)], b)
    if nb == b: same += 1; continue
    changed += 1
    if not dry:
        bk = os.path.join(BK, n)
        if not os.path.exists(bk): shutil.copy2(s, bk)
        open(s, "wb").write(nb)
print(f"TEXTURE_SWAP surfaces repointed {changed}, already current {same}" + ("  (dry run)" if dry else f"  backup {BK}"))
