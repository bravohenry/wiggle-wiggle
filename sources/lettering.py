# /// script
# requires-python = ">=3.11"
# dependencies = ["fonttools>=4.50", "skia-pathops>=0.8"]
# ///
"""
[INPUT]: 依赖 build.py 的 shape/glyph_layout/REGULAR（与 Regular 字体同一份排字规则、骨架、种子、位移与笔）；依赖 pen.py 的 sweep_strokes 与 _fit
[OUTPUT]: letter（逐笔墨迹、笔尖轨迹与骨架点）、schedule（逐笔起止与进度关键帧）、open_path；命令行写书写数据 TS 模块
[POS]: scripts/scrawl 的书写动画导出器。字体与手写字标共用骨架；vault/notes-lettering/strokes.ts 由它生成，specimen.py 的书写样张也用它。
[PROTOCOL]: 变更时更新此头部，然后检查 AGENTS.md

    uv run --system-certs --no-project scripts/scrawl/lettering.py Notes src/vault/notes-lettering/strokes.ts

骨架本来就记着每一笔的中心线与书写顺序，所以字标的书写动画不必再手描：
每笔单独扫出墨迹轮廓，遮罩笔沿笔尖实际走过的轨迹（含手抖与回钩）揭开它；全部笔画并起来正是字体里的字形。
节奏按人手运笔推算：落笔收笔慢、中段快、拐角减速；同字内抬笔短、换字抬笔长；总长归一到共用书写时钟。
"""
import math
import sys
from pathlib import Path

import build as B
from pen import _fit, sweep_strokes

SCALE = 0.25          # 字体单位 → viewBox 单位；Notes 落在约 200 高，与旧字标的坐标量级一致
PAD = 6               # viewBox 四周留白（viewBox 单位），容纳遮罩笔的圆头
PEN_COVER = 1.3       # 遮罩笔半宽 = 最大墨迹半径 × 此倍数：盖住端头墨团、回钩与墨宽起伏
CLOCK_MS = 1175       # 书写总时长；甲板卡与 /notes 题头共用这一拍
LIFT_IN_GLYPH = 18    # 同一个字内两笔之间抬笔（ms，绝对时长，不参与归一）
LIFT_BETWEEN = 32     # 换字抬笔（ms）
LIFT_RETURN = 70      # 写完整词后回笔补横、补点前的停顿（ms）
# 连写时整词写完才回头补的笔：t 与 f 的横、i 与 j 的点（字形笔画下标）。原 Notes 字标就是最后补 t 横
LATE = {"t": {1}, "f": {1}, "i": {1}, "j": {1}}
KEYFRAMES = 7         # 每笔进度关键帧数（含首尾）


