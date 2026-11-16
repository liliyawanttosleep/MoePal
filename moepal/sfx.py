"""角色音频轨道：用 numpy 合成每个角色专属的提示音与“咿呀语”。
Per-character sound design, synthesized procedurally (no sampled audio, no third-party sounds).

音色族 / timbres
  crystal  (白龙娘)   水晶风铃 + 小龙“啾”叫 + 龙焰气息
  musicbox (大小姐)   八音盒拨弦 + 翻书声
  bubble   (大肥鱼)   水泡“啵” + 鲸歌滑音 + 声呐
"""
import wave
from pathlib import Path

import numpy as np

SR = 44100
# 五声音阶（半音偏移），听起来怎么排都和谐
PENTA = [0, 2, 4, 7, 9, 12, 14, 16, 19, 21, 24]
BASE = {"crystal": 1046.5, "musicbox": 523.25, "bubble": 392.0}

# 事件 → [(音阶级数, 起始秒, 时值秒, 音量)]
EVENTS = {
    "greet":      [(0, 0.00, .35, .8), (2, 0.10, .35, .8), (4, 0.20, .35, .8), (5, 0.32, .7, 1)],
    "show":       [(0, 0.00, .2, .7), (3, 0.07, .2, .7), (5, 0.14, .25, .8), (7, 0.22, .8, 1)],
    "hide":       [(5, 0.00, .25, .8), (3, 0.09, .25, .7), (1, 0.18, .3, .6), (0, 0.28, .6, .5)],
    "poke":       [(4, 0.00, .22, .9)],
    "pet":        [(2, 0.00, .5, .6), (4, 0.16, .5, .6), (5, 0.34, .8, .7)],
    "happy":      [(3, 0.00, .18, .8), (5, 0.07, .18, .8), (3, 0.14, .18, .8), (6, 0.21, .6, 1)],
    "cheer":      [(0, 0.00, .2, .8), (2, 0.08, .2, .8), (4, 0.16, .2, .8), (5, 0.24, .2, .9), (7, 0.34, .9, 1)],
    "sad":        [(4, 0.00, .6, .6), (3, 0.28, .6, .5), (1, 0.60, 1.0, .5)],
    "surprise":   [(1, 0.00, .1, .9), (8, 0.05, .45, 1)],
    "think":      [(2, 0.00, .2, .4), (3, 0.22, .2, .4)],
    "notify":     [(5, 0.00, .5, .9), (3, 0.22, .9, .8)],
    "sleep":      [(4, 0.00, .8, .4), (2, 0.45, .8, .35), (0, 0.95, 1.4, .3)],
    "wake":       [(0, 0.00, .3, .6), (4, 0.14, .6, .8)],
    "angry":      [(0, 0.00, .25, 1), (0, 0.16, .35, 1)],
    "listen_on":  [(3, 0.00, .15, .7), (6, 0.08, .3, .8)],
    "listen_off": [(6, 0.00, .15, .7), (3, 0.08, .3, .7)],
    "send":       [(5, 0.00, .15, .5)],
    "error":      [(1, 0.00, .3, .7), (0, 0.22, .6, .7)],
    "land":       [(0, 0.00, .18, .8)],
}


def _t(dur):
    return np.arange(int(SR * dur)) / SR


def _freq(timbre, degree, shift=0.0):
    return BASE[timbre] * 2 ** ((PENTA[degree % len(PENTA)] + shift) / 12)


