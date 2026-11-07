"""
[INPUT]: 依赖 build.py 的 shape/glyph_name/WEIGHTS/SPACE（与字体同一套排字规则）；依赖 lettering.py 的 letter/schedule/open_path（逐笔书写数据）
[OUTPUT]: write_all(built, out_dir, names, card)：README 样张 SVG——hero（12 帧/秒沸腾）、write（逐笔书写后沸腾）、anatomy（骨架 × 笔）、
          weights、charset、alternates；card=False 出无底版本，z1han.com/font 放进自己的羽化黄面板
[POS]: scripts/scrawl 的样张器。build.py --specimen 调它；字形直接取内存轮廓转 SVG 路径，不依赖系统装了什么字体。
       开源仓库的 README 用 <img> 引这些 SVG：动画只用 SVG 内的 CSS，GitHub 的图片沙箱里也能播。
[PROTOCOL]: 变更时更新此头部，然后检查 AGENTS.md

两段动画与站点同法：沸腾是三帧叠放、按 12 帧/秒 step-end 轮流显形（site/opening/OpeningAct）；
书写是每笔一张遮罩、遮罩笔沿笔尖轨迹走（vault/notes-lettering）。减弱动态时只显示写完的第一帧。
"""
from pathlib import Path

import build as B
import lettering as L

# ── 版面：站点的黄底墨（globals.css 的 --yellow 与 --ink-on-yellow） ─────
PAPER, INK = "#fef991", "#171717"
WIDTH, MARGIN, RADIUS = 1200, 64, 28
BOIL_FPS = 12                      # 与 kernel/scrawl-boil 的 BOIL_FPS 同拍
BOIL_MS = 1000 / BOIL_FPS
STYLE_KEY = {"Light": "L", "Regular": "R", "Bold": "B"}


def _n(v):
    """坐标与百分比统一一位小数，去掉多余的 0。"""
    return f"{v:.1f}".rstrip("0").rstrip(".")


def _path(contours, fx=lambda p: (round(p[0]), round(p[1]))):
    d = []
    for c in contours:
        x, y = fx(c[0][0])
        d.append(f"M{_n(x)} {_n(y)}")
        d.append("C" + " ".join(f"{_n(a)} {_n(b)}" for seg in c for a, b in map(fx, seg[1:])))
        d.append("Z")
    return "".join(d)


class Sheet:
    """一张 SVG：字形进 <defs> 只写一次，排字用 <use> 引；整行包在一个 scale(s,-s) 组里，x 用字体单位。"""

    def __init__(self, built, height):
        self.built, self.h = built, height
        self.defs, self.body, self.css = {}, [], []

    def _ref(self, style, ch, alt):
        name = B.glyph_name(ch) + (f".alt{alt}" if alt else "")
        key = f"{STYLE_KEY[style]}-{name.replace('.', '-')}"
        if key not in self.defs:
            self.defs[key] = f'<path id="{key}" d="{_path(self.built[style][0][name][0])}"/>'
        return key, self.built[style][1][name]

    def measure(self, text, style="Regular", frame=0, track=0):
        """一行的宽度（字体单位）；track 是额外字距（字体单位）。"""
        total = 0
        for ch, alt in B.shape(text, frame):
            total += B.SPACE if alt is None else self._ref(style, ch, alt)[1] + track
        return total - (track if text else 0)

    def line(self, text, x, base, size, style="Regular", frame=0, track=0, cls=None):
        s, pen, uses = size / B.UPM, 0, []
        for ch, alt in B.shape(text, frame):
            if alt is None:
                pen += B.SPACE
                continue
            key, w = self._ref(style, ch, alt)
            uses.append(f'<use href="#{key}" x="{pen}"/>')
            pen += w + track
        klass = f' class="{cls}"' if cls else ""
        return f'<g{klass} transform="translate({_n(x)} {_n(base)}) scale({s:.4f} {-s:.4f})">{"".join(uses)}</g>'

    def centered(self, text, base, size, **kw):
        w = self.measure(text, kw.get("style", "Regular"), kw.get("frame", 0), kw.get("track", 0)) * size / B.UPM
        return self.line(text, (WIDTH - w) / 2, base, size, **kw)

    def svg(self, card=True):
        """card=False 不画黄底卡片：网站把样张放进自己的羽化黄面板，底与边缘由页面负责。"""
        style = f"<style>{''.join(self.css)}</style>" if self.css else ""
        ground = f'<rect width="{WIDTH}" height="{self.h}" rx="{RADIUS}" fill="{PAPER}"/>' if card else ""
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{self.h}" viewBox="0 0 {WIDTH} {self.h}">'
                f"{style}<defs>{''.join(self.defs.values())}</defs>{ground}"
                f'<g fill="{INK}">{"".join(self.body)}</g></svg>\n')


