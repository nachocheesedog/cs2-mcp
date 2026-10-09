# CS2 trim-sheet atlas for NA_LibertyHouse (system Python: numpy + PIL).
# 2048 x 2048. U spans the full width = 16 m of facade (128 px/m) and wraps, so strips tile horizontally.
# Strip modes (used by export_build.py's UV mapper):
#   floor   - each (per-floor split) glass quad's full height maps onto the strip
#   uniform - constant along V (V = strip middle); U still runs along the face so noise/variation tiles
# Almost all detail (frames, mullions, bands, rails) is real geometry, so only the glass strip carries a pattern:
# per-pane interior variation + a reflection gradient + ~18% warm lit panes on the Emissive map (night windows).
# Outputs: cs2\NA_LibertyHouse\NA_LibertyHouse_{BaseColor,Normal,MaskMap,ControlMask,Emissive}.png
#          cs2_build\atlas_layout.json
import numpy as np, json, os
from PIL import Image
ROOT = os.path.expanduser(r"~\blender\liberty-house")
NAME = "NA_LibertyHouse"
OUT = os.path.join(ROOT, "cs2", NAME); LAYOUT = os.path.join(ROOT, "cs2_build", "atlas_layout.json")
os.makedirs(OUT, exist_ok=True); os.makedirs(os.path.dirname(LAYOUT), exist_ok=True)
N, PPM = 2048, 128.0
rng = np.random.default_rng(7)
TYP = 2.85
# Emissive strength (linear). CS2 renders the Emissive map as bright light, day and night: 0.35 for lit windows was
# white-hot in game, 0.05 "still a bit too bright" -> 0.02; the accent strip stays clearly brighter.
EMIS_WINDOWS = 0.0          # windows now light at night via the game's _Win interior mapping
EMIS_ACCENT = 0.12
STRIPS = [  # name, mode, px height, world height (m) for floor
    ("glass", "floor", round(TYP * PPM), TYP),
    ("white", "uniform", 48, None), ("mullion", "uniform", 32, None), ("grey_alu", "uniform", 32, None),
    ("bal_glass", "uniform", 32, None), ("spandrel", "uniform", 32, None), ("cw_frame", "uniform", 32, None),
    ("reveal", "uniform", 32, None), ("green", "uniform", 48, None), ("paver", "uniform", 48, None),
    ("water", "uniform", 32, None), ("accent", "uniform", 32, None), ("roof", "uniform", 32, None),
    ("plaza", "ground", 512, 4.0),          # 4 m x 16 m of concrete pavers; exporter maps each 4 m band of the lot slab
]
PAD = 12
layout, y = {}, PAD
for name, mode, h, wh in STRIPS:
    layout[name] = {"mode": mode, "y0": y, "h": h, "world_h": wh}
    y += h + 2 * PAD
assert y < N, y

base = np.zeros((N, N, 3), np.float32); height = np.zeros((N, N), np.float32)
metal = np.zeros((N, N), np.float32); gloss = np.full((N, N), 0.3, np.float32)
emis = np.zeros((N, N, 3), np.float32); snow_ok = np.ones((N, N), np.float32)
U = np.arange(N)[None, :] / PPM
def region(name):
    s = layout[name]; return slice(max(s["y0"] - PAD, 0), min(s["y0"] + s["h"] + PAD, N)), s
