"""角色 PV 合成器：分镜脚本（tools/pv_scripts.py）→ 配音 + 画面 + 字幕 + 配乐 → 1080p MP4。
Character PV composer: script → CosyVoice narration + Qt-rendered frames + subtitles + synthesized BGM → MP4.

  .venv\\Scripts\\python tools\\make_pv.py gpt [--preview 30] [--target 300]
输出：data/pv/<角色>/<角色>_PV.mp4（并复制到桌面素材文件夹的 角色PV\\）

画面全部由程序绘制：角色用桌宠同一套渲染（序列帧 + 三维转身 + 交叉淡入 + 粒子），
背景、字幕、资料卡、立绘推拉镜头、桌面窗口用 QPainter 画。没有新画的手绘帧。
配乐：numpy 合成（每位角色自己的音色：水晶铃 / 八音盒 / 水泡），人声出现时自动压低。
"""
import hashlib
import json
import math
import random
import subprocess
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402
from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QGuiApplication, QImage, QLinearGradient, QPainter,  # noqa: E402
                           QPainterPath, QPen, QRadialGradient)

from moepal import sfx, voice  # noqa: E402
from moepal.characters import CHARACTERS  # noqa: E402
from moepal.stage import ACTIONS, EMO_ACTION, Stage  # noqa: E402
from pv_scripts import PV  # noqa: E402

W, H, FPS, SR = 1920, 1080, 30, 44100
FONT = "Microsoft YaHei UI"
FFMPEG = voice.find_ffmpeg() or "ffmpeg"
GAP = 0.55                 # 两句台词之间的基本停顿
CARD = 2.6                 # 章节标题卡


# ============================================================ 配音
def narrate(cid, out_dir):
    """逐句合成（有缓存）。返回 {文本: (wav 路径, 时长秒)}。"""
    spec = next(v for v in CHARACTERS[cid].voices if v["engine"] == "cosy")
    pw, pt = voice.ensure_prompt(cid, spec, 1)
    res = {}
    lines = []
    for sc in PV[cid]["scenes"]:
        lines += [(e, t) for e, t in sc.get("lines", [])]
        lines += [(it[2], it[3]) for it in sc.get("items", [])]
    for k, (emo, text) in enumerate(lines):
        p = out_dir / f"vo_{hashlib.md5(f'{emo}|{text}'.encode('utf-8')).hexdigest()[:12]}.wav"   # 稳定的缓存名
        if not p.exists():
            t = time.time()
            voice.COSY.synth(text, pw, pt, p, speed=spec["speed"], emo=emo)
            print(f"  配音 {k + 1}/{len(lines)} {time.time() - t:4.1f}s  [{emo}] {text}", flush=True)
        with wave.open(str(p)) as w:
            res[(emo, text)] = (p, w.getnframes() / w.getframerate())
    voice.COSY.close()
    return res


# ============================================================ 时间轴
def timeline(cid, vo, target):
    """把分镜排成时间轴：段落 [{kind, t0, t1, ...}]，台词事件 [{t0, t1, text, emo, action, who}]。"""
    segs, events, cards = [], [], []
    t = 0.0
    for sc in PV[cid]["scenes"]:
        kind = sc["kind"]
        seg = {"kind": kind, "t0": t, "sc": sc}
        if kind == "open":
            t += 9.0
        elif kind == "end":
            t += 14.0
        else:
            cards.append((t, sc["chapter"]))
            t += CARD
            if kind == "scenes":
                for title, act, emo, text in sc["items"]:
                    cards.append((t, "§" + title))       # 小标题
                    t += 1.6
                    p, d = vo[(emo, text)]
                    events.append({"t0": t, "t1": t + d, "text": text, "emo": emo, "action": act, "wav": p})
                    t += d + 1.4
            else:
                acts = sc.get("actions") or []
                for k, (emo, text) in enumerate(sc["lines"]):
                    p, d = vo[(emo, text)]
                    act = acts[k] if k < len(acts) else EMO_ACTION.get(emo, "talk")
                    events.append({"t0": t, "t1": t + d, "text": text, "emo": emo, "action": act, "wav": p,
                                   "idx": k})
                    t += d + GAP
            t += 1.2
        seg["t1"] = t
        segs.append(seg)
    # 拉长到目标时长：把多出来的时间均匀加到每句之后（每处最多 2.4 秒），再给开场、片尾
    extra = target - t
    if extra > 0:
        n = len(events)
        per = min(2.4, extra * 0.8 / max(1, n))
        shift = 0.0
        ev_i = 0
        for seg in segs:
            seg["t0"] += shift
            if seg["kind"] == "open":
                add = max(0.0, (extra - per * n) * 0.35)
                shift += add
            for e in events:
                pass
            # 段内的事件整体右移，并在每句后加 per
            while ev_i < n and events[ev_i]["t0"] < seg["t1"]:
                events[ev_i]["t0"] += shift
                events[ev_i]["t1"] += shift
                shift += per
                ev_i += 1
            if seg["kind"] == "end":
                shift += max(0.0, (extra - per * n) * 0.65)
            seg["t1"] += shift
        # 章节卡跟着平移
        new_cards = []
        for tc, name in cards:
            off = 0.0
            for seg in segs:
                pass
            new_cards.append((tc, name))
        cards = remap_cards(cards, segs, events)
    return segs, events, cards


