# /// script
# requires-python = ">=3.11"
# dependencies = ["fonttools>=4.50", "skia-pathops>=0.8", "brotli>=1.1"]
# ///
"""
[INPUT]: 依赖 glyphs.py 的 GLYPHS/POSE/COUNTERS/SPACING/METRICS；依赖 pen.py 的 Nib 与 sweep_glyph；
         依赖 fontTools 的 FontBuilder、T2CharStringPen、feaLib；--specimen 时依赖 specimen.py
[OUTPUT]: --out 目录下的 Z1Scrawl-{Light,Regular,Bold}.woff2（calt 轮换 + ss01/ss02 沸腾帧，name 表内嵌 OFL-1.1）；
         --weight 只建某档；--otf 另存 OTF；--specimen 写 README 样张 SVG；export 供 release.py 复用同一条落盘流程；
         glyph_layout 与 shape 供 lettering.py、specimen.py 取同坐标的骨架与同规则的排字
[POS]: scripts/scrawl 的入口与装配器。骨架与笔模型各在一处，它只负责「几支笔、几份异体、怎么排、怎么装」。
       本文件原样镜像到开源仓库 bravohenry/z1-scrawl 的 sources/，所以不写死 z1 的目录。
[PROTOCOL]: 变更时更新此头部，然后检查 AGENTS.md

    uv run scripts/scrawl/build.py --out public/fonts [--otf DIR] [--specimen DIR]

手写字体最大的破绽是同一字母重复两次长得一模一样。
每个字形生成 ALTS 份种子异体，calt 按「前一个字是第几份」轮换，ll、ee、oo 永远不撞脸。
ss01/ss02 是线条沸腾的第 2、3 帧：lookup 排在 calt 之后，把整行每个字平移到下一份/再下一份异体。
网页按 12 帧/秒在三帧间切换，字就像《平行宇宙》的手绘线一样逐帧沸腾。
种子由字名派生：同样的骨架永远构建出逐字节相同的字体。
"""
import argparse
import math
import random
import zlib
from pathlib import Path

from fontTools.agl import UV2AGL
from fontTools.fontBuilder import FontBuilder
from fontTools.misc.timeTools import timestampFromString
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.t2CharStringPen import T2CharStringPen

import glyphs as G
from pen import Nib, sweep_glyph

# ── 字体身份与参数：改风格只改这里 ─────────────────────────────
FAMILY = "Z1 Scrawl"
VERSION = "1.000"
STAMP = "Fri Nov  6 00:00:00 2026"   # head 表时间戳钉死：同一骨架必须构建出逐字节相同的字体
UPM = 1000
ALTS = 3                    # 每字异体份数；calt 轮换周期


class Weight:
    """一个字重 = 同一份骨架换一支笔。笔一粗，窄字的内白被吃掉，所以骨架按 widen 横向放宽把内白撑回来。"""

    def __init__(self, style, wght, radius, widen):
        self.style, self.wght, self.radius, self.widen = style, wght, radius, widen
        self.nib = Nib(radius=radius)
        self.lift = radius          # 中心线整体上提一个笔半径，墨底落在基线
        self.ps_name = f"Z1Scrawl-{style}"


# Regular 的笔宽 ≈ 76 ≈ 大写墨高的 0.10（参考图 13 字实测均值 0.103）。Light 是细马克笔，Bold 是粗 Sharpie。
WEIGHTS = {w.style: w for w in (
    Weight("Light", 300, 26, 0.96),
    Weight("Regular", 400, 38, 1.0),
    Weight("Bold", 700, 54, 1.14),
)}
REGULAR = WEIGHTS["Regular"]
# 异体幅度：整字旋转(度)、横纵缩放(相对)、基线跳动(单位)、控制点抖动(单位)、侧距抖动(单位)。
# alt0 乘 base，其余满幅。单字的固定脾气在 glyphs.POSE，这里只管「同一个字每次写都不一样」
WOBBLE = {"rot": 5.0, "sx": 0.07, "sy": 0.08, "dy": 30.0, "jit": 16.0, "sb": 18.0, "base": 0.8}
SIDEBEARING = (30, 30)
SPACE = 260
REPO = "https://github.com/bravohenry/z1-scrawl"
OFL = "https://openfontlicense.org"


def glyph_name(ch):
    return UV2AGL.get(ord(ch), f"uni{ord(ch):04X}")


