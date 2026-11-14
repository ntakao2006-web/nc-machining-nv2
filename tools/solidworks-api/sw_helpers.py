"""SOLIDWORKS を Python（pywin32）から COM API で動かして部品を作るための小さな道具。

使い方は template_part.py と docs/solidworks/api-python.md。寸法はすべて mm で受け取り、API には m で渡す。
SOLIDWORKS 2026 SP2.1（日本語版・Z が上の部品テンプレート）で確認した。
"""
import contextlib
import os
import pythoncom
import win32com.client as wc
from win32com.client import gencache

SW_DIR = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                      "SOLIDWORKS Corp", "SOLIDWORKS")


def _typelib(name):
    a = pythoncom.LoadTypeLib(os.path.join(SW_DIR, name)).GetLibAttr()
    return gencache.EnsureModule(str(a[0]), a[1], a[3], a[4])


SW = _typelib("sldworks.tlb")      # インターフェース（ISldWorks, IModelDoc2, ...）
C = _typelib("swconst.tlb").constants  # 定数（C.swEndCondBlind など）


def mm(v):
    return v / 1000.0


def W(cls, obj):
    """COM の戻り値を型付きの皮で包む。None はそのまま返す。"""
    return None if obj is None else cls(getattr(obj, "_oleobj_", obj))


def connect(visible=True):
    """起動中の SOLIDWORKS につなぐ（起動していなければ起動する）。"""
    sw = W(SW.ISldWorks, wc.Dispatch("SldWorks.Application"))
    sw.Visible = visible
    return sw


@contextlib.contextmanager
def user_toggle(sw, pref, value):
    """ユーザー設定を一時的に変え、終わったら必ず元に戻す。"""
    old = sw.GetUserPreferenceToggle(pref)
    sw.SetUserPreferenceToggle(pref, value)
    try:
        yield
    finally:
        sw.SetUserPreferenceToggle(pref, old)


def new_part(sw, template=None):
    tpl = template or sw.GetUserPreferenceStringValue(C.swDefaultTemplatePart)
    model = W(SW.IModelDoc2, sw.NewDocument(tpl, 0, 0, 0))
    if model is None:
        raise RuntimeError(f"部品を作れなかった（テンプレート: {tpl}）")
    return model


def features(model):
    f = W(SW.IFeature, model.FirstFeature())
    while f is not None:
        yield f
        f = W(SW.IFeature, f.GetNextFeature())


def planes(model):
    """基準面の一覧: [(feature, 名前, 法線(x,y,z), 面内のX方向)]"""
    out = []
    for f in features(model):
        if f.GetTypeName2() == "RefPlane":
            rp = W(SW.IRefPlane, f.GetSpecificFeature2())
            d = W(SW.IMathTransform, rp.Transform).ArrayData
            r = lambda v: tuple(round(x, 6) + 0.0 for x in v)
            out.append((f, f.Name, r(d[6:9]), r(d[0:3])))
    return out


def plane_by_normal(model, normal):
    """法線が normal（例 (0,0,1)）と平行な基準面を返す。向きの正負は問わない。"""
    for f, name, n, _ in planes(model):
        if abs(sum(a * b for a, b in zip(n, normal))) > 0.999:
            return f
    raise RuntimeError(f"法線 {normal} の基準面が無い: {[(p[1], p[2]) for p in planes(model)]}")


@contextlib.contextmanager
def sketch_on(sw, model, plane_feature):
    """with sketch_on(sw, model, plane) as (sk, P): で、sk に線を描く。抜けるとスケッチを閉じる。
    P(x, y, z=0) はモデル座標（mm）をスケッチ座標（m）に直す。**座標は必ず P を通して渡す。**
    スケッチの横軸がモデルのXとは限らない（Z が上のテンプレートでは、XY 平面のスケッチの横軸が +Y）。
    閉じたスケッチの IFeature は last_feature(model) で取れる。"""
    model.ClearSelection2(True)
    plane_feature.Select2(False, 0)
    sk = W(SW.ISketchManager, model.SketchManager)
    sk.InsertSketch(True)
    # 基準面の座標系（面内のX'・Y'・法線と原点）でモデル座標をスケッチ座標に直す。
    # ISketch.ModelToSketchTransform を IMathUtility.CreatePoint 経由で使うと、pywin32 からでは
    # 正しい値にならなかった（(20,15)→(20,0)）ので使わない。
    d = W(SW.IMathTransform, W(SW.IRefPlane, plane_feature.GetSpecificFeature2()).Transform).ArrayData
    ex, ey, ez, org = d[0:3], d[3:6], d[6:9], d[9:12]
    dot = lambda u, v: sum(a * b for a, b in zip(u, v))

    def P(x, y, z=0.0):
        r = [mm(x) - org[0], mm(y) - org[1], mm(z) - org[2]]
        return tuple(round(dot(r, e), 12) + 0.0 for e in (ex, ey, ez))

    sk.AddToDB = True  # スナップ・推測拘束で点がずれないようにする
    try:
        yield sk, P
    finally:
        sk.AddToDB = False
        sk.InsertSketch(True)