def remap_cards(cards, segs, events):
    """章节卡放在每段开头；小标题放在对应台词之前 1.6 秒。"""
    out = []
    ev = [e for e in events]
    sub_events = iter([e for e in ev if e.get("idx") is None])
    for seg in segs:
        if seg["kind"] in ("open", "end"):
            continue
        out.append((seg["t0"], seg["sc"]["chapter"]))
    for e in ev:
        if e.get("idx") is None:
            title = next(it[0] for sc in [s["sc"] for s in segs if s["kind"] == "scenes"] for it in sc["items"]
                         if it[3] == e["text"])
            out.append((e["t0"] - 1.6, "§" + title))
    return sorted(out)


# ============================================================ 配乐
PROG = {   # 每位角色的和弦进行（半音，相对主音）与速度
    "gpt": (60, 100, [(0, 4, 7), (7, 11, 14), (9, 12, 16), (5, 9, 12)], "crystal"),
    "claude": (53, 72, [(5, 9, 12), (7, 11, 14), (4, 7, 11), (9, 12, 16)], "musicbox"),
    "deepseek": (55, 88, [(0, 4, 7), (9, 12, 16), (5, 9, 12), (7, 11, 14)], "bubble"),
}
INTENSITY = {"open": 0.55, "intro": 0.8, "profile": 0.8, "story": 0.5, "scenes": 1.0, "friends": 0.9, "you": 0.7,
             "end": 0.6}


def midi(n):
    return 440.0 * 2 ** ((n - 69) / 12)