def _boil_css(prefix):
    """三帧叠放：静态只显第一帧；允许动态时按 12 帧/秒轮流显形，相位错开半帧避免两帧同显。"""
    delays = "".join(f".{prefix}{i}{{animation-delay:{-((3 - i) % 3 + 0.5) * BOIL_MS:.1f}ms}}" for i in range(3))
    return (f".{prefix}1,.{prefix}2{{opacity:0}}"
            f"@media (prefers-reduced-motion:no-preference){{"
            f".{prefix}0,.{prefix}1,.{prefix}2{{animation:{prefix}b {3 * BOIL_MS:.0f}ms step-end infinite}}{delays}"
            f"@keyframes {prefix}b{{0%{{opacity:1}}33.333%{{opacity:0}}100%{{opacity:0}}}}}}")


# ════════════════════════════════════════════════════════════
#  六张样张
# ════════════════════════════════════════════════════════════

def hero(built):
    sh = Sheet(built, 520)
    for f in range(3):
        sh.body.append(f'<g class="h{f}">'
                       + sh.centered("Z1 Scrawl", 290, 230, frame=f)
                       + sh.centered("Make it the one.", 430, 88, frame=f) + "</g>")
    sh.css.append(_boil_css("h"))
    return sh


def write(built, word="z1han!", size=300, slow=1.5, boil_ms=2000, fade_ms=450, rest_ms=600):
    """逐笔书写 → 沸腾 → 淡出 → 循环。写完的第一帧就是遮罩全开的笔画；沸腾时它与第 2、3 帧轮流显形。"""
    sh = Sheet(built, 440)
    strokes = L.letter(word)
    plan = L.schedule(strokes)
    s = size / B.UPM
    xs = [p[0] for st in strokes for c in st["contours"] for seg in c for p in seg]
    ink_w = (max(xs) - min(xs)) * s
    x0 = (WIDTH - ink_w) / 2 - min(xs) * s
    base = 300
    fx = lambda p: (x0 + p[0] * s, base - p[1] * s)
    pen = 2 * L.PEN_COVER * max(st["radius"] for st in strokes) * s

    write_end = max(t + d for t, d, _ in plan) * slow
    boil0 = write_end + 120
    boil1 = boil0 + boil_ms
    total = boil1 + fade_ms + rest_ms
    pct = lambda t: f"{100 * t / total:.3f}".rstrip("0").rstrip(".") + "%"

    masks, inks, css = [], [], []
    for i, (st, (t0, dur, progress)) in enumerate(zip(strokes, plan)):
        seg_pts = [fx(p) for p in st["trace"]]
        length = sum(((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5 for a, b in zip(seg_pts, seg_pts[1:])) or 1
        d = ""
        for j, seg in enumerate(L.open_path(st["trace"], 1.0)):
            pts = [fx(p) for p in seg]
            d += (f"M{_n(pts[0][0])} {_n(pts[0][1])}" if j == 0 else "") + "C" + " ".join(f"{_n(a)} {_n(b)}" for a, b in pts[1:])
        hidden = length + pen
        masks.append(f'<mask id="m{i}" maskUnits="userSpaceOnUse" x="0" y="0" width="{WIDTH}" height="{sh.h}">'
                     f'<path class="p{i}" d="{d}" pathLength="{_n(length)}" fill="none" stroke="#fff" '
                     f'stroke-width="{_n(pen)}" stroke-linecap="round" stroke-linejoin="round" '
                     f'stroke-dasharray="{_n(length)} {_n(length + 2 * pen)}"/></mask>')
        inks.append(f'<path d="{_path(st["contours"], fx)}" mask="url(#m{i})"/>')
        t0, dur = t0 * slow, dur * slow
        stops = [f"0%,{pct(t0)}{{stroke-dashoffset:{_n(hidden)}}}"]
        for k, (tf, af) in enumerate(progress):
            t = t0 + tf * dur + (4 if k == 0 else 0)     # 起笔先落一个圆点，再开始走
            stops.append(f"{pct(t)}{{stroke-dashoffset:{_n(length * (1 - af))}}}")
        stops.append(f"{pct(boil1 + fade_ms)}{{stroke-dashoffset:0}}")
        stops.append(f"{pct(boil1 + fade_ms + 20)},100%{{stroke-dashoffset:{_n(hidden)}}}")
        css.append(f"@keyframes p{i}{{{''.join(stops)}}}.p{i}{{animation:p{i} {total:.0f}ms linear infinite}}")

    # 沸腾帧：写完的笔画组算第 0 帧，另两帧取整字异体平移 1、2 份
    def flicker(f):
        stops, last = [f"0%{{opacity:{1 if f == 0 else 0}}}"], None
        t, j = boil0, 0
        while t < boil1:
            on = 1 if j % 3 == f else 0
            if on != last:
                stops.append(f"{pct(t)}{{opacity:{on}}}")
                last = on
            t, j = t + BOIL_MS, j + 1
        stops.append(f"{pct(boil1)}{{opacity:{1 if f == 0 else 0}}}")
        return f"@keyframes w{f}{{{''.join(stops)}}}.w{f}{{animation:w{f} {total:.0f}ms step-end infinite}}"

    frames = [f'<g class="w0">{"".join(inks)}</g>']
    for f in (1, 2):
        frames.append(sh.line(word, x0, base, size, frame=f, cls=f"w{f}"))
    fade = (f"@keyframes wf{{0%,{pct(boil1)}{{opacity:1}}{pct(boil1 + fade_ms)},100%{{opacity:0}}}}"
            f".wf{{animation:wf {total:.0f}ms linear infinite}}")
    sh.defs["masks"] = "".join(masks)
    sh.body.append(f'<g class="wf">{"".join(frames)}</g>')
    sh.css.append(".w1,.w2{opacity:0}@media (prefers-reduced-motion:no-preference){"
                  + "".join(css) + flicker(0) + flicker(1) + flicker(2) + fade + "}")
    return sh


def anatomy(built, word="Rag", size=380):
    """骨架 × 笔：浅墨是成字，细线是笔尖实际走过的轨迹，空心圈是骨架控制点，实心点是起笔处。"""
    sh = Sheet(built, 520)
    strokes = L.letter(word)
    s = size / B.UPM
    xs = [p[0] for st in strokes for c in st["contours"] for seg in c for p in seg]
    x0 = (WIDTH - (max(xs) - min(xs)) * s) / 2 - min(xs) * s
    base = 390
    fx = lambda p: (x0 + p[0] * s, base - p[1] * s)
    ink = "".join(f'<path d="{_path(st["contours"], fx)}"/>' for st in strokes)
    lines, dots = [], []
    for st in strokes:
        pts = [fx(p) for p in st["trace"]]
        lines.append("M" + "L".join(f"{_n(x)} {_n(y)}" for x, y in pts))
        dots += [f'<circle cx="{_n(x)}" cy="{_n(y)}" r="5"/>' for x, y in map(fx, st["points"])]
        sx, sy = pts[0]
        dots.append(f'<circle cx="{_n(sx)}" cy="{_n(sy)}" r="9" fill="{INK}" stroke="none"/>')
    sh.body.append(f'<g opacity=".16">{ink}</g>'
                   f'<path d="{"".join(lines)}" fill="none" stroke="{INK}" stroke-width="2.5" '
                   f'stroke-linecap="round" stroke-linejoin="round"/>'
                   f'<g fill="{PAPER}" stroke="{INK}" stroke-width="2.5">{"".join(dots)}</g>')
    return sh


def weights(built):
    sh = Sheet(built, 560)
    for i, style in enumerate(B.WEIGHTS):
        base = 170 + i * 150
        sh.body.append(sh.line(style, MARGIN, base, 52, style=style))
        sh.body.append(sh.line("Make it the one!", 340, base, 118, style=style))
    return sh


CHARSET = ("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz", "0123456789 &@#$%*+=<>^~|",
           ".,:;!?'\"‘’“”-–—…·•()[]{}/\\_`")


def charset(built, track=24):
    sh = Sheet(built, 0)
    widest = max(sh.measure(row, track=track) for row in CHARSET)
    size = min(96, (WIDTH - 2 * MARGIN) * B.UPM / widest)
    step = size * 1.45
    for i, row in enumerate(CHARSET):
        sh.body.append(sh.line(row, MARGIN, MARGIN + size * 0.9 + i * step, size, track=track))
    sh.h = round(MARGIN * 2 + size * 0.9 + (len(CHARSET) - 1) * step + size * 0.25)
    return sh


def alternates(built, chars="aegR", size=128):
    """上行：每个字的三份异体并排；下行：calt 轮换后，连写的同一个字永不撞脸。"""
    sh = Sheet(built, 460)
    x, gap = MARGIN, 70
    for ch in chars:
        for alt in range(B.ALTS):
            key, w = sh._ref("Regular", ch, alt)
            sh.body.append(f'<use href="#{key}" transform="translate({_n(x)} 200) scale({size / B.UPM:.4f} {-size / B.UPM:.4f})"/>')
            x += (w + 30) * size / B.UPM
        x += gap
    sh.body.append(sh.line("little balloon, all three", MARGIN, 380, 100))
    return sh


SHEETS = {"hero": hero, "write": write, "anatomy": anatomy, "weights": weights, "charset": charset, "alternates": alternates}


def write_all(built, out_dir, names=None, card=True):
    """names 缺省写全部样张；card=False 写无底版本给网站。"""
    missing = set(B.WEIGHTS) - set(built)
    if missing:
        raise SystemExit(f"样张需要三档全建，缺 {', '.join(sorted(missing))}")
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in names or SHEETS:
        path = Path(out_dir) / f"{name}.svg"
        path.write_text(SHEETS[name](built).svg(card))
        print(f"  specimen {path.stat().st_size / 1024:.0f} KB → {path}")
