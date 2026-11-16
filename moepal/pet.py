"""桌宠本体：窗口、情绪、鼠标互动、桌面反应、对话流程。 The pet itself."""
import math
import random
import time
from collections import deque
from datetime import datetime

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QCursor, QPainter
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QWidget

from .brain import BACKEND_ZH, quick_sentiment
from .characters import CHARACTERS
from .config import load_json, save_json
from .deskctx import DeskState
from .stage import ACTIONS, EMO_ACTION, Stage
from .widgets import (FLAGS, Bubble, HistoryWindow, InputBar, Theme, menu_header, screen_rect_at,
                      style_menu)

EMO_SFX = {"happy": "happy", "love": "pet", "cheer": "cheer", "proud": "happy", "surprised": "surprise",
           "sad": "sad", "cry": "sad", "worried": "think", "angry": "angry", "sleepy": "sleep", "curious": "poke"}
EMO_MOOD = {"happy": (.12, .1), "love": (.15, .05), "cheer": (.1, .15), "proud": (.1, .1), "sad": (-.12, -.08),
            "cry": (-.2, -.1), "worried": (-.08, .02), "angry": (-.12, .12), "sleepy": (0, -.15)}


def clamp(x, a, b):
    return max(a, min(b, x))


class Pet(QWidget):
    quit_requested = Signal()

    def __init__(self, settings, audio, listener, brain, voice):
        super().__init__(None, FLAGS | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.s, self.audio, self.listener, self.brain, self.voice = settings, audio, listener, brain, voice
        self.char = CHARACTERS.get(self.s["character"], CHARACTERS["gpt"])
        self.stage = Stage(self.char)
        self.stage.bank.preload()
        st = load_json("state.json", {})
        self.mood = st.get("mood", {"v": 0.2, "e": 0.2, "a": 30.0})
        self.memory = st.get("memory", {"task": "", "task_day": "", "facts": []})
        self.history = st.get("history", {})          # char_id -> [[role, text], ...]
        self.desk = DeskState()
        self.rng = random.Random()

        self.mode = "normal"        # normal | sleep | drag | walk | peek
        self.busy = None            # None | listen | think
        self.speaking = False
        self.speak_base = "talk"
        self.petting = False
        self.oneshot_end = 0.0
        self.pending = {}           # brain 请求号 -> "chat" | "proactive"
        self.awaiting_task = False

        # 鼠标轨迹
        self.samples = deque(maxlen=64)
        self.hover = False
        self.hover_since = 0.0
        self.pet_last = 0.0
        self.cool = {}
        self.pokes = deque(maxlen=12)
        self.press_pos = None
        self.dragging = False
        self.drag_off = QPoint()
        self.walk_target = None
        self.next_idle_var = time.time() + 20
        self.next_stroll = time.time() + self.rng.uniform(70, 160)
        self.sleep_since = 0.0
        self.last_chatter = time.time() - 400
        self.last_ctx = {}
        self.cat_since = (None, time.time())
        self.last_break = time.time()
        self.last_proactive_llm = time.time()

        # 说话
        self.say_id = 0
        self.tts_req = 0
        self.tts_done = True
        self.type_timer = QTimer(self)
        self.type_timer.timeout.connect(self._type_step)
        self.babble = False
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self._end_say)
        self.type_target = 0

        self.bubble = Bubble()
        self.bubble.clicked.connect(self._end_say)
        self.inputbar = InputBar()
        self.inputbar.submitted.connect(self.on_user_text)
        self.inputbar.mic.connect(self.toggle_mic)
        self.histwin = HistoryWindow()
        self._recolor()

        voice.seg_started.connect(self._tts_started)
        voice.done.connect(self._tts_finished)
        voice.failed.connect(self._tts_failed)
        listener.state.connect(self._mic_state)
        listener.level.connect(self._mic_level)
        listener.text.connect(self._mic_text)
        listener.failed.connect(self._mic_failed)
        brain.reply.connect(self._on_reply)
        brain.failed.connect(self._on_fail)
        brain.notice.connect(lambda m: self.inputbar.set_status("提示：" + m[:60]))

        self._resize()
        self._place_initial()
        self.last_tick = time.time()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.start(16)                # 60fps：程序化运动和粒子按显示器刷新率走
        self.desk_timer = QTimer(self)
        self.desk_timer.timeout.connect(self.desk_tick)
        self.desk_timer.start(4000)
        QTimer.singleShot(600, self.first_hello)

    # ------------------------------------------------------------ 基础
    @property
    def scale(self):
        return float(self.s["scale"])

    def _resize(self):
        self.resize(int(Stage.W * self.scale), int(Stage.H * self.scale))

    def _recolor(self):
        self.theme = Theme(self.char.color)
        self.bubble.set_theme(self.theme, self.char.short)
        self.inputbar.set_theme(self.theme)

    def voice_spec(self):
        vid = self.s["voice_" + self.char.id]
        for v in self.char.voices:
            if v["id"] == vid:
                return v
        return self.char.voices[0]

    def _place_initial(self):
        area = QApplication.primaryScreen().availableGeometry()
        pos = self.s["pos"]
        if pos and QApplication.screenAt(QPoint(pos[0] + 50, pos[1] + 50)):
            self.move(pos[0], pos[1])
        else:
            self.move(area.right() - self.width() - 40, area.bottom() - self.height() + 10)
        self.home = self.pos()

    def body_global(self) -> QRect:
        r = self.stage.body_rect(self.scale).toRect()
        r.translate(self.pos())
        return r

    def hist(self):
        return self.history.setdefault(self.char.id, [])

    def save_state(self):
        for k in self.history:
            self.history[k] = self.history[k][-40:]
        save_json("state.json", {"mood": self.mood, "memory": self.memory, "history": self.history})

    def cooled(self, key, seconds):
        now = time.time()
        if now - self.cool.get(key, 0) >= seconds:
            self.cool[key] = now
            return True
        return False

    def line(self, key):
        opts = self.char.lines.get(key)
        return self.rng.choice(opts) if opts else ""

    def sfx(self, event, vol=1.0):
        self.audio.play(self.char.id, event, vol)

    def shift_mood(self, dv=0.0, de=0.0, da=0.0):
        self.mood["v"] = clamp(self.mood["v"] + dv, -1, 1)
        self.mood["e"] = clamp(self.mood["e"] + de, -1, 1)
        self.mood["a"] = clamp(self.mood["a"] + da, 0, 100)

    def mood_text(self):
        v, e, a = self.mood["v"], self.mood["e"], self.mood["a"]
        feel = ("很开心" if v > .55 else "心情不错" if v > .15 else "平静" if v > -.2 else "有点低落" if v > -.55 else "很难过")
        vigor = "精神很好" if e > .4 else "精神一般" if e > -.3 else "困困的"
        bond = "和用户很亲近" if a > 65 else "和用户渐渐熟了" if a > 35 else "和用户还不太熟，有点拘谨"
        return f"{feel}，{vigor}；{bond}（好感 {a:.0f}/100）"

    # ------------------------------------------------------------ 动作状态机
    def base_action(self):
        if self.mode == "peek":
            return "peek"
        if self.mode == "drag":
            return "drag"
        if self.mode == "sleep":
            return "sleep"
        if self.mode == "walk":
            return "walk"
        if self.busy == "listen":
            return "listen"
        if self.busy == "think":
            return "think"
        if self.petting:
            return "pet"
        if self.speaking:
            return self.speak_base
        if self.mood["v"] < -0.45:
            return "mope"
        return "idle"

    def refresh(self):
        if time.time() >= self.oneshot_end:
            b = self.base_action()
            if self.stage.action != b:
                self.stage.set_action(b)

    def act(self, name, dur=None, sfx=None, vol=1.0):
        """播放一次性动作，结束后回到基础状态。"""
        if name not in ACTIONS:
            name = "idle"
        d = dur or ACTIONS[name][4] or 2.0
        self.stage.set_action(name)
        self.oneshot_end = time.time() + d
        if sfx:
            self.sfx(sfx, vol)

    # ------------------------------------------------------------ 主循环
    def tick(self):
        now = time.time()
        dt = min(0.1, now - self.last_tick)
        self.last_tick = now
        cur = QCursor.pos()
        self.samples.append((now, cur.x(), cur.y()))
        self._mouse_logic(now, cur, dt)

        if self.mode == "walk" and self.walk_target is not None:
            x = self.x()
            step = 60 * self.scale / 0.72 * dt
            if abs(self.walk_target - x) <= step + 1:
                self.move(self.walk_target, self.y())
                self.mode, self.walk_target = "normal", None
                self.stage.flip = False
                self.home = self.pos()
                self.s["pos"] = [self.x(), self.y()]
            else:
                self.move(int(x + math.copysign(max(1, step), self.walk_target - x)), self.y())
            self._follow_widgets()

        # 情绪缓慢回归基线
        hour = datetime.now().hour
        e_base = -0.35 if (hour >= 23 or hour < 6) else 0.25
        v_base = 0.1 + self.mood["a"] / 300
        self.mood["v"] += (v_base - self.mood["v"]) * dt / 240
        self.mood["e"] += (e_base - self.mood["e"]) * dt / 300

        # 闲时小动作
        if (self.mode == "normal" and not self.busy and not self.speaking and not self.petting
                and now >= self.oneshot_end):
            if now >= self.next_idle_var:
                self.next_idle_var = now + self.rng.uniform(18, 42)
                pool = ["hum", "read", "yawn", "float", "wave", "twirl", "special", "curious", "tiptoe",
                        "sneeze", "shower", "dance"]
                if self.mood["e"] < -0.2:
                    pool += ["yawn", "yawn", "coffee"]
                if self.mood["v"] > 0.4:
                    pool += ["hum", "twirl", "dance", "heartbeat"]
                if self.mood["v"] < -0.3:
                    pool = ["raincloud", "raincloud", "yawn", "read"]
                if self.desk.cat in ("code", "study") and self.desk.idle < 30:
                    pool += ["study", "focus", "study"]
                self.act(self.rng.choice(pool))
            elif (self.s["stroll"] and now >= self.next_stroll and not self.inputbar.isVisible()
                  and not self.hover and self.desk.idle < 120):
                self.next_stroll = now + self.rng.uniform(90, 240)
                self._start_stroll()

        self.refresh()
        self.stage.advance(dt)
        self.update()

    def _start_stroll(self):
        area = screen_rect_at(self.pos() + QPoint(self.width() // 2, self.height() // 2))
        lo, hi = area.left() + 10, area.right() - self.width() - 10
        tgt = int(clamp(self.x() + self.rng.choice((-1, 1)) * self.rng.uniform(120, 380), lo, hi))
        if abs(tgt - self.x()) < 60:
            return
        self.walk_target = tgt
        self.stage.flip = tgt < self.x()
        self.mode = "walk"

    # ------------------------------------------------------------ 鼠标互动
    def _mouse_logic(self, now, cur, dt):
        s = self.samples
        if len(s) < 3:
            return
        (t1, x1, y1), (t0, x0, y0) = s[-1], s[-2]
        speed = math.hypot(x1 - x0, y1 - y0) / max(1e-3, t1 - t0)
        # 最近 1.2 秒内的水平折返次数和路程
        rev, path, last_sign = 0, 0.0, 0
        px, py = None, None
        for t, x, y in s:
            if now - t > 1.2:
                continue
            if px is not None:
                dx = x - px
                path += math.hypot(dx, y - py)
                if abs(dx) > 3:
                    sg = 1 if dx > 0 else -1
                    if last_sign and sg != last_sign:
                        rev += 1
                    last_sign = sg
            px, py = x, y

        body = self.body_global()
        center = body.center()
        dist = math.hypot(cur.x() - center.x(), cur.y() - center.y())
        inside = body.contains(cur)
        on_head = inside and cur.y() < body.top() + body.height() * 0.45

        # 视线：身体朝鼠标微微倾斜
        tgt = clamp((cur.x() - center.x()) / 70, -4, 4) if (dist < 520 and self.mode == "normal") else 0
        self.stage.lean += (tgt - self.stage.lean) * min(1, dt * 5)

        if self.mode == "drag":
            vx = (x1 - x0) / max(1e-3, t1 - t0)
            self.stage.extra_rot += (clamp(-vx * 0.02, -28, 28) - self.stage.extra_rot) * min(1, dt * 8)
            return
        self.stage.extra_rot *= max(0, 1 - dt * 8)

        if self.mode == "peek":
            if inside and self.cooled("peek_wiggle", 2.5):
                self.sfx("poke", .4)
            return

        entered = inside and not self.hover
        if entered:
            self.hover_since = now
        self.hover = inside

        if self.mode == "sleep":
            if inside and speed > 40:
                self.wake_up()
            return
        if self.mode == "walk" and inside:
            self.mode, self.walk_target = "normal", None
            self.stage.flip = False

        # 1) 摸头：在头上来回蹭
        if on_head and self.press_pos is None and rev >= 3 and 60 < path:
            if not self.petting:
                self.petting = True
                self.oneshot_end = 0
                if self.cooled("pet_line", 14):
                    self.say(self.line("pet"), "love", action="pet", sfx="pet")
            self.pet_last = now
            self.shift_mood(dv=.25 * dt, de=.05 * dt, da=1.2 * dt)
            if self.cooled("purr", 1.5):
                self.sfx("purr", .8)
        elif self.petting and now - self.pet_last > 0.8:
            self.petting = False

        if self.petting or self.busy:
            return

        # 2) 在旁边疯狂晃鼠标 → 晕
        if dist < 340 and rev >= 8 and path > 2400 and self.cooled("dizzy", 9):
            self.act("dizzy", sfx="dizzy")
            self.say(self.line("dizzy"), None, action="dizzy", voice=self.rng.random() < .5)
            self.shift_mood(de=-.05)
            return
        # 3) 鼠标高速冲过来 → 吓一跳
        if entered and speed > 2600 and self.cooled("startle", 6):
            self.act("surprised", sfx="surprise")
            if self.rng.random() < .5:
                self.say(self.line("startle"), None, action="surprised", voice=False)
            return
        # 4) 慢慢靠近 → 歪头看你
        if entered and now >= self.oneshot_end and not self.speaking and self.cooled("hover", 25):
            self.act("curious", 1.4, sfx="poke", vol=.35)
        # 5) 一直盯着看 → 害羞
        if (inside and now - self.hover_since > 7 and speed < 30 and not self.speaking
                and not self.inputbar.isVisible() and self.cooled("shy", 120)):
            self.say(self.line("shy"), "shy")

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.press_pos = e.globalPosition().toPoint()
            self.drag_off = self.press_pos - self.pos()
            self.dragging = False

    def mouseMoveEvent(self, e):
        if self.press_pos is None:
            return
        g = e.globalPosition().toPoint()
        if not self.dragging and (g - self.press_pos).manhattanLength() > 6:
            if self.mode == "peek":
                return
            self.dragging = True
            self.mode = "drag"
            self.petting = False
            self.oneshot_end = 0
            self.sfx("surprise", .6)
            if self.rng.random() < .45:
                self.say(self.line("drag"), None, action="drag", voice=False)
        if self.dragging:
            self.move(g - self.drag_off)
            self._follow_widgets()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self.press_pos is None:
            return
        self.press_pos = None
        if self.dragging:
            self.dragging = False
            self.mode = "normal"
            area = screen_rect_at(self.pos() + QPoint(self.width() // 2, self.height() // 2))
            self.move(int(clamp(self.x(), area.left() - 40, area.right() - self.width() + 40)),
                      int(clamp(self.y(), area.top() - 40, area.bottom() - self.height() + 12)))
            self.home = self.pos()
            self.s["pos"] = [self.x(), self.y()]
            self.act("land", sfx="land")
            self._follow_widgets()
            if self.rng.random() < .3:
                self.say(self.line("land"), None, action="land", voice=False)
        elif self.mode == "peek":
            self.summon()
        else:
            self.poke()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.mode != "peek":
            self.open_chat()

    def wheelEvent(self, e):
        k = clamp(self.scale + (0.04 if e.angleDelta().y() > 0 else -0.04), 0.45, 1.3)
        bottom, cx = self.y() + self.height(), self.x() + self.width() // 2
        self.s["scale"] = round(k, 2)
        self._resize()
        self.move(cx - self.width() // 2, bottom - self.height())
        self._follow_widgets()

    def poke(self):
        now = time.time()
        if self.mode == "sleep":
            self.wake_up()
            return
        self.pokes.append(now)
        recent = sum(1 for t in self.pokes if now - t < 4)
        if recent >= 5:
            self.pokes.clear()
            self.shift_mood(dv=-.18, de=.1, da=-1)
            self.say(self.line("poke_angry"), "angry")
            return
        self.shift_mood(de=.03, da=.2)
        self.act(self.rng.choice(["curious", "surprised", "happy", "twirl", "wave"]), 1.3, sfx="poke")
        if recent <= 2 and self.rng.random() < .55 and not self.speaking:
            self.say(self.line("poke"), None, action=self.stage.action, voice=self.rng.random() < .6)

    # ------------------------------------------------------------ 说话
    def say(self, text, emo="calm", action=None, voice=True, sfx=None, hold=None, thought=""):
        if not text:
            return
        self.say_id += 1
        self.hide_timer.stop()
        self.type_timer.stop()
        emo_key = emo or "calm"
        act = action or EMO_ACTION.get(emo_key, "talk")
        self.speaking = True
        self.speak_base = "talk" if ACTIONS[act][4] else act
        if act != "talk":
            self.act(act)
        ev = sfx or (EMO_SFX.get(emo_key) if emo else None)
        mode = self.s["voice_mode"]
        if ev:
            self.sfx(ev, .45 if (voice and mode == "tts") else 1.0)
        shown = text if len(text) <= 360 else text[:350] + "…（完整内容在“对话记录”里）"
        self._hold = hold
        self.bubble.below = False
        self.bubble.layout_for(shown, f"（{thought}）" if thought else "")
        self._place_bubble()
        self.bubble.reveal(0)
        self.bubble.fade_in()
        self.babble = False
        self.tts_done = True
        self.tts_req = 0
        self.type_target = len(shown)
        if voice and mode == "tts":
            self.tts_done = False
            self.type_target = 0               # 字跟着声音走：哪一句开始念，哪一句才出字
            self.tts_req = self.voice.speak(self.voice_spec(), text, self.mood["v"], self.mood["e"])
            self.type_timer.start(60)
            sid = self.say_id
            QTimer.singleShot(8000, lambda: self._tts_timeout(sid))
        elif voice and mode == "babble":
            self.babble = True
            self.type_timer.start(62)
        else:
            self.type_timer.start(38)

    def _tts_timeout(self, sid):
        if sid == self.say_id and not self.tts_done and self.type_target == 0:
            self._tts_failed(self.tts_req, "timeout")

    def _tts_started(self, n, a, b, dur):
        if n != self.tts_req:
            return
        total = len(self.bubble.full)
        self.type_target = total if b >= 0.999 else int(round(total * b))
        left = max(1, self.type_target - self.bubble.shown)
        self.type_timer.start(int(clamp(dur * 1000 * 0.9 / left, 18, 240)))

    def _tts_finished(self, n):
        if n == self.tts_req:
            self.tts_done = True
            self._maybe_finish()

    def _tts_failed(self, n, msg):
        if n != self.tts_req or self.tts_done:
            return
        self.tts_done = True          # 合成失败（多半是断网）→ 改用角色的咿呀语
        self.voice.stop()
        self.type_target = len(self.bubble.full)
        self.babble = True
        self.type_timer.start(62)
        self._maybe_finish()

    def _type_step(self):
        if self.bubble.shown >= self.type_target and self.bubble.shown < len(self.bubble.full):
            return                                  # 等下一句的声音
        n = self.bubble.shown + 1
        self.bubble.reveal(n)
        if self.babble and n % 2 == 0 and n <= len(self.bubble.full):
            ch = self.bubble.full[n - 1]
            if not ch.isspace() and ch not in "，。！？…、,.!?~～":
                self.sfx(f"babble{ord(ch) % 8}", .8)
        if n >= len(self.bubble.full):
            self.type_timer.stop()
            self._maybe_finish()

    def _maybe_finish(self):
        if self.type_timer.isActive() or not self.tts_done:
            return
        hold = self._hold or clamp(1.6 + 0.07 * len(self.bubble.full), 2.2, 9)
        self.hide_timer.start(int(hold * 1000))

    def _end_say(self):
        self.type_timer.stop()
        self.hide_timer.stop()
        self.bubble.fade_out()
        self.voice.stop()
        self.tts_done = True
        self.speaking = False

    def _place_bubble(self):
        body = self.body_global()
        b = self.bubble
        area = screen_rect_at(body.center())
        x = body.center().x() - b.width() // 2
        y = body.top() - b.height() + int(18 * self.scale)
        y += 14                                   # 气泡自带阴影边距
        if y < area.top() + 4 and not b.dots:     # 上面放不下 → 放到脚下
            b.below = True
            b.layout_for(b.full, b.thought)
            y = body.bottom() - 10
        x2 = int(clamp(x, area.left() + 4, area.right() - b.width() - 4))
        b.tail_x = clamp((body.center().x() - x2) / max(1, b.width()), .12, .88)
        b.move(x2, y)
        b.update()

    def _follow_widgets(self):
        if self.bubble.isVisible():
            self._place_bubble()
        if self.inputbar.isVisible():
            self.inputbar.move(self._input_pos())

    def _input_pos(self):
        body = self.body_global()
        area = screen_rect_at(body.center())
        x = int(clamp(body.center().x() - self.inputbar.width() // 2, area.left() + 4,
                      area.right() - self.inputbar.width() - 4))
        y = body.bottom() - 10
        if y + self.inputbar.height() > area.bottom() + 12:
            y = area.bottom() - self.inputbar.height() + 12
        return QPoint(x, y)

    # ------------------------------------------------------------ 对话
    def open_chat(self):
        if self.mode == "sleep":
            self.wake_up()
        if self.mode == "walk":
            self.mode, self.walk_target, self.stage.flip = "normal", None, False
        self.inputbar.set_status("")
        self.inputbar.open_at(self._input_pos())
        today = datetime.now().strftime("%Y-%m-%d")
        if self.memory.get("task_day") != today and not self.awaiting_task:
            self.awaiting_task = True          # 每天第一次开聊：问问今天要做什么
            self._ask_task_when_free()

    def _ask_task_when_free(self):
        if not self.awaiting_task or not self.inputbar.isVisible():
            return
        if self.speaking or self.busy:
            QTimer.singleShot(1200, self._ask_task_when_free)
        else:
            self.say(self.line("ask_task"), "curious")

    def on_user_text(self, text):
        self._end_say()
        self.hist().append(["user", text])
        today = datetime.now().strftime("%Y-%m-%d")
        if self.awaiting_task or self.memory.get("task_day") != today:
            self.awaiting_task = False
            self.memory["task"], self.memory["task_day"] = text[:200], today
        elif any(k in text for k in ("我现在要", "我要做", "我在做", "今天要", "接下来要", "我准备")):
            self.memory["task"], self.memory["task_day"] = text[:200], today
        # 先用本地情绪判断立刻给出表情，不等模型
        sent = quick_sentiment(text)
        if sent < 0:
            self.shift_mood(dv=-.15)
            self.act("worried", 1.6, sfx="think")
        elif sent > 0:
            self.shift_mood(dv=.12, de=.08)
            self.act("happy", 1.2, sfx="happy", vol=.6)
        else:
            self.sfx("send")
        self.shift_mood(da=.6)
        self.busy = "think"
        self.inputbar.set_status(self.line("thinking"))
        desk = self.desk.describe() if self.s["share_desktop_context"] else ""
        prompt = self.brain.build_prompt(self.hist()[:-1], text, desk, self.mood_text(), self.memory)
        self.pending[self.brain.ask(self.char, prompt)] = "chat"
        self.histwin.render(self.hist(), self.char, self.theme) if self.histwin.isVisible() else None
        self._show_dots()

    def _show_dots(self):
        """等模型回答时：头顶气泡里三个跳动的点。"""
        self.hide_timer.stop()
        self.type_timer.stop()
        self.bubble.below = False
        self.bubble.show_dots()
        self._place_bubble()
        self.bubble.fade_in()

    def _on_reply(self, n, emo, text, backend, thought=""):
        kind = self.pending.pop(n, None)
        if kind is None:
            return
        if kind == "chat":
            self.busy = None
            self.inputbar.set_status("")
        elif self.speaking or self.busy or self.mode != "normal":
            return                                   # 主动搭话赶上用户正忙 → 丢弃
        self.hist().append(["pet", text])
        dv, de = EMO_MOOD.get(emo, (0, 0))
        was_low = self.mood["v"] < -0.3
        self.shift_mood(dv, de)
        if was_low and self.mood["v"] >= -0.3:
            QTimer.singleShot(6000, lambda: self.act("sunshine") if not self.speaking else None)
        self.oneshot_end = 0
        self.say(text, emo, thought=thought)
        if self.histwin.isVisible():
            self.histwin.render(self.hist(), self.char, self.theme)
        self.save_state()

    def _on_fail(self, n, msg):
        kind = self.pending.pop(n, None)
        if kind != "chat":
            return
        self.busy = None
        self.say(self.line("error"), "sad", voice=False)
        self.inputbar.set_status("连接失败：" + msg[:70])

    # ------------------------------------------------------------ 语音输入
    def toggle_mic(self):
        if not self.inputbar.isVisible():
            self.open_chat()
        self.listener.toggle()

    def _mic_state(self, st):
        if st == "recording":
            self._end_say()
            self.busy = "listen"
            self.oneshot_end = 0
            self.sfx("listen_on")
            self.inputbar.restyle(True)
            self.inputbar.set_status("在听…说完停一下就好（再点 🎤 结束）")
        elif st == "loading":
            self.busy = "think"
            self.inputbar.restyle(False)
            self.inputbar.set_status("第一次用语音：正在准备识别模型（约 460MB，只需一次）…")
        elif st == "transcribing":
            self.busy = "think"
            self.sfx("listen_off")
            self.inputbar.restyle(False)
            self.inputbar.level = 0
            self.inputbar.set_status("正在听写…")
        else:
            if self.busy == "listen":
                self.busy = None
            self.inputbar.restyle(False)
            self.inputbar.level = 0

    def _mic_level(self, v):
        self.inputbar.level = v
        self.inputbar.update()

    def _mic_text(self, text):
        self.busy = None
        self.inputbar.set_status("")
        self.on_user_text(text)

    def _mic_failed(self, msg):
        self.busy = None
        self.inputbar.restyle(False)
        self.inputbar.set_status(msg)
        if not msg:
            self.say(self.line("no_hear"), "curious", voice=False)

    # ------------------------------------------------------------ 出现 / 躲起来 / 睡觉
    def first_hello(self):
        self.desk.poll()
        if self.desk.full:                 # 启动时用户正全屏（游戏等）→ 先不露面，等退出全屏再打招呼
            QTimer.singleShot(5000, self.first_hello)
            return
        self.show()
        self.act("show", sfx="show")
        h = datetime.now().hour
        key = "morning" if 5 <= h < 11 else "noon" if 11 <= h < 14 else "evening" if 18 <= h < 23 else "greet"
        QTimer.singleShot(900, lambda: self.say(self.line(key) or self.line("greet"), "happy"))

    def summon(self, chat=False):
        was_away = self.mode == "peek" or not self.isVisible()
        if was_away:
            self.mode = "normal"
            self.move(self.home)
            self.show()
            self.raise_()
            self.act("show", sfx="show")
            self.shift_mood(dv=.08, de=.1)
            QTimer.singleShot(700, lambda: self.say(self.line("show"), "happy"))
        if chat:
            QTimer.singleShot(1500 if was_away else 0, self.open_chat)

    def hide_away(self):
        if self.mode == "peek":
            return
        self.inputbar.fade_out(120)
        self.home = self.pos()
        self.say(self.line("hide"), "calm", action="wave", sfx="hide", hold=1.2)
        QTimer.singleShot(1900, self._go_peek)

    def _go_peek(self):
        self._end_say()
        area = screen_rect_at(self.pos() + QPoint(self.width() // 2, self.height() // 2))
        self.mode = "peek"
        self.oneshot_end = 0
        self.move(area.right() - int(self.width() * 0.47), self.y())

    def toggle(self):
        """全局热键：不在 → 召唤并开聊；在但没开输入栏 → 开聊；已经在聊 → 躲起来。"""
        if self.mode == "peek" or not self.isVisible():
            self.summon(chat=True)
        elif not self.inputbar.isVisible():
            self.open_chat()
        else:
            self.hide_away()

    def vanish(self):
        self._end_say()
        self.inputbar.fade_out(120)
        if self.mode != "peek":
            self.home = self.pos()
        self.mode = "peek"
        self.hide()

    def wake_up(self):
        slept = time.time() - self.sleep_since
        self.mode = "normal"
        self.act("wake", sfx="wake")
        self.shift_mood(de=.3)
        if slept > 240:
            QTimer.singleShot(900, lambda: self.say(self.line("wake"), "happy"))

    # ------------------------------------------------------------ 桌面反应
    def can_chatter(self, gap=420):
        return (self.s["proactive"] and self.mode == "normal" and not self.busy and not self.speaking
                and not self.desk.full and self.isVisible() and time.time() - self.last_chatter > gap)

    def chatter(self, key, emo, action=None):
        text = self.line(key)
        if text:
            self.last_chatter = time.time()
            self.say(text, emo, action=action)

    def desk_tick(self):
        d = self.desk
        d.poll()
        now = time.time()
        # 前台是全屏程序（游戏、全屏视频）→ 整个人让开，不挡画面、不抢焦点、不出声；退出全屏再回来
        if d.full and self.isVisible() and not self.inputbar.isVisible() and not self.dragging:
            self._end_say()
            self.auto_hidden = True
            self.hide()
            return
        if getattr(self, "auto_hidden", False):
            if d.full:
                return
            self.auto_hidden = False
            if self.mode != "peek" or self.x() < 10 ** 6:
                self.show()
        if self.mode in ("normal", "walk") and d.idle > 240 and not self.busy and not self.speaking:
            self.mode, self.sleep_since = "sleep", now
            self.walk_target, self.stage.flip = None, False
            self.sfx("sleep", .6)
            self.inputbar.fade_out(120)
            return
        if self.mode == "sleep":
            if d.idle < 4:
                self.wake_up()
            return
        hour = datetime.now().hour
        if d.battery is not None and d.battery <= 20 and not d.plugged and self.cooled("battery", 600) \
                and self.can_chatter(30):
            return self.chatter("battery", "worried")
        if d.cpu_high_since and now - d.cpu_high_since > 25 and self.cooled("cpu", 900) and self.can_chatter(60):
            return self.chatter("cpu", "worried")
        if d.active_minutes >= 50 and now - self.last_break > 3000 and self.can_chatter(60):
            self.last_break = now
            return self.chatter("break", "worried")
        if 0 <= hour < 5 and d.idle < 60 and self.cooled("late", 2400) and self.can_chatter(120):
            return self.chatter("late", "worried", action="night")
        if 11 <= hour < 13 and self.cooled("noon", 86400) and self.can_chatter(120):
            return self.chatter("noon", "curious", action="hungry")
        # 前台应用换了类别并且稳定 20 秒 → 评论一句
        cat, since = self.cat_since
        if d.cat != cat:
            self.cat_since = (d.cat, now)
        elif (d.cat != "other" and now - since > 20 and now - self.last_ctx.get(d.cat, 0) > 2400
              and self.can_chatter()):
            self.last_ctx[d.cat] = now
            emo = {"code": "cheer", "study": "cheer", "video": "curious", "game": "happy"}.get(d.cat, "calm")
            return self.chatter("ctx_" + d.cat, emo)
        mins = float(self.s["proactive_llm_minutes"] or 0)
        if mins > 0 and now - self.last_proactive_llm > mins * 60 and d.idle < 90 and self.can_chatter(300):
            self.last_proactive_llm = self.last_chatter = now
            desk = d.describe() if self.s["share_desktop_context"] else ""
            prompt = self.brain.build_prompt(self.hist(), "", desk, self.mood_text(), self.memory, proactive=True)
            self.pending[self.brain.ask(self.char, prompt)] = "proactive"

    # ------------------------------------------------------------ 角色 / 菜单
    def set_char(self, cid):
        if cid == self.char.id or cid not in CHARACTERS:
            return
        self._end_say()
        self.char = CHARACTERS[cid]
        self.s["character"] = cid
        self.stage.set_char(self.char)
        self.stage.bank.preload()
        self._recolor()
        self.stage.set_action("idle")
        self.act("show", sfx="show")
        QTimer.singleShot(800, lambda: self.say(self.line("greet"), "happy"))

    def build_menu(self) -> QMenu:
        t = self.theme = Theme(self.char.color)       # 每次打开都重新读系统深浅色
        self.bubble.set_theme(t, self.char.short)
        self.inputbar.set_theme(t)

        def sub(parent, title):
            return style_menu(parent.addMenu(title), t)

        def radio(menu, items, current, setter):
            g = QActionGroup(menu)
            for key, label in items:
                a = QAction(label, menu, checkable=True, checked=(key == current))
                a.triggered.connect(lambda _=False, k=key: setter(k))
                g.addAction(a)
                menu.addAction(a)

        m = style_menu(QMenu(), t)
        menu_header(m, t, self.char.name, f"接入 {self.char.agent} · {self.mood_text().split('；')[0]}")
        m.addAction("和我聊天        双击", self.open_chat)
        m.addAction("语音输入", self.toggle_mic)
        m.addAction("对话记录", self.show_history)
        m.addSeparator()
        radio(sub(m, "切换角色"), [(c.id, f"{c.name}  ·  {c.agent}") for c in CHARACTERS.values()], self.char.id,
              self.set_char)
        am = sub(m, "动作表演")
        groups = (("情绪", ("happy", "cheer", "love", "shy", "proud", "surprised", "worried", "sad", "wipe", "cry",
                           "angry", "heartbeat", "raincloud", "sunshine")),
                  ("日常", ("hum", "read", "study", "focus", "coffee", "yawn", "sleep", "sneeze", "tiptoe", "wave",
                           "salute", "yes", "no", "idea", "curious", "think", "hungry", "night")),
                  ("表演", ("dance", "twirl", "backflip", "celebrate", "applaud", "moonwalk", "float", "special",
                           "shower", "dizzy")))
        for title, names in groups:
            g = sub(am, title)
            for n in names:
                g.addAction(ACTIONS[n][5], lambda n=n: self.act(n, ACTIONS[n][4] or 3.0,
                                                                sfx=EMO_SFX.get(n) or ("pet" if n == "love" else None)))
        vm = sub(m, "声音")
        radio(vm, (("tts", "开口说话"), ("babble", "咿呀语（离线）"), ("mute", "静音")), self.s["voice_mode"],
              lambda k: self.s.__setitem__("voice_mode", k))
        vm.addSeparator()
        from .voice import kokoro_available
        vs = [v for v in self.char.voices if v["engine"] != "kokoro" or kokoro_available()]
        radio(sub(vm, "声线（点一下试听）"), [(v["id"], v["label"]) for v in vs], self.voice_spec()["id"], self.pick_voice)
        om = sub(m, "行为")
        for key, label in (("proactive", "主动搭话"), ("share_desktop_context", "把桌面状态告诉模型"),
                           ("stroll", "偶尔散步")):
            a = QAction(label, om, checkable=True, checked=bool(self.s[key]))
            a.triggered.connect(lambda v, k=key: self.s.__setitem__(k, bool(v)))
            om.addAction(a)
        a = QAction("每 30 分钟主动关心一次（用模型）", om, checkable=True, checked=float(self.s["proactive_llm_minutes"]) > 0)
        a.triggered.connect(lambda v: self.s.__setitem__("proactive_llm_minutes", 30 if v else 0))
        om.addAction(a)
        wm = sub(m, "工作模式")
        on = bool(self.s["work_mode"] and self.s["work_dir"])
        wm.addAction(("已开启：" + self.s["work_dir"]) if on else "未开启（开启后可以改文件）").setEnabled(False)
        wm.addAction("选择工作文件夹并开启…", self.pick_work_dir)
        wm.addAction("关闭", lambda: self.s.__setitem__("work_mode", False))
        m.addSeparator()
        m.addAction("忘记这段对话", self.forget)
        m.addAction("躲到屏幕边", self.hide_away)
        m.addAction("完全隐藏        Ctrl+Alt+M 召回", self.vanish)
        m.addSeparator()
        m.addAction("退出", self.quit_requested.emit)
        return m

    def pick_voice(self, vid):
        self.s["voice_" + self.char.id] = vid
        self.s["voice_mode"] = "tts"
        self.say(self.line("greet"), "happy")

    def contextMenuEvent(self, e):
        self._menu = self.build_menu()
        self._menu.popup(e.globalPos())

    def pick_work_dir(self):
        d = QFileDialog.getExistingDirectory(None, "选择允许桌宠读写的工作文件夹", self.s["work_dir"] or "D:\\")
        if d:
            self.s["work_dir"] = d
            self.s["work_mode"] = True
            self.say("工作模式开启啦，我可以改这个文件夹里的文件了。", "proud", voice=False)

    def show_history(self):
        self.histwin.render(self.hist(), self.char, self.theme)
        self.histwin.show()
        self.histwin.raise_()

    def forget(self):
        self.history[self.char.id] = []
        self.memory.update(task="", task_day="")
        self.save_state()
        self.say("嗯，刚才的都忘掉了，我们重新开始。", "calm", voice=False)

    def paintEvent(self, e):
        p = QPainter(self)
        try:
            self.stage.paint(p, self.scale)
        except Exception:
            pass
        p.end()
