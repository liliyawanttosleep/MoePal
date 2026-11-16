"""把 assets/ 里的动图用动漫超分模型放大 2 倍（清晰边缘），输出 <clip>@2x.webp。
Upscale every clip 2x with Real-ESRGAN animevideov3 (SRVGGNetCompact) for crisp edges.

做法：RGB 先做“颜色外扩”（把透明处填上附近的颜色，避免边缘发黑/发绿），过 4x 网络后 Lanczos 缩到 2x；
alpha 通道单独当灰度图过同一个网络，得到锐利的轮廓，再收紧过渡带。
模型：https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-animevideov3.pth  → data/models/

用法: .venv\\Scripts\\python tools\\upscale_assets.py [--only gpt] [--clip idle] [--test 输出png]
"""
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from PIL import Image, ImageFilter, ImageSequence

ROOT = Path(__file__).resolve().parent.parent
MODEL = ROOT / "data" / "models" / "realesr-animevideov3.pth"


class SRVGGNetCompact(nn.Module):
    def __init__(self, num_feat=64, num_conv=16, upscale=4):
        super().__init__()
        self.upscale = upscale
        body = [nn.Conv2d(3, num_feat, 3, 1, 1), nn.PReLU(num_parameters=num_feat)]
        for _ in range(num_conv):
            body += [nn.Conv2d(num_feat, num_feat, 3, 1, 1), nn.PReLU(num_parameters=num_feat)]
        body.append(nn.Conv2d(num_feat, 3 * upscale * upscale, 3, 1, 1))
        self.body = nn.ModuleList(body)
        self.upsampler = nn.PixelShuffle(upscale)

    def forward(self, x):
        out = x
        for layer in self.body:
            out = layer(out)
        return self.upsampler(out) + F.interpolate(x, scale_factor=self.upscale, mode="nearest")


def load_model():
    net = SRVGGNetCompact()
    sd = torch.load(MODEL, map_location="cpu")
    net.load_state_dict(sd.get("params", sd.get("params_ema", sd)), strict=True)
    return net.eval()


def bleed(rgba: Image.Image) -> Image.Image:
    """把不透明区域的颜色向外扩散到透明区，返回 RGB。"""
    a = np.asarray(rgba.getchannel("A"), dtype=np.float32) / 255
    rgb = np.asarray(rgba.convert("RGB"), dtype=np.float32)
    acc, w = rgb * a[..., None], a.copy()
    for r in (2, 4, 8, 16):
        bl_c = np.asarray(Image.fromarray(np.clip(acc, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(r)), np.float32)
        bl_w = np.asarray(Image.fromarray((np.clip(w, 0, 1) * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(r)), np.float32) / 255
        fill = bl_c / np.maximum(bl_w[..., None], 1e-3)
        m = (w < 0.02)[..., None]
        acc = np.where(m, fill * (bl_w[..., None] > 0.004), acc)
        w = np.where(w < 0.02, (bl_w > 0.004).astype(np.float32), w)
    solid = acc / np.maximum(w[..., None], 1e-3)
    out = rgb * a[..., None] + solid * (1 - a[..., None])   # 半透明边缘去预乘式修正
    out = np.where((a > 0.98)[..., None], rgb, np.where((a < 0.02)[..., None], solid, out))
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


@torch.no_grad()
def run(net, imgs):
    x = torch.from_numpy(np.stack([np.asarray(i, np.float32) / 255 for i in imgs])).permute(0, 3, 1, 2)
    y = net(x).clamp(0, 1).permute(0, 2, 3, 1).numpy()
    return [Image.fromarray((f * 255 + 0.5).astype(np.uint8)) for f in y]


def upscale_frames(net, frames, batch=6):
    out = []
    for i in range(0, len(frames), batch):
        chunk = frames[i:i + batch]
        w, h = chunk[0].size
        rgb = run(net, [bleed(f) for f in chunk])
        alp = run(net, [Image.merge("RGB", [f.getchannel("A")] * 3) for f in chunk])
        for c, a in zip(rgb, alp):
            c2 = c.resize((w * 2, h * 2), Image.LANCZOS)
            a2 = a.convert("L").resize((w * 2, h * 2), Image.LANCZOS)
            a2 = a2.point(lambda v: 0 if v < 26 else 255 if v > 232 else int((v - 26) * 255 / 206))   # 收紧过渡带
            c2.putalpha(a2)
            out.append(c2)
    return out


def main():
    arg = lambda k: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else None
    only, clip, test = arg("--only"), arg("--clip"), arg("--test")
    torch.set_num_threads(max(4, torch.get_num_threads()))
    net = load_model()
    files = sorted(p for p in (ROOT / "assets").glob("*/*.webp") if "@2x" not in p.name)
    for p in files:
        if (only and p.parent.name != only) or (clip and p.stem != clip):
            continue
        dst = p.with_name(p.stem + "@2x.webp")
        if dst.exists() and not test:
            continue
        t = time.time()
        im = Image.open(p)
        frames, durs = [], []
        for fr in ImageSequence.Iterator(im):
            frames.append(fr.convert("RGBA"))
            durs.append(fr.info.get("duration", 40) or 40)
        if test:
            up = upscale_frames(net, frames[:1])[0]
            bg = Image.new("RGBA", up.size, (40, 60, 90, 255))
            bg.alpha_composite(up)
            bg.save(test)
            print("test ->", test, f"{time.time() - t:.1f}s")
            return
        up = upscale_frames(net, frames)
        up[0].save(dst, save_all=True, append_images=up[1:], duration=durs, loop=0, quality=92, method=3,
                   minimize_size=False, allow_mixed=False)
        print(f"{p.parent.name}/{p.stem}: {len(up)} 帧 {up[0].size} {dst.stat().st_size // 1024}KB {time.time() - t:.0f}s", flush=True)


if __name__ == "__main__":
    main()