# ════════════════════════════════════════════════════════════
#  异体：整字旋转、缩放、基线跳动，控制点逐个抖开
# ════════════════════════════════════════════════════════════

def _points(stroke):
    return stroke["p"] if isinstance(stroke, dict) else stroke


def pose(strokes, ch):
    """套用 glyphs.POSE：绕字底中点倾斜，从基线纵向缩放，再整体升降。"""
    lean, sy, dy = G.POSE.get(ch, (0, 1.0, 0))
    if (lean, sy, dy) == (0, 1.0, 0):
        return strokes
    pts = [p for s in strokes for p in _points(s)]
    cx = (min(p[0] for p in pts) + max(p[0] for p in pts)) / 2
    th = math.radians(-lean)
    cos, sin = math.cos(th), math.sin(th)

    def move(p):
        x, y = p[0] - cx, p[1] * sy
        return (x * cos - y * sin + cx, x * sin + y * cos + dy) + tuple(p[2:])

    return [{**s, "p": [move(p) for p in s["p"]]} if isinstance(s, dict) else [move(p) for p in s] for s in strokes]


def widen(strokes, k):
    """骨架绕自身横向中点放宽 k 倍。k=1 原样返回，Regular 因此逐字节不变。"""
    if k == 1.0:
        return strokes
    pts = [p for s in strokes for p in _points(s)]
    cx = (min(p[0] for p in pts) + max(p[0] for p in pts)) / 2
    move = lambda p: ((p[0] - cx) * k + cx, p[1]) + tuple(p[2:])
    return [{**s, "p": [move(p) for p in s["p"]]} if isinstance(s, dict) else [move(p) for p in s] for s in strokes]


def variant(strokes, name, alt, lift):
    rnd = random.Random(zlib.crc32(f"{name}#{alt}".encode()))
    pts = [p for s in strokes for p in _points(s)]
    cx = (min(p[0] for p in pts) + max(p[0] for p in pts)) / 2
    cy = (min(p[1] for p in pts) + max(p[1] for p in pts)) / 2
    w = WOBBLE
    amp = w["base"] if alt == 0 else 1.0
    rot = math.radians(rnd.uniform(-w["rot"], w["rot"]) * amp)
    sx, sy = 1 + rnd.uniform(-w["sx"], w["sx"]) * amp, 1 + rnd.uniform(-w["sy"], w["sy"]) * amp
    dy = rnd.uniform(-w["dy"], w["dy"]) * amp
    jit = w["jit"] * amp
    cos, sin = math.cos(rot), math.sin(rot)

    def place(p, ox, oy):
        x, y = (p[0] - cx) * sx + ox, (p[1] - cy) * sy + oy
        return (x * cos - y * sin + cx, x * sin + y * cos + cy + dy + lift) + tuple(p[2:])

    out = []
    for s in strokes:
        if isinstance(s, dict) and s.get("dot"):
            ox, oy = rnd.uniform(-jit, jit), rnd.uniform(-jit, jit)
            out.append({**s, "p": [place(p, ox, oy) for p in s["p"]]})
            continue
        moved = [place(p, rnd.uniform(-jit, jit), rnd.uniform(-jit, jit)) for p in _points(s)]
        out.append({**s, "p": moved} if isinstance(s, dict) else moved)
    return out, zlib.crc32(f"{name}~{alt}".encode()) & 0xFFFF


def _bounds(contours):
    xs = [p[0] for c in contours for seg in c for p in seg]
    ys = [p[1] for c in contours for seg in c for p in seg]
    return min(xs), min(ys), max(xs), max(ys)


def glyph_layout(ch, alt, weight=REGULAR):
    """一份异体的全部中间量：落墨前的骨架、种子、水平位移 dx 与字宽。字体与书写动画共用，坐标一致。"""
    # 加粗时并排几道内白就放宽几倍：m 的两道缝各被粗笔吃掉一圈，只按一倍放宽会糊成三根柱子。
    # 细笔不翻倍：细笔本就留白多，再按内白数收窄反而把 m 挤成梳子
    k = 1 + (weight.widen - 1) * (G.COUNTERS.get(ch, 1) if weight.widen > 1 else 1)
    strokes, seed = variant(widen(pose(G.GLYPHS[ch], ch), k), glyph_name(ch), alt, weight.lift)
    contours = sweep_glyph(strokes, weight.nib, seed)
    x0, _, x1, _ = _bounds(contours)
    lsb, rsb = G.SPACING.get(ch, SIDEBEARING)
    # 字距忽松忽紧：参考图字间空隙从 0.02 到 0.21 大写高不等
    rs = random.Random(seed * 31 + 7)
    lsb, rsb = lsb + rs.uniform(-WOBBLE["sb"], WOBBLE["sb"]), rsb + rs.uniform(-WOBBLE["sb"], WOBBLE["sb"])
    return {"strokes": strokes, "seed": seed, "contours": contours, "dx": lsb - x0,
            "width": round(x1 - x0 + lsb + rsb)}


