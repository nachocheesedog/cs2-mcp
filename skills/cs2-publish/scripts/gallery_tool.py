r"""Gallery images for a Paradox Mods upload (system Python + Pillow). Never carries metadata over.

  python gallery_tool.py strip <file or folder> ...        rewrite PNG/JPG/WEBP files in place without any metadata
  python gallery_tool.py sheet <folder> <out.jpg>          contact sheet of the PNG/JPG files in a folder (for review)
  python gallery_tool.py make <image> <ShortName> <build dir> [--cx 0.5] [--thumb <image>]
  python gallery_tool.py extra <image> <ShortName>_Gallery_<n>_<label> <build dir>   extra gallery shots (clean JPG)
        -> <ShortName>_Gallery.jpg (full frame) + <ShortName>_THUMBNAIL.jpg (square 1024, cropped around --cx = the
           asset's horizontal centre as a fraction of the width, or from a separate --thumb image), written to the
           build dir AND the game's Screenshots folder (the only folder the upload picker lists). Both under 2 MB.
"""
import os, sys, glob
from PIL import Image

SHOTS = os.path.expandvars(r"%USERPROFILE%\AppData\LocalLow\Colossal Order\Cities Skylines II\Screenshots")
EXT = (".png", ".jpg", ".jpeg", ".webp")

def fresh(im):
    """Copy of the pixels only: no EXIF, XMP, C2PA, ICC text, PNG text chunks."""
    mode = "RGBA" if im.mode in ("RGBA", "LA", "P") and "transparency" in im.info or im.mode == "RGBA" else "RGB"
    src = im.convert(mode); out = Image.new(mode, src.size); out.paste(src); return out

def files_of(args):
    out = []
    for a in args:
        out += [f for f in glob.glob(os.path.join(a, "*")) if f.lower().endswith(EXT)] if os.path.isdir(a) else [a]
    return out

def save_jpg(im, path, max_bytes=2_000_000):
    # Upload limit is 2 MB per image, and the game RE-ENCODES screenshots when uploading: a 2560x1440 JPG of ~1.3 MB came
    # out over the limit. So gallery images are capped at 1920 wide and start at quality 85.
    im = im.convert("RGB")
    if im.width > 1920: im = im.resize((1920, round(im.height * 1920 / im.width)), Image.LANCZOS)
    q = 85
    while True:
        im.convert("RGB").save(path, "JPEG", quality=q)
        if os.path.getsize(path) <= max_bytes or q <= 60: return path
        q -= 6

cmd = sys.argv[1]
if cmd == "strip":
    for f in files_of(sys.argv[2:]):
        im = Image.open(f); im.load(); fr = fresh(im)
        fmt = {".png": "PNG", ".webp": "WEBP"}.get(os.path.splitext(f)[1].lower(), "JPEG")
        fr.save(f, fmt, **({"quality": 92} if fmt != "PNG" else {}))
        print("STRIPPED", os.path.basename(f), list(Image.open(f).info.keys()))
elif cmd == "sheet":
    fs = sorted(files_of([sys.argv[2]])); ims = []
    for f in fs:
        im = fresh(Image.open(f)); bg = Image.new("RGB", im.size, (255, 0, 255))
        bg.paste(im, (0, 0), im if im.mode == "RGBA" else None); bg.thumbnail((480, 480)); ims.append(bg)
    W = sum(i.width for i in ims); sh = Image.new("RGB", (W, max(i.height for i in ims)), (40, 40, 40)); x = 0
    for i in ims: sh.paste(i, (x, 0)); x += i.width
    sh.save(sys.argv[3], quality=85); print("SHEET", [os.path.basename(f) for f in fs])
elif cmd == "extra":
    src, name, build = sys.argv[2], sys.argv[3], sys.argv[4]
    g = fresh(Image.open(src)).convert("RGB")
    for d in (build, SHOTS):
        p = save_jpg(g, os.path.join(d, name + ".jpg"))
    print("MADE", p, g.size, os.path.getsize(p) // 1024, "KB", list(Image.open(p).info.keys()))
elif cmd == "make":
    src, short, build = sys.argv[2], sys.argv[3], sys.argv[4]
    cx = float(sys.argv[sys.argv.index("--cx") + 1]) if "--cx" in sys.argv else 0.5
    g = fresh(Image.open(src)).convert("RGB")
    if "--thumb" in sys.argv:
        t = fresh(Image.open(sys.argv[sys.argv.index("--thumb") + 1])).convert("RGB")
    else:
        t = g
    s = min(t.size); x0 = int(max(0, min(t.width - s, cx * t.width - s / 2))); y0 = (t.height - s) // 2
    th = t.crop((x0, y0, x0 + s, y0 + s)).resize((1024, 1024), Image.LANCZOS)
    for d in (build, SHOTS):
        os.makedirs(d, exist_ok=True)
        a = save_jpg(g, os.path.join(d, f"{short}_Gallery.jpg")); b = save_jpg(th, os.path.join(d, f"{short}_THUMBNAIL.jpg"))   # caps: easy to spot in the picker
    for p in (a, b): print("MADE", p, Image.open(p).size, os.path.getsize(p) // 1024, "KB", list(Image.open(p).info.keys()))