def note(timbre, f, dur, minor=False):
    t = _t(dur)
    if timbre == "crystal":      # 非谐泛音的铃 + 微颤
        vib = 1 + 0.003 * np.sin(2 * np.pi * 7 * t)
        y = sum(a * np.sin(2 * np.pi * f * r * t * vib) * np.exp(-t * d)
                for r, a, d in ((1, 1, 5), (2.76, .45, 8), (5.4, .22, 13), (8.93, .1, 20)))
        y *= 1 - np.exp(-t * 900)
    elif timbre == "musicbox":   # 拨弦：谐波衰减快，带一点点木质敲击
        y = sum(a * np.sin(2 * np.pi * f * h * t) * np.exp(-t * d)
                for h, a, d in ((1, 1, 6), (2, .5, 9), (3, .28, 13), (4, .12, 18), (6, .05, 25)))
        click = np.random.default_rng(1).standard_normal(len(t)) * np.exp(-t * 400) * 0.08
        y = y * (1 - np.exp(-t * 1500)) + click
    else:                        # bubble：音高上滑的“啵”
        glide = f * (0.72 + 0.5 * (1 - np.exp(-t * 28)))
        ph = 2 * np.pi * np.cumsum(glide) / SR
        y = (np.sin(ph) + 0.18 * np.sin(2 * ph)) * np.exp(-t * 9) * (1 - np.exp(-t * 600))
    return y


def _mix(parts, total):
    out = np.zeros(int(SR * total) + 1)
    for start, y in parts:
        i = int(SR * start)
        n = min(len(y), len(out) - i)
        out[i:i + n] += y[:n]
    return out


def _noise(dur, seed=0):
    return np.random.default_rng(seed).standard_normal(int(SR * dur))


def _lowpass(x, k):
    kern = np.hanning(k)
    return np.convolve(x, kern / kern.sum(), mode="same")


def extra(timbre, event):
    """角色专属的非旋律层。 Character-specific non-melodic layer."""
    if timbre == "crystal":
        if event in ("poke", "surprise", "greet", "show", "angry"):   # 小龙“啾/嗷”
            dur = .16 if event != "angry" else .3
            t = _t(dur)
            f = np.linspace(520, 1250, len(t)) if event != "angry" else np.linspace(420, 260, len(t))
            ph = 2 * np.pi * np.cumsum(f) / SR
            y = (np.sin(ph) + .5 * np.sin(2 * ph) + .3 * np.sin(3 * ph)) * np.hanning(len(t))
            if event == "angry":
                y *= 1 + .6 * np.sin(2 * np.pi * 38 * t)        # 低吼的粗糙感
            return [(0.0, y * .5)]
        if event in ("happy", "cheer"):                              # 龙焰“呼”
            n = _lowpass(_noise(.5, 3), 40) * np.hanning(int(SR * .5)) * 3
            return [(0.15, n)]
    if timbre == "musicbox":
        if event in ("think", "hide", "show"):                       # 翻书
            n = _noise(.22, 5)
            n = (n - _lowpass(n, 30)) * np.linspace(0, 1, len(n)) ** 2 * np.exp(-_t(.22) * 6)
            return [(0.0, n * .25)]
        if event == "angry":                                         # 合上书“啪”
            n = _lowpass(_noise(.08, 7), 12) * np.exp(-_t(.08) * 60)
            return [(0.0, n * 2.5)]
    if timbre == "bubble":
        if event in ("greet", "show", "sad", "sleep", "hide"):       # 鲸歌
            dur = 1.1
            t = _t(dur)
            up = event in ("greet", "show")
            f = (220 + 160 * np.sin(np.pi * t / dur) ** 2) if up else np.linspace(330, 170, len(t))
            f = f * (1 + .012 * np.sin(2 * np.pi * 5.5 * t))
            ph = 2 * np.pi * np.cumsum(f) / SR
            y = (np.sin(ph) + .35 * np.sin(2 * ph) + .12 * np.sin(3 * ph)) * np.hanning(len(t))
            return [(0.05, y * .35)]
        if event == "notify":                                        # 声呐
            t = _t(.9)
            return [(0.0, np.sin(2 * np.pi * 1320 * t) * np.exp(-t * 5) * .3)]
        if event in ("happy", "cheer", "pet"):                       # 一串小水泡
            rng = np.random.default_rng(11)
            return [(.05 + i * .07, note("bubble", 700 + rng.uniform(0, 900), .12) * .3) for i in range(6)]
    return []


