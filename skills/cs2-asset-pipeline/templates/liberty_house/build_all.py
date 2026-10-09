# Rebuild Liberty House: exec(open(os.path.expanduser(r"~\blender\liberty-house\scripts\build_all.py")).read())
import os
import time
LH_DIR = os.path.expanduser(r"~\blender\liberty-house\scripts")
LH_LOG = {}
import bpy   # every LH_ mesh is rebuilt below, so fresh materials are safe (picks up MAT_SPECS edits)
for _m in [m for m in bpy.data.materials if m.name.startswith("LH_") and not m.name.startswith("LH_blk_")]:
    bpy.data.materials.remove(_m)
for LH_STEP in ("build_podium", "build_tower"):
    LH_T0 = time.time(); exec(open(LH_DIR + "\\" + LH_STEP + ".py").read()); LH_LOG[LH_STEP] = [round(time.time() - LH_T0, 2), result]
_bk = bpy.data.collections.get("LH_Blockout")
if _bk: _bk.hide_render = True; _bk.hide_viewport = True
result = LH_LOG
