"""
[INPUT]: 依赖 skia-pathops 的 Path.simplify（非零环绕并集）；骨架点来自 glyphs.py
[OUTPUT]: 对外提供 Nib 笔参数、sweep_glyph（骨架 → 闭合三次贝塞尔轮廓）、sweep_strokes（逐笔轮廓与笔尖轨迹）、noise/fbm 噪声原语
[POS]: scripts/scrawl 的马克笔笔模型。build.py 调它出每个字形与异体；它不认识字体格式。
[PROTOCOL]: 变更时更新此头部，然后检查 AGENTS.md

笔模型 = 圆头毡尖沿中心线扫掠：
  中心线（Catmull-Rom 过控制点，末端可加回钩）→ 二维抖动位移 → 骨头形宽度（端头鼓、中段收）
  → 每段取凸包 + 端点/转角圆
  → pathops 并集 → Schneider 拟合三次曲线。
凸包而非偏移线：内侧急弯的偏移线会自交出燕尾，环绕数不可控；
半径 r 内的点集的凸包必落在扫掠区内，并集永远正确。
"""
import math
import random

import pathops

# ════════════════════════════════════════════════════════════
#  噪声：确定性一维值噪声，种子相同则字形逐字节相同
# ════════════════════════════════════════════════════════════

def _hash(seed, i):
    h = (i * 374761393 + seed * 668265263 + 0x9E3779B9) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    h ^= h >> 16
    return (h / 0xFFFFFFFF) * 2 - 1


def noise(seed, x):
    i = math.floor(x)
    f = x - i
    u = f * f * (3 - 2 * f)
    return _hash(seed, i) * (1 - u) + _hash(seed, i + 1) * u


def fbm(seed, x, octaves=3):
    total, amp, freq, norm = 0.0, 1.0, 1.0, 0.0
    for o in range(octaves):
        total += amp * noise(seed + o * 1013, x * freq)
        norm += amp
        amp *= 0.5
        freq *= 2.0
    return total / norm


