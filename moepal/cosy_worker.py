"""语音子进程：Fun-CosyVoice3-0.5B（阿里通义，Apache-2.0），按情绪换语气。
Voice worker: Fun-CosyVoice3-0.5B with emotion instructions.

由独立环境 D:\\MoePal\\.conda-voice（Python 3.10 + CUDA PyTorch）运行，主程序通过标准输入输出发 JSON 行。
  {"id": 1, "text": "...", "prompt_wav": "...", "prompt_text": "...", "emo": "happy", "speed": 1.0, "out": "x.wav"}
  → {"id": 1, "ok": true, "out": "x.wav", "secs": 1.2, "dur": 3.4}
启动后先回 {"ready": true, "provider": "cuda"}。

语气怎么来：
- 平静 / 思考 / 好奇 → 普通零样本（只学参考音色，语调由模型自己根据句子决定）。
- 开心类 → 指令“请非常开心地说一句话”；难过类 → “请非常伤心地说”；生气 → “请非常生气地说”；
  害羞 / 担心 / 困 → “Please say a sentence in a very soft voice.”（轻声、温柔）。
  这几句是模型训练时见过的原句（cosyvoice/utils/common.py 的 instruct_list），效果最稳。
- 文本里的语气词换成模型认识的声音标记：开头的“哈哈 / 嘿嘿”前加 [laughter]，“唉”前加 [sigh]，
  省略号后加 [breath]——听起来会笑、会叹气、会换气，而不是平铺直叙地念字。
"""
import json
import os
import re
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COSY = ROOT / "data" / "tools" / "CosyVoice"
MODEL = ROOT / "data" / "models" / "Fun-CosyVoice3-0.5B"

SYS = "You are a helpful assistant."
INSTRUCT = {
    "happy": "请非常开心地说一句话。", "cheer": "请非常开心地说一句话。", "proud": "请非常开心地说一句话。",
    "love": "请非常开心地说一句话。", "surprised": "请非常开心地说一句话。",
    "sad": "请非常伤心地说一句话。", "cry": "请非常伤心地说一句话。",
    "angry": "请非常生气地说一句话。",
    "shy": "Please say a sentence in a very soft voice.", "worried": "Please say a sentence in a very soft voice.",
    "sleepy": "Please say a sentence in a very soft voice.",
}


def decorate(text: str, emo: str) -> str:
    t = text.strip()
    t = re.sub(r"^(哈哈+|嘿嘿+|嘻嘻+)", r"[laughter]\1", t)
    t = re.sub(r"(^|[，。！？\s])(唉|哎)([，…。！？])", r"\1[sigh]\2\3", t)
    t = re.sub(r"……(?=\S)", "……[breath]", t)
    if emo in ("happy", "cheer", "love") and "[laughter]" not in t and re.search(r"(哈哈|嘿嘿|嘻嘻)", t):
        t = re.sub(r"(哈哈|嘿嘿|嘻嘻)", r"[laughter]\1", t, count=1)
    return t


def main():
    sys.path[:0] = [str(COSY), str(COSY / "third_party" / "Matcha-TTS")]
    out = sys.stdout
    sys.stdout = sys.stderr
    import numpy as np
    import torch
    from cosyvoice.cli.cosyvoice import AutoModel
    try:
        model = AutoModel(model_dir=str(MODEL), fp16=torch.cuda.is_available())
    except Exception as e:  # noqa
        out.write(json.dumps({"ready": False, "error": f"模型加载失败：{e}"[:300]}) + "\n")
        out.flush()
        return
    sr = model.sample_rate
    out.write(json.dumps({"ready": True, "provider": "cuda" if torch.cuda.is_available() else "cpu", "sr": sr}) + "\n")
    out.flush()
    for line in sys.stdin:
        try:
            r = json.loads(line)
        except Exception:
            continue
        try:
            t0 = time.time()
            emo = r.get("emo") or "calm"
            text = decorate(r["text"], emo)
            speed = float(r.get("speed", 1.0))
            ins = INSTRUCT.get(emo)
            if ins:
                gen = model.inference_instruct2(text, f"{SYS} {ins}<|endofprompt|>", r["prompt_wav"], stream=False,
                                                speed=speed)
            else:
                gen = model.inference_zero_shot(text, f"{SYS}<|endofprompt|>{r['prompt_text']}", r["prompt_wav"],
                                                stream=False, speed=speed)
            parts = [j["tts_speech"].squeeze(0).float().cpu().numpy() for j in gen]
            y = np.concatenate(parts) if parts else np.zeros(1, "float32")
            idx = np.where(np.abs(y) > 0.01)[0]
            if len(idx):
                y = y[max(0, idx[0] - int(0.03 * sr)): idx[-1] + int(0.08 * sr)]
            f = min(len(y) // 4, int(0.01 * sr))
            if f > 0:
                y[:f] *= np.linspace(0, 1, f)
                y[-f:] *= np.linspace(1, 0, f)
            peak = float(np.max(np.abs(y))) if len(y) else 1.0
            y = y * (0.85 / peak if peak > 0.05 else 1.0)
            with wave.open(r["out"], "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(sr)
                w.writeframes((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())
            out.write(json.dumps({"id": r.get("id"), "ok": True, "out": r["out"], "secs": round(time.time() - t0, 2),
                                  "dur": round(len(y) / sr, 2), "text": text}, ensure_ascii=False) + "\n")
        except Exception as e:
            out.write(json.dumps({"id": r.get("id"), "ok": False, "error": str(e)[:300]}, ensure_ascii=False) + "\n")
        out.flush()


if __name__ == "__main__":
    main()
