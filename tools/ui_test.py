"""界面联调：真的开窗口，用脚本代替人操作一遍，并截图。 Scripted end-to-end UI run with screenshots.
用法: .venv\\Scripts\\python tools\\ui_test.py <截图输出目录> [角色id]
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from PIL import ImageGrab
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from moepal.audio import Audio, Listener
from moepal.brain import Brain
from moepal.config import Settings
from moepal.pet import Pet

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
app = QApplication(sys.argv)
s = Settings()
s.save = lambda: None                      # 测试不改用户设置
if len(sys.argv) > 2:
    s.d["character"] = sys.argv[2]
s.d["pos"] = None
from moepal.voice import Voice
pet = Pet(s, Audio(s), Listener(s), Brain(s), Voice(s))
pet.save_state = lambda: None
pet.desk_timer.stop()                      # 测试期间不让“空闲→睡觉”打断
t0 = time.time()
log = lambda m: print(f"{time.time() - t0:5.1f}s {m}", flush=True)


def shot(name):
    g = pet.geometry()
    k = app.primaryScreen().devicePixelRatio()
    box = (int((g.left() - 260) * k), int((g.top() - 230) * k), int((g.right() + 40) * k), int((g.bottom() + 70) * k))
    ImageGrab.grab(bbox=box, include_layered_windows=True).save(out / f"{name}.png")
    log(f"shot {name}: action={pet.stage.action} mode={pet.mode} busy={pet.busy} speaking={pet.speaking} "
        f"bubble={pet.bubble.full[:40]!r} mood={pet.mood_text()}")


pet.brain.reply.connect(lambda n, emo, txt, b, th: log(f"REPLY via {b} [{emo}] ({th}) {txt}"))
pet.brain.failed.connect(lambda n, m: log(f"FAILED {m}"))
pet.brain.notice.connect(lambda m: log(f"NOTICE {m}"))
pet.voice.seg_started.connect(lambda n, a, b, d: log(f"TTS seg {a:.2f}-{b:.2f} {d:.1f}s"))
pet.voice.failed.connect(lambda n, m: log(f"TTS failed {m}"))


def show_menu():
    from PySide6.QtCore import QPoint
    pet._menu = pet.build_menu()
    pet._menu.popup(pet.pos() + QPoint(-250, 30))

steps = [
    (2.5, lambda: shot("1_hello")),
    (5.5, pet.open_chat),
    (7.0, lambda: shot("2_ask_task")),
    (9.0, lambda: pet.on_user_text("唉，回归分析作业还有三道题没写，明天就要交了，好烦啊")),
    (10.2, lambda: shot("3_thinking")),
    (21.0, lambda: shot("4_reply")),
    (22.6, show_menu),
    (23.4, lambda: shot("4b_menu")),
    (23.6, lambda: pet._menu.close()),
    (24.0, lambda: (pet._end_say(), pet.act("dizzy", sfx="dizzy"))),
    (25.0, lambda: shot("5_dizzy")),
    (27.5, lambda: (setattr(pet, "petting", True), setattr(pet, "pet_last", time.time() + 2))),
    (28.6, lambda: shot("6_pet")),
    (30.0, lambda: (setattr(pet, "petting", False), pet.on_user_text("哈哈其实我刚把第一题搞定了！"))),
    (38.0, lambda: shot("7_reply2")),
    (44.0, pet.hide_away),
    (47.5, lambda: shot("8_peek")),
    (48.5, lambda: pet.summon()),
    (50.5, lambda: shot("9_back")),
    (53.0, app.quit),
]
for t, fn in steps:
    QTimer.singleShot(int(t * 1000), fn)
app.exec()
