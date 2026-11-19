"""舞台：序列帧 + 程序化动作 + 粒子。桌宠窗口和动图导出工具共用。
Stage = sprite clips + procedural motion + particles. Shared by the pet window and the GIF exporter.

“组合动作”= 一段手绘/生成的序列帧（clip）× 一种程序化运动（motion）× 一种粒子（emit）。
已有 12 段序列帧，这里组合出 30 多个动作，不需要新的美术帧。
"""
import bisect
import json
import math
import random
import threading
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QRadialGradient

from .config import ASSETS

CLIPS = ["jump", "wipe", "down", "anxious", "daze", "startle", "cry", "wave", "idle", "read", "hum", "special"]
TAU = math.tau


class Clip:
    """一段动图的流式读取器。
    - 解码放在后台线程，提前解好接下来几帧；界面线程只取现成的帧，永远不等解码（之前偶尔卡一下就是在等它）。
    - pair(pos) 返回相邻两帧和混合比例，由 Stage 在两帧之间做线性混合，
      把 25fps 的素材补成和屏幕刷新率一样密的画面。素材本身已是光流补帧，相邻帧差别很小，混合不会出重影。
    - 优先读超分后的 <clip>@2x.webp；.png 当作单帧（三视图立绘）。
    """
    AHEAD = 6

    def __init__(self, path, px):
        self.im = Image.open(path)
        self.n = getattr(self.im, "n_frames", 1)
        if str(path).lower().endswith(".png"):
            px = self.im.height / 300.0               # 立绘缩到和序列帧里的人物一样高
        self.px = px
        self.w, self.h = self.im.width / px, self.im.height / px
        # 时间轴：WebP 编码器会把完全相同的连续帧合并成一张“长帧”，所以每帧时长不一定相同。
        # 补帧工具会写一个同名 .json 记下每帧时长；没有的话按第一帧的时长均匀处理。
        durs = None
        side = Path(str(path)).with_suffix(".json")
        if side.exists():
            try:
                durs = json.loads(side.read_text(encoding="utf-8"))["durations"]
                if len(durs) != self.n:
                    durs = None
            except Exception:
                durs = None
        if durs is None:
            try:
                self.im.seek(0)
                self.im.load()
                d0 = float(self.im.info.get("duration") or 40)
            except Exception:
                d0 = 40.0
            durs = [d0] * self.n
        self.starts = [0.0]
        for d in durs[:-1]:
            self.starts.append(self.starts[-1] + d)
        self.durs = durs
        self.total_ms = sum(durs) or 1.0
        self.frame_ms = self.total_ms / max(1, self.n)
        self.scale = 0.86 if (self.h < 400 and self.n > 1) else 1.0   # v1 素材（方形构图）人物偏大，归一身高
        self.blend = self.n > 12 and self.frame_ms <= 60      # v1 的 4 帧素材姿势差太大，不混合
        self.lock = threading.Lock()
        self.cache = {}
        self.want = 0
        self.wake = threading.Event()
        self.thread = None
        first = self._decode(0)
        if self.n == 1:
            self.foot = self.h
        else:
            bb = self.im.convert("RGBA").getchannel("A").point(lambda v: 255 if v > 40 else 0).getbbox()
            self.foot = (bb[3] if bb else self.im.height) / px
        self.cache[0] = first
        self.last = first

    def _decode(self, i):
        self.im.seek(i)
        rgba = self.im.convert("RGBA")
        q = QImage(rgba.tobytes(), rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888)
        return q.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

    def _run(self):
        while True:
            self.wake.wait()
            self.wake.clear()
            while True:
                want = self.want
                need = [(want + k) % self.n for k in range(self.AHEAD)]
                with self.lock:
                    todo = next((x for x in need if x not in self.cache), None)
                    for k in [k for k in self.cache if (k - want) % self.n > self.AHEAD + 1 and (want - k) % self.n > 2]:
                        del self.cache[k]
                if todo is None:
                    break
                try:
                    img = self._decode(todo)
                except Exception:
                    break
                with self.lock:
                    self.cache[todo] = img

    def at(self, ms: float):
        """按时间（毫秒）取画面：返回 (当前帧, 下一帧, 混合比例)。长帧在最后一小段才开始往下一帧过渡。"""
        if self.n == 1:
            return self.last, self.last, 0.0
        ms %= self.total_ms
        i = bisect.bisect_right(self.starts, ms) - 1
        into, d = ms - self.starts[i], self.durs[i]
        base = 20.0                                  # 正常帧长；长帧只在末尾 base 毫秒内混合
        frac = 0.0 if into < d - base else (into - (d - base)) / base
        return self.pair(i + max(0.0, min(0.999, frac)))

    def pair(self, pos: float):
        """pos 是带小数的帧位置。返回 (前一帧, 后一帧, 混合比例)。"""
        if self.n == 1:
            return self.last, self.last, 0.0
        i = int(pos) % self.n
        self.want = i
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
        self.wake.set()
        with self.lock:
            a = self.cache.get(i)
            b = self.cache.get((i + 1) % self.n)
        if a is None:                      # 后台还没解到（刚切动作的头几毫秒）→ 先用上一张
            return self.last, self.last, 0.0
        self.last = a
        if b is None or not self.blend:
            return a, a, 0.0
        return a, b, pos - int(pos)

    def frame(self, i) -> QImage:
        return self.pair(float(i))[0]

    @property
    def frames(self):
        return range(self.n)