def bgm(cid, segs, total):
    root, bpm, prog, timbre = PROG[cid]
    beat = 60 / bpm
    bar = beat * 4
    n = int(total * SR) + SR
    L = np.zeros(n, np.float32)
    R = np.zeros(n, np.float32)
    rng = np.random.default_rng(7)

    def inten(t):
        for s in segs:
            if s["t0"] <= t < s["t1"]:
                return INTENSITY[s["kind"]], s["kind"]
        return 0.5, "end"

    def add(sig, t, pan=0.0, gain=1.0):
        i = int(t * SR)
        if i >= n:
            return
        j = min(n, i + len(sig))
        s = sig[: j - i] * gain
        L[i:j] += s * (1 - pan) * 0.5 * 2
        R[i:j] += s * (1 + pan) * 0.5 * 2

    sfx_sr = sfx.SR
    k = 0
    t = 0.0
    while t < total:
        chord = prog[k % len(prog)]
        g, kind = inten(t)
        # 铺底：三个和弦音的柔和正弦，慢起慢收
        tt = np.arange(int(bar * SR)) / SR
        env = np.minimum(1, tt / 0.6) * np.minimum(1, (bar - tt) / 0.6)
        pad = sum(np.sin(2 * np.pi * midi(root + c - 12) * tt * (1 + d)) for c in chord for d in (-0.002, 0.002))
        add((pad * env * 0.05).astype(np.float32), t, 0.0, g)
        # 低音
        for b in range(4):
            tb = np.arange(int(beat * SR)) / SR
            f0 = midi(root + chord[0] - 24)
            bass = (np.sin(2 * np.pi * f0 * tb) + 0.3 * np.sin(4 * np.pi * f0 * tb)) * np.exp(-tb * 3)
            add((bass * 0.10).astype(np.float32), t + b * beat, 0.0, g)
        # 角色音色的琶音（八分音符）
        notes = [chord[0], chord[1], chord[2], chord[1] + 12, chord[2], chord[1], chord[0] + 12, chord[2]]
        for e, nn in enumerate(notes):
            if kind == "story" and e % 2:
                continue
            y = sfx.note(timbre, midi(root + nn + 12), 0.9)
            y = np.interp(np.arange(int(len(y) * SR / sfx_sr)) * sfx_sr / SR, np.arange(len(y)), y).astype(np.float32)
            add(y * 0.07, t + e * beat / 2, pan=0.35 * math.sin(e), gain=g)
        # 轻打击（小剧场、介绍段更明显；故事段没有）
        if kind not in ("story", "open", "end") and cid != "claude":
            for b in range(4):
                tk = np.arange(int(0.25 * SR)) / SR
                if b % 2 == 0:
                    kick = np.sin(2 * np.pi * (50 + 70 * np.exp(-tk * 30)) * tk) * np.exp(-tk * 14)
                    add((kick * 0.16).astype(np.float32), t + b * beat, 0.0, g)
                th = np.arange(int(0.06 * SR)) / SR
                hat = rng.standard_normal(len(th)) * np.exp(-th * 60)
                hat = hat - np.convolve(hat, np.ones(6) / 6, "same")
                add((hat * 0.05).astype(np.float32), t + b * beat + beat / 2, 0.3, g)
        elif kind not in ("open", "end"):                # 大小姐：轻轻的沙锤
            for b in range(8):
                th = np.arange(int(0.08 * SR)) / SR
                sh = rng.standard_normal(len(th)) * np.exp(-th * 40)
                sh = sh - np.convolve(sh, np.ones(8) / 8, "same")
                add((sh * 0.025).astype(np.float32), t + b * beat / 2, -0.2, g)
        t += bar
        k += 1
    return L[:int(total * SR)], R[:int(total * SR)]


def read_wav_mono(p):
    with wave.open(str(p)) as w:
        sr, ch = w.getframerate(), w.getnchannels()
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768
    if ch == 2:
        x = x.reshape(-1, 2).mean(1)
    if sr != SR:
        x = np.interp(np.arange(int(len(x) * SR / sr)) * sr / SR, np.arange(len(x)), x).astype(np.float32)
    return x


