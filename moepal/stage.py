"""舞台：序列帧 + 程序化动作 + 粒子。桌宠窗口和动图导出工具共用。
Stage = sprite clips + procedural motion + particles. Shared by the pet window and the GIF exporter.

“组合动作”= 一段手绘/生成的序列帧（clip）× 一种程序化运动（motion）× 一种粒子（emit）。
已有 12 段序列帧，这里组合出 30 多个动作，不需要新的美术帧。
"""
import math
import random
import threading
from dataclasses import dataclass

from PIL import Image, ImageSequence
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QRadialGradient

from .config import ASSETS

CLIPS = ["jump", "wipe", "down", "anxious", "daze", "startle", "cry", "wave", "idle", "read", "hum", "special"]
TAU = math.tau


@dataclass
class Clip:
    frames: list
    frame_ms: float
    w: int
    h: int
    foot: int          # 脚底所在的 y（素材像素）
    scale: float       # 把不同批次素材归一到同一身高


class ClipBank:
    """按需解码 WebP 动图并缓存。96 帧的光流补帧动图每 3 帧取 1（≈8fps 关键节奏 + 平滑过渡）。"""

    def __init__(self, char):
        self.char = char
        self.dir = ASSETS / char.id
        self.cache = {}
        self.lock = threading.Lock()

    def resolve(self, key):
        if (self.dir / f"{key}.webp").exists():
            return key
        fb = self.char.fallback.get(key)
        if fb and (self.dir / f"{fb}.webp").exists():
            return fb
        for k in ("idle", "anxious", "wave", "jump"):
            if (self.dir / f"{k}.webp").exists():
                return k
        raise FileNotFoundError(f"assets/{self.char.id} 里没有任何动图，请先运行 tools/import_assets.py")

    def get(self, key) -> Clip:
        key = self.resolve(key)
        with self.lock:
            if key in self.cache:
                return self.cache[key]
        im = Image.open(self.dir / f"{key}.webp")
        n = getattr(im, "n_frames", 1)
        step = 3 if n > 40 else (2 if n > 20 else 1)
        frames, durs, foot = [], [], 0
        for i, fr in enumerate(ImageSequence.Iterator(im)):
            durs.append(fr.info.get("duration", 40) or 40)
            if i % step:
                continue
            rgba = fr.convert("RGBA")
            bb = rgba.getchannel("A").point(lambda a: 255 if a > 40 else 0).getbbox()
            if bb:
                foot = max(foot, bb[3])
            q = QImage(rgba.tobytes(), rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888).copy()
            frames.append(q.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied))
        frame_ms = sum(durs) / max(1, len(frames))
        w, h = im.size
        clip = Clip(frames, frame_ms, w, h, foot or h, 0.86 if h < 400 else 1.0)
        with self.lock:
            self.cache[key] = clip
        return clip

    def preload(self):
        def run():
            for k in CLIPS:
                try:
                    self.get(k)
                except Exception:
                    pass
        threading.Thread(target=run, daemon=True).start()


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

    def set_char(self, char):
        self.char = char
        self.bank = ClipBank(char)
        self.particles.clear()

    def set_action(self, name):
        if name not in ACTIONS:
            name = "idle"
        same_clip = ACTIONS[name][0] == ACTIONS[self.action][0]
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
        rate = {"rise": 2.6, "sweat": 2.2, "fall": 5.0, "zzz": 0.8, "think": 1.1, "orbit": 0.0}.get(style, 1.5)
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
    def frame(self):
        clip = self.bank.get(ACTIONS[self.action][0])
        i = int(self.clip_t * 1000 / clip.frame_ms) % len(clip.frames)
        return clip, clip.frames[i]

    def paint(self, p: QPainter, scale=1.0, shadow=True):
        """把舞台画到 painter 上，画布左上角为原点，scale 为整体缩放。"""
        p.save()
        p.scale(scale, scale)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        clip, img = self.frame()
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
        p.scale(sx * s * (-1 if self.flip else 1), sy * s)
        p.drawImage(QPointF(-clip.w / 2, -clip.foot), img)
        p.restore()
        # 粒子
        f = QFont("Segoe UI Emoji")
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
            f.setPixelSize(max(6, int(size)))
            p.setFont(f)
            p.setOpacity(alpha)
            p.setPen(QColor(255, 255, 255))
            p.drawText(QRectF(x - size, y - size, size * 2, size * 2), Qt.AlignmentFlag.AlignCenter, q.g)
        p.setOpacity(1.0)
        p.restore()

    def body_rect(self, scale=1.0) -> QRectF:
        """角色身体大致所在的矩形（用于鼠标命中）。"""
        return QRectF((self.W / 2 - 105) * scale, (self.FLOOR - 330) * scale, 210 * scale, 330 * scale)
