"""把全部“组合动作”离屏渲染成透明 WebP + GIF（和桌宠里看到的是同一套渲染代码）。
Render every composite action offscreen to transparent WebP + GIF.

用法: .venv\\Scripts\\python tools\\export_actions.py [输出目录] [--only gpt]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtCore import Qt

from moepal.characters import CHARACTERS
from moepal.stage import ACTIONS, Stage

FPS, SCALE = 15, 0.75
SKIP = {"drag", "peek", "walk", "talk", "mope", "hide"}


def render(char, name, seconds):
    st = Stage(char)
    st.rng.seed(7)
    st.set_action(name)
    w, h = int(Stage.W * SCALE), int(Stage.H * SCALE)
    frames = []
    for _ in range(int(seconds * FPS)):
        st.advance(1 / FPS)
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        st.paint(p, SCALE, shadow=False)
        p.end()
        img = img.convertToFormat(QImage.Format.Format_RGBA8888)
        frames.append(Image.frombuffer("RGBA", (w, h), bytes(img.constBits()), "raw", "RGBA", img.bytesPerLine(), 1).copy())
    return frames


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    out = Path(args[0]) if args else Path.home() / "Desktop" / "AI动漫助手_角色素材" / "情绪动作_v3_组合动作"
    app = QGuiApplication(sys.argv)   # noqa: F841  字体渲染需要
    names = {"gpt": "GPT白龙娘", "claude": "Claude大小姐", "deepseek": "DeepSeek大肥鱼"}
    index = []
    for cid, char in CHARACTERS.items():
        if only and cid != only:
            continue
        d = out / names[cid]
        d.mkdir(parents=True, exist_ok=True)
        for i, (name, spec) in enumerate((k, v) for k, v in ACTIONS.items() if k not in SKIP):
            secs = min(spec[4] or 3.2, 3.6)
            frames = render(char, name, secs)
            stem = f"{i + 1:02d}_{spec[5]}"
            frames[0].save(d / f"{stem}.webp", save_all=True, append_images=frames[1:], duration=int(1000 / FPS),
                           loop=0, lossless=False, quality=82, method=4)
            frames[0].save(d / f"{stem}.gif", save_all=True, append_images=frames[1:], duration=int(1000 / FPS),
                           loop=0, disposal=2)
            index.append(f"{names[cid]}/{stem}  ← 序列帧「{spec[0]}」× 运动「{spec[1].__name__[2:]}」× 粒子「{spec[2] or '无'}」")
            print(index[-1], flush=True)
    (out / "说明.txt").write_text(
        "情绪动作 v3：组合动作\n"
        "每个动作 = 已有序列帧 × 程序化运动（弹跳/摇摆/旋转/挤压…）× 表情粒子，由 D:\\MoePal\\tools\\export_actions.py 渲染。\n"
        "没有新画的美术帧；人物画面全部来自 v1/v2 素材，来源与许可限制（署名、非商业）继续适用。\n"
        "DeepSeek 的 待机/翻书/哼歌/专属互动 四段在 v2 中缺失，这里用其他序列帧替代。\n\n" + "\n".join(index),
        encoding="utf-8")


if __name__ == "__main__":
    main()