def _dist(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _unit(v):
    d = math.hypot(*v) or 1.0
    return (v[0] / d, v[1] / d)


def _rhythm(trace):
    """沿轨迹推时间：速度 = 起落渐变 × 拐角减速。返回总时长（任意单位）与 KEYFRAMES 个 [时间比, 弧长比]。"""
    seg = [_dist(trace[i], trace[i + 1]) for i in range(len(trace) - 1)]
    total = sum(seg) or 1.0
    s, t, samples = 0.0, 0.0, [(0.0, 0.0)]
    for i, ds in enumerate(seg):
        a = trace[max(0, i - 3)]
        b = trace[i]
        c = trace[min(len(trace) - 1, i + 4)]
        v1, v2 = _unit((b[0] - a[0], b[1] - a[1])), _unit((c[0] - b[0], c[1] - b[1]))
        turn = math.acos(max(-1.0, min(1.0, v1[0] * v2[0] + v1[1] * v2[1])))
        ramp = 0.3 + 0.7 * min(1.0, s / 70) * min(1.0, (total - s) / 70)
        speed = ramp / (1 + 1.6 * turn)
        t += ds / max(speed, 0.05)
        s += ds
        samples.append((t, s))
    out = []
    for k in range(KEYFRAMES):
        target = t * k / (KEYFRAMES - 1)
        j = next(i for i, (tt, _) in enumerate(samples) if tt >= target - 1e-9)
        if j == 0:
            out.append([0.0, 0.0])
            continue
        (t0, s0), (t1, s1) = samples[j - 1], samples[j]
        f = 0.0 if t1 == t0 else (target - t0) / (t1 - t0)
        out.append([round(k / (KEYFRAMES - 1), 3), round((s0 + (s1 - s0) * f) / total, 3)])
    out[-1] = [1, 1]
    return t, out


def open_path(points, tol):
    """笔尖轨迹 → 三次贝塞尔开放路径。"""
    pts = [points[0]]
    for p in points[1:]:
        if _dist(pts[-1], p) > 0.6:
            pts.append(p)
    if len(pts) < 2:
        pts = [points[0], (points[0][0] + 0.5, points[0][1] + 0.5)]
    k = min(3, len(pts) - 1)
    t1 = _unit((pts[k][0] - pts[0][0], pts[k][1] - pts[0][1]))
    t2 = _unit((pts[-1 - k][0] - pts[-1][0], pts[-1 - k][1] - pts[-1][1]))
    return _fit(pts, t1, t2, tol)


def letter(word):
    """排字（与字体 calt 同规则）并逐笔导出。坐标为字体单位，y 向上。"""
    strokes, pen_x = [], 0
    for gi, (ch, alt) in enumerate(B.shape(word)):
        if alt is None:
            pen_x += B.SPACE
            continue
        g = B.glyph_layout(ch, alt)
        shift = pen_x + g["dx"]
        parts = sweep_strokes(g["strokes"], B.REGULAR.nib, g["seed"])
        for si, (bone, part) in enumerate(zip(g["strokes"], parts, strict=True)):
            strokes.append({
                "glyph": gi,
                "late": si in LATE.get(ch, ()),
                "contours": [[tuple((p[0] + shift, p[1]) for p in seg) for seg in c] for c in part["contours"]],
                "trace": [(p[0] + shift, p[1]) for p in part["trace"]],
                "points": [(p[0] + shift, p[1]) for p in B._points(bone)],
                "radius": part["radius"],
            })
        pen_x += g["width"]
    # 稳定排序：正常笔画保持书写顺序，回笔补的横与点挪到整词之后
    return [s for s in strokes if not s["late"]] + [s for s in strokes if s["late"]]


def schedule(strokes):
    """逐笔节奏 [(起笔 ms, 运笔 ms, 进度关键帧)]。抬笔是绝对毫秒；运笔时长按运笔模型求比例，填满 CLOCK_MS 减去抬笔后的时间。"""
    lifts = [0] + [LIFT_RETURN if s["late"] and not strokes[i - 1]["late"]
                   else LIFT_IN_GLYPH if s["glyph"] == strokes[i - 1]["glyph"] else LIFT_BETWEEN
                   for i, s in enumerate(strokes) if i]
    rhythms = [_rhythm(s["trace"]) for s in strokes]
    norm = (CLOCK_MS - sum(lifts)) / sum(r[0] for r in rhythms)
    out, cursor = [], 0.0
    for lift, (duration, progress) in zip(lifts, rhythms):
        cursor += lift
        out.append((cursor, duration * norm, progress))
        cursor += duration * norm
    return out


def to_ts(word, strokes):
    xs = [p[0] for s in strokes for c in s["contours"] for seg in c for p in seg]
    ys = [p[1] for s in strokes for c in s["contours"] for seg in c for p in seg]
    x0, y1 = min(xs), max(ys)
    X = lambda x: round((x - x0) * SCALE + PAD, 1)
    Y = lambda y: round((y1 - y) * SCALE + PAD, 1)
    width = round((max(xs) - x0) * SCALE + 2 * PAD)
    height = round((y1 - min(ys)) * SCALE + 2 * PAD)
    pen = round(2 * PEN_COVER * max(s["radius"] for s in strokes) * SCALE, 1)

    rows = []
    for s, (start, duration, progress) in zip(strokes, schedule(strokes)):
        d = ""
        for j, seg in enumerate(open_path(s["trace"], 1.0)):
            if j == 0:
                d += f"M{X(seg[0][0])} {Y(seg[0][1])}"
            d += "C" + " ".join(f"{X(p[0])} {Y(p[1])}" for p in seg[1:])
        outline = ""
        for c in s["contours"]:
            outline += f"M{X(c[0][0][0])} {Y(c[0][0][1])}"
            for seg in c:
                outline += "C" + " ".join(f"{X(p[0])} {Y(p[1])}" for p in seg[1:])
            outline += "Z"
        rows.append(f'  {{ // {word[s["glyph"]]}\n'
                    f'    d: "{d}",\n'
                    f'    outline: "{outline}",\n'
                    f'    delay: {round(start)}, duration: {round(duration)},\n'
                    f'    progress: {progress},\n'
                    f'  }},')
    return f'''/**
 * [INPUT]: 由 scripts/scrawl/lettering.py 从 Z1 Scrawl 骨架生成（pnpm font:scrawl 连同字体一起重建），勿手改
 * [OUTPUT]: LETTERING（viewBox 宽高与遮罩笔宽）、STROKES（{word} 每笔的笔迹 d、墨迹 outline 与书写节奏）
 * [POS]: vault/notes-lettering 的书写数据层。NotesLettering 只负责播放；字形与笔顺的真相源是字体骨架。
 * [PROTOCOL]: 变更时更新此头部，然后检查 AGENTS.md
 */
import type {{ InkStroke }} from "./NotesLettering";

export const LETTERING = {{ width: {width}, height: {height}, pen: {pen} }} as const;

export const STROKES: InkStroke[] = [
{chr(10).join(rows)}
];
'''


def main():
    word, out = sys.argv[1], Path(sys.argv[2])
    out.write_text(to_ts(word, letter(word)))
    print(f"  lettering {word!r} → {out}")


if __name__ == "__main__":
    main()
