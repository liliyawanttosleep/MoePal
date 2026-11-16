"""界面部件：气泡、输入栏、菜单样式、对话记录。跟随系统深浅色，强调色取当前角色。
UI widgets. Follows the system light/dark setting; the accent is the current character's colour.
"""
import math
import time
import winreg

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QPropertyAnimation, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QTextBrowser, QVBoxLayout, QWidget, QWidgetAction

FLAGS = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
         | Qt.WindowType.NoDropShadowWindowHint)
FONT = "Microsoft YaHei UI"
M = 14          # 给柔和阴影留的外边距


def is_dark() -> bool:
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return True


class Theme:
    def __init__(self, accent="#b9a4e8"):
        self.dark = is_dark()
        self.accent = QColor(accent)
        if self.dark:
            self.surface, self.text, self.muted = QColor(31, 28, 38, 246), QColor("#f3eff8"), QColor("#a9a1b8")
            self.line, self.field = QColor(255, 255, 255, 26), QColor(255, 255, 255, 16)
            # 深色底上把强调色提亮一点，保证文字可读
            h, s, l, _ = self.accent.getHslF()
            self.accent_text = QColor.fromHslF(h, min(s, .85), max(l, .72))
        else:
            self.surface, self.text, self.muted = QColor(255, 253, 250, 250), QColor("#2b2733"), QColor("#8a8296")
            self.line, self.field = QColor(0, 0, 0, 22), QColor(0, 0, 0, 10)
            h, s, l, _ = self.accent.getHslF()
            self.accent_text = QColor.fromHslF(h, min(1.0, s + .1), min(l, .45))

    def rgba(self, c: QColor, a=None):
        return f"rgba({c.red()},{c.green()},{c.blue()},{c.alpha() if a is None else a})"


def soft_shadow(p: QPainter, rect: QRectF, radius: float, strength=1.0):
    p.setPen(Qt.PenStyle.NoPen)
    for i in range(8, 0, -1):
        c = QColor(0, 0, 0, int(7 * strength * (9 - i) / 4))
        p.setBrush(c)
        p.drawRoundedRect(rect.adjusted(-i, -i + 3, i, i + 3), radius + i, radius + i)


class FadeMixin:
    def _init_fade(self):
        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.finished.connect(self._fade_done)
        self._closing = False

    def fade_in(self, ms=170):
        self._closing = False
        self._anim.stop()
        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self.show()
        self._anim.setDuration(ms)
        self._anim.setStartValue(self.windowOpacity())
        self._anim.setEndValue(1.0)
        self._anim.start()

    def fade_out(self, ms=220):
        if not self.isVisible() or self._closing:
            return
        self._closing = True
        self._anim.stop()
        self._anim.setDuration(ms)
        self._anim.setStartValue(self.windowOpacity())
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _fade_done(self):
        if self._closing:
            self._closing = False
            self.hide()


