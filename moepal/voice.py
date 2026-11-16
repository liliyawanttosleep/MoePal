"""说话：把一段话拆成短句，逐句给不同的语速 / 音高 / 音量和停顿，再按顺序播放。
Speech with phrasing: split a reply into clauses, give each its own rate / pitch / volume and pause.

之前整段用同一个语速音高一口气念完，听起来像播音腔。现在：
  感叹句更快更高更响，疑问句句尾上扬，省略号拖慢并留白，语气词单独成句，
  第一句略高、最后一句略慢略低（自然的收尾），每句再加一点随机起伏。

引擎 / engines
  edge    微软在线神经语音（edge-tts），自然度高，需要联网
  kokoro  本地 Kokoro v1.1 多语种模型（sherpa-onnx），离线，约 100 个中文声线
"""
import asyncio
import hashlib
import random
import re
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer

from .config import CACHE, DATA

KOKORO_DIR = DATA / "models" / "kokoro-multi-lang-v1_1"
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")
_KAOMOJI = re.compile(r"[（(][^（()）]{0,12}[▽ω∀﹏╯°□╰´`≧≦ﾟДд・^_><;TT*]+[^（()）]{0,12}[)）]")
_CLAUSE = re.compile(r"[^。！？!?…~～\n；;]+[。！？!?…~～；;]*|[。！？!?…~～]+")


@dataclass
class Seg:
    text: str
    rate: int        # 相对语速 %
    pitch: int       # 相对音高 Hz
    vol: int         # 相对音量 %
    gap: int         # 说完后的停顿 ms
    chars: int = 0


def clean(text: str) -> str:
    t = _KAOMOJI.sub("", _EMOJI.sub("", text))
    return re.sub(r"[*_#`>]+", "", t).strip()


def plan(text, base_rate, base_pitch, valence=0.0, energy=0.0, seed=None):
    rng = random.Random(seed if seed is not None else hash(text))
    raw = [c.strip() for c in _CLAUSE.findall(clean(text)) if c.strip()]
    parts = []
    for c in raw:
        if len(c) > 28:                       # 太长的句子在逗号处再分一次气口
            parts += [x for x in re.findall(r"[^，,、]+[，,、]?", c) if x.strip()]
        else:
            parts.append(c)
    merged = []
    for c in parts:                           # 纯标点、单字并回上一句
        if merged and (len(re.sub(r"\W", "", c)) < 2 or len(merged[-1]) < 4 and not re.search(r"[。！？!?…]$", merged[-1])):
            merged[-1] += c
        else:
            merged.append(c)
    segs = []
    n = len(merged)
    for i, c in enumerate(merged):
        rate = base_rate + energy * 8 + (-8 if valence < -0.35 else 0)
        pitch = base_pitch + valence * 6
        vol, gap = 0, 200
        body = re.sub(r"\W", "", c)
        if re.search(r"[！!]+$", c):
            rate, pitch, vol, gap = rate + 6, pitch + 9, 8, 240
        elif re.search(r"[？?]+$", c):
            rate, pitch, gap = rate - 2, pitch + 11, 260
        elif "…" in c:
            rate, pitch, vol, gap = rate - 12, pitch - 5, -6, 430
        elif re.search(r"[~～]+$", c):
            rate, pitch, gap = rate - 7, pitch + 5, 260
        elif re.search(r"[，,、]$", c):
            gap = 130
        elif re.search(r"[。；;]$", c):
            gap = 310
        if len(body) <= 3:                    # 嗯、哼哼、哎呀 —— 语气词
            rate, pitch, gap = rate - 4, pitch + 7, max(gap, 250)
        if i == 0 and n > 1:
            pitch += 3
        if i == n - 1 and n > 1:
            rate, pitch = rate - 4, pitch - 3
        rate += rng.uniform(-3, 3)
        pitch += rng.uniform(-3, 3)
        segs.append(Seg(c, int(max(-35, min(35, rate))), int(max(-25, min(35, pitch))), vol, gap, max(1, len(c))))
    return segs


# ---------------------------------------------------------------- 引擎
def synth_edge(seg: Seg, voice: str, path):
    import edge_tts

    async def go():
        com = edge_tts.Communicate(seg.text, voice, rate=f"{seg.rate:+d}%", pitch=f"{seg.pitch:+d}Hz",
                                   volume=f"{seg.vol:+d}%")
        await asyncio.wait_for(com.save(str(path)), timeout=20)
    asyncio.run(go())


_kokoro = None
_kokoro_lock = threading.Lock()


def kokoro_available() -> bool:
    return (KOKORO_DIR / "model.onnx").exists()