def mixdown(cid, segs, events, cards, total, out):
    L, R = bgm(cid, segs, total)
    vo = np.zeros_like(L)
    for e in events:
        x = read_wav_mono(e["wav"])
        i = int(e["t0"] * SR)
        j = min(len(vo), i + len(x))
        vo[i:j] += x[: j - i]
    chime = read_wav_mono(ROOT / "data" / "cache" / "sfx" / cid / "show.wav")
    for tc, name in cards:
        if not name.startswith("§"):
            i = int(tc * SR)
            j = min(len(vo), i + len(chime))
            L[i:j] += chime[: j - i] * 0.25
            R[i:j] += chime[: j - i] * 0.25
    # 人声出现时把音乐压低（平滑的包络）
    env = np.abs(vo)
    k = int(0.25 * SR)
    cs = np.concatenate([[0.0], np.cumsum(env, dtype=np.float64)])     # 滑动平均（累加和，O(N)）
    idx = np.arange(len(env))
    lo, hi = np.clip(idx - k // 2, 0, len(env)), np.clip(idx + k // 2, 0, len(env))
    env = ((cs[hi] - cs[lo]) / np.maximum(1, hi - lo)).astype(np.float32)
    duck = 1 - 0.62 * np.clip(env / 0.03, 0, 1)
    fade = np.ones_like(L)
    fi = int(2.0 * SR)
    fade[:fi] = np.linspace(0, 1, fi)
    fade[-int(5 * SR):] = np.linspace(1, 0, int(5 * SR))
    L = (L * duck + vo) * fade
    R = (R * duck + vo) * fade
    peak = max(np.abs(L).max(), np.abs(R).max(), 1e-3)
    st = np.stack([L, R], 1) / peak * 0.89
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((st * 32767).astype("<i2").tobytes())


# ============================================================ 画面
def qimg(path, max_h=None):
    im = Image.open(path).convert("RGBA")
    if max_h and im.height > max_h:
        im = im.resize((round(im.width * max_h / im.height), max_h), Image.LANCZOS)
    q = QImage(im.tobytes(), im.width, im.height, im.width * 4, QImage.Format.Format_RGBA8888).copy()
    return q.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


def ease(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def fade_in_out(t, t0, t1, a=0.4, b=0.4):
    return ease((t - t0) / a) * ease((t1 - t) / b)


class Painter:
    def __init__(self, cid, segs, events, cards, total):
        self.cid, self.segs, self.events, self.cards, self.total = cid, segs, events, cards, total
        self.char = CHARACTERS[cid]
        self.col = QColor(self.char.color)
        self.stages = {c: Stage(CHARACTERS[c]) for c in CHARACTERS}
        for s in self.stages.values():
            s.set_action("idle")
        self.main = self.stages[cid]
        self.illust = qimg(ROOT / "assets" / cid / "illust.webp", 1500)
        self.rng = random.Random(3)
        self.bokeh = [(self.rng.random(), self.rng.random(), self.rng.uniform(30, 140), self.rng.uniform(0.02, 0.07),
                       self.rng.random() * 6.28) for _ in range(26)]
        self.cur_action = {}
        self.has3d = self.main.bank._path("turn360")[0] is not None
        others = [c for c in CHARACTERS if c != cid]
        self.friends = others

    # ---------- 工具 ----------
    def seg_at(self, t):
        for s in self.segs:
            if s["t0"] <= t < s["t1"]:
                return s
        return self.segs[-1]

    def event_at(self, t):
        for e in self.events:
            if e["t0"] - 0.15 <= t < e["t1"] + 0.6:
                return e
        return None

    def set_act(self, stage_key, name):
        if self.cur_action.get(stage_key) != name:
            self.cur_action[stage_key] = name
            self.stages[stage_key].set_action(name)

    def draw_char(self, p, key, x, y, k, alpha=1.0):
        """把 key 角色的舞台画到 (x 中心, y 脚底)，k 为缩放。"""
        st = self.stages[key]
        p.save()
        p.setOpacity(alpha)
        p.translate(x - Stage.W / 2 * k, y - Stage.FLOOR * k)
        st.paint(p, k, shadow=True)
        p.restore()

    def text(self, p, s, rect, size, color, bold=False, align=Qt.AlignmentFlag.AlignCenter, outline=0, alpha=1.0):
        f = QFont(FONT)
        f.setPixelSize(size)
        f.setBold(bold)
        p.save()
        p.setOpacity(alpha)
        p.setFont(f)
        if outline:
            path = QPainterPath()
            fm = QFontMetricsF(f)
            lines = s.split("\n")
            lh = fm.height() * 1.15
            y0 = rect.center().y() - lh * len(lines) / 2 + fm.ascent()
            for i, ln in enumerate(lines):
                w = fm.horizontalAdvance(ln)
                x0 = rect.center().x() - w / 2 if align & Qt.AlignmentFlag.AlignHCenter else rect.left()
                path.addText(QPointF(x0, y0 + i * lh), f, ln)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.strokePath(path, QPen(QColor(0, 0, 0, 170), outline, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                    Qt.PenJoinStyle.RoundJoin))
            p.fillPath(path, color)
        else:
            p.setPen(color)
            p.drawText(rect, int(align) | int(Qt.TextFlag.TextWordWrap), s)
        p.restore()

    # ---------- 背景 ----------
    def background(self, p, t, kind):
        c = self.col
        dark = QColor.fromHslF(c.hslHueF(), min(0.55, c.hslSaturationF()), 0.12)
        mid = QColor.fromHslF(c.hslHueF(), min(0.5, c.hslSaturationF()), 0.30 if kind != "story" else 0.20)
        g = QLinearGradient(0, 0, W * (0.6 + 0.2 * math.sin(t * 0.05)), H)
        g.setColorAt(0, mid)
        g.setColorAt(1, dark)
        p.fillRect(0, 0, W, H, g)
        p.setPen(Qt.PenStyle.NoPen)
        for (bx, by, r, a, ph) in self.bokeh:
            x = (bx * W + 30 * math.sin(t * 0.13 + ph)) % W
            y = (by * H - t * 9 * (0.5 + a * 6)) % (H + 200) - 100
            rg = QRadialGradient(QPointF(x, y), r)
            cc = QColor(c)
            cc.setAlphaF(a)
            rg.setColorAt(0, cc)
            cc.setAlphaF(0)
            rg.setColorAt(1, cc)
            p.setBrush(rg)
            p.drawEllipse(QPointF(x, y), r, r)
        vg = QRadialGradient(QPointF(W / 2, H / 2), W * 0.75)
        vg.setColorAt(0.6, QColor(0, 0, 0, 0))
        vg.setColorAt(1, QColor(0, 0, 0, 150))
        p.setBrush(vg)
        p.drawRect(0, 0, W, H)

    def subtitle(self, p, t):
        e = self.event_at(t)
        if not e:
            return
        a = fade_in_out(t, e["t0"] - 0.15, e["t1"] + 0.6, 0.25, 0.35)
        if a <= 0:
            return
        p.save()
        p.setOpacity(a * 0.55)
        band = QLinearGradient(0, H - 210, 0, H)
        band.setColorAt(0, QColor(0, 0, 0, 0))
        band.setColorAt(1, QColor(0, 0, 0, 200))
        p.fillRect(0, H - 210, W, 210, band)
        p.restore()
        txt = e["text"]
        if len(txt) > 26:                                   # 长句折成两行
            cut = max((txt.rfind(ch, 0, len(txt) // 2 + 6) for ch in "，。！？、…"), default=-1)
            cut = cut + 1 if cut > 6 else len(txt) // 2
            txt = txt[:cut] + "\n" + txt[cut:]
        self.text(p, txt, QRectF(80, H - 175, W - 160, 130), 40, QColor(255, 255, 255), True, outline=6, alpha=a)

    def card(self, p, t):
        for tc, name in self.cards:
            if name.startswith("§"):
                d = 1.6 + (self.event_after(tc + 1.6) or 0)
                if tc <= t < tc + d:
                    a = fade_in_out(t, tc, tc + d, 0.3, 0.4)
                    self.text(p, name[1:], QRectF(0, 70, W, 90), 52, QColor(255, 255, 255), True, outline=7, alpha=a)
                    p.save()
                    p.setOpacity(a)
                    p.setPen(QPen(self.col, 4))
                    w = 120 * ease((t - tc) / 0.5)
                    p.drawLine(QPointF(W / 2 - w, 168), QPointF(W / 2 + w, 168))
                    p.restore()
            elif tc <= t < tc + CARD:
                a = fade_in_out(t, tc, tc + CARD, 0.45, 0.55)
                p.save()
                p.setOpacity(a * 0.6)
                p.fillRect(0, 0, W, H, QColor(0, 0, 0))
                p.restore()
                k = ease((t - tc) / 0.8)
                self.text(p, name, QRectF(0, H / 2 - 70, W, 110), 86, QColor(255, 255, 255), True, outline=8, alpha=a)
                p.save()
                p.setOpacity(a)
                p.setPen(QPen(self.col, 5))
                p.drawLine(QPointF(W / 2 - 260 * k, H / 2 + 62), QPointF(W / 2 + 260 * k, H / 2 + 62))
                p.restore()
                self.text(p, self.char.name, QRectF(0, H / 2 + 80, W, 50), 30, QColor(255, 255, 255, 200), alpha=a)

    def event_after(self, t):
        for e in self.events:
            if abs(e["t0"] - t) < 0.05:
                return e["t1"] - e["t0"]
        return None

    # ---------- 各段落 ----------
    def frame(self, p, t):
        seg = self.seg_at(t)
        kind = seg["kind"]
        self.background(p, t, kind)
        e = self.event_at(t)
        lt = t - seg["t0"]
        if kind == "open":
            self.set_act(self.cid, "turn" if lt < 2.4 + 2.0 else "idle")
            k = 2.3 * (0.6 + 0.4 * ease(lt / 1.8))
            self.draw_char(p, self.cid, W / 2, H * 0.86, k, ease(lt / 1.2))
            a = fade_in_out(t, seg["t0"] + 3.0, seg["t1"], 0.9, 0.6)
            self.text(p, "MoePal 萌伴 · 角色 PV", QRectF(0, 90, W, 50), 30, QColor(255, 255, 255, 210), alpha=a)
            self.text(p, PV[self.cid]["title"], QRectF(0, 140, W, 120), 96, QColor(255, 255, 255), True, outline=9, alpha=a)
            self.text(p, PV[self.cid]["subtitle"], QRectF(0, 262, W, 60), 38, QColor(255, 255, 255, 230), outline=5, alpha=a)
        elif kind == "intro":
            if e:
                self.set_act(self.cid, e["action"])
            self.draw_char(p, self.cid, W * 0.5, H * 0.86, 2.4)
            a = fade_in_out(t, seg["t0"] + CARD, seg["t1"], 0.6, 0.6)
            p.save()
            p.setOpacity(a)
            p.setBrush(QColor(255, 255, 255, 30))
            p.setPen(QPen(self.col, 3))
            p.drawRoundedRect(QRectF(90, 120, 520, 150), 24, 24)
            p.restore()
            self.text(p, self.char.name, QRectF(120, 135, 480, 70), 54, QColor(255, 255, 255), True,
                      Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, alpha=a)
            self.text(p, self.char.tagline, QRectF(120, 200, 480, 60), 24, QColor(255, 255, 255, 220), False,
                      Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, alpha=a)
        elif kind == "profile":
            self.set_act(self.cid, "turn" if (int(lt / 6) % 2 == 0) else ("idle" if not e else EMO_ACTION.get(e["emo"], "talk")))
            self.draw_char(p, self.cid, W * 0.28, H * 0.88, 2.25)
            rows = self.char.profile
            span = max(1.0, seg["t1"] - seg["t0"] - CARD - 2)
            shown = int(len(rows) * ease((lt - CARD) / span) + 0.999)
            panel = QRectF(W * 0.52, 150, W * 0.42, 700)
            a = fade_in_out(t, seg["t0"] + CARD, seg["t1"], 0.6, 0.6)
            p.save()
            p.setOpacity(a)
            p.setBrush(QColor(255, 255, 255, 34))
            p.setPen(QPen(self.col, 3))
            p.drawRoundedRect(panel, 28, 28)
            p.restore()
            self.text(p, "角色资料", QRectF(panel.left() + 40, panel.top() + 24, 400, 60), 40, QColor(255, 255, 255), True,
                      Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, alpha=a)
            for i, (kk, vv) in enumerate(rows[:shown]):
                y = panel.top() + 105 + i * 64
                ra = a * ease((lt - CARD - span * i / len(rows)) / 0.5)
                self.text(p, kk, QRectF(panel.left() + 40, y, 150, 56), 28, QColor(self.col).lighter(150), True,
                          Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, alpha=ra)
                self.text(p, vv, QRectF(panel.left() + 190, y, panel.width() - 220, 56), 28, QColor(255, 255, 255), False,
                          Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, alpha=ra)
        elif kind == "story":
            dur = seg["t1"] - seg["t0"]
            z = 1.0 + 0.10 * lt / dur
            ih = 800 * z
            iw = ih * self.illust.width() / self.illust.height()
            frame = QRectF(W * 0.58, 70, 560, 800)          # 下沿留给字幕
            a = fade_in_out(t, seg["t0"] + CARD * 0.6, seg["t1"], 1.0, 0.8)
            p.save()
            p.setOpacity(a)
            path = QPainterPath()
            path.addRoundedRect(frame, 30, 30)
            p.setClipPath(path)
            pan = (iw - frame.width()) * (0.5 + 0.4 * math.sin(lt / dur * math.pi - math.pi / 2))
            p.drawImage(QRectF(frame.left() - pan, frame.top() - (ih - frame.height()) * 0.3, iw, ih), self.illust)
            p.setClipping(False)
            p.setPen(QPen(self.col, 4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(frame, 30, 30)
            p.restore()
            if e:
                self.set_act(self.cid, e["action"])
            self.draw_char(p, self.cid, W * 0.27, H * 0.88, 2.1)
        elif kind == "scenes":
            if e:
                self.set_act(self.cid, e["action"])
            else:
                self.set_act(self.cid, "idle")
            self.draw_char(p, self.cid, W / 2, H * 0.88, 2.4)
        elif kind == "friends":
            idx = e.get("idx", 0) if e else 0
            focus = 0 if idx in (0, len(seg["sc"]["lines"]) - 1) else (1 if idx == 1 else 2)
            if focus == 1 and len(seg["sc"]["lines"]) >= 5 and idx == 2:
                focus = 1
            pos = {self.cid: (W * 0.5, 2.05), self.friends[0]: (W * 0.2, 1.6), self.friends[1]: (W * 0.8, 1.6)}
            who = [self.cid, self.friends[0], self.friends[1]][focus]
            for key, (x, k) in pos.items():
                act = "wave" if key == who and key != self.cid else ("talk" if key == self.cid and e else "idle")
                self.set_act(key, act)
                self.draw_char(p, key, x, H * 0.86, k * (1.08 if key == who else 1.0),
                               fade_in_out(t, seg["t0"] + CARD * 0.7, seg["t1"], 0.8, 0.6) * (1.0 if key == who else 0.75))
                if key != self.cid:
                    self.text(p, CHARACTERS[key].name, QRectF(x - 200, H * 0.86 + 6, 400, 40), 26,
                              QColor(255, 255, 255, 220), outline=4,
                              alpha=fade_in_out(t, seg["t0"] + CARD, seg["t1"], 0.8, 0.6))
            for st in self.stages.values():
                pass
        elif kind == "you":
            a = fade_in_out(t, seg["t0"] + CARD * 0.6, seg["t1"], 0.8, 0.8)
            win = QRectF(W * 0.12, 110, W * 0.76, 760)
            p.save()
            p.setOpacity(a)
            p.setBrush(QColor(250, 250, 252, 235))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(win, 22, 22)
            p.setBrush(QColor(230, 232, 240))
            p.drawRoundedRect(QRectF(win.left(), win.top(), win.width(), 52), 22, 22)
            p.fillRect(QRectF(win.left(), win.top() + 30, win.width(), 22), QColor(230, 232, 240))
            for i, cc in enumerate(("#ff5f57", "#febc2e", "#28c840")):
                p.setBrush(QColor(cc))
                p.drawEllipse(QPointF(win.left() + 30 + i * 26, win.top() + 26), 8, 8)
            p.setBrush(QColor(210, 214, 224))
            for i in range(9):
                w_ = 600 - (i * 53) % 260
                p.drawRoundedRect(QRectF(win.left() + 60, win.top() + 110 + i * 56, w_, 18), 9, 9)
            p.fillRect(QRectF(0, H - 64, W, 64), QColor(20, 22, 30, 220))
            p.restore()
            if e:
                self.set_act(self.cid, e["action"])
            self.draw_char(p, self.cid, win.right() - 230, win.bottom() - 10, 1.25, a)
            if e and a > 0.1:
                bw = 640
                b = QRectF(win.right() - 230 - bw / 2 - 120, win.bottom() - 640, bw, 120)
                ba = a * fade_in_out(t, e["t0"] - 0.15, e["t1"] + 0.6, 0.25, 0.35)
                p.save()
                p.setOpacity(ba)
                p.setBrush(QColor(255, 253, 250))
                p.setPen(QPen(self.col, 3))
                p.drawRoundedRect(b, 22, 22)
                p.restore()
                self.text(p, e["text"], b.adjusted(24, 10, -24, -10), 30, QColor(40, 36, 50), alpha=ba)
            return                                         # 这一段用气泡，不另外画字幕
        elif kind == "end":
            self.set_act(self.cid, "wave" if lt < 4 else "idle")
            self.draw_char(p, self.cid, W * 0.72, H * 0.9, 2.1)
            a = fade_in_out(t, seg["t0"], seg["t1"] + 10, 1.0, 1.0)
            self.text(p, PV[self.cid]["title"], QRectF(140, 170, 900, 100), 70, QColor(255, 255, 255), True,
                      Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, outline=7, alpha=a)
            self.text(p, "双击我，或者按 Ctrl+Alt+M 叫我出来", QRectF(140, 275, 900, 60), 34, QColor(255, 255, 255, 230),
                      False, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, alpha=a)
            credits = ("角色形象：社区二创（ai-moe-atlas、ZipZipPipe 等），署名 · 非商业",
                       "配音：Fun-CosyVoice3（阿里通义，Apache-2.0）；参考音色由微软合成声线生成",
                       "三维：Hunyuan3D-2mv（腾讯）；补帧：RIFE；超分：Real-ESRGAN",
                       "画面与配乐：MoePal 萌伴程序生成 · github.com/liliyawanttosleep/MoePal")
            for i, ln in enumerate(credits):
                self.text(p, ln, QRectF(140, 420 + i * 52, 1100, 46), 24, QColor(255, 255, 255, 190), False,
                          Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                          alpha=a * ease((lt - 1.5 - i * 0.6) / 0.8))
        self.subtitle(p, t)

    def advance(self, dt):
        for s in self.stages.values():
            s.advance(dt)


# ============================================================ 主流程
def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    cid = args[0] if args else "gpt"
    preview = float(sys.argv[sys.argv.index("--preview") + 1]) if "--preview" in sys.argv else None
    target = float(sys.argv[sys.argv.index("--target") + 1]) if "--target" in sys.argv else 300.0
    out_dir = ROOT / "data" / "pv" / cid
    out_dir.mkdir(parents=True, exist_ok=True)
    app = QGuiApplication(sys.argv)  # noqa: F841
    print(f"== {cid}: 配音", flush=True)
    vo = narrate(cid, out_dir)
    segs, events, cards = timeline(cid, vo, target)
    total = segs[-1]["t1"]
    print(f"== 时间轴：{total:.1f} 秒，{len(events)} 句台词", flush=True)
    (out_dir / "timeline.json").write_text(json.dumps(
        {"total": total, "segs": [{k: s[k] for k in ("kind", "t0", "t1")} for s in segs],
         "events": [{k: e[k] for k in ("t0", "t1", "text", "emo", "action")} for e in events]},
        ensure_ascii=False, indent=1), encoding="utf-8")
    length = preview or total
    wav = out_dir / "mix.wav"
    print("== 配乐与混音", flush=True)
    mixdown(cid, segs, events, cards, total, wav)
    name = f"{cid}_PV{'_preview' if preview else ''}.mp4"
    mp4 = out_dir / name
    cmd = [FFMPEG, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
           "-i", "-", "-i", str(wav), "-t", f"{length:.2f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(mp4)]
    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    pt = Painter(cid, segs, events, cards, total)
    img = QImage(W, H, QImage.Format.Format_RGB888)
    n = int(length * FPS)
    t0 = time.time()
    print(f"== 画面：{n} 帧", flush=True)
    for i in range(n):
        t = i / FPS
        pt.advance(1 / FPS)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        pt.frame(p, t)
        if t < 1.5:
            p.fillRect(0, 0, W, H, QColor(0, 0, 0, int(255 * (1 - ease(t / 1.5)))))
        if t > length - 3:
            p.fillRect(0, 0, W, H, QColor(0, 0, 0, int(255 * ease((t - (length - 3)) / 3))))
        p.end()
        ff.stdin.write(bytes(img.constBits())[: W * H * 3] if img.bytesPerLine() == W * 3 else
                       b"".join(bytes(img.scanLine(y))[: W * 3] for y in range(H)))
        if i % (FPS * 20) == 0:
            print(f"  {t:6.1f}s / {length:.0f}s  ({(time.time() - t0) / max(1, i) * 1000:.0f} ms/帧)", flush=True)
            if i in (FPS * 20, FPS * 60, FPS * 120):
                img.save(str(out_dir / f"frame_{int(t)}s.png"))
    ff.stdin.close()
    ff.wait()
    print(f"== 完成：{mp4}（{mp4.stat().st_size / 1e6:.1f} MB，用时 {time.time() - t0:.0f}s）", flush=True)


if __name__ == "__main__":
    main()
