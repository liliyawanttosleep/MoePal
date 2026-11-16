"""把桌面素材文件夹里的动图导入 assets/（英文短名），程序只读 assets/。
Import the animated WebPs from the Desktop asset folder into assets/ under short ASCII names.

用法 / usage:  .venv\\Scripts\\python tools\\import_assets.py  [素材根目录]
"""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "Desktop" / "AI动漫助手_角色素材"

ACTIONS = {
    "01_开心跳跃": "jump", "02_伤心擦泪": "wipe", "03_难过低头": "down", "04_焦虑搓手": "anxious",
    "05_忧郁发呆": "daze", "06_惊讶后退": "startle", "07_悲伤坐哭": "cry", "08_释然挥手": "wave",
    "09_待机眨眼": "idle", "10_翻书阅读": "read", "11_轻声哼曲": "hum", "12_专属互动": "special",
}
V2 = {"gpt": "GPT", "claude": "Claude", "deepseek": "DeepSeek"}
V1 = {"gpt": "GPT白龙娘", "claude": "Claude大小姐", "deepseek": "DeepSeek大肥鱼"}
REF = {"gpt": "01_GPT白色龙娘", "claude": "02_Claude橙色大小姐", "deepseek": "03_DeepSeek蓝色大肥鱼"}


def main():
    report = []
    for cid in V2:
        out = ROOT / "assets" / cid
        out.mkdir(parents=True, exist_ok=True)
        for zh, key in ACTIONS.items():
            v2 = SRC / "情绪动作_v2" / V2[cid] / "Q版" / zh / "流畅动画.webp"
            v1 = SRC / "情绪动作_v1" / V1[cid] / "动态WebP" / f"{zh}.webp"
            if v2.exists():
                shutil.copy2(v2, out / f"{key}.webp")
                shutil.copy2(v2.parent / "静态.png", out / f"{key}.png")
                report.append(f"{cid}/{key}: v2 (8 关键帧 + 光流补帧)")
            elif v1.exists():
                shutil.copy2(v1, out / f"{key}.webp")
                st = SRC / "情绪动作_v1" / V1[cid] / "静态PNG" / f"{zh}.png"
                if st.exists():
                    shutil.copy2(st, out / f"{key}.png")
                report.append(f"{cid}/{key}: v1 (4 帧)")
            else:
                report.append(f"{cid}/{key}: 缺失 → 运行时用替代动作 + 程序化动画")
        ref = SRC / REF[cid] / "首选_Q版三视图_社区派生.png"
        if ref.exists():
            shutil.copy2(ref, out / "turnaround.png")
    lic = ROOT / "assets" / "LICENSES"
    lic.mkdir(exist_ok=True)
    for f in (SRC / "来源与许可").glob("*"):
        shutil.copy2(f, lic / f.name)
    shutil.copy2(SRC / "先读我_来源与选用说明.txt", lic / "先读我_来源与选用说明.txt")
    (ROOT / "assets" / "import_report.txt").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))


if __name__ == "__main__":
    main()
