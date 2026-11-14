"""SOLIDWORKS で部品を1個作るスクリプトのひな形（穴あきプレート）。
このファイルを部品のリポジトリにコピーし、sw_helpers.py の場所を sys.path に足して使う。
寸法 → 形を作る（スケッチは毎回完全定義） → assert で確かめる → 書き出す、の順。
書き出した STEP は、必ず別のソフト（FreeCAD など）で読み直して寸法を測る（SOLIDWORKS 上の形と STEP が食い違ったことがある）。
座標は「Z が上（厚み方向）」で考え、面は名前でなく法線で選ぶ。
"""
import math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # sw_helpers.py のあるフォルダ
from sw_helpers import *

# ---- 寸法（mm） ----
L, Wd, T = 40.0, 30.0, 10.0          # 外形 X × Y × 厚み Z
D_CENTER = 6.0                         # 中央の穴
D_CORNER, PX, PY = 3.4, 30.0, 20.0     # 四隅の穴とピッチ
NAME = "trial_plate"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT, exist_ok=True)

sw = connect()
model = new_part(sw)
xy = plane_by_normal(model, (0, 0, 1))   # XY 平面（Z が法線）
print("スケッチ面:", xy.Name)

# 外形：原点中心の長方形を Z+ に押し出す
with sketch_on(sw, model, xy) as (sk, P):
    sk.CreateCenterRectangle(*P(0, 0), *P(L / 2, Wd / 2))
    st = fully_define(model)
    assert st == C.swFullyConstrained, f"外形スケッチが完全定義でない: {st}"
base = extrude(model, last_feature(model), T)

# 穴：同じ面にスケッチして両方向に全貫通（穴は外形と別フィーチャーにして、あとで直しやすくする）
with sketch_on(sw, model, xy) as (sk, P):
    sk.CreateCircleByRadius(*P(0, 0), mm(D_CENTER / 2))
    for sx in (-1, 1):
        for sy in (-1, 1):
            c = sk.CreateCircleByRadius(*P(sx * PX / 2, sy * PY / 2), mm(D_CORNER / 2))
            dim_from_origin(sw, model, center_of(c))  # 穴の位置は原点からの寸法で決める
    st = fully_define(model)  # 原点にある中央の穴の中心と、径の寸法はここで付く
    assert st == C.swFullyConstrained, f"穴スケッチが完全定義でない: {st}"
holes = cut_through_all(model, last_feature(model))

# ---- 確かめる ----
assert not rebuild_errors(model), rebuild_errors(model)
vol, box = report(model)
expect = L * Wd * T - math.pi / 4 * (D_CENTER**2 + 4 * D_CORNER**2) * T
print(f"体積 {vol:.3f} mm3（計算 {expect:.3f}）  外形 {box}")
# SOLIDWORKS の体積・外形は概算なので、ゆるく確かめる（正確には FreeCAD で STEP を測る）
assert abs(vol - expect) / expect < 1e-4, (vol, expect)
assert all(abs(a - b) < 0.2 for a, b in zip(box, [-L/2, -Wd/2, 0, L/2, Wd/2, T])), box
for f in features(model):
    if f.GetTypeName2() in ("ProfileFeature",):
        print(f.Name, dimensions(f))
print(base.Name, dimensions(base))

# ---- 書き出す ----
for ext in ("SLDPRT", "step"):
    print("保存:", save(model, os.path.join(OUT, f"{NAME}.{ext}")))
