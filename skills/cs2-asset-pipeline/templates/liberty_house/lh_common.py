# Liberty House (Liberty Village, Toronto) — reference dims + shared helpers.
# Sources: UrbanToronto db (25 storeys, 84.12 m, IBI Group/Arcadis, CentreCourt + Fitzrovia), OSM ways 1098971042
# (25-level tower part ~22 x 55 m) / 1099343143 (whole building ~24 x 60 m); photos in ..\refs (p0 aerial dusk,
# u1/u2 renderings, u3 construction).
# Axes: Y = long axis of the slab (north = +Y, the dark-glass end over the corner podium), X = east. Centred on origin.
import os
exec(open(os.path.expanduser(r"~\.claude\skills\blender-mcp-architecture\helpers\lbt_helpers.py")).read())
PREFIX = "LH"

# footprints (metres). Both LONG faces = white frame reading as 2-storey blocks; both SHORT ends = dark curtain wall.
# At each corner a recessed dark slot (NOTCH deep along the slab, SLOT wide) separates the white face from the dark end.
POD_X = 13.0                 # podium half width (26 m), x in [-13, 13]
POD_Y = (-31.0, 31.0)        # podium length 62 m
XW, XE = -11.0, 11.0         # tower x extent (22 m slab)
TWR_Y = (-30.5, 25.0)        # tower 55.5 m long (OSM ~55); ~6 m podium terrace in front of the north end
NOTCH = 3.0                  # corner slot depth (along y)
SLOT = 1.5                   # corner slot width (along x)

# storey table (26 floors + the pool level, checked against photos; published 84.12 m ~ pool-level roof):
# GF + 3 podium floors (L2-L4) + 22 residential (L5-L26 = 11 two-storey blocks) + pool/amenity level on the roof
GF = 5.0                     # retail / lobby ground floor behind the colonnade
POD_FL = 3.3                 # podium floors L2-L4
N_POD = 3
POD_TOP = GF + N_POD * POD_FL        # 14.9 podium roof / terrace
REVEAL = 0.8                 # dark recess between podium parapet and the transfer band
BAND = 1.6                   # white transfer band
TWR_BASE = POD_TOP + REVEAL  # 15.7 underside of the tower
Z0 = TWR_BASE + BAND         # 17.3 first residential floor (L5)
TYP = 2.85                   # residential floor-to-floor
N_BLOCKS = 11                # white frame reads as 2-storey blocks: L5-L26
N_TWR = 2 * N_BLOCKS         # 22 floors
TWR_TOP = Z0 + N_TWR * TYP   # 80.0 roof of L26 = pool deck
AMEN_TOP = TWR_TOP + 4.1     # pool-level glass amenity pavilion (and top of the dark street end), 84.1
CAP = 0.8                    # floating white roof cap -> 84.9
TOP = AMEN_TOP + CAP + 1.2   # mech screen top, 86.1

# ---------------------------------------------------------------- materials (get-or-create: never recreate -> no empty slots)
def _glass_mat(name):
    mb = MB(name, (0.05, 0.065, 0.08), rough=0.06, metal=0.55)
    tc = mb.node("ShaderNodeTexCoord"); sp = mb.sepxyz(_out(tc, "Object"))
    u = mb.math('ADD', sp.outputs[0], sp.outputs[1])
    cmb = mb.node("ShaderNodeCombineXYZ"); mb.link(u, cmb.inputs[0]); mb.link(sp.outputs[2], cmb.inputs[1])
    br = mb.brick(_out(cmb, "Vector"), (0.04, 0.055, 0.07), (0.075, 0.085, 0.095), (0.02, 0.02, 0.02),
                  mortar_size=0.0, width=1.6, height=TYP, offset=0.0, bias=-0.2)
    mb.base(_out(br, "Color")); return mb.m

MAT_SPECS = [  # name, base, rough, metal, alpha
    ("LH_White", (0.80, 0.80, 0.78), 0.35, 0.1, 1.0),
    ("LH_Glass", None, 0, 0, 1.0),
    ("LH_Mullion", (0.10, 0.11, 0.12), 0.35, 0.6, 1.0),
    ("LH_GreyAlu", (0.58, 0.59, 0.60), 0.35, 0.6, 1.0),
    ("LH_BalGlass", (0.16, 0.2, 0.21), 0.05, 0.3, 0.35),
    ("LH_Spandrel", (0.13, 0.145, 0.16), 0.3, 0.5, 1.0),
    ("LH_Green", (0.10, 0.22, 0.06), 0.9, 0.0, 1.0),
    ("LH_Paver", (0.50, 0.49, 0.46), 0.8, 0.0, 1.0),
    ("LH_Water", (0.08, 0.40, 0.55), 0.05, 0.0, 1.0),
    ("LH_Ground", (0.33, 0.33, 0.32), 0.85, 0.0, 1.0),
    ("LH_CWFrame", (0.30, 0.32, 0.35), 0.3, 0.7, 1.0),
    ("LH_Reveal", (0.03, 0.03, 0.035), 0.7, 0.0, 1.0),
    ("LH_Plaza", (0.52, 0.51, 0.49), 0.85, 0.0, 1.0),
]
WHITE, GLASS, MULL, GREY, BGLASS, SPAN, GREEN, PAVER, WATER, GROUND, CWF, RVL, PLAZA = range(13)
def get_mats():
    out = []
    for name, base, rough, metal, alpha in MAT_SPECS:
        m = bpy.data.materials.get(name)
        if m is None:
            m = _glass_mat(name) if name == "LH_Glass" else MB(name, base, rough=rough, metal=metal, alpha=alpha).m
        out.append(m)
    return out

# ---------------------------------------------------------------- face-local boxes
# A face F = (axis, sign): 'x'/'y' normal axis, sign +1/-1. n = distance from the centre along the outward normal,
# u = coordinate along the face (absolute x or y). Boxes are axis aligned, so this is just a coordinate swap.
EAST, WEST, NORTH, SOUTH = ('x', 1), ('x', -1), ('y', 1), ('y', -1)
def fb(bm, F, u0, u1, n0, n1, z0, z1, mi):
    ax, s = F; a, b = sorted((s * n0, s * n1)); u0, u1 = sorted((u0, u1))
    if u1 - u0 < 1e-4 or b - a < 1e-4 or z1 - z0 < 1e-4: return
    if ax == 'x': add_box(bm, a, b, u0, u1, z0, z1, mi)
    else: add_box(bm, u0, u1, a, b, z0, z1, mi)

def spaced(a, b, step):
    """Evenly spaced interior positions between a and b, ~step apart."""
    n = max(1, round((b - a) / step)); return [a + (b - a) * i / n for i in range(1, n)]
