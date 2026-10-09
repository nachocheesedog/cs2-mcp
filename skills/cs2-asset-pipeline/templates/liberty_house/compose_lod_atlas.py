# Pack the S/E/N/W/Top elevation renders into LOD atlases (system Python):
#   NA_LibertyHouse_LOD1_*.png at 2048 and NA_LibertyHouse_LOD2_*.png at 1024 (same layout -> same UVs).
# Writes cs2_build\lod_layout.json with each view's pixel rect (in 2048 space).
import json, os
import numpy as np
from PIL import Image
ROOT = os.path.expanduser(r"~\blender\liberty-house")
NAME = "NA_LibertyHouse"
SRC = os.path.join(ROOT, "cs2_build", "lod_src"); DST = os.path.join(ROOT, "cs2", NAME)
N = 2048; PAD = 4
info = json.load(open(os.path.join(SRC, "views.json")))
rects, x = {}, PAD
for v in ["S", "E", "N", "W", "T"]:
    w, h = info["views"][v]["px"]; rects[v] = [x, PAD, w, min(h, N - 2 * PAD)]; x += w + 2 * PAD
assert x - PAD <= N, x
def load(v, kind):
    im = Image.open(os.path.join(SRC, f"{v}_{kind}.png")).convert("RGBA")
    r = rects[v]
    if im.size != (r[2], r[3]): im = im.resize((r[2], r[3]), Image.LANCZOS)
    a = np.asarray(im, np.float32) / 255
    bg = np.array([0.55, 0.55, 0.53]) if kind == "BaseColor" else np.zeros(3)    # background = white-ish cladding
    return a[..., :3] * a[..., 3:4] + bg * (1 - a[..., 3:4])
for kind in ("BaseColor", "Emissive"):
    atlas = np.zeros((N, N, 3), np.float32)
    if kind == "BaseColor": atlas[:] = (0.55, 0.55, 0.53)
    for v, (x0, y0, w, h) in rects.items():
        rgb = load(v, kind)
        atlas[y0:y0 + h, x0:x0 + w] = rgb
        atlas[max(0, y0 - PAD):y0, x0:x0 + w] = rgb[:1]; atlas[y0 + h:y0 + h + PAD, x0:x0 + w] = rgb[-1:]
        atlas[y0:y0 + h, max(0, x0 - PAD):x0] = rgb[:, :1]; atlas[y0:y0 + h, x0 + w:x0 + w + PAD] = rgb[:, -1:]
    if kind == "BaseColor": base = atlas
    else: emis = atlas
lum = base.mean(axis=2)
metal = np.where(lum > 0.35, 0.1, 0.5); gloss = np.where(lum > 0.35, 0.5, 0.9)      # pale = cladding, dark = glass
snow = np.ones((N, N), np.float32); tx, ty, tw, th = rects["T"]; snow[ty:ty + th, tx:tx + tw] = 0.0
normal = np.zeros((N, N, 3), np.float32); normal[:] = (0.5, 0.5, 1.0)
mask = np.dstack([metal, np.zeros_like(metal), np.zeros_like(metal), gloss])
ctrl = np.dstack([np.zeros_like(metal)] * 3 + [snow])
def srgb(x): return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(np.clip(x, 0, 1), 1 / 2.4) - 0.055)
def save(arr, lod, name, mode, size):
    im = Image.fromarray(np.clip(arr * 255 + 0.5, 0, 255).astype(np.uint8), mode)
    if size != N: im = im.resize((size, size), Image.LANCZOS)
    im.save(os.path.join(DST, f"{NAME}_{lod}_{name}.png"))
# elevation renders were written with the Standard view transform = already sRGB-encoded pixels -> save as-is
for lod, size in (("LOD1", 2048), ("LOD2", 1024)):
    save(base, lod, "BaseColor", "RGB", size); save(emis, lod, "Emissive", "RGB", size)
    save(mask, lod, "MaskMap", "RGBA", size); save(ctrl, lod, "ControlMask", "RGBA", size); save(normal, lod, "Normal", "RGB", size)
json.dump({"size": N, "rects": rects, "bbox": info["bbox"]}, open(os.path.join(ROOT, "cs2_build", "lod_layout.json"), "w"), indent=1)
print("lod atlases ok", rects)