def synth_kokoro(seg: Seg, sid: int, path):
    global _kokoro
    import numpy as np
    import sherpa_onnx
    with _kokoro_lock:
        if _kokoro is None:
            d = KOKORO_DIR
            cfg = sherpa_onnx.OfflineTtsConfig(
                model=sherpa_onnx.OfflineTtsModelConfig(
                    kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(
                        model=str(d / "model.onnx"), voices=str(d / "voices.bin"), tokens=str(d / "tokens.txt"),
                        data_dir=str(d / "espeak-ng-data"), dict_dir=str(d / "dict"),
                        lexicon=f"{d / 'lexicon-us-en.txt'},{d / 'lexicon-zh.txt'}"),
                    num_threads=4, provider="cpu"),
                max_num_sentences=1)
            _kokoro = sherpa_onnx.OfflineTts(cfg)
        audio = _kokoro.generate(seg.text, sid=sid, speed=max(0.6, min(1.5, 1 + seg.rate / 100)))
    pcm = (np.clip(np.asarray(audio.samples), -1, 1) * 32767 * (1 + seg.vol / 100)).clip(-32767, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(audio.sample_rate)
        w.writeframes(pcm.tobytes())


class Voice(QObject):
    """逐句合成（并行）、按顺序播放。信号里的 frac 是“说到全文的哪个位置”，给气泡打字机对齐用。"""
    seg_started = Signal(int, float, float, float)   # 请求号, 起点比例, 终点比例, 本句时长秒
    done = Signal(int)
    failed = Signal(int, str)
    _ready = Signal(int, int, str)

    def __init__(self, settings):
        super().__init__()
        self.s = settings
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.out = QAudioOutput(QMediaDevices.defaultAudioOutput(), self)
        self.devices = QMediaDevices(self)
        self.devices.audioOutputsChanged.connect(lambda: self.out.setDevice(QMediaDevices.defaultAudioOutput()))
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.out)
        self.player.mediaStatusChanged.connect(self._status)
        self.player.durationChanged.connect(self._dur)
        self._ready.connect(self._on_ready)
        self.seq = 0
        self.segs, self.paths = [], []
        self.k = -1                 # 正在播的句子
        self.waiting = False        # 轮到第 k+1 句但还没合成好
        self._announced = False
        self.playing = False

    def speak(self, voice_spec, text, valence=0.0, energy=0.0) -> int:
        self.stop()
        self.seq += 1
        n = self.seq
        segs = plan(text[:420], voice_spec.get("rate", 0), voice_spec.get("pitch", 0), valence, energy)
        if not segs:
            QTimer.singleShot(0, lambda: self.failed.emit(n, "空文本"))
            return n
        self.segs, self.paths = segs, [None] * len(segs)
        self.total = sum(s.chars for s in segs)
        self.k, self.waiting, self.playing = -1, True, True
        for i, seg in enumerate(segs):
            self.pool.submit(self._synth, n, i, seg, dict(voice_spec))
        return n

    def _synth(self, n, i, seg, spec):
        try:
            eng = spec.get("engine", "edge")
            key = hashlib.md5(f"{eng}|{spec.get('voice')}|{spec.get('sid')}|{seg.rate}|{seg.pitch}|{seg.vol}|{seg.text}"
                              .encode("utf-8")).hexdigest()[:18]
            d = CACHE / "tts"
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"{key}.{'wav' if eng == 'kokoro' else 'mp3'}"
            for attempt in range(3):          # 在线服务并发时偶尔回空音频，重试即可
                if path.exists() and path.stat().st_size >= 400:
                    break
                try:
                    (synth_kokoro(seg, int(spec.get("sid", 0)), path) if eng == "kokoro"
                     else synth_edge(seg, spec["voice"], path))
                except Exception:
                    if attempt == 2 or n != self.seq:
                        raise
                    import time
                    time.sleep(0.35 * (attempt + 1))
            if path.stat().st_size < 400:
                raise RuntimeError("空音频")
            self._ready.emit(n, i, str(path))
        except Exception as e:
            self._ready.emit(n, i, "!" + str(e)[:100])

    def _on_ready(self, n, i, path):
        if n != self.seq or not self.playing:
            return
        self.paths[i] = path
        if self.waiting and i == self.k + 1:
            self._next()

    def _next(self):
        nxt = self.k + 1
        if nxt >= len(self.segs):
            self.playing = False
            self.done.emit(self.seq)
            return
        p = self.paths[nxt]
        if p is None:
            self.waiting = True
            return
        self.waiting = False
        self.k = nxt
        if p.startswith("!"):
            if nxt == 0:                       # 第一句就失败（多半是断网）→ 交给上层改用咿呀语
                self.playing = False
                self.failed.emit(self.seq, p[1:])
            else:
                self._next()
            return
        self._announced = False
        self.out.setVolume(float(self.s["voice_volume"]))
        self.player.setSource(QUrl.fromLocalFile(p))
        self.player.play()

    def _dur(self, ms):
        if ms > 0 and self.playing and not self._announced and self.k >= 0:
            self._announced = True
            a = sum(s.chars for s in self.segs[:self.k]) / self.total
            b = sum(s.chars for s in self.segs[:self.k + 1]) / self.total
            self.seg_started.emit(self.seq, a, b, ms / 1000.0)

    def _status(self, st):
        if not self.playing:
            return
        if st in (QMediaPlayer.MediaStatus.EndOfMedia, QMediaPlayer.MediaStatus.InvalidMedia):
            n = self.seq
            gap = self.segs[self.k].gap if 0 <= self.k < len(self.segs) else 0
            QTimer.singleShot(gap, lambda: self._next() if (n == self.seq and self.playing) else None)

    def stop(self):
        self.playing = False
        self.seq += 1
        self.player.stop()

    def gc(self):
        try:
            files = sorted((CACHE / "tts").glob("*.*"), key=lambda p: p.stat().st_mtime)
            for p in files[:-200]:
                p.unlink(missing_ok=True)
        except Exception:
            pass
