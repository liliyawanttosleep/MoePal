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
from pathlib import Path
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


def plan_flow(text, base_rate, base_pitch, valence=0.0, energy=0.0, limit=48):
    """连贯模式（默认）：整段话交给语音引擎一口气念，停顿和语调由引擎自己根据标点决定。
    v0.2 把每个短句拆开单独合成、各给一个音高，听起来一顿一顿、忽高忽低；这里只在整句边界切，
    并且全程同一个音高，只让语速随心情有很小的变化。短回复就是一次合成。"""
    t = clean(text)
    sents = [s.strip() for s in re.findall(r"[^。！？!?\n]+[。！？!?…~～]*|[。！？!?…]+", t) if s.strip()]
    chunks = []
    for s in sents:
        if chunks and (len(chunks[-1]) + len(s) <= limit or len(re.sub(r"\W", "", s)) < 2) \
                and not (limit < 48 and len(chunks) == 1):     # 本地模型：第一句单独合成，开口更快
            chunks[-1] += s
        else:
            chunks.append(s)
    rate = int(max(-20, min(20, base_rate + energy * 4 + (-5 if valence < -0.35 else 0))))
    return [Seg(c, rate, int(base_pitch), 0, 90, max(1, len(c))) for c in chunks]


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


MELO_DIR = DATA / "models" / "vits-melo-tts-zh_en"
_melo = None


def melo_available() -> bool:
    return (MELO_DIR / "model.onnx").exists()


def zipvoice_available() -> bool:
    return (ZV_DIR / "decoder.onnx").exists() and (DATA / "models" / "vocos_24khz.onnx").exists()


def engine_available(eng: str) -> bool:
    return {"kokoro": kokoro_available, "melo": melo_available, "zipvoice": zipvoice_available}.get(eng, lambda: True)()


# ---------------------------------------------------------------- ZipVoice（参考音色，显卡）
ZV_DIR = DATA / "models" / "sherpa-onnx-zipvoice-distill-fp32-zh-en-emilia"
VOICES = DATA / "voices"


def find_ffmpeg():
    import shutil
    for p in (shutil.which("ffmpeg"), r"D:\ffmpeg\bin\ffmpeg.exe"):
        if p and Path(p).exists():
            return p
    return None


def ensure_prompt(char_id, spec):
    """每个角色的参考音色：data/voices/<角色>/prompt.wav + prompt.txt。
    你可以换成自己有权使用的录音（5~10 秒，单人、干净），并把录音里说的话原样写进 prompt.txt。
    没有的话，用微软的合成声线念一段口语化的话当参考（合成声线，不是任何真人的录音）。"""
    d = VOICES / char_id
    wav, txt = d / "prompt.wav", d / "prompt.txt"
    if wav.exists() and txt.exists():
        return wav, txt.read_text(encoding="utf-8").strip()
    d.mkdir(parents=True, exist_ok=True)
    mp3 = d / "prompt_src.mp3"
    import edge_tts

    async def go():
        await edge_tts.Communicate(spec["ref_text"], spec["ref_voice"], rate=spec.get("ref_rate", "+0%"),
                                   pitch=spec.get("ref_pitch", "+0Hz")).save(str(mp3))
    asyncio.run(go())
    ff = find_ffmpeg()
    if not ff:
        raise RuntimeError("需要 ffmpeg 来生成参考音色")
    import subprocess
    subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(mp3), "-ac", "1", "-ar", "24000", str(wav)], check=True,
                   creationflags=0x08000000)
    txt.write_text(spec["ref_text"], encoding="utf-8")
    return wav, spec["ref_text"]