def _origin_id(model):
    origin = next(f for f in features(model) if f.GetTypeName2() == "OriginProfileFeature")
    return f"Point1@{origin.Name}"


def center_of(segment):
    """CreateCircleByRadius などが返した円・円弧の中心点（ISketchPoint）。"""
    return W(SW.ISketchPoint, W(SW.ISketchArc, segment._oleobj_ if hasattr(segment, "_oleobj_") else segment).GetCenterPoint2())


def dim_from_origin(sw, model, point):
    """スケッチ点を、原点からの水平・垂直の寸法で位置決めする（0 の向きは整列の拘束にする）。
    FullyDefineSketch は原点を基準に渡しても位置の寸法を付けないことが多いので、
    位置が大事な点（穴の中心など）は、この関数で明示的に決める。"""
    point = W(SW.ISketchPoint, point)
    me = W(SW.IModelDocExtension, model.Extension)
    oid = _origin_id(model)
    x, y = point.X, point.Y
    # 寸法を付けるたびに出る「寸法値の入力」の小窓を、終わるまで止める（設定は元に戻す）
    with user_toggle(sw, C.swInputDimValOnCreate, False):
        for along, v in (("h", x), ("v", y)):
            model.ClearSelection2(True)
            point.Select4(False, None)
            me.SelectByID2(oid, "EXTSKETCHPOINT", 0, 0, 0, True, 0, None, 0)
            if abs(v) < 1e-9:
                # X が同じ → 縦に並ぶ（鉛直）、Y が同じ → 横に並ぶ（水平）
                model.SketchAddConstraints("sgVERTICALPOINTS2D" if along == "h" else "sgHORIZONTALPOINTS2D")
            elif along == "h":
                model.AddHorizontalDimension2(x / 2, y + 0.005, 0)
            else:
                model.AddVerticalDimension2(x + 0.005, y / 2, 0)
            model.ClearSelection2(True)


def fully_define(model):
    """編集中のスケッチの残りに寸法と拘束を自動で付けて、完全定義にする。
    戻り値は ISketch.GetConstrainedStatus()（3 = 完全定義）。必ず 3 か確かめる。
    - 原点にある点・原点が中点の補助線（中心矩形）は、ここで原点に留める
    - 大きさ（径・幅・高さ）の寸法はここで付く
    - **原点から離れた点の位置には寸法が付かないことが多い。** 先に dim_from_origin で決めておく"""
    # 基準（datum）に None を渡すと何も付かずに終わる（戻り値も 0 のまま）。原点の点を明示して渡す。
    oid = _origin_id(model)
    me = W(SW.IModelDocExtension, model.Extension)
    model.ClearSelection2(True)
    if not me.SelectByID2(oid, "EXTSKETCHPOINT", 0, 0, 0, False, 0, None, 0):
        raise RuntimeError("原点の点を選べない")
    o = W(SW.ISelectionMgr, model.SelectionManager).GetSelectedObject6(1, -1)
    model.ClearSelection2(True)
    sk = W(SW.ISketchManager, model.SketchManager)
    s = W(SW.ISketch, sk.ActiveSketch)

    # 自動寸法は「原点からの距離が 0」の物に寸法を付けない（原点に置いた円や中心矩形が浮いたまま残る）。
    # 原点にある点は一致、中点が原点にある補助線（中心矩形の対角線）は中点の拘束で、先に原点へ留める。
    at0 = lambda x, y: abs(x) < 1e-9 and abs(y) < 1e-9
    def tie(entity, relation):
        model.ClearSelection2(True)
        entity.Select4(False, None)
        me.SelectByID2(oid, "EXTSKETCHPOINT", 0, 0, 0, True, 0, None, 0)
        model.SketchAddConstraints(relation)
        model.ClearSelection2(True)
    for p in (s.GetSketchPoints2() or []):
        p = W(SW.ISketchPoint, p)
        if at0(p.X, p.Y):
            tie(p, "sgCOINCIDENT")
    for g in (s.GetSketchSegments() or []):
        g = W(SW.ISketchSegment, g)
        if g.ConstructionGeometry and g.GetType() == 0:  # swSketchLINE
            ln = W(SW.ISketchLine, g._oleobj_)
            a = W(SW.ISketchPoint, ln.GetStartPoint2())
            b = W(SW.ISketchPoint, ln.GetEndPoint2())
            if at0((a.X + b.X) / 2, (a.Y + b.Y) / 2):
                tie(g, "sgATMIDDLE")
                break  # 対角線は1本留めれば足りる（2本目は過剰定義になる）
    # AddToDB が True のままだと、これも何も付かずに終わる
    old, sk.AddToDB = sk.AddToDB, False
    try:
        sk.FullyDefineSketch(True, True, 1023, True,
                             C.swAutodimSchemeBaseline, o,
                             C.swAutodimSchemeBaseline, o, 0, 0)
    finally:
        sk.AddToDB = old
    return W(SW.ISketch, sk.ActiveSketch).GetConstrainedStatus()


