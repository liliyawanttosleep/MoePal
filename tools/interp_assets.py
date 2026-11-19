"""从关键帧重新生成平滑动画：Real-ESRGAN 放大 2 倍 → RIFE 补帧 → 带透明通道的 <clip>@2x.webp。
Rebuild smooth clips from the keyframes: 2x Real-ESRGAN → RIFE interpolation → transparent <clip>@2x.webp.

为什么：原来的“流畅动画”是 8 张关键帧用光流补出来的，每到关键帧就跳一下（相邻帧差异在关键帧处是平时的 2~3 倍），
动作像一顿一顿的。RIFE 是专门做视频补帧的网络，对动漫的大动作也稳定得多。

做法：
- 关键帧：情绪动作_v2/<角色>/Q版/<动作>/关键帧/01..08.png；v2 没有的用 情绪动作_v1 的 4 帧。
- 首尾相接（最后补一张第一帧）让循环无缝。
- 透明通道：RGB 先做颜色外扩、alpha 单独当灰度图，两路分别补帧再合并（RIFE 只处理 RGB）。
- 工具：data/tools/rife-ncnn-vulkan-20221029-windows/rife-ncnn-vulkan.exe（nihui，GitHub 官方发布，Vulkan 用显卡），
  模型 rife-v4.6。
旧文件移到 assets/_old/。

用法: .venv\\Scripts\\python tools\\interp_assets.py [--only gpt] [--clip idle] [--per 20]
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from upscale_assets import bleed, load_model, upscale_frames  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = Path.home() / "Desktop" / "AI动漫助手_角色素材"
RIFE_DIR = next(iter(sorted((ROOT / "data" / "tools").glob("rife-ncnn-vulkan-*"))), None)
ZH = {"jump": "01_开心跳跃", "wipe": "02_伤心擦泪", "down": "03_难过低头", "anxious": "04_焦虑搓手", "daze": "05_忧郁发呆",
      "startle": "06_惊讶后退", "cry": "07_悲伤坐哭", "wave": "08_释然挥手", "idle": "09_待机眨眼", "read": "10_翻书阅读",
      "hum": "11_轻声哼曲", "special": "12_专属互动"}
V2 = {"gpt": "GPT", "claude": "Claude", "deepseek": "DeepSeek"}
V1 = {"gpt": "GPT白龙娘", "claude": "Claude大小姐", "deepseek": "DeepSeek大肥鱼"}
# 每段关键帧之间的时长（秒）。待机、发呆这类慢动作长一点，跳跃、惊讶快一点。
GAP = {"idle": 0.55, "daze": 0.6, "read": 0.55, "hum": 0.45, "down": 0.55, "cry": 0.45, "wipe": 0.45,
       "anxious": 0.35, "jump": 0.28, "startle": 0.3, "wave": 0.35, "special": 0.45}
FPS = 50


def keyframes(cid, clip):
    d = SRC / "情绪动作_v2" / V2[cid] / "Q版" / ZH[clip] / "关键帧"
    files = sorted(d.glob("*.png")) if d.exists() else []
    if len(files) >= 4:
        return [Image.open(f).convert("RGBA") for f in files], "v2"
    d = SRC / "情绪动作_v1" / V1[cid] / "逐帧PNG" / ZH[clip]
    files = sorted(d.glob("*.png")) if d.exists() else []
    return [Image.open(f).convert("RGBA") for f in files], "v1"


def rife(in_dir: Path, out_dir: Path, n_out: int):
    exe = RIFE_DIR / "rife-ncnn-vulkan.exe"
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [str(exe), "-i", str(in_dir), "-o", str(out_dir), "-n", str(n_out), "-m", str(RIFE_DIR / "rife-v4.6"),
           "-f", "%05d.png"]
    p = subprocess.run(cmd, capture_output=True, text=True, creationflags=0x08000000)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-400:])
    return sorted(out_dir.glob("*.png"))


def build(net, cid, clip, per_override=None):
    keys, ver = keyframes(cid, clip)
    if len(keys) < 2:
        return None
    t0 = time.time()
    big = upscale_frames(net, keys)                  # 2x、边缘清晰
    w, h = big[0].size
    per = per_override or max(6, round(GAP[clip] * FPS))
    loop = big + [big[0]]
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "rgb").mkdir()
        (tmp / "a").mkdir()
        for i, f in enumerate(loop):
            bleed(f).save(tmp / "rgb" / f"{i:05d}.png")
            Image.merge("RGB", [f.getchannel("A")] * 3).save(tmp / "a" / f"{i:05d}.png")
        n_out = (len(loop) - 1) * per + 1
        rgb = rife(tmp / "rgb", tmp / "rgb_o", n_out)
        alp = rife(tmp / "a", tmp / "a_o", n_out)
        frames = []
        for fr, fa in zip(rgb[:-1], alp[:-1]):           # 去掉最后那张（= 第一帧），循环无缝
            c = Image.open(fr).convert("RGB")
            a = Image.open(fa).convert("L").point(lambda v: 0 if v < 20 else 255 if v > 236 else int((v - 20) * 255 / 216))
            c.putalpha(a)
            frames.append(c)
    dst = ROOT / "assets" / cid / f"{clip}@2x.webp"
    if dst.exists():
        old = ROOT / "assets" / "_old" / cid
        old.mkdir(parents=True, exist_ok=True)
        if not (old / dst.name).exists():
            shutil.move(str(dst), old / dst.name)
    tmp_dst = dst.with_name(dst.stem + ".tmp.webp")       # 先写临时文件再替换，正在运行的桌宠不会读到半个文件
    frames[0].save(tmp_dst, save_all=True, append_images=frames[1:], duration=round(1000 / FPS), loop=0, quality=90,
                   method=3)
    os.replace(tmp_dst, dst)
    # 编码器会合并完全相同的连续帧：读回真实的每帧时长，写成同名 .json 给播放器建时间轴
    im = Image.open(dst)
    durs = []
    for i in range(im.n_frames):
        im.seek(i)
        im.load()
        durs.append(int(im.info.get("duration") or round(1000 / FPS)))
    dst.with_suffix(".json").write_text(json.dumps({"durations": durs, "source": ver, "keyframes": len(keys)}),
                                        encoding="utf-8")
    print(f"{cid}/{clip}: {ver} {len(keys)} 关键帧 → {len(frames)} 帧（存为 {len(durs)} 张）@{FPS}fps {w}x{h} "
          f"{dst.stat().st_size // 1024}KB {time.time() - t0:.0f}s", flush=True)
    return dst


def main():
    arg = lambda k: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else None
    only, one, per = arg("--only"), arg("--clip"), arg("--per")
    if not RIFE_DIR:
        sys.exit("缺少 data/tools/rife-ncnn-vulkan-*")
    net = load_model()
    for cid in ("gpt", "claude", "deepseek"):
        if only and cid != only:
            continue
        for clip in ZH:
            if one and clip != one:
                continue
            try:
                build(net, cid, clip, int(per) if per else None)
            except Exception as e:
                print(f"{cid}/{clip}: 失败 {e}", flush=True)


if __name__ == "__main__":
    main()
