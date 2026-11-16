"""生成应用图标 assets/icon.ico，以及 Claude Code 主题 mod 用的背景图。
Builds the app icon and the backdrop picture for the Claude Code look mod.
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
A = ROOT / "assets"


def icon():
    S = 512
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # 三位角色的代表色做成渐变圆环：薰衣草紫 / 橙 / 蓝
    ring = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    px = ring.load()
    import math
    cols = [(185, 164, 232), (232, 135, 58), (61, 111, 214)]
    for y in range(S):
        for x in range(S):
            a = (math.atan2(y - S / 2, x - S / 2) / math.tau + 0.75) % 1 * 3
            i, f = int(a) % 3, a - int(a)
            c0, c1 = cols[i], cols[(i + 1) % 3]
            px[x, y] = tuple(int(c0[k] + (c1[k] - c0[k]) * f) for k in range(3)) + (255,)
    mask = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse((8, 8, S - 8, S - 8), fill=255)
    img.paste(ring, (0, 0), mask)
    inner = Image.new("L", (S, S), 0)
    ImageDraw.Draw(inner).ellipse((40, 40, S - 40, S - 40), fill=255)
    img.paste(Image.new("RGBA", (S, S), (255, 250, 244, 255)), (0, 0), inner)
    head = Image.open(A / "gpt" / "idle.png").convert("RGBA").crop((40, 78, 250, 288))
    head = head.resize((S - 60, S - 60), Image.LANCZOS)
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    layer.paste(head, (30, 44), head)
    img = Image.alpha_composite(img, Image.composite(layer, Image.new("RGBA", (S, S), (0, 0, 0, 0)), mask))
    img.save(A / "icon.png")
    img.save(A / "icon.ico", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    print("icon ->", A / "icon.ico")


def backdrop(out: Path, ground=(33, 24, 19)):
    """深色暖棕底 + 右下角半透明的 Claude 大小姐。底色必须等于主题的 surface-0。"""
    W, H = 2560, 1600
    bg = Image.new("RGB", (W, H), ground)
    glow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(glow).ellipse((W * 0.62, H * 0.38, W * 1.02, H * 1.12), fill=70)
    glow = glow.filter(ImageFilter.GaussianBlur(190))
    bg = Image.composite(Image.new("RGB", (W, H), (232, 135, 58)), bg, glow.point(lambda v: int(v * 0.5)))
    ch = Image.open(A / "claude" / "idle.png").convert("RGBA")
    k = 2.1
    ch = ch.resize((int(ch.width * k), int(ch.height * k)), Image.LANCZOS)
    alpha = ch.getchannel("A").point(lambda v: int(v * 0.5))
    ch.putalpha(alpha)
    bg.paste(ch, (int(W * 0.80 - ch.width / 2), int(H * 0.93 - ch.height * 0.93)), ch)
    # 几片飘落的橙色花瓣
    import random
    r = random.Random(4)
    pet = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(pet)
    for _ in range(26):
        x, y, s = r.uniform(W * .45, W), r.uniform(0, H), r.uniform(10, 26)
        d.ellipse((x, y, x + s, y + s * .6), fill=(240, 150, 80, r.randint(18, 46)))
    bg = Image.alpha_composite(bg.convert("RGBA"), pet.filter(ImageFilter.GaussianBlur(1.2))).convert("RGB")
    out.parent.mkdir(parents=True, exist_ok=True)
    bg.save(out, quality=86, optimize=True)
    print("backdrop ->", out, out.stat().st_size, "bytes")


if __name__ == "__main__":
    icon()
    backdrop(ROOT / "claude-mod" / "ojousama-study" / "art" / "ojousama.jpg")
