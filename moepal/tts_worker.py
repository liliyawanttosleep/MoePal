"""语音子进程：ZipVoice（零样本音色参考，sherpa-onnx）。优先用显卡（CUDA），不行就用 CPU。
Voice worker process: ZipVoice zero-shot TTS via sherpa-onnx, CUDA if available, else CPU.

为什么单独一个进程：显卡上下文要占几百 MB 内存，第一次在新显卡上运行还要几十秒预热（之后有缓存）；
放在子进程里，桌宠界面不会被拖慢，也不会因为语音出问题而一起崩。

协议：标准输入每行一个 JSON 请求，标准输出每行一个 JSON 回复。
  {"id": 1, "text": "...", "prompt_wav": "...", "prompt_text": "...", "speed": 1.0, "steps": 8, "out": "x.wav"}
  → {"id": 1, "ok": true, "out": "x.wav", "secs": 1.2, "dur": 3.4}
启动后先回一行 {"ready": true, "provider": "cuda"}。
"""
import glob
import json
import os
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL = ROOT / "data" / "models" / "sherpa-onnx-zipvoice-distill-fp32-zh-en-emilia"
VOCODER = ROOT / "data" / "models" / "vocos_24khz.onnx"


def add_cuda_dlls():
    dirs = sorted(glob.glob(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.*\bin"), reverse=True)[:1]
    dirs += glob.glob(os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "*", "bin"))
    for d in dirs:
        try:
            os.add_dll_directory(d)
            os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]
        except OSError:
            pass
    os.environ.setdefault("CUDA_CACHE_MAXSIZE", str(2 << 30))   # 首次预热的编译结果缓存下来，下次秒开


def load(provider):
    import sherpa_onnx
    cfg = sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            zipvoice=sherpa_onnx.OfflineTtsZipvoiceModelConfig(
                tokens=str(MODEL / "tokens.txt"), encoder=str(MODEL / "encoder.onnx"),
                decoder=str(MODEL / "decoder.onnx"), vocoder=str(VOCODER),
                data_dir=str(MODEL / "espeak-ng-data"), lexicon=str(MODEL / "lexicon.txt")),
            num_threads=4 if provider == "cuda" else 8, provider=provider),
        max_num_sentences=1)
    return sherpa_onnx.OfflineTts(cfg)


def read_wav(p):
    import numpy as np
    with wave.open(str(p)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype("float32") / 32768
        if w.getnchannels() == 2:
            x = x.reshape(-1, 2).mean(axis=1)
        return x, w.getframerate()


def main():
    import numpy as np
    add_cuda_dlls()
    out = sys.stdout
    sys.stdout = sys.stderr                     # 库自己的打印别混进协议
    want = os.environ.get("MOEPAL_TTS_PROVIDER", "cuda")
    tts, provider = None, ""
    for p in ([want, "cpu"] if want != "cpu" else ["cpu"]):
        try:
            tts, provider = load(p), p
            break
        except Exception as e:  # noqa
            print("load", p, "failed:", e, file=sys.stderr)
    if tts is None:
        out.write(json.dumps({"ready": False, "error": "模型加载失败"}) + "\n")
        out.flush()
        return
    prompts = {}
    out.write(json.dumps({"ready": True, "provider": provider}) + "\n")
    out.flush()
    for line in sys.stdin:
        try:
            r = json.loads(line)
        except Exception:
            continue
        try:
            t0 = time.time()
            key = r["prompt_wav"]
            if key not in prompts:
                prompts[key] = read_wav(key)
            x, sr = prompts[key]
            a = tts.generate(r["text"], prompt_text=r["prompt_text"], prompt_samples=x.tolist(), sample_rate=sr,
                             speed=float(r.get("speed", 1.0)), num_steps=int(r.get("steps", 8)))
            y = np.clip(np.asarray(a.samples, dtype="float32"), -1, 1)
            # 去掉首尾静音，再做一个很短的淡入淡出，句子之间接起来更顺
            idx = np.where(np.abs(y) > 0.01)[0]
            if len(idx):
                y = y[max(0, idx[0] - int(0.03 * a.sample_rate)): idx[-1] + int(0.08 * a.sample_rate)]
            f = min(len(y) // 4, int(0.01 * a.sample_rate))
            if f > 0:
                y[:f] *= np.linspace(0, 1, f)
                y[-f:] *= np.linspace(1, 0, f)
            peak = float(np.max(np.abs(y))) if len(y) else 1.0
            y = y * (0.85 / peak if peak > 0.05 else 1.0) * float(r.get("gain", 1.0))
            with wave.open(r["out"], "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(a.sample_rate)
                w.writeframes((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())
            out.write(json.dumps({"id": r.get("id"), "ok": True, "out": r["out"], "secs": round(time.time() - t0, 2),
                                  "dur": round(len(y) / a.sample_rate, 2)}) + "\n")
        except Exception as e:
            out.write(json.dumps({"id": r.get("id"), "ok": False, "error": str(e)[:200]}) + "\n")
        out.flush()


if __name__ == "__main__":
    main()