class ClipBank:
    def __init__(self, char):
        self.char = char
        self.dir = ASSETS / char.id
        self.cache = {}

    def _path(self, key):
        if key.startswith("view_"):
            p = self.dir / f"{key}.png"
            return (p, 1) if p.exists() else (None, 1)
        hi = self.dir / f"{key}@2x.webp"
        if hi.exists():
            return hi, 2
        lo = self.dir / f"{key}.webp"
        return (lo, 1) if lo.exists() else (None, 1)

    def resolve(self, key):
        if self._path(key)[0]:
            return key
        fb = self.char.fallback.get(key)
        if fb and self._path(fb)[0]:
            return fb
        for k in ("idle", "anxious", "wave", "jump"):
            if self._path(k)[0]:
                return k
        raise FileNotFoundError(f"assets/{self.char.id} 里没有任何动图，请先运行 tools/import_assets.py")

    def get(self, key) -> Clip:
        key = self.resolve(key)
        c = self.cache.get(key)
        if c is None:
            c = self.cache[key] = Clip(*self._path(key))
        return c

    def preload(self):
        """启动时把每段动图的第一帧都准备好（约 0.3 秒），之后切动作不会顿一下。"""
        for k in CLIPS + ["view_front", "view_side", "view_back"]:
            try:
                if self._path(k)[0]:
                    self.get(k)
            except Exception:
                pass