def last_feature(model):
    return W(SW.IFeature, model.FeatureByPositionReverse(0))


def extrude(model, sketch_feature, depth_mm, midplane=False):
    """ボス押し出し（ブラインド、または中間平面）。"""
    model.ClearSelection2(True)
    sketch_feature.Select2(False, 0)
    fm = W(SW.IFeatureManager, model.FeatureManager)
    t1 = C.swEndCondMidPlane if midplane else C.swEndCondBlind
    f = fm.FeatureExtrusion2(True, False, False, t1, 0, mm(depth_mm), 0,
                             False, False, False, False, 0, 0,
                             False, False, False, False,
                             True, True, True, 0, 0, False)
    if f is None:
        raise RuntimeError("押し出しに失敗")
    return W(SW.IFeature, f)


def cut_through_all(model, sketch_feature):
    """押し出しカットを両方向に全貫通（スケッチ面がどこにあっても抜ける）。"""
    model.ClearSelection2(True)
    sketch_feature.Select2(False, 0)
    fm = W(SW.IFeatureManager, model.FeatureManager)
    f = fm.FeatureCut4(False, False, False, C.swEndCondThroughAll, C.swEndCondThroughAll,
                       0, 0, False, False, False, False, 0, 0,
                       False, False, False, False, False, True, True,
                       False, False, False, 0, 0, False, True)
    if f is None:
        raise RuntimeError("カットに失敗")
    return W(SW.IFeature, f)


def dimensions(feature):
    """フィーチャーの寸法: [(フルネーム, 値mm)]。角度寸法はラジアンのまま。"""
    out = []
    dd = feature.GetFirstDisplayDimension()
    while dd is not None:
        d = W(SW.IDimension, W(SW.IDisplayDimension, dd).GetDimension2(0))
        out.append((d.FullName, round(d.SystemValue * 1000, 4)))
        dd = feature.GetNextDisplayDimension(dd)
    return out


def report(model):
    """体積 mm3 と外形 [xmin,ymin,zmin,xmax,ymax,zmax] mm。
    **どちらも SOLIDWORKS の概算値**（外形は 0.1mm 程度ふくらむことがある）。粗い確認に使い、
    正確な寸法は STEP に書き出して FreeCAD などで測る。"""
    me = W(SW.IModelDocExtension, model.Extension)
    mp = W(SW.IMassProperty, me.CreateMassProperty())
    boxes = [W(SW.IBody2, b).GetBodyBox() for b in
             (W(SW.IPartDoc, model._oleobj_).GetBodies2(0, True) or [])]
    box = [min(bx[i] for bx in boxes) for i in range(3)] + [max(bx[i] for bx in boxes) for i in range(3, 6)]
    return mp.Volume * 1e9, [round(v * 1000, 4) for v in box]


def rebuild_errors(model):
    """作り直してエラーのあるフィーチャー名を返す（空なら健全）。"""
    model.ForceRebuild3(False)
    bad = []
    for f in features(model):
        code, warn = f.GetErrorCode2()
        if code != 0:
            bad.append((f.Name, code, warn))
    return bad


def save(model, path):
    """拡張子で形式が決まる（.SLDPRT / .step / .stl など）。黙って上書きする。"""
    err = model.SaveAs3(os.path.abspath(path), 0, C.swSaveAsOptions_Silent)
    if err != 0 or not os.path.exists(path):
        raise RuntimeError(f"保存に失敗 err={err}: {path}")
    return path
