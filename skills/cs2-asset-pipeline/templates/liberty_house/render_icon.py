# Build-menu icon: 3/4 view of the detailed model (street end + long face), transparent background, square PNG ->
# cs2_build\NA_LibertyHouse_Icon.png (picked up by cs2_asset_import via NA_LibertyHouse_meta.json).
# In the SOURCE model the dark street end faces +Y (the exporter turns it 180 deg to -Y), so view from az 145 deg.
# Headless: blender -b liberty_house.blend -P scripts\render_icon.py
import os
exec(open(os.path.expanduser(r"~\blender\liberty-house\scripts\lh_common.py")).read())
ICON_OUT = os.path.expanduser(r"~\blender\liberty-house\cs2_build\NA_LibertyHouse_Icon.png")
result = {"icon": render_icon(["LH_Podium", "LH_Tower"], ICON_OUT, size=512, az_deg=145.0, elev_deg=14.0, lens=50)}
print("ICON", result)