def noise2(h, w, scale, oct=3):
    out = np.zeros((h, w), np.float32)
    for o in range(oct):
        cell = max(2, int(scale / (2 ** o)))
        g = rng.random((h // cell + 2, w // cell + 2)).astype(np.float32)
        img = np.array(Image.fromarray((g * 255).astype(np.uint8)).resize((w + cell * 2, h + cell * 2), Image.BICUBIC), np.float32)[:h, :w] / 255
        out += img / (2 ** o)
    return out / sum(1 / (2 ** o) for o in range(oct))
def fill(name, rgb, m=0.0, gl=0.3, var=0.05, vscale=64, snow=1.0):
    rs, s = region(name); h = rs.stop - rs.start
    nz = noise2(h, N, vscale)
    base[rs] = np.clip(np.array(rgb, np.float32)[None, None, :] * (1 + var * (nz[..., None] - 0.5) * 2), 0, 1)
    metal[rs] = m; gloss[rs] = gl; snow_ok[rs] = snow; height[rs] = nz * 0.15
    return rs, s

# glass: one storey, bottom of the strip = floor level
rs, s = region("glass"); rows = np.arange(rs.start, rs.stop)
zf = np.clip((s["y0"] + s["h"] - rows) / s["h"] * TYP, 0, TYP)[:, None]
PANE = 1.5
pane_i = np.floor(U / PANE).astype(int)
tone = rng.random(pane_i.max() + 2).astype(np.float32)               # per-pane interior (curtains / furniture) variation
grad = (zf / TYP) * 0.3                                              # sky reflection brighter toward the head
glass_c = np.array([0.045, 0.058, 0.072], np.float32)
col = glass_c * (1 + grad[..., None]) * (0.85 + 0.35 * tone[pane_i][..., None])
curtain = (tone[pane_i] > 0.7) & (zf > 0.1)                          # some panes with pale drawn blinds
col = np.where(curtain[..., None], np.array([0.16, 0.16, 0.15], np.float32), col)
base[rs] = col
metal[rs] = np.broadcast_to(np.where(curtain, 0.0, 0.5), (len(rows), N))
gloss[rs] = np.broadcast_to(np.where(curtain, 0.55, 0.93), (len(rows), N))
lit = rng.random(pane_i.max() + 2) < 0.18
warm = rng.random(pane_i.max() + 2) * 0.5 + 0.5
e = (lit[pane_i] & (zf > 0.15) & (zf < TYP - 0.3)).astype(np.float32) * warm[pane_i]
emis[rs] = e[..., None] * np.array([1.0, 0.78, 0.5], np.float32) * EMIS_WINDOWS

fill("white", (0.80, 0.80, 0.78), m=0.1, gl=0.55, var=0.04)
fill("mullion", (0.10, 0.11, 0.12), m=0.8, gl=0.6)
fill("grey_alu", (0.58, 0.59, 0.60), m=0.85, gl=0.6)
fill("bal_glass", (0.16, 0.20, 0.21), m=0.3, gl=0.95)
fill("spandrel", (0.13, 0.145, 0.16), m=0.6, gl=0.7)
fill("cw_frame", (0.30, 0.32, 0.35), m=0.85, gl=0.65)
fill("reveal", (0.03, 0.03, 0.035), m=0.0, gl=0.2)
fill("green", (0.10, 0.22, 0.06), m=0.0, gl=0.1, var=0.35, vscale=6, snow=0.0)
fill("paver", (0.50, 0.49, 0.46), m=0.0, gl=0.2, var=0.12, vscale=12)
fill("water", (0.08, 0.40, 0.55), m=0.0, gl=0.95, snow=0.0)
rs, s = fill("accent", (1.0, 0.62, 0.25), m=0.0, gl=0.5); emis[rs] = np.array([1.0, 0.55, 0.18], np.float32) * EMIS_ACCENT
fill("roof", (0.24, 0.24, 0.23), m=0.0, gl=0.15, var=0.2, vscale=12)
# plaza: 0.6 x 0.6 m concrete pavers, 8 mm joints, per-paver tone, a darker 1.2 m banding every 4th row
rs, s = region("plaza"); rows = np.arange(rs.start, rs.stop)
yv = ((s["y0"] + s["h"] - rows) / s["h"] * s["world_h"])[:, None]
pi_, pj = np.floor(U / 0.6).astype(int), np.floor(yv / 0.6).astype(int)
joint = ((U % 0.6) < 0.008) | ((yv % 0.6) < 0.008)
r = np.random.default_rng(31).random((200, 200)).astype(np.float32)
t = r[pj % 200, pi_ % 200]
band = (pj % 4 == 0)
col = np.array([0.50, 0.49, 0.46], np.float32) * (0.9 + 0.2 * t[..., None])
col = np.where(band[..., None], col * 0.82, col)
col = np.where(joint[..., None], np.array([0.30, 0.29, 0.28], np.float32), col)
base[rs] = col * (0.95 + 0.1 * noise2(len(rows), N, 48)[..., None])
metal[rs] = 0.0; gloss[rs] = np.where(joint, 0.05, 0.18); height[rs] = np.where(joint, 0.0, 0.6)

def srgb(x): return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(np.clip(x, 0, 1), 1 / 2.4) - 0.055)
def save(arr, name, mode):
    Image.fromarray(np.clip(arr * 255 + 0.5, 0, 255).astype(np.uint8), mode).save(os.path.join(OUT, f"{NAME}_{name}.png"))
save(srgb(base), "BaseColor", "RGB")
save(srgb(emis), "Emissive", "RGB")
save(np.dstack([metal, np.zeros_like(metal), np.zeros_like(metal), gloss]), "MaskMap", "RGBA")
save(np.dstack([np.zeros_like(metal)] * 3 + [snow_ok]), "ControlMask", "RGBA")
gy, gx = np.gradient(height); k = 2.0
nx, ny = -gx * k, gy * k; nz_ = np.ones_like(nx); ln = np.sqrt(nx * nx + ny * ny + nz_ * nz_)
save(np.dstack([nx / ln * 0.5 + 0.5, ny / ln * 0.5 + 0.5, nz_ / ln * 0.5 + 0.5]), "Normal", "RGB")
json.dump({"size": N, "ppm_u": PPM, "tile_m": N / PPM, "strips": layout}, open(LAYOUT, "w"), indent=1)
print("atlas ok, used rows:", y)