def build_glyph(ch, alt, weight=REGULAR):
    g = glyph_layout(ch, alt, weight)
    contours = [[tuple((p[0] + g["dx"], p[1]) for p in seg) for seg in c] for c in g["contours"]]
    return contours, g["width"]


# ════════════════════════════════════════════════════════════
#  装配
# ════════════════════════════════════════════════════════════

def _charstring(contours, width):
    pen = T2CharStringPen(width, None)
    for c in contours:
        pen.moveTo((round(c[0][0][0]), round(c[0][0][1])))
        for seg in c:
            pen.curveTo(*[(round(p[0]), round(p[1])) for p in seg[1:]])
        pen.closePath()
    return pen.getCharString()


def _notdef():
    pen = T2CharStringPen(500, None)
    for box in ([(60, 0), (440, 0), (440, 700), (60, 700)], [(110, 50), (110, 650), (390, 650), (390, 50)]):
        pen.moveTo(box[0])
        for p in box[1:]:
            pen.lineTo(p)
        pen.closePath()
    return pen.getCharString()


def _features(bases):
    alt = lambda k: " ".join(f"{b}.alt{k}" for b in bases)
    return f"""
languagesystem DFLT dflt;
languagesystem latn dflt;
@b = [{" ".join(bases)}];
@a1 = [{alt(1)}];
@a2 = [{alt(2)}];
lookup cycle {{
  sub @a1 @b' by @a2;
  sub @b @b' by @a1;
  sub @a1 space @b' by @a2;
  sub @b space @b' by @a1;
}} cycle;
feature calt {{ lookup cycle; }} calt;
lookup boil1 {{
  sub @b by @a1;
  sub @a1 by @a2;
  sub @a2 by @b;
}} boil1;
lookup boil2 {{
  sub @b by @a2;
  sub @a1 by @b;
  sub @a2 by @a1;
}} boil2;
feature ss01 {{ featureNames {{ name "Boil frame 2"; }}; lookup boil1; }} ss01;
feature ss02 {{ featureNames {{ name "Boil frame 3"; }}; lookup boil2; }} ss02;
"""


def shape(text, frame=0):
    """_features 里 calt 与 ss01/ss02 的 Python 孪生：返回 [(字, 第几份异体)]，空格与字库外的字记 None。
    前一个字（或隔一个空格的前一个字）是 alt0 → 本字 alt1；是 alt1 → alt2；是 alt2 → 回到 alt0。
    frame 是沸腾帧：ss01/ss02 在 calt 之后把每个字再平移 1/2 份。改 cycle 或 boil 必须同改这里。"""
    out, prev, spaces = [], None, 0
    for ch in text:
        if ch in (" ", "\u00a0"):
            out.append((ch, None))
            spaces += 1
            continue
        if ch not in G.GLYPHS:
            out.append((ch, None))
            prev, spaces = None, 0
            continue
        k = (prev + 1) % ALTS if prev is not None and spaces <= 1 else 0
        out.append((ch, (k + frame) % ALTS))
        prev, spaces = k, 0
    return out


def _names(weight):
    """Regular/Bold 进四样式组（RIBBI）；其余字重按惯例另起旧式家族名，并用 16/17 号名声明真家族。"""
    names = {
        "copyright": f"Copyright 2026 Zihan Huang ({REPO})",
        "familyName": FAMILY,
        "styleName": weight.style,
        "uniqueFontIdentifier": f"{VERSION};Z1;{weight.ps_name}",
        "fullName": f"{FAMILY} {weight.style}",
        "version": f"Version {VERSION}",
        "psName": weight.ps_name,
        "designer": "Zihan Huang",
        "vendorURL": REPO,
        "designerURL": "https://z1han.com",
        "licenseDescription": "This Font Software is licensed under the SIL Open Font License, Version 1.1. "
                              f"This license is available with a FAQ at: {OFL}",
        "licenseInfoURL": OFL,
    }
    if weight.style not in ("Regular", "Bold"):
        names.update(familyName=f"{FAMILY} {weight.style}", styleName="Regular",
                     typographicFamily=FAMILY, typographicSubfamily=weight.style)
    return names


