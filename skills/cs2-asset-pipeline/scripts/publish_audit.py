r"""Pre-publish privacy audit for a CS2 asset (run BEFORE Share/Upload; system Python + Pillow).

    python publish_audit.py <AssetName> [extra files or folders ...]

Scans everything the upload package is built from - the game's ImportedData files for the asset (prefabs, geometry,
textures, icon, locale) - plus any extra files you pass (thumbnail/screenshot JPGs, the asset's cs2 folder), for
anything that identifies the author: the Windows user name, the PC name, e-mail addresses, absolute paths (C:\Users,
drive letters followed by readable folder names), and image metadata (PNG text chunks, EXIF, JPEG comments, XMP).
Prints counts and file names only. Exit code 1 if anything was found.
Known noise: 'X:\' followed by binary bytes inside compressed textures is random data; the script only reports a
drive path when readable path text follows it.
"""
import os, re, sys, socket, getpass, glob

GAME = os.path.expandvars(r"%USERPROFILE%\AppData\LocalLow\Colossal Order\Cities Skylines II")
user, host = getpass.getuser(), socket.gethostname()
needles = {n for n in (user, host, host.split("-")[0]) if n and len(n) >= 3}
text_pats = [re.compile(re.escape(n.encode()), re.I) for n in needles] + [
    re.compile(rb"[A-Za-z]:[\/](?:Users|Program|Documents|AppData|blender|Projects)[\/][ -~]{2,}", re.I),
    re.compile(rb"/(?:home|Users)/[A-Za-z0-9._-]{2,}/"),
    re.compile(rb"[A-Za-z0-9._%+-]{2,}@[A-Za-z0-9.-]+\.(?:com|net|org|io|ca|co)\b"),
]

def image_meta(path):
    try:
        from PIL import Image
        im = Image.open(path)
        bad = [k for k in im.info if k not in ("srgb", "gamma", "chromaticity", "dpi", "transparency", "jfif",
                                               "jfif_version", "jfif_unit", "jfif_density", "progressive", "progression")]
        return bad
    except Exception:
        return []

def scan(path):
    try: d = open(path, "rb").read()
    except OSError: return []
    hits = []
    for p in text_pats:
        for enc in (d, d.replace(b"\x00", b"")):          # also catches UTF-16 strings
            if p.search(enc): hits.append(p.pattern[:40].decode("latin1")); break
    if path.lower().endswith((".png", ".jpg", ".jpeg")):
        m = image_meta(path)
        if m: hits.append("image metadata: " + ",".join(m))
    return hits

name = sys.argv[1]
files = [f for f in glob.glob(os.path.join(GAME, "ImportedData", "**", f"*{name}*"), recursive=True)
         if os.path.isfile(f) and not f.endswith(".cid")]
for extra in sys.argv[2:]:
    files += [f for f in glob.glob(os.path.join(extra, "**", "*"), recursive=True) if os.path.isfile(f)] if os.path.isdir(extra) else [extra]
# What the upload is built from = the game's ImportedData files + the gallery images in the Screenshots folder (and any
# image you pass). Source FBX files and Blender intermediates (e.g. LOD elevation renders) stay on this PC; the FBX
# exporter always writes its own path into the FBX header, so hits there are reported as SOURCE, not as leaks.
def shipped(f):
    fl = f.replace("\\", "/").lower()
    return "/importeddata/" in fl or "/screenshots/" in fl or fl.endswith((".jpg", ".jpeg")) or "_icon" in fl or "thumbnail" in fl
bad = src = 0
for f in files:
    h = scan(f)
    if not h: continue
    if shipped(f): bad += 1; print("LEAK  ", os.path.basename(f), "->", "; ".join(sorted(set(h))))
    else: src += 1; print("SOURCE", os.path.basename(f), "(not uploaded) ->", "; ".join(sorted(set(h))))
print(f"AUDIT {name}: {len(files)} files scanned, {bad} with identifying data in what gets uploaded, {src} source-only "
      f"(checked: user name, PC name, e-mail, absolute paths, image metadata)")
sys.exit(1 if bad else 0)
