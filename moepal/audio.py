"""声音输出与语音输入。 Audio out (sfx + TTS) and voice in (mic + Whisper).

输出走系统“当前默认输出设备”，设备切换（插耳机等）时自动跟随。
"""
import asyncio
import hashlib
import threading
import time

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer, QSoundEffect

from . import sfx
from .characters import CHARACTERS
from .config import CACHE


class Audio(QObject):
    speech_ready = Signal(int, str)     # 请求号, mp3 路径
    speech_failed = Signal(int, str)
    speech_started = Signal(int, float)  # 请求号, 时长秒
    speech_done = Signal(int)

    def __init__(self, settings):
        super().__init__()
        self.s = settings
        self.files = sfx.build_all(CACHE / "sfx", CHARACTERS)
        self.effects = {}
        self.devices = QMediaDevices(self)
        self.devices.audioOutputsChanged.connect(self._follow_device)
        self.out = QAudioOutput(QMediaDevices.defaultAudioOutput(), self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.out)
        self.player.mediaStatusChanged.connect(self._status)
        self.player.durationChanged.connect(self._dur)
        self.seq = 0
        self.playing = 0
        self._announced = False
        self.speech_ready.connect(self._play_speech)

    def _follow_device(self):
        dev = QMediaDevices.defaultAudioOutput()
        self.out.setDevice(dev)
        for e in self.effects.values():
            e.setAudioDevice(dev)

    # ---------- 音效 ----------
    def play(self, char_id, event, volume=1.0):
        if self.s["voice_mode"] == "mute" and not event.startswith("force:"):
            return
        event = event.replace("force:", "")
        path = self.files.get(char_id, {}).get(event)
        if not path:
            return
        key = (char_id, event)
        e = self.effects.get(key)
        if e is None:
            e = QSoundEffect(QMediaDevices.defaultAudioOutput(), self)
            e.setSource(QUrl.fromLocalFile(str(path)))
            self.effects[key] = e
        e.setVolume(max(0.0, min(1.0, self.s["sfx_volume"] * volume)))
        e.play()

    # ---------- 语音合成 ----------
    def speak(self, char, text, valence=0.0, energy=0.0) -> int:
        """异步合成并播放；返回请求号。失败时发 speech_failed，由桌宠改用咿呀语。"""
        self.seq += 1
        n = self.seq
        self.stop_speech()
        rate = int(char.tts_rate + energy * 10 + (-8 if valence < -0.35 else 0))
        pitch = int(char.tts_pitch + valence * 8 + energy * 4)
        threading.Thread(target=self._synth, args=(n, char.tts_voice, text, rate, pitch), daemon=True).start()
        return n

    def _synth(self, n, voice, text, rate, pitch):
        try:
            import edge_tts
            key = hashlib.md5(f"{voice}|{rate}|{pitch}|{text}".encode("utf-8")).hexdigest()[:16]
            d = CACHE / "tts"
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"{key}.mp3"
            if not path.exists() or path.stat().st_size < 500:
                async def go():
                    com = edge_tts.Communicate(text, voice, rate=f"{rate:+d}%", pitch=f"{pitch:+d}Hz")
                    await asyncio.wait_for(com.save(str(path)), timeout=25)
                asyncio.run(go())
            if path.stat().st_size < 500:
                raise RuntimeError("空音频")
            self.speech_ready.emit(n, str(path))
        except Exception as e:
            self.speech_failed.emit(n, str(e)[:120])
        self._gc()

    def _gc(self):
        try:
            files = sorted((CACHE / "tts").glob("*.mp3"), key=lambda p: p.stat().st_mtime)
            for p in files[:-80]:
                p.unlink(missing_ok=True)
        except Exception:
            pass

    def _play_speech(self, n, path):
        if n != self.seq:
            return
        self.playing = n
        self._announced = False
        self.out.setVolume(self.s["voice_volume"])
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()

    def _dur(self, ms):
        if ms > 0 and self.playing and not self._announced:
            self._announced = True
            self.speech_started.emit(self.playing, ms / 1000.0)

    def _status(self, st):
        if st == QMediaPlayer.MediaStatus.EndOfMedia and self.playing:
            n, self.playing = self.playing, 0
            self.speech_done.emit(n)
        elif st == QMediaPlayer.MediaStatus.InvalidMedia and self.playing:
            n, self.playing = self.playing, 0
            self.speech_failed.emit(n, "播放失败")

    def stop_speech(self):
        self.playing = 0
        self.player.stop()


class Listener(QObject):
    """麦克风录音 → 本地 Whisper 识别。说完停顿约 1.2 秒自动结束，也可以手动结束。"""
    level = Signal(float)
    state = Signal(str)        # recording | transcribing | loading | idle
    text = Signal(str)
    failed = Signal(str)

    SR = 16000

    def __init__(self, settings):
        super().__init__()
        self.s = settings
        self.model = None
        self.stream = None
        self.chunks = []
        self.recording = False
        self._lock = threading.Lock()

    def toggle(self):
        if self.recording:
            self.stop()
        else:
            self.start()

    def start(self):
        try:
            import numpy as np
            import sounddevice as sd
        except Exception as e:
            self.failed.emit(f"录音组件不可用：{e}")
            return
        self.chunks, self.recording = [], True
        self._t0 = time.time()
        self._voiced = False
        self._last_voice = time.time()
        self._noise = 0.01

        def cb(indata, frames, t, status):
            if not self.recording:
                return
            x = indata[:, 0].copy()
            self.chunks.append(x)
            rms = float(np.sqrt(np.mean(x * x)) + 1e-9)
            self.level.emit(min(1.0, rms * 12))
            now = time.time()
            if rms > max(0.012, self._noise * 3):
                self._voiced = True
                self._last_voice = now
            elif not self._voiced:
                self._noise = 0.9 * self._noise + 0.1 * rms
            if (self._voiced and now - self._last_voice > 1.2) or now - self._t0 > 30 \
                    or (not self._voiced and now - self._t0 > 7):
                threading.Thread(target=self.stop, daemon=True).start()

        try:
            self.stream = sd.InputStream(samplerate=self.SR, channels=1, dtype="float32", callback=cb,
                                         blocksize=1600)   # 默认输入设备
            self.stream.start()
            self.state.emit("recording")
        except Exception as e:
            self.recording = False
            self.failed.emit(f"打不开麦克风：{str(e)[:80]}")

    def stop(self):
        with self._lock:
            if not self.recording:
                return
            self.recording = False
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass
        import numpy as np
        if not self.chunks or not self._voiced:
            self.state.emit("idle")
            self.failed.emit("")
            return
        audio = np.concatenate(self.chunks)
        threading.Thread(target=self._transcribe, args=(audio,), daemon=True).start()

    def _transcribe(self, audio):
        try:
            if self.model is None:
                self.state.emit("loading")
                from faster_whisper import WhisperModel
                self.model = WhisperModel(self.s["whisper_model"], device="cpu", compute_type="int8",
                                          download_root=str(CACHE / "whisper"))
            self.state.emit("transcribing")
            segs, _ = self.model.transcribe(audio, language="zh", beam_size=3, vad_filter=True,
                                            initial_prompt="以下是普通话的句子，使用简体中文。")
            txt = "".join(s.text for s in segs).strip()
            self.state.emit("idle")
            if txt:
                self.text.emit(txt)
            else:
                self.failed.emit("")
        except Exception as e:
            self.state.emit("idle")
            self.failed.emit(f"语音识别失败：{str(e)[:100]}")
