# Share identical textures between imported assets (game CLOSED). Every import stores its own copy of the textures, so a
# pack of N assets on one shared atlas carries N identical 5-map sets (a 100-asset pack: 530 files / 2.96 GB, 5 unique /
# 28 MB). This points every .Surface at ONE canonical .Texture per identical content and moves the duplicates to a backup.
#   python dedupe_textures.py <prefix> [backup_dir] [--dry]      e.g.  python dedupe_textures.py NA_MyPack
# .Surface stores each texture as 0xFF + 16 GUID bytes; the GUID bytes are the texture's .cid hex with the two nibbles of
# every byte swapped (verified: cid 4ed7c928... -> bytes e4 7d 9c 82 ...).
import sys, os, glob, hashlib, shutil, collections
DB = os.path.join(os.environ["USERPROFILE"], r"AppData\LocalLow\Colossal Order\Cities Skylines II\ImportedData")
prefix = sys.argv[1]; dry = "--dry" in sys.argv
args = [a for a in sys.argv[2:] if not a.startswith("--")]
# NEVER inside the game's user folder: the game loads assets from ALL of "Cities Skylines II\", not just ImportedData
backup = args[0] if args else os.path.join(os.environ["USERPROFILE"], r"blender\_cs2_asset_backups\_texture_dedupe_backup")
os.chdir(DB)
def guid(cid_hex): return bytes(int(cid_hex[i + 1] + cid_hex[i], 16) for i in range(0, 32, 2))
# Note: a re-imported surface can still point at a STALE texture (restored from here) - for texture changes use
# texture_swap.py (it takes the reference asset's newest texture FILES), then this script.
# preset-None RE-IMPORTS point their surfaces back at their ORIGINAL texture GUIDs, whose files an earlier run moved to the
# backup -> put every still-referenced backup texture back first, so it gets remapped below instead of dangling.
if os.path.isdir(backup):
    refs = set()
    for s in glob.glob(prefix + "*.Surface") + glob.glob(os.path.join(prefix + "*", "*.Surface")):
        refs.update(m.group(1) for m in __import__("re").finditer(b"_\w+?\xff(.{16})", open(s, "rb").read(), __import__("re").S))
    restored = 0
    for c in glob.glob(os.path.join(backup, "*.Texture.cid")):
        if guid(open(c).read().strip()) in refs and not dry:
            if os.path.exists(os.path.join(DB, os.path.basename(c[:-4]))): continue   # never overwrite a file in ImportedData
            for f in (c[:-4], c):
                if os.path.exists(f): shutil.move(f, os.path.join(DB, os.path.basename(f)))
            restored += 1
    if restored: print(f"DEDUPE restored {restored} still-referenced textures from the backup")
groups = collections.defaultdict(list)
for f in glob.glob(prefix + "*.Texture") + glob.glob(os.path.join(prefix + "*", "*.Texture")):
    groups[hashlib.sha1(open(f, "rb").read()).hexdigest()].append(f)
remap, dups = {}, []
for files in groups.values():
    files.sort(); canon = files[0]; cg = guid(open(canon + ".cid").read().strip())
    for f in files[1:]:
        remap[guid(open(f + ".cid").read().strip())] = cg; dups.append(f)
patched = 0
for s in glob.glob(prefix + "*.Surface") + glob.glob(os.path.join(prefix + "*", "*.Surface")):
    b = open(s, "rb").read(); nb = b
    for old, new in remap.items(): nb = nb.replace(old, new)
    if nb != b:
        patched += 1
        if not dry: open(s, "wb").write(nb)
freed = sum(os.path.getsize(f) for f in dups)
if not dry:
    os.makedirs(backup, exist_ok=True)
    for f in dups:
        for g in (f, f + ".cid"):
            if os.path.exists(g): shutil.move(g, os.path.join(backup, os.path.basename(g)))
print(f"DEDUPE unique {len(groups)}  duplicates {len(dups)}  surfaces patched {patched}  freed {freed / 1e6:.0f} MB"
      + ("  (dry run)" if dry else f"  backup {os.path.abspath(backup)}"))