def build(weight=REGULAR):
    chars = list(G.GLYPHS)
    outlines = {}
    for ch in chars:
        for alt in range(ALTS):
            name = glyph_name(ch) + (f".alt{alt}" if alt else "")
            outlines[name] = build_glyph(ch, alt, weight)
    order = [".notdef", "space"] + list(outlines)
    cmap = {32: "space", 0xA0: "space", **{ord(ch): glyph_name(ch) for ch in chars}}

    fb = FontBuilder(UPM, isTTF=False)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap(cmap)
    charstrings = {".notdef": _notdef(), "space": T2CharStringPen(SPACE, None).getCharString()}
    widths = {".notdef": 500, "space": SPACE}
    for name, (contours, width) in outlines.items():
        charstrings[name] = _charstring(contours, width)
        widths[name] = width
    fb.setupCFF(weight.ps_name, {"FullName": f"{FAMILY} {weight.style}", "FamilyName": FAMILY, "Weight": weight.style},
                charstrings, {})
    metrics, y_min, y_max = {}, 0, 0
    for name in order:
        bp = BoundsPen(None)
        for c in outlines.get(name, ([], 0))[0]:
            bp.moveTo((round(c[0][0][0]), round(c[0][0][1])))
            for seg in c:
                bp.curveTo(*[(round(p[0]), round(p[1])) for p in seg[1:]])
            bp.closePath()
        lsb = round(bp.bounds[0]) if bp.bounds else 0
        if bp.bounds:
            y_min, y_max = min(y_min, bp.bounds[1]), max(y_max, bp.bounds[3])
        metrics[name] = (widths[name], lsb)
    fb.setupHorizontalMetrics(metrics)
    asc, desc = 900, -300
    fb.setupHorizontalHeader(ascent=asc, descent=desc)
    fb.setupNameTable(_names(weight))
    fb.setupOS2(
        sTypoAscender=asc, sTypoDescender=desc, sTypoLineGap=0,
        usWinAscent=max(asc, math.ceil(y_max)), usWinDescent=max(-desc, math.ceil(-y_min)),
        sxHeight=G.METRICS["xheight"] + weight.lift + weight.radius, sCapHeight=G.METRICS["cap"] + weight.lift + weight.radius,
        achVendID="Z1  ", fsType=0, version=4, usWeightClass=weight.wght,
        # USE_TYPO_METRICS 加 BOLD 或 REGULAR；Light 的字重写在 16/17 号名里，样式位按 Regular 记
        fsSelection=(0x20 if weight.style == "Bold" else 0x40) | 0x80,
    )
    if weight.style == "Bold":
        fb.font["head"].macStyle = 1
    fb.setupPost()
    head = fb.font["head"]
    head.created = head.modified = timestampFromString(STAMP)
    fb.font.recalcTimestamp = False
    fb.addOpenTypeFeatures(_features([glyph_name(ch) for ch in chars]))
    return fb.font, outlines, widths


def export(out, otf=None, specimen_dir=None, styles=None):
    """建字重并落盘：WOFF2 进 out，OTF 进 otf，README 样张进 specimen_dir。release.py 与命令行共用。"""
    built = {}
    for style in styles or list(WEIGHTS):
        weight = WEIGHTS[style]
        font, outlines, widths = build(weight)
        built[style] = (outlines, widths)
        if otf:
            otf.mkdir(parents=True, exist_ok=True)
            font.save(otf / f"{weight.ps_name}.otf")
        font.flavor = "woff2"
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{weight.ps_name}.woff2"
        font.save(path)
        print(f"  woff2 {path.stat().st_size / 1024:.1f} KB · {len(outlines)} glyphs → {path}")
    if specimen_dir:
        import specimen
        specimen.write_all(built, specimen_dir)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True, help="WOFF2 输出目录")
    ap.add_argument("--weight", choices=list(WEIGHTS), action="append", help="只建指定字重；缺省三档全建")
    ap.add_argument("--otf", type=Path, help="另存桌面安装版 OTF 的目录")
    ap.add_argument("--specimen", type=Path, help="写 README 样张 SVG 的目录；需三档全建")
    args = ap.parse_args()
    export(args.out, args.otf, args.specimen, args.weight)


if __name__ == "__main__":
    main()