class Bubble(QWidget, FadeMixin):
    """对话气泡：角色名 + （可选）一句心里话 + 正文；等待回复时显示三个跳动的点。"""
    clicked = Signal()
    MAXW = 318

    def __init__(self):
        super().__init__(None, FLAGS | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._init_fade()
        self.theme = Theme()
        self.name = ""
        self.full = self.thought = ""
        self.shown = 0
        self.dots = False
        self.tail_x = 0.5
        self.below = False
        self.f_name = QFont(FONT, 8, QFont.Weight.DemiBold)
        self.f_text = QFont(FONT, 10)
        self.f_thought = QFont(FONT, 9)
        self.f_thought.setItalic(True)
        self._r_thought = self._r_text = QRectF()
        self.dot_timer = QTimer(self)
        self.dot_timer.timeout.connect(self.update)

    def set_theme(self, theme, name):
        self.theme, self.name = theme, name
        self.update()

    def _wrap_h(self, font, text, w):
        return QFontMetricsF(font).boundingRect(QRectF(0, 0, w, 4000), int(Qt.TextFlag.TextWordWrap), text)

    def layout_for(self, text, thought=""):
        """按完整文本定尺寸，打字机效果不会让气泡抖动。"""
        self.dots = False
        self.dot_timer.stop()
        self.full, self.thought = text, thought
        fm = QFontMetricsF(self.f_text)
        w = min(self.MAXW, max(96.0, max(fm.horizontalAdvance(l) for l in text.split("\n")) + 4))
        if thought:
            w = min(self.MAXW, max(w, min(self.MAXW, QFontMetricsF(self.f_thought).horizontalAdvance(thought) + 4)))
        x, y = M + 16, M + (9 if self.below else 0) + 11
        y += 17                                     # 角色名一行
        if thought:
            r = self._wrap_h(self.f_thought, thought, w)
            self._r_thought = QRectF(x, y, w, r.height())
            y += r.height() + 5
        r = self._wrap_h(self.f_text, text, w)
        self._r_text = QRectF(x, y, w, r.height() + 2)
        y += r.height() + 13
        self.resize(int(w + 32 + 2 * M), int(y + (0 if self.below else 9) + M))
        self.update()

    def show_dots(self):
        self.dots, self.full, self.thought, self.shown = True, "", "", 0
        self.resize(84 + 2 * M, 46 + 9 + 2 * M)
        self.dot_timer.start(33)
        self.update()

    def reveal(self, n):
        self.shown = n
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        t = self.theme
        top = M + (9 if self.below else 0)
        body = QRectF(M, top, self.width() - 2 * M, self.height() - 2 * M - 9)
        soft_shadow(p, body, 16)
        path = QPainterPath()
        path.addRoundedRect(body, 16, 16)
        tx = body.left() + body.width() * self.tail_x
        tail = QPainterPath()
        if self.below:
            tail.moveTo(tx - 9, body.top() + 1)
            tail.quadTo(tx - 2, body.top() - 3, tx, body.top() - 9)
            tail.quadTo(tx + 2, body.top() - 3, tx + 9, body.top() + 1)
        else:
            tail.moveTo(tx - 9, body.bottom() - 1)
            tail.quadTo(tx - 2, body.bottom() + 3, tx, body.bottom() + 9)
            tail.quadTo(tx + 2, body.bottom() + 3, tx + 9, body.bottom() - 1)
        path = path.united(tail)
        edge = QColor(t.accent)
        edge.setAlpha(150)
        p.setPen(QPen(edge, 1.2))
        p.setBrush(t.surface)
        p.drawPath(path)
        if self.dots:
            ph = time.time() * 5.2
            p.setPen(Qt.PenStyle.NoPen)
            for i in range(3):
                k = max(0.0, math.sin(ph - i * 0.9))
                c = QColor(t.accent_text)
                c.setAlphaF(0.45 + 0.55 * k)
                p.setBrush(c)
                p.drawEllipse(QPointF(body.center().x() + (i - 1) * 15, body.center().y() - 5 * k), 3.6, 3.6)
            return
        p.setFont(self.f_name)
        p.setPen(t.accent_text)
        p.drawText(QRectF(M + 16, top + 9, body.width() - 32, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.name)
        if self.thought:
            p.setFont(self.f_thought)
            p.setPen(t.muted)
            p.drawText(self._r_thought, int(Qt.TextFlag.TextWordWrap), self.thought)
        p.setFont(self.f_text)
        p.setPen(t.text)
        p.drawText(self._r_text, int(Qt.TextFlag.TextWordWrap), self.full[:self.shown])

    def mousePressEvent(self, e):
        self.clicked.emit()


class IconButton(QPushButton):
    """矢量图标按钮（麦克风 / 发送），不用 emoji，任何缩放下都清晰。"""

    def __init__(self, kind):
        super().__init__()
        self.kind = kind
        self.theme = Theme()
        self.active = False
        self.setFixedSize(32, 32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFlat(True)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self.theme
        r = QRectF(1, 1, 30, 30)
        if self.active:
            pulse = 0.5 + 0.5 * math.sin(time.time() * 6)
            p.setBrush(QColor(235, 87, 87, int(190 + 60 * pulse)))
        elif self.kind == "send":
            c = QColor(t.accent)
            c.setAlpha(255 if self.underMouse() else 215)
            p.setBrush(c)
        else:
            p.setBrush(t.field if not self.underMouse() else QColor(t.accent.red(), t.accent.green(), t.accent.blue(), 70))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(r)
        fg = QColor("#ffffff") if (self.active or self.kind == "send") else t.text
        pen = QPen(fg, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if self.kind == "mic":
            p.drawRoundedRect(QRectF(13, 8, 6, 10.5), 3, 3)
            arc = QPainterPath()
            arc.moveTo(10, 15.5)
            arc.cubicTo(10, 22.5, 22, 22.5, 22, 15.5)
            p.drawPath(arc)
            p.drawLine(QPointF(16, 21), QPointF(16, 24))
        else:
            p.drawLine(QPointF(16, 22.5), QPointF(16, 10))
            p.drawLine(QPointF(11, 14.5), QPointF(16, 9.5))
            p.drawLine(QPointF(21, 14.5), QPointF(16, 9.5))


class InputBar(QWidget, FadeMixin):
    submitted = Signal(str)
    mic = Signal()
    closed = Signal()
    HINT = "和我说点什么…"

    def __init__(self):
        super().__init__(None, FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._init_fade()
        self.theme = Theme()
        self.level = 0.0
        lay = QHBoxLayout(self)
        lay.setContentsMargins(M + 8, M + 7, M + 8, M + 7)
        lay.setSpacing(8)
        self.micbtn = IconButton("mic")
        self.edit = QLineEdit()
        self.send = IconButton("send")
        lay.addWidget(self.micbtn)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.send)
        self.edit.returnPressed.connect(self._submit)
        self.send.clicked.connect(self._submit)
        self.micbtn.clicked.connect(self.mic.emit)
        self.resize(348 + 2 * M, 46 + 2 * M)
        self.pulse = QTimer(self)
        self.pulse.timeout.connect(self.micbtn.update)
        self.set_theme(self.theme)

    def set_theme(self, theme):
        self.theme = self.micbtn.theme = self.send.theme = theme
        t = theme
        self.edit.setStyleSheet(
            f'QLineEdit{{border:none;background:transparent;color:{t.text.name()};font:10pt "{FONT}";'
            f'selection-background-color:{t.rgba(t.accent, 130)};}}')
        pal = self.edit.palette()
        pal.setColor(pal.ColorRole.PlaceholderText, t.muted)
        self.edit.setPalette(pal)
        self.set_status("")
        self.update()

    def restyle(self, recording=False):
        self.micbtn.active = recording
        (self.pulse.start(40) if recording else self.pulse.stop())
        self.micbtn.update()
        self.update()

    def set_status(self, text):
        self.edit.setPlaceholderText(text or self.HINT)

    def _submit(self):
        t = self.edit.text().strip()
        if t:
            self.edit.clear()
            self.submitted.emit(t)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self.theme
        r = QRectF(M, M, self.width() - 2 * M, self.height() - 2 * M)
        soft_shadow(p, r, 23)
        edge = QColor(t.accent)
        edge.setAlpha(210 if self.edit.hasFocus() else 120)
        p.setPen(QPen(edge, 1.3))
        p.setBrush(t.surface)
        p.drawRoundedRect(r, 23, 23)
        if self.level > 0.02:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(235, 87, 87, 190))
            w = (r.width() - 110) * min(1.0, self.level)
            p.drawRoundedRect(QRectF(r.left() + 55, r.bottom() - 6, w, 2.6), 1.3, 1.3)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.fade_out(140)
            self.closed.emit()
        else:
            super().keyPressEvent(e)

    def open_at(self, pos: QPoint):
        self.move(pos)
        self.fade_in()
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(40, self.edit.setFocus)


def style_menu(menu: QMenu, t: Theme):
    """圆角、半透明、跟随角色强调色的菜单。子菜单也要各自调用一次。"""
    menu.setWindowFlags(menu.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    a = t.accent
    menu.setStyleSheet(f"""
        QMenu {{ background:{t.rgba(t.surface)}; border:1px solid {t.rgba(a, 95)}; border-radius:12px;
                 padding:6px; font:10pt "{FONT}"; color:{t.text.name()}; }}
        QMenu::item {{ padding:7px 30px 7px 26px; border-radius:7px; margin:1px 2px; }}
        QMenu::item:selected {{ background:{t.rgba(a, 70 if t.dark else 60)}; }}
        QMenu::item:disabled {{ color:{t.muted.name()}; }}
        QMenu::separator {{ height:1px; background:{t.rgba(t.line)}; margin:5px 12px; }}
        QMenu::indicator {{ width:7px; height:7px; left:10px; border-radius:3px; }}
        QMenu::indicator:checked {{ background:{t.accent_text.name()}; }}
        QMenu::right-arrow {{ width:8px; height:8px; right:10px; }}
    """)
    return menu


def menu_header(menu: QMenu, t: Theme, title: str, sub: str):
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(14, 8, 14, 8)
    lay.setSpacing(2)
    a = QLabel(title)
    a.setStyleSheet(f'color:{t.accent_text.name()};font:600 11pt "{FONT}";background:transparent;')
    b = QLabel(sub)
    b.setStyleSheet(f'color:{t.muted.name()};font:8.5pt "{FONT}";background:transparent;')
    lay.addWidget(a)
    lay.addWidget(b)
    act = QWidgetAction(menu)
    act.setDefaultWidget(w)
    menu.addAction(act)
    menu.addSeparator()


class HistoryWindow(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("MoePal · 对话记录")
        self.resize(430, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.view = QTextBrowser()
        lay.addWidget(self.view)

    def render(self, history, char, t: Theme):
        bg = "#1f1c26" if t.dark else "#fffdfa"
        self.view.setStyleSheet(f'QTextBrowser{{background:{bg};border:none;padding:12px;font:10pt "{FONT}";color:{t.text.name()};}}')
        me_bg = t.rgba(t.accent, 60)
        pet_bg = "rgba(255,255,255,14)" if t.dark else "rgba(0,0,0,10)"
        rows = []
        for role, text in history:
            s = text.replace("&", "&amp;").replace("<", "&lt;").replace("\n", "<br>")
            if role == "user":
                rows.append(f'<table width="100%"><tr><td width="18%"></td><td style="background:{me_bg};padding:8px 10px;">'
                            f'{s}</td></tr></table>')
            else:
                rows.append(f'<table width="100%"><tr><td style="background:{pet_bg};padding:8px 10px;">'
                            f'<span style="color:{t.accent_text.name()};font-size:8pt;"><b>{char.short}</b></span><br>{s}</td>'
                            f'<td width="18%"></td></tr></table>')
        self.view.setHtml("<br>".join(rows) or f"<p style='color:{t.muted.name()}'>还没有对话。</p>")
        sb = self.view.verticalScrollBar()
        sb.setValue(sb.maximum())


def screen_rect_at(pos: QPoint):
    scr = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
    return scr.availableGeometry()