class ZipVoiceClient:
    """管理 tts_worker 子进程。一次只处理一个请求（显卡上排队本来也是串行的）。"""

    def __init__(self):
        self.proc = None
        self.lock = threading.Lock()
        self.provider = ""
        self.n = 0

    def _start(self):
        import json
        import subprocess
        import sys
        exe = sys.executable.replace("pythonw.exe", "python.exe")
        self.proc = subprocess.Popen([exe, "-m", "moepal.tts_worker"], cwd=str(Path(__file__).resolve().parent.parent),
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, encoding="utf-8", bufsize=1, creationflags=0x08000000)
        first = json.loads(self.proc.stdout.readline() or "{}")
        if not first.get("ready"):
            self.proc.kill()
            self.proc = None
            raise RuntimeError(first.get("error", "语音进程启动失败"))
        self.provider = first.get("provider", "")

    def synth(self, text, prompt_wav, prompt_text, out, speed=1.0, steps=8):
        import json
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self._start()
            self.n += 1
            req = {"id": self.n, "text": text, "prompt_wav": str(prompt_wav), "prompt_text": prompt_text,
                   "out": str(out), "speed": speed, "steps": steps}
            self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
            while True:
                line = self.proc.stdout.readline()
                if not line:
                    self.proc = None
                    raise RuntimeError("语音进程退出了")
                r = json.loads(line)
                if r.get("id") == self.n:
                    if not r.get("ok"):
                        raise RuntimeError(r.get("error", "合成失败"))
                    return r

    def warm(self, char_id, spec):
        """后台预热：启动进程并合成一句很短的话，让第一次开口不用等。"""
        def go():
            try:
                wav, txt = ensure_prompt(char_id, spec)
                self.synth("嗯。", wav, txt, CACHE / "tts" / "_warm.wav", steps=4)
            except Exception:
                pass
        threading.Thread(target=go, daemon=True).start()

    def close(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.close()
                self.proc.wait(2)
            except Exception:
                self.proc.kill()


ZIPVOICE = ZipVoiceClient()


def _write_wav(audio, vol, path):
    import numpy as np
    pcm = (np.clip(np.asarray(audio.samples), -1, 1) * 32767 * (1 + vol / 100)).clip(-32767, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(audio.sample_rate)
        w.writeframes(pcm.tobytes())


def synth_melo(seg: Seg, path):
    """本地 MeloTTS 中文女声（sherpa-onnx VITS），离线。"""
    global _melo
    import sherpa_onnx
    with _kokoro_lock:
        if _melo is None:
            d = MELO_DIR
            cfg = sherpa_onnx.OfflineTtsConfig(
                model=sherpa_onnx.OfflineTtsModelConfig(
                    vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                        model=str(d / "model.onnx"), lexicon=str(d / "lexicon.txt"), tokens=str(d / "tokens.txt"),
                        dict_dir=str(d / "dict")),
                    num_threads=4, provider="cpu"),
                rule_fsts=",".join(str(d / x) for x in ("date.fst", "number.fst", "phone.fst") if (d / x).exists()),
                max_num_sentences=2)
            _melo = sherpa_onnx.OfflineTts(cfg)
        audio = _melo.generate(seg.text, sid=0, speed=max(0.7, min(1.4, 1 + seg.rate / 100)))
    _write_wav(audio, seg.vol, path)


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
        if voice_spec.get("engine") == "zipvoice":
            segs = plan_flow(text[:420], 0, 0, valence, energy, limit=30)
        else:
            planner = plan if self.s["voice_style"] == "expressive" else plan_flow
            segs = planner(text[:420], voice_spec.get("rate", 0), voice_spec.get("pitch", 0), valence, energy)
        self.char_id = voice_spec.get("char", "")
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
            if eng == "zipvoice":
                try:
                    wav, ptxt = ensure_prompt(spec["char"], spec)
                    key = hashlib.md5(f"zv|{spec['char']}|{wav.stat().st_mtime}|{seg.rate}|{seg.text}".encode("utf-8")).hexdigest()[:18]
                    d = CACHE / "tts"
                    d.mkdir(parents=True, exist_ok=True)
                    path = d / f"{key}.wav"
                    if not path.exists() or path.stat().st_size < 400:
                        speed = float(spec.get("speed", 1.0)) * (1 + seg.rate / 100)
                        ZIPVOICE.synth(seg.text, wav, ptxt, path, speed=speed, steps=int(spec.get("steps", 8)))
                    self._ready.emit(n, i, str(path))
                    return
                except Exception:
                    # 显卡语音不可用 → 用同一位参考声线的在线版本顶上，不要哑掉
                    eng, spec = "edge", dict(spec, voice=spec["ref_voice"])
            key = hashlib.md5(f"{eng}|{spec.get('voice')}|{spec.get('sid')}|{seg.rate}|{seg.pitch}|{seg.vol}|{seg.text}"
                              .encode("utf-8")).hexdigest()[:18]
            d = CACHE / "tts"
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"{key}.{'mp3' if eng == 'edge' else 'wav'}"
            for attempt in range(3):          # 在线服务并发时偶尔回空音频，重试即可
                if path.exists() and path.stat().st_size >= 400:
                    break
                try:
                    if eng == "kokoro":
                        synth_kokoro(seg, int(spec.get("sid", 0)), path)
                    elif eng == "melo":
                        synth_melo(seg, path)
                    else:
                        synth_edge(seg, spec["voice"], path)
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
