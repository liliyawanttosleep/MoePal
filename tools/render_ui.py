"""不上屏、不出声地把界面渲染成图片（开发时用户可能正在全屏玩游戏，不能弹窗打扰）。
Render the UI to images without showing anything on screen or playing sound.
用法: .venv\\Scripts\\python tools\\render_ui.py <输出目录> [角色id]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from PIL import Image
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

from moepal import widgets
from moepal.audio import Audio, Listener
from moepal.brain import Brain
from moepal.config import Settings
from moepal.pet import Pet
from moepal.voice import Voice

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
cid = sys.argv[2] if len(sys.argv) > 2 else "gpt"
app = QApplication(sys.argv)
s = Settings()
s.save = lambda: None
s.d.update(character=cid, voice_mode="mute", pos=None, scale=0.8)


def grab(w, name):
    w.grab().toImage().save(str(out / f"{name}.png"))
    return Image.open(out / f"{name}.png").convert("RGBA")


def build(dark):
    widgets.is_dark = lambda: dark
    pet = Pet(s, Audio(s), Listener(s), Brain(s), Voice(s))
    pet.save_state = lambda: None
    pet.timer.stop()
    pet.desk_timer.stop()
    for w in (pet, pet.bubble, pet.inputbar):
        w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    return pet


def sheet(dark, tag):
    pet = build(dark)
    pet._recolor()
    bg = (24, 26, 34, 255) if dark else (226, 230, 238, 255)
    canvas = Image.new("RGBA", (1500, 900), bg)
    k = app.primaryScreen().devicePixelRatio()
    # 角色（三个动作）
    x = 20
    for act in ("idle", "raincloud", "dance"):
        pet.stage.set_action(act)
        for _ in range(40):
            pet.stage.advance(1 / 30)
        im = grab(pet, f"{tag}_pet_{act}")
        canvas.alpha_composite(im, (x, 330))
        x += im.width - 60
    # 气泡：带心里话的回复 / 等待中的三个点
    pet.bubble.layout_for("唉，三道题还没写，明天就要交，难怪你觉得烦呀。\n别急，我们一道一道来，好不好？", "（他今天好像真的很累）")
    pet.bubble.reveal(999)
    canvas.alpha_composite(grab(pet.bubble, f"{tag}_bubble"), (20, 10))
    pet.bubble.show_dots()
    canvas.alpha_composite(grab(pet.bubble, f"{tag}_dots"), (20, 200))
    pet.bubble.dot_timer.stop()
    # 输入栏
    pet.inputbar.edit.setText("")
    canvas.alpha_composite(grab(pet.inputbar, f"{tag}_input"), (260, 200))
    pet.inputbar.edit.setText("哈哈其实我刚把第一题搞定了！")
    pet.inputbar.restyle(True)
    canvas.alpha_composite(grab(pet.inputbar, f"{tag}_input_rec"), (680, 200))
    pet.inputbar.pulse.stop()
    # 菜单 + 一个子菜单
    m = pet.build_menu()
    m.ensurePolished()
    m.adjustSize()
    im = grab(m, f"{tag}_menu")
    canvas.alpha_composite(im, (1500 - im.width - 300, 20))
    for a in m.actions():
        if a.menu() and a.text() == "声音":
            sm = a.menu()
            for b in sm.actions():
                if b.menu():
                    sm = b.menu()
            sm.ensurePolished()
            sm.adjustSize()
            im2 = grab(sm, f"{tag}_submenu")
            canvas.alpha_composite(im2, (1500 - im2.width - 10, 60))
    canvas.save(out / f"sheet_{tag}_{cid}.png")
    # 角色档案窗口 + 转身动作的三个瞬间
    from moepal.config import ASSETS
    pet.profwin.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    pet.profwin.load(pet.char, pet.theme, ASSETS / pet.char.id)
    pet.profwin.show()
    app.processEvents()
    grab(pet.profwin, f"{tag}_profile")
    pet.profwin.hide()
    strip = Image.new("RGBA", (1400, 460), bg)
    x = 0
    for tsec in (0.35, 1.1, 1.85, 2.6, 0.75):
        pet.stage.set_action("turn")
        import time as _t
        for _ in range(int(tsec * 60)):
            pet.stage.advance(1 / 60)
        im = grab(pet, f"{tag}_turn")
        strip.alpha_composite(im, (x, 0))
        x += 270
    strip.save(out / f"turn_{tag}_{cid}.png")
    print("sheet", tag, "dpr", k)


sheet(True, "dark")
sheet(False, "light")
QTimer.singleShot(0, app.quit)
app.exec()