def render_event(timbre, event):
    seq = EVENTS[event]
    minor = event in ("sad", "error", "angry")
    parts = []
    for deg, start, dur, vol in seq:
        shift = -1 if (minor and deg in (1, 4)) else 0
        if event == "angry":
            shift = -12
        parts.append((start, note(timbre, _freq(timbre, deg, shift), dur) * vol))
        if event == "angry":   # 小二度叠音 → 不高兴
            parts.append((start, note(timbre, _freq(timbre, deg, shift + 1), dur) * vol * .8))
    parts += extra(timbre, event)
    total = max(s + len(y) / SR for s, y in parts) + .05
    return _mix(parts, total)


def render_dizzy(timbre):
    t = _t(1.2)
    f = BASE[timbre] * (1 + .25 * np.sin(2 * np.pi * 3.2 * t)) * np.linspace(1.1, .8, len(t))
    ph = 2 * np.pi * np.cumsum(f) / SR
    return (np.sin(ph) + .3 * np.sin(2 * ph)) * np.hanning(len(t)) * .6


def render_purr(timbre):
    t = _t(1.4)
    f0 = {"crystal": 95, "musicbox": 70, "bubble": 60}[timbre]
    y = sum(np.sin(2 * np.pi * f0 * h * t) / h for h in range(1, 6))
    y *= (0.55 + 0.45 * np.sin(2 * np.pi * 24 * t)) * np.hanning(len(t))
    return _lowpass(y, 25) * .5


def render_babble(timbre, f0, idx):
    """一个音节：带共振峰的短促元音。 One 'syllable' of animalese-style speech."""
    vowels = [(800, 1200), (350, 2300), (450, 900), (300, 800), (600, 1700), (500, 1500), (700, 1100), (400, 2000)]
    f1, f2 = vowels[idx % len(vowels)]
    dur = .075
    t = _t(dur)
    f = f0 * 2 ** (PENTA[(idx * 3) % 5] / 12) * (1 + .04 * np.sin(2 * np.pi * 14 * t))
    ph = 2 * np.pi * np.cumsum(f) / SR
    y = np.zeros(len(t))
    for h in range(1, 14):
        fh = f0 * h
        gain = np.exp(-((fh - f1) / 260) ** 2) + .7 * np.exp(-((fh - f2) / 380) ** 2) + .05
        y += gain * np.sin(h * ph) / (h ** .5)
    env = np.minimum(1, t / .008) * np.exp(-t * 16)
    if timbre == "bubble":
        y += note("bubble", f0 * 2.2, dur) * .8
    elif timbre == "crystal":
        y += note("crystal", f0 * 3, dur) * .35
    return y * env


def _write(path: Path, y, peak=0.8):
    y = np.asarray(y, dtype=np.float64)
    m = np.max(np.abs(y)) or 1.0
    y = y / m * peak
    fade = min(200, len(y) // 4)
    y[-fade:] *= np.linspace(1, 0, fade)
    stereo = np.repeat((y * 32767).astype("<i2")[:, None], 2, axis=1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(stereo.tobytes())


VERSION = "3"


def build_all(out_root: Path, characters) -> dict:
    """生成全部音效，返回 {char_id: {event: path}}。带版本戳，改了合成参数才重建。"""
    result = {}
    for c in characters.values():
        d = out_root / c.id
        d.mkdir(parents=True, exist_ok=True)
        stamp = d / "version.txt"
        fresh = stamp.exists() and stamp.read_text() == VERSION
        files = {}
        for ev in EVENTS:
            files[ev] = d / f"{ev}.wav"
            if not fresh:
                _write(files[ev], render_event(c.timbre, ev), .7)
        files["dizzy"] = d / "dizzy.wav"
        files["purr"] = d / "purr.wav"
        if not fresh:
            _write(files["dizzy"], render_dizzy(c.timbre), .6)
            _write(files["purr"], render_purr(c.timbre), .5)
        for i in range(8):
            files[f"babble{i}"] = d / f"babble{i}.wav"
            if not fresh:
                _write(files[f"babble{i}"], render_babble(c.timbre, c.babble_hz, i), .55)
        stamp.write_text(VERSION)
        result[c.id] = files
    return result
