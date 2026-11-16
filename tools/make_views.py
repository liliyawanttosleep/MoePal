"""从三视图里抠出 正面 / 侧面 / 背面 三张透明立绘，并导入常规比例立绘，供“角色档案”和“转身”动作使用。
Cut the turnaround sheet into transparent front / side / back views; copy the full-body illustration.

白底抠图：只去掉“从图片边缘连通进来的近白色区域”，角色身上被线稿围住的白色（头发、裙子）不受影响。
"""
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "Desktop" / "AI动漫助手_角色素材"
REF = {"gpt": "01_GPT白色龙娘", "claude": "02_Claude橙色大小姐", "deepseek": "03_DeepSeek蓝色大肥鱼"}
THRESH = 236


def cutout(img: Image.Image) -> Image.Image:
    rgb = np.asarray(img.convert("RGB"), dtype=np.int16)
    near_white = (rgb.min(axis=2) >= THRESH)
    m = Image.fromarray((near_white * 255).astype(np.uint8))
    m = Image.eval(m, lambda v: 255 if v else 0)
    w, h = m.size
    canvas = Image.new("L", (w + 2, h + 2), 255)          # 外围加一圈白，保证从角落能灌到所有边缘
    canvas.paste(m, (1, 1))
    ImageDraw.floodfill(canvas, (0, 0), 128)
    bg = np.asarray(canvas.crop((1, 1, w + 1, h + 1))) == 128
    alpha = Image.fromarray(((~bg) * 255).astype(np.uint8))
    alpha = alpha.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.8))   # 收 1px 白边再柔化
    out = img.convert("RGBA")
    out.putalpha(alpha)
    bb = alpha.point(lambda v: 255 if v > 30 else 0).getbbox()
    return out.crop(bb) if bb else out


def main():
    for cid, folder in REF.items():
        out = ROOT / "assets" / cid
        sheet = SRC / folder / "首选_Q版三视图_社区派生.png"
        if sheet.exists():
            im = Image.open(sheet)
            w = im.width // 3
            for i, name in enumerate(("front", "side", "back")):
                c = cutout(im.crop((i * w, 0, (i + 1) * w, im.height)))
                c.save(out / f"view_{name}.png")
                print(cid, name, c.size)
        ill = SRC / folder / "补充_常规比例立绘_网站虽标Q版.webp"
        if ill.exists():
            shutil.copy2(ill, out / "illust.webp")


if __name__ == "__main__":
    main()
