"""在真实窗口上测流畅度：记录每次 paintEvent 的时间间隔、画一帧的耗时，以及素材本身相邻帧的变化量。
Measure on-screen smoothness: paint intervals, paint cost, and how much consecutive source frames differ.
用法: .venv\\Scripts\\python tools\\fps_probe.py [秒数] [动作...]
静音运行；窗口会在屏幕上出现几秒。
"""
import statistics as st
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from moepal.audio import Audio, Listener
from moepal.brain import Brain
from moepal.config import Settings
from moepal.pet import Pet
from moepal.voice import Voice

secs = float(sys.argv[1]) if len(sys.argv) > 1 else 4
acts = sys.argv[2:] or ["idle", "happy", "hum"]
app = QApplication(sys.argv)
s = Settings()
s.save = lambda: None
s.d.update(voice_mode="mute", proactive=False, stroll=False, sfx_volume=0.0)
pet = Pet(s, Audio(s), Listener(s), Brain(s), Voice(s))
pet.save_state = lambda: None
pet.desk_timer.stop()
pet.next_idle_var = time.time() + 1e9
QTimer.singleShot(0, pet.bubble.hide)

marks = []
orig = pet.paintEvent


def probe(e):
    t0 = time.perf_counter()
    orig(e)
    marks.append((t0, time.perf_counter() - t0))


pet.paintEvent = probe
results = []


def run(i=0):
    if i:
        a = acts[i - 1]
        iv = [b[0] - a_[0] for a_, b in zip(marks, marks[1:])]
        cost = [m[1] * 1000 for m in marks]
        iv_ms = sorted(x * 1000 for x in iv) or [0]
        late = sum(1 for x in iv_ms if x > 1.8 * 1000 / pet.fps)
        results.append(f"{a:8} 目标 {pet.fps:.0f}fps | 实际 {len(marks) / secs:.0f} 次/秒 | 间隔 中位 {st.median(iv_ms):.1f}ms "
                       f"95% {iv_ms[int(len(iv_ms) * .95)]:.1f}ms 最长 {iv_ms[-1]:.0f}ms | 掉帧 {late} | 画一帧 平均 {st.mean(cost):.1f}ms")
    if i >= len(acts):
        print("\n".join(results))
        app.quit()
        return
    pet.oneshot_end = time.time() + secs + 5
    pet.stage.set_action(acts[i])
    marks.clear()
    QTimer.singleShot(int(secs * 1000), lambda: run(i + 1))


QTimer.singleShot(2500, run)
app.exec()