def _smooth(e0, e1, x):
    t = min(1.0, max(0.0, (x - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


# ════════════════════════════════════════════════════════════
#  中心线：向心 Catmull-Rom，转角点切断成独立段
# ════════════════════════════════════════════════════════════

def _dist(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _catmull(points, step):
    """过所有控制点的稠密折线；首尾用镜像点补齐。"""
    if len(points) == 1:
        return [points[0]]
    p = [(2 * points[0][0] - points[1][0], 2 * points[0][1] - points[1][1])]
    p += points
    p.append((2 * points[-1][0] - points[-2][0], 2 * points[-1][1] - points[-2][1]))
    out = [points[0]]
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
        t0 = 0.0
        t1 = t0 + max(_dist(p0, p1), 1e-3) ** 0.5
        t2 = t1 + max(_dist(p1, p2), 1e-3) ** 0.5
        t3 = t2 + max(_dist(p2, p3), 1e-3) ** 0.5
        n = max(2, math.ceil(_dist(p1, p2) / step))
        for k in range(1, n + 1):
            t = t1 + (t2 - t1) * k / n

            def lerp(a, b, ta, tb):
                wa, wb = (tb - t) / (tb - ta), (t - ta) / (tb - ta)
                return (a[0] * wa + b[0] * wb, a[1] * wa + b[1] * wb)

            a1 = lerp(p0, p1, t0, t1)
            a2 = lerp(p1, p2, t1, t2)
            a3 = lerp(p2, p3, t2, t3)
            b1 = lerp(a1, a2, t0, t2)
            b2 = lerp(a2, a3, t1, t3)
            out.append(lerp(b1, b2, t1, t2))
    return out


def _runs(stroke):
    """按转角点切段；相邻两段共享转角点。"""
    runs, cur = [], []
    for pt in stroke:
        cur.append((pt[0], pt[1]))
        if len(pt) > 2 and pt[2] and len(cur) > 1:
            runs.append(cur)
            cur = [(pt[0], pt[1])]
    if len(cur) > 1 or not runs:
        runs.append(cur)
    return runs


# ════════════════════════════════════════════════════════════
#  扫掠：凸包段 + 圆，交给 pathops 求并
# ════════════════════════════════════════════════════════════

def _circle(c, r, n=28):
    return [(c[0] + r * math.cos(2 * math.pi * k / n), c[1] + r * math.sin(2 * math.pi * k / n)) for k in range(n)]


def _hull(pts):
    pts = sorted(set(pts))
    if len(pts) < 3:
        return []

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    hull = lower[:-1] + upper[:-1]
    return hull if len(hull) >= 3 else []


def _normals(pts):
    out = []
    for i in range(len(pts)):
        a = pts[max(0, i - 1)]
        b = pts[min(len(pts) - 1, i + 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        d = math.hypot(dx, dy) or 1.0
        out.append((-dy / d, dx / d))
    return out


class Nib:
    """毡尖参数。radius 是中心线到墨边的距离；其余是相对量。

    马克笔的端头不会变尖：落笔与停笔时笔尖静止，墨向四周洇开，端头反而鼓成墨团。
    行笔快的中段才略细。所以宽度曲线是「两头鼓、中间收」的骨头形。"""

    def __init__(self, radius, step=5.0, tremor=(1.6, 60.0, 18.0, 300.0), swell=(0.10, 80.0),
                 knob=(0.11, 0.09, 55.0), thin=(0.07, (200.0, 600.0)), weight=(0.78, 1.12),
                 hook=(0.35, 16.0, 70.0)):
        self.radius = radius
        self.step = step
        self.tremor = tremor   # 手抖：高频幅度, 高频波长, 低频幅度, 低频波长（单位都是字体单位）
        self.swell = swell     # 墨宽起伏：相对幅度, 波长。波长要长——短波长读出来是砂纸边，不是马克笔
        self.knob = knob       # 端头墨团：起笔增量, 停笔增量, 衰减长度。衰减要长于笔半径，否则读成火柴头
        self.thin = thin       # 行笔变细：中段最大减量, 笔画长度区间（短笔画来不及加速，不变细）
        self.weight = weight   # 每笔粗细区间：同一个字里各笔压力不同，竖画常比碗形细
        self.hook = hook       # 停笔回钩：出现概率, 钩长, 最大偏转角（度）


def _add_hook(stroke, nib, rnd):
    """停笔前手腕带出的小回钩：沿末段方向再走一小截并拐弯，墨团因此偏向一侧。"""
    prob, length, turn = nib.hook
    if len(stroke) < 2 or rnd.random() > prob:
        return stroke
    a, b = stroke[-2], stroke[-1]
    d = _unit((b[0] - a[0], b[1] - a[1]))
    ang = math.radians(rnd.uniform(0.5, 1.0) * turn * rnd.choice((-1, 1)))
    t = (d[0] * math.cos(ang) - d[1] * math.sin(ang), d[0] * math.sin(ang) + d[1] * math.cos(ang))
    mid = (b[0] + d[0] * length * 0.45, b[1] + d[1] * length * 0.45)
    end = (mid[0] + t[0] * length * 0.7, mid[1] + t[1] * length * 0.7)
    return list(stroke) + [mid, end]


def _stroke_polys(stroke, nib, seed, weight):
    runs = [_catmull(r, 3.0) for r in _runs(stroke)]
    total = sum(sum(_dist(r[i], r[i + 1]) for i in range(len(r) - 1)) for r in runs)
    total = max(total, 1.0)

    def displace(p, s):
        a1, l1, a2, l2 = nib.tremor
        return (p[0] + a1 * fbm(seed + 11, s / l1) + a2 * fbm(seed + 23, s / l2),
                p[1] + a1 * fbm(seed + 37, s / l1) + a2 * fbm(seed + 41, s / l2))

    thin = nib.thin[0] * _smooth(nib.thin[1][0], nib.thin[1][1], total)

    def radius(s, side):
        r = nib.radius * weight
        r *= 1 + nib.knob[0] * math.exp(-s / nib.knob[2]) + nib.knob[1] * math.exp(-(total - s) / nib.knob[2])
        r *= 1 - thin * math.sin(math.pi * min(1.0, max(0.0, s / total)))
        # 两侧墨边各自起伏，宽度变化不对称，读起来才像纸面吃墨不均
        r *= 1 + nib.swell[0] * fbm(seed + 53 + side * 7, s / nib.swell[1], 2)
        return r

    polys, s0 = [], 0.0
    ends, trace, rmax = [], [], 0.0
    for run in runs:
        # 按弧长重采样，噪声参数 s 跨段连续
        res, acc, s_list = [run[0]], 0.0, [s0]
        seg_s = s0
        for i in range(len(run) - 1):
            d = _dist(run[i], run[i + 1])
            acc += d
            if acc >= nib.step or i == len(run) - 2:
                res.append(run[i + 1])
                s_list.append(seg_s + d)
                acc = 0.0
            seg_s += d
        s_end = seg_s
        pts = [displace(p, s) for p, s in zip(res, s_list)]
        if len(pts) == 1:
            pts = [pts[0], (pts[0][0] + 0.5, pts[0][1] + 0.5)]
            s_list = [s_list[0], s_list[0] + 0.5]
        norms = _normals(pts)
        left = [(p[0] + n[0] * radius(s, 0), p[1] + n[1] * radius(s, 0)) for p, n, s in zip(pts, norms, s_list)]
        right = [(p[0] - n[0] * radius(s, 1), p[1] - n[1] * radius(s, 1)) for p, n, s in zip(pts, norms, s_list)]
        for i in range(len(pts) - 1):
            h = _hull([left[i], left[i + 1], right[i + 1], right[i]])
            if h:
                polys.append(h)
        trace += pts if not trace else pts[1:]
        rmax = max([rmax] + [max(radius(s, 0), radius(s, 1)) for s in s_list])
        ends.append((pts[0], s_list[0]))
        ends.append((pts[-1], s_end))
        s0 = s_end
    # 起笔、收笔与每个转角各补一个圆：凸包段在这些点不连续
    for p, s in ends:
        polys.append(_circle(p, (radius(s, 0) + radius(s, 1)) / 2))
    # trace 是笔尖实际走过的折线（已含手抖与回钩），书写动画的遮罩笔沿它走
    return polys, trace, rmax


def _union(polys):
    path = pathops.Path()
    for poly in polys:
        path.moveTo(*poly[0])
        for p in poly[1:]:
            path.lineTo(*p)
        path.close()
    path.simplify(fix_winding=True, keep_starting_points=False)
    contours, cur = [], []
    for verb, pts in path.segments:
        if verb == "moveTo":
            cur = [pts[0]]
        elif verb == "lineTo":
            cur.append(pts[0])
        elif verb in ("qCurveTo", "curveTo"):
            cur.extend(pts)
        elif verb in ("closePath", "endPath"):
            if len(cur) >= 3:
                contours.append(cur)
            cur = []
    return contours


# ════════════════════════════════════════════════════════════
#  Schneider 曲线拟合：并集折线 → 三次贝塞尔，保留真实转角
# ════════════════════════════════════════════════════════════

def _sub(a, b): return (a[0] - b[0], a[1] - b[1])
def _add(a, b): return (a[0] + b[0], a[1] + b[1])
def _mul(a, k): return (a[0] * k, a[1] * k)
def _dot(a, b): return a[0] * b[0] + a[1] * b[1]


def _unit(a):
    d = math.hypot(*a)
    return (a[0] / d, a[1] / d) if d > 1e-9 else (0.0, 0.0)


def _bez(b, t):
    mt = 1 - t
    return (mt ** 3 * b[0][0] + 3 * mt * mt * t * b[1][0] + 3 * mt * t * t * b[2][0] + t ** 3 * b[3][0],
            mt ** 3 * b[0][1] + 3 * mt * mt * t * b[1][1] + 3 * mt * t * t * b[2][1] + t ** 3 * b[3][1])


def _bez_d1(b, t):
    mt = 1 - t
    return (3 * (mt * mt * (b[1][0] - b[0][0]) + 2 * mt * t * (b[2][0] - b[1][0]) + t * t * (b[3][0] - b[2][0])),
            3 * (mt * mt * (b[1][1] - b[0][1]) + 2 * mt * t * (b[2][1] - b[1][1]) + t * t * (b[3][1] - b[2][1])))


def _bez_d2(b, t):
    return (6 * ((1 - t) * (b[2][0] - 2 * b[1][0] + b[0][0]) + t * (b[3][0] - 2 * b[2][0] + b[1][0])),
            6 * ((1 - t) * (b[2][1] - 2 * b[1][1] + b[0][1]) + t * (b[3][1] - 2 * b[2][1] + b[1][1])))


def _chord(pts):
    u = [0.0]
    for i in range(1, len(pts)):
        u.append(u[-1] + _dist(pts[i - 1], pts[i]))
    return [x / u[-1] for x in u] if u[-1] > 0 else u


def _generate(pts, u, t1, t2):
    p0, p3 = pts[0], pts[-1]
    c00 = c01 = c11 = x0 = x1 = 0.0
    for p, t in zip(pts, u):
        mt = 1 - t
        b0, b1, b2, b3 = mt ** 3, 3 * mt * mt * t, 3 * mt * t * t, t ** 3
        a0, a1 = _mul(t1, b1), _mul(t2, b2)
        c00 += _dot(a0, a0)
        c01 += _dot(a0, a1)
        c11 += _dot(a1, a1)
        tmp = _sub(p, _add(_mul(p0, b0 + b1), _mul(p3, b2 + b3)))
        x0 += _dot(a0, tmp)
        x1 += _dot(a1, tmp)
    det = c00 * c11 - c01 * c01
    seg = _dist(p0, p3)
    al = ar = seg / 3
    if abs(det) > 1e-12:
        l, r = (x0 * c11 - x1 * c01) / det, (c00 * x1 - c01 * x0) / det
        if 1e-6 * seg < l < 2 * seg and 1e-6 * seg < r < 2 * seg:
            al, ar = l, r
    return (p0, _add(p0, _mul(t1, al)), _add(p3, _mul(t2, ar)), p3)


def _max_err(pts, b, u):
    worst, split = 0.0, len(pts) // 2
    for i in range(1, len(pts) - 1):
        d = _dist(_bez(b, u[i]), pts[i])
        if d > worst:
            worst, split = d, i
    return worst, split


def _reparam(b, pts, u):
    out = []
    for p, t in zip(pts, u):
        q, q1, q2 = _bez(b, t), _bez_d1(b, t), _bez_d2(b, t)
        diff = _sub(q, p)
        den = _dot(q1, q1) + _dot(diff, q2)
        out.append(min(1.0, max(0.0, t - _dot(diff, q1) / den)) if abs(den) > 1e-12 else t)
    return out


def _fit(pts, t1, t2, tol, depth=0):
    if len(pts) == 2 or depth > 24:
        d = _dist(pts[0], pts[-1]) / 3
        return [(pts[0], _add(pts[0], _mul(t1, d)), _add(pts[-1], _mul(t2, d)), pts[-1])]
    u = _chord(pts)
    b = _generate(pts, u, t1, t2)
    err, split = _max_err(pts, b, u)
    if err < tol:
        return [b]
    if err < tol * 4:
        for _ in range(12):
            u = _reparam(b, pts, u)
            b = _generate(pts, u, t1, t2)
            err, split = _max_err(pts, b, u)
            if err < tol:
                return [b]
    split = min(max(split, 1), len(pts) - 2)
    tc = _unit(_sub(pts[split - 1], pts[split + 1]))
    return _fit(pts[:split + 1], t1, tc, tol, depth + 1) + _fit(pts[split:], _mul(tc, -1), t2, tol, depth + 1)


def _walk(pts, i, direction, length):
    """沿闭合折线从 i 走 length 弧长，返回落点下标。"""
    n, acc, j = len(pts), 0.0, i
    while acc < length:
        k = (j + direction) % n
        acc += _dist(pts[j], pts[k])
        j = k
        if j == i:
            break
    return j


def _fit_closed(poly, tol, corner_deg=38.0):
    pts = []
    for p in poly:
        if not pts or _dist(pts[-1], p) > 0.8:
            pts.append(p)
    if len(pts) > 2 and _dist(pts[0], pts[-1]) < 0.8:
        pts.pop()
    n = len(pts)
    if n < 4:
        return []
    turn = []
    for i in range(n):
        a, c = pts[_walk(pts, i, -1, 7.0)], pts[_walk(pts, i, 1, 7.0)]
        v1, v2 = _unit(_sub(pts[i], a)), _unit(_sub(c, pts[i]))
        turn.append(math.degrees(math.acos(max(-1.0, min(1.0, _dot(v1, v2))))))
    corners = [i for i in range(n) if turn[i] > corner_deg
               and turn[i] >= turn[(i - 1) % n] and turn[i] > turn[(i + 1) % n]]
    smooth = not corners
    if smooth:
        corners = [0, n // 3, 2 * n // 3]

    def tangent_out(i):
        if smooth:
            return _unit(_sub(pts[(i + 1) % n], pts[(i - 1) % n]))
        return _unit(_sub(pts[_walk(pts, i, 1, 9.0)], pts[i]))

    def tangent_in(i):
        if smooth:
            return _unit(_sub(pts[(i - 1) % n], pts[(i + 1) % n]))
        return _unit(_sub(pts[_walk(pts, i, -1, 9.0)], pts[i]))

    segs = []
    for k, c in enumerate(corners):
        nxt = corners[(k + 1) % len(corners)]
        span = [pts[(c + j) % n] for j in range(((nxt - c) % n or n) + 1)]
        if len(span) < 2:
            continue
        segs += _fit(span, tangent_out(c), tangent_in(nxt), tol)
    return segs


def _area(poly):
    return 0.5 * sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
                     for i in range(len(poly)))


def _inside(p, poly):
    x, y, hit = p[0], p[1], False
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        if (a[1] > y) != (b[1] > y) and x < a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1]):
            hit = not hit
    return hit


def _reverse(segs):
    return [(s[3], s[2], s[1], s[0]) for s in reversed(segs)]


# ════════════════════════════════════════════════════════════
#  入口
# ════════════════════════════════════════════════════════════

def _strokes_swept(strokes, nib, seed):
    """逐笔落墨：每笔的粗细与回钩取自该笔自己的种子。字形与书写动画共用这一步，结果逐字节一致。"""
    for k, stroke in enumerate(strokes):
        rnd = random.Random(seed * 97 + k * 13)
        is_dot = isinstance(stroke, dict) and stroke.get("dot")
        weight = stroke.get("w", 1.0) if isinstance(stroke, dict) else 1.0
        pts = stroke["p"] if isinstance(stroke, dict) else stroke
        if not is_dot:
            weight *= rnd.uniform(*nib.weight)
            pts = _add_hook(pts, nib, rnd)
        yield _stroke_polys(pts, nib, seed * 97 + k * 13, weight)


def sweep_glyph(strokes, nib, seed, tol=1.4):
    """骨架笔画 → [contour]，contour 是三次段列表；外圈逆时针、内洞顺时针（CFF 约定）。"""
    polys = [poly for swept, _, _ in _strokes_swept(strokes, nib, seed) for poly in swept]
    return _contours(polys, tol)


def sweep_strokes(strokes, nib, seed, tol=1.4):
    """逐笔拆开的字形：[{contours, trace, radius}]。全部笔画的墨迹并起来等于 sweep_glyph 的字形。
    书写动画用它：每笔一张遮罩，笔沿 trace 走，只揭开这一笔自己的墨。"""
    return [{"contours": _contours(swept, tol), "trace": trace, "radius": rmax}
            for swept, trace, rmax in _strokes_swept(strokes, nib, seed)]


def _contours(polys, tol):
    if not polys:
        return []
    raw = _union(polys)
    out = []
    for poly in raw:
        if abs(_area(poly)) < 30:
            continue
        segs = _fit_closed(poly, tol)
        if not segs:
            continue
        depth = sum(1 for other in raw if other is not poly and _inside(poly[0], other))
        ccw = _area(poly) > 0
        if ccw != (depth % 2 == 0):
            segs = _reverse(segs)
        out.append(segs)
    return out
