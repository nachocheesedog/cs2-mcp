# Check a built / published CS2 package (.cok) for the empty VT mid-mip cache that crashes the game at start.
#   python package_vt_check.py <file.cok | folder> [...]
# Folders are searched for *.cok recursively: the game's temp build folder %LocalLow%\...\Cities Skylines II\.packages
# (right after Build Package, BEFORE Submit), or .cache\Mods\pdx_mods\<id>_<ver> (a downloaded subscription).
# Format (decompiled AtlasMaterialsGrouper.Read + AtlasCategory(BinaryReader), game 1.6.2f1): int tileSize, int mipLevels,
# int maxGroupSize, int stacks, int groups, int entries; per stack: int count, then per category: int w, int h,
# int entries, int firstTile, int tiles, bool preProcessedMidMipDisabled, int mipBiasOverride.
# REGISTERED MATERIALS = sum of entries over categories that are not disabled. 0 = a game loading this package crashes at start
# (FATAL "Can't test if a material is a duplicate with registered materials"). Expect about 2 x the number of materials
# (two VT stacks per material): e.g. 26 for a 13-material package, 6 for a 3-material tower, 0 = broken.
import sys, os, glob, struct, zipfile


def parse(data):
    o = 0
    def i32():
        nonlocal o; v = struct.unpack_from("<i", data, o)[0]; o += 4; return v
    tile, mips, _maxg, nstacks, ngroups, nentries = (i32() for _ in range(6))
    cats = []
    for s in range(nstacks):
        for _ in range(i32()):
            w, h, ec, _ft, _tc = (i32() for _ in range(5)); dis = data[o]; o += 1; i32()
            cats.append((s, w, h, ec, dis))
    return tile, mips, ngroups, nentries, cats


paths = []
for a in sys.argv[1:] or ["."]:
    paths += [a] if a.lower().endswith(".cok") else glob.glob(os.path.join(a, "**", "*.cok"), recursive=True)
if not paths: raise SystemExit("no .cok found")
bad = 0
for p in paths:
    z = zipfile.ZipFile(p)
    mm = [n for n in z.namelist() if n.endswith(".MidMips")]
    surfaces = sum(1 for n in z.namelist() if n.endswith(".Surface"))
    if not mm:
        print(f"PKG_VT {os.path.basename(p)}: no MidMips file ({surfaces} surfaces)"); continue
    for n in mm:
        tile, mips, ng, ne, cats = parse(z.read(n))
        reg = sum(ec for (_s, _w, _h, ec, dis) in cats if not dis)
        sizes = sorted({f"{w}x{h}" for (_s, w, h, _e, _d) in cats})
        verdict = "OK" if reg > 0 else "BROKEN - empty cache, crashes the game at start; do NOT submit"
        bad += reg == 0
        print(f"PKG_VT {os.path.basename(p)}: {n} tile {tile} mips {mips} categories {len(cats)} sizes {sizes} "
              f"registered materials {reg} (surfaces in package {surfaces}) -> {verdict}")
sys.exit(1 if bad else 0)