# ---------------- 程序化运动：t(秒) → (dx, dy, 旋转角, sx, sy) ----------------
def _ease(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def m_breathe(t):
    s = 0.012 * math.sin(TAU * t / 3.2)
    return 0, 0, 0, 1 - s * 0.6, 1 + s


def m_bob(t):
    return 0, -3 * abs(math.sin(TAU * t * 1.4)), 1.2 * math.sin(TAU * t * 0.7), 1, 1 + 0.012 * math.sin(TAU * t * 2.8)


def m_hop(t):
    a = abs(math.sin(math.pi * t * 2.2))
    sq = 0.08 * (1 - a) ** 3
    return 0, -20 * a, 0, 1 + sq, 1 - sq


def m_sway(t):
    return 4 * math.sin(TAU * t / 1.6), 0, 5 * math.sin(TAU * t / 1.6), 1, 1


def m_shake(t):
    return 5 * math.sin(TAU * t * 13), 0, 1.5 * math.sin(TAU * t * 9), 1, 1


def m_shiver(t):
    return 1.8 * math.sin(TAU * t * 19), 0, 0, 1, 1 + 0.008 * math.sin(TAU * t * 6)


def m_spin(t):
    return 10 * math.sin(TAU * t * 0.9), 0, 11 * math.sin(TAU * t * 1.3), 1 + 0.04 * math.sin(TAU * t * 2), 1


def m_tilt(t):
    return 0, 0, 7 * _ease(t / 0.35) + 1.2 * math.sin(TAU * t / 2.2), 1, 1


def m_droop(t):
    s = 0.03 * math.sin(TAU * t / 4.5)
    return 0, 5, 4 + 1.5 * math.sin(TAU * t / 4.5), 1 - s * 0.5, 1 + s


def m_squash(t):
    s = 0.22 * math.exp(-6 * t) * math.cos(18 * t)
    return 0, 0, 0, 1 + s, 1 - s


def m_stretch(t):
    s = 0.09 * math.sin(math.pi * min(t, 1.6) / 1.6) ** 2
    return 0, 0, 0, 1 - s * 0.5, 1 + s


def m_pop(t):
    k = 1 - math.exp(-9 * t) * math.cos(14 * t)
    k = max(0.05, k)
    return 0, 0, 0, k, k


def m_shrink(t):
    k = max(0.02, 1 - _ease(t / 0.45))
    return 0, 0, -20 * _ease(t / 0.45), k, k


def m_nod(t):
    return 0, 3 * math.sin(TAU * t * 0.8) ** 2, 1.5 * math.sin(TAU * t * 0.4), 1, 1


def m_peek(t):
    return 0, -2 * math.sin(TAU * t / 2.5), -13 + 2 * math.sin(TAU * t / 2.5), 1, 1


def m_float(t):
    return 3 * math.sin(TAU * t / 3.1), -12 - 6 * math.sin(TAU * t / 2.4), 2 * math.sin(TAU * t / 3.1), 1, 1


def m_wiggle(t):
    return 0, 3, 3 * math.sin(TAU * t * 2.5), 1.03, 0.965


def m_twirl(t):
    return 0, -6 * abs(math.sin(TAU * t / 0.9)), 0, math.cos(TAU * t / 0.9), 1


def m_backflip(t):
    p = min(1.0, t / 0.75)
    return 0, -70 * math.sin(math.pi * p), -360 * _ease(p), 1, 1


def m_walk(t):
    a = abs(math.sin(math.pi * t * 3.0))
    return 0, -7 * a, 3 * math.sin(TAU * t * 1.5), 1, 1 - 0.03 * (1 - a)


def m_none(t):
    return 0, 0, 0, 1, 1


TURN_STEP = 0.75        # 每个视角停留的秒数
TURN_VIEWS = (("view_front", False), ("view_side", False), ("view_back", False), ("view_side", True),
              ("view_front", False))


def c_turn(t):          # 转身展示：正面 → 侧面 → 背面 → 另一侧 → 正面
    return TURN_VIEWS[min(len(TURN_VIEWS) - 1, int(t / TURN_STEP))]


def m_turn(t):          # 换视角的瞬间把身体横向压扁再展开，看起来像原地转了一下
    ph = (t % TURN_STEP) / TURN_STEP
    edge = min(ph, 1 - ph) * TURN_STEP          # 距离最近一次换视角的秒数
    k = min(1.0, edge / 0.11)
    if t < 0.11 * 0.5 or t > TURN_STEP * len(TURN_VIEWS) - 0.06:
        k = 1.0
    return 0, -4 * (1 - k), 0, 0.12 + 0.88 * _ease(k), 1


def c_back(t):
    return ("view_back", False)


def c_side(t):
    return ("view_side", False)


def m_pulse(t):          # 心跳：咚咚、停
    ph = (t * 1.25) % 1
    k = 0.06 * (math.exp(-((ph - .08) / .05) ** 2) + .7 * math.exp(-((ph - .3) / .05) ** 2))
    return 0, 0, 0, 1 + k, 1 + k


def m_no(t):             # 摇头
    return 5 * math.sin(TAU * t * 2.2) * math.exp(-t * .9), 0, 4 * math.sin(TAU * t * 2.2) * math.exp(-t * .9), 1, 1


def m_yes(t):            # 用力点头
    k = math.sin(TAU * t * 2.0) ** 2 * math.exp(-t * .8)
    return 0, 7 * k, 0, 1 + .03 * k, 1 - .05 * k


def m_sneeze(t):         # 蓄力……阿嚏！
    if t < .7:
        k = _ease(t / .7)
        return 0, 0, -6 * k, 1 - .04 * k, 1 + .07 * k
    s = .2 * math.exp(-7 * (t - .7)) * math.cos(22 * (t - .7))
    return 0, 0, 8 * math.exp(-6 * (t - .7)), 1 + s, 1 - s


def m_idea(t):           # 灵光一闪：小跳一下定住
    return 0, -26 * math.exp(-5 * t) * abs(math.sin(9 * t)), 0, 1, 1 + .05 * math.exp(-6 * t)


def m_tiptoe(t):         # 踮脚张望
    k = _ease(t / .4)
    return 6 * math.sin(TAU * t / 1.8) * k, -9 * k, 3 * math.sin(TAU * t / 1.8) * k, 1 - .02 * k, 1 + .05 * k


def m_dance(t):          # 左右踩点
    b = t * 2.4
    return 12 * math.sin(math.pi * b), -8 * abs(math.sin(math.pi * b * 2)), 6 * math.sin(math.pi * b), 1, 1


# 动作表： name: (clip, motion, 粒子, 粒子样式, 默认时长秒(None=循环), 中文名)
# 粒子写 "@happy" 之类表示取角色专属粒子。
ACTIONS = {
    "idle":      ("idle", m_breathe, None, None, None, "待机呼吸"),
    "talk":      ("idle", m_bob, None, None, None, "说话点头"),
    "mope":      ("daze", m_droop, None, None, None, "闷闷不乐"),
    "happy":     ("jump", m_none, "@happy", "burst", 2.0, "开心跳跃"),
    "cheer":     ("jump", m_none, "⭐", "rise", 2.4, "加油打气"),
    "celebrate": ("special", m_backflip, "🎉", "burst", 1.8, "后空翻庆祝"),
    "love":      ("special", m_wiggle, "💗", "rise", 2.6, "爱心发射"),
    "pet":       ("hum", m_wiggle, "@pet", "rise", None, "被摸头"),
    "shy":       ("daze", m_tilt, "💗", "blush", 2.6, "害羞"),
    "think":     ("read", m_nod, "💭", "think", None, "思考翻书"),
    "curious":   ("idle", m_tilt, "❔", "pop", 2.0, "歪头好奇"),
    "surprised": ("startle", m_none, "❗", "pop", 1.6, "惊讶后退"),
    "worried":   ("anxious", m_none, "💦", "sweat", 2.8, "担心搓手"),
    "sad":       ("down", m_droop, "💧", "sweat", 3.0, "难过低头"),
    "wipe":      ("wipe", m_none, "💧", "fall", 3.0, "伤心擦泪"),
    "cry":       ("cry", m_shiver, "💧", "fall", 3.2, "坐地大哭"),
    "angry":     ("anxious", m_shake, "💢", "pop", 2.0, "生气跺脚"),
    "sleep":     ("daze", m_droop, "💤", "zzz", None, "打瞌睡"),
    "yawn":      ("daze", m_stretch, "💤", "pop", 1.8, "伸懒腰"),
    "wake":      ("startle", m_pop, "❕", "pop", 1.0, "惊醒"),
    "proud":     ("special", m_stretch, "✨", "burst", 2.4, "得意"),
    "dizzy":     ("startle", m_spin, "💫", "orbit", 2.6, "晕头转向"),
    "drag":      ("startle", m_none, "💦", "sweat", None, "被拎起来"),
    "land":      ("idle", m_squash, "💨", "ground", 0.7, "落地回弹"),
    "walk":      ("hum", m_walk, None, None, None, "散步"),
    "listen":    ("idle", m_tilt, "🎤", "pop", None, "竖耳倾听"),
    "hum":       ("hum", m_sway, "🎵", "rise", 4.0, "哼歌摇摆"),
    "read":      ("read", m_breathe, None, None, 5.0, "安静看书"),
    "wave":      ("wave", m_none, None, None, 2.0, "挥手"),
    "special":   ("special", m_float, "@special", "rise", 3.0, "专属互动"),
    "float":     ("idle", m_float, "@idle", "rise", 3.5, "漂浮"),
    "twirl":     ("hum", m_twirl, "🎶", "burst", 1.8, "转圈圈"),
    "backflip":  ("jump", m_backflip, "✨", "burst", 0.9, "后空翻"),
    "hungry":    ("anxious", m_sway, "🍙", "pop", 2.6, "肚子饿"),
    "night":     ("daze", m_shiver, "🌙", "pop", 2.8, "夜深了"),
    "peek":      ("idle", m_peek, None, None, None, "探头偷看"),
    "show":      ("wave", m_pop, "@happy", "burst", 1.6, "登场"),
    "hide":      ("wave", m_shrink, "💨", "ground", 0.5, "退场"),
    # ---- v0.3 用三视图立绘做的动作 ----
    "turn":      (c_turn, m_turn, "✨", "burst", TURN_STEP * 5, "转身展示"),
    "back":      (c_back, m_breathe, "💭", "pop", 3.0, "背过身去"),
    "profile":   (c_side, m_breathe, None, None, 3.0, "侧身远望"),
    # ---- v0.2 新增的创意动作 ----
    "idea":      ("idle", m_idea, "💡", "pop", 1.6, "灵光一闪"),
    "yes":       ("idle", m_yes, "⭕", "pop", 1.6, "用力点头"),
    "no":        ("anxious", m_no, "❌", "pop", 1.8, "摇头不行"),
    "heartbeat": ("special", m_pulse, "💓", "rise", 3.0, "小鹿乱撞"),
    "sneeze":    ("startle", m_sneeze, "💨", "pop", 1.6, "打喷嚏"),
    "tiptoe":    ("idle", m_tiptoe, "👀", "pop", 2.6, "踮脚张望"),
    "dance":     ("hum", m_dance, "🎶", "rise", 4.0, "踩点小舞"),
    "coffee":    ("hum", m_breathe, "☕", "pop", 3.0, "喝杯咖啡"),
    "study":     ("read", m_nod, "📚", "pop", 4.0, "陪你学习"),
    "applaud":   ("jump", m_none, "👏", "rise", 2.2, "鼓掌叫好"),
    "raincloud": ("down", m_droop, "🌧️", "cloud", 3.6, "头顶乌云"),
    "sunshine":  ("wave", m_stretch, "☀️", "cloud", 3.0, "雨过天晴"),
    "shower":    ("special", m_float, "@idle", "snow", 4.0, "专属飘落"),
    "focus":     ("read", m_breathe, "🔥", "cloud", 4.0, "专注燃烧"),
    "moonwalk":  ("hum", m_walk, "🕺", "pop", 2.4, "太空步"),
    "salute":    ("wave", m_yes, "🫡", "pop", 1.8, "收到敬礼"),
}

EMO_ACTION = {"happy": "happy", "love": "love", "shy": "shy", "calm": "talk", "think": "think",
              "curious": "curious", "surprised": "surprised", "worried": "worried", "sad": "sad", "cry": "cry",
              "angry": "angry", "sleepy": "yawn", "proud": "proud", "cheer": "cheer"}


class Particle:
    __slots__ = ("g", "x", "y", "vx", "vy", "age", "life", "size", "style", "ph")

    def __init__(self, g, x, y, vx, vy, life, size, style):
        self.g, self.x, self.y, self.vx, self.vy = g, x, y, vx, vy
        self.age, self.life, self.size, self.style = 0.0, life, size, style
        self.ph = random.random() * TAU


_GLYPHS = {}


def glyph_image(g: str) -> QImage:
    """把表情符号预先画成一张小图并缓存。直接 drawText 彩色 emoji 很慢（每帧几毫秒，首次上百毫秒），
    粒子一多就掉帧；画缓存好的图片只要零点几毫秒。"""
    img = _GLYPHS.get(g)
    if img is None:
        S = 96
        img = QImage(S, S, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        p = QPainter(img)
        f = QFont("Segoe UI Emoji")
        f.setPixelSize(64)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRectF(0, 0, S, S), Qt.AlignmentFlag.AlignCenter, g)
        p.end()
        _GLYPHS[g] = img
    return img


def warm_glyphs(char=None):
    for g in "✨💗💧💦💤💢❗❔💫💨🎵🎶⭐🎉💭❕💡👀☕📚👏☀️🔥🌙🍙💓⭕❌":
        if g.strip() and g != "\ufe0f":
            glyph_image(g)
    glyph_image("🌧️")
    glyph_image("☀️")
    if char:
        for g in char.particles.values():
            glyph_image(g)


class Stage:
    """逻辑画布：宽 W 高 H（素材像素坐标），角色脚底站在 (W/2, FLOOR)。"""
    W, H, FLOOR = 400, 500, 462

    def __init__(self, char):
        self.char = char
        self.bank = ClipBank(char)
        self.action = "idle"
        self.t = 0.0
        self.clip_t = 0.0
        self.particles = []
        self.emit_acc = 0.0
        self.flip = False
        self.lean = 0.0          # 视线追随鼠标的倾斜
        self.extra_rot = 0.0     # 拖拽时的甩动
        self.rng = random.Random()
        self._burst_done = False
        warm_glyphs(char)

    def set_char(self, char):
        self.char = char
        self.bank = ClipBank(char)
        self.particles.clear()
        warm_glyphs(char)

    def set_action(self, name):
        if name not in ACTIONS:
            name = "idle"
        same_clip = ACTIONS[name][0] == ACTIONS[self.action][0] and isinstance(ACTIONS[name][0], str)
        self.action = name
        self.t = 0.0
        if not same_clip:
            self.clip_t = 0.0
        self.emit_acc = 0.0
        self._burst_done = False

    def duration(self, name):
        return ACTIONS.get(name, ACTIONS["idle"])[4]

    # ---------- 更新 ----------
    def advance(self, dt):
        self.t += dt
        self.clip_t += dt
        _, _, glyph, style, _, _ = ACTIONS[self.action]
        if glyph:
            if glyph.startswith("@"):
                glyph = self.char.particles.get(glyph[1:], "✨")
            self._emit(glyph, style, dt)
        for p in self.particles:
            p.age += dt
            if p.style == "orbit":
                p.ph += dt * 5
            else:
                p.x += p.vx * dt
                p.y += p.vy * dt
                if p.style in ("fall", "burst"):
                    p.vy += 260 * dt
        self.particles = [p for p in self.particles if p.age < p.life]

    def _emit(self, g, style, dt):
        r = self.rng
        cx, top = self.W / 2, self.FLOOR - 330
        add = self.particles.append
        if style in ("burst", "pop", "ground", "blush"):
            if self._burst_done:
                return
            self._burst_done = True
            if style == "burst":
                for _ in range(9):
                    a = r.uniform(-math.pi, 0)
                    add(Particle(g, cx + r.uniform(-30, 30), top + 150, math.cos(a) * r.uniform(60, 170),
                                 math.sin(a) * r.uniform(120, 240), r.uniform(0.9, 1.4), r.uniform(18, 28), "burst"))
            elif style == "pop":
                add(Particle(g, cx + 78, top + 40, 0, -14, 1.5, 34, "pop"))
            elif style == "ground":
                for sx in (-1, 1):
                    add(Particle(g, cx + sx * 50, self.FLOOR - 12, sx * 50, -10, 0.5, 22, "ground"))
            elif style == "blush":
                for i in range(3):
                    add(Particle(g, cx - 40 + i * 40, top + 30, 0, -18, 2.2, 20, "pop"))
            return
        if style == "cloud":                 # 悬在头顶的一个大图标
            if not self._burst_done:
                self._burst_done = True
                add(Particle(g, cx, top + 4, 0, 0, (ACTIONS[self.action][4] or 3.0), 46, "cloud"))
            if g.startswith("🌧"):
                self.emit_acc += 7 * dt
                while self.emit_acc >= 1:
                    self.emit_acc -= 1
                    add(Particle("💧", cx + r.uniform(-30, 30), top + 36, 0, 150, 0.55, 11, "drip"))
            return
        rate = {"rise": 2.6, "sweat": 2.2, "fall": 5.0, "zzz": 0.8, "think": 1.1, "orbit": 0.0,
                "snow": 4.0}.get(style, 1.5)
        if style == "snow":
            self.emit_acc += rate * dt
            while self.emit_acc >= 1:
                self.emit_acc -= 1
                add(Particle(g, cx + r.uniform(-150, 150), top - 40, r.uniform(-14, 14), r.uniform(60, 110),
                             r.uniform(2.4, 3.4), r.uniform(15, 24), "rise"))
            return
        if style == "orbit":
            if not self._burst_done:
                self._burst_done = True
                for i in range(4):
                    p = Particle(g, cx, top + 30, 0, 0, 2.6, 24, "orbit")
                    p.ph = i * TAU / 4
                    add(p)
            return
        self.emit_acc += rate * dt
        while self.emit_acc >= 1:
            self.emit_acc -= 1
            if style == "rise":
                add(Particle(g, cx + r.uniform(-90, 90), top + r.uniform(60, 200), r.uniform(-12, 12),
                             r.uniform(-70, -40), r.uniform(1.2, 1.9), r.uniform(18, 28), "rise"))
            elif style == "sweat":
                s = r.choice((-1, 1))
                add(Particle(g, cx + s * r.uniform(60, 85), top + r.uniform(30, 70), s * 40, -50, 0.7, 20, "fall"))
            elif style == "fall":
                s = r.choice((-1, 1))
                add(Particle(g, cx + s * r.uniform(25, 45), top + 120, s * r.uniform(40, 110), -150, 0.8, 16, "fall"))
            elif style == "zzz":
                add(Particle(g, cx + 60, top + 50, 16, -30, 2.6, 22, "zzz"))
            elif style == "think":
                add(Particle(g, cx + 70, top + 40, 6, -22, 1.6, 26, "rise"))

    # ---------- 绘制 ----------
    def _clip(self):
        spec = ACTIONS[self.action][0]
        flip = False
        if callable(spec):                       # 随时间换素材的动作（转身展示）
            spec, flip = spec(self.t)
            try:
                return self.bank.get(spec) if self.bank._path(spec)[0] else self.bank.get("idle"), flip
            except Exception:
                return self.bank.get("idle"), False
        return self.bank.get(spec), flip

    def frame(self):
        clip, _ = self._clip()
        a, b, k = clip.at(self.clip_t * 1000)
        return clip, self._mix(a, b, k)

    def _mix(self, a, b, k):
        """两帧之间线性混合（预乘 alpha 下 A·(1-k) + B·k 是精确的）。"""
        if b is a or k < 0.04:
            return a
        if k > 0.96:
            return b
        buf = getattr(self, "_buf", None)
        if buf is None or buf.size() != a.size():
            buf = self._buf = QImage(a.size(), QImage.Format.Format_ARGB32_Premultiplied)
        buf.fill(0)
        q = QPainter(buf)
        q.setOpacity(1 - k)
        q.drawImage(0, 0, a)
        q.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        q.setOpacity(k)
        q.drawImage(0, 0, b)
        q.end()
        return buf

    def paint(self, p: QPainter, scale=1.0, shadow=True):
        """把舞台画到 painter 上，画布左上角为原点，scale 为整体缩放。"""
        p.save()
        p.scale(scale, scale)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        clip, img = self.frame()
        vflip = self._clip()[1]
        dx, dy, rot, sx, sy = ACTIONS[self.action][1](self.t)
        cx = self.W / 2
        if shadow:
            lift = max(0.0, -dy)
            k = max(0.45, 1 - lift / 160) * min(1.0, abs(sx) + 0.4)
            g = QRadialGradient(QPointF(cx + dx, self.FLOOR - 4), 80 * k)
            g.setColorAt(0, QColor(0, 0, 0, 70))
            g.setColorAt(1, QColor(0, 0, 0, 0))
            p.setBrush(g)
            p.setPen(Qt.PenStyle.NoPen)
            p.save()
            p.translate(cx + dx, self.FLOOR - 4)
            p.scale(1, 0.22)
            p.drawEllipse(QPointF(0, 0), 80 * k, 80 * k)
            p.restore()
        p.save()
        p.translate(cx + dx, self.FLOOR + dy)
        p.rotate(rot + self.lean + self.extra_rot)
        s = clip.scale
        p.scale(sx * s * (-1 if (self.flip != vflip) else 1), sy * s)
        p.drawImage(QRectF(-clip.w / 2, -clip.foot, clip.w, clip.h), img)
        p.restore()
        # 粒子
        for q in self.particles:
            k = q.age / q.life
            alpha = 1.0 if k < 0.6 else max(0.0, 1 - (k - 0.6) / 0.4)
            size = q.size * (0.5 + 0.5 * min(1.0, q.age * 8))
            x, y = q.x, q.y
            if q.style == "orbit":
                x += 62 * math.cos(q.ph)
                y += 16 * math.sin(q.ph)
            elif q.style == "zzz":
                x += 8 * math.sin(q.age * 3 + q.ph)
                size *= 0.7 + k * 0.8
            elif q.style == "rise":
                x += 6 * math.sin(q.age * 4 + q.ph)
            elif q.style == "cloud":
                y += 4 * math.sin(q.age * 2.2)
                alpha = min(1.0, q.age * 4) * (1.0 if k < 0.85 else max(0.0, 1 - (k - 0.85) / 0.15))
            p.setOpacity(alpha)
            d = size * 1.5                       # 缓存图 96px 里字形占 64px
            p.drawImage(QRectF(x - d / 2, y - d / 2, d, d), glyph_image(q.g))
        p.setOpacity(1.0)
        p.restore()

    def body_rect(self, scale=1.0) -> QRectF:
        """角色身体大致所在的矩形（用于鼠标命中）。"""
        return QRectF((self.W / 2 - 105) * scale, (self.FLOOR - 330) * scale, 210 * scale, 330 * scale)
