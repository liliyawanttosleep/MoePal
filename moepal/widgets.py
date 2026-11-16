"""气泡、输入栏、对话记录窗口。 Speech bubble, input bar, history window."""
from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser, QVBoxLayout, QWidget

FLAGS = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
         | Qt.WindowType.NoDropShadowWindowHint)
FONT = "Microsoft YaHei UI"


class Bubble(QWidget):
    clicked = Signal()

    def __init__(self):
        super().__init__(None, FLAGS | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.color = QColor("#b9a4e8")
        self.label = QLabel(self)
        self.label.setWordWrap(True)
        self.label.setFont(QFont(FONT, 10))
        self.label.setStyleSheet("color:#2b2733;background:transparent;")
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.full = ""
        self.shown = 0
        self.tail_x = 0.5
        self.below = False

    def set_color(self, c):
        self.color = QColor(c)
        self.update()

    def layout_for(self, text):
        """按完整文本定尺寸，这样打字机效果不会让气泡抖动。"""
        self.full = text
        fm = self.label.fontMetrics()
        w = min(300, max(70, fm.horizontalAdvance(text) + 8))
        r = fm.boundingRect(0, 0, w, 2000, int(Qt.TextFlag.TextWordWrap), text)
        self.label.setGeometry(15, 11 + (10 if self.below else 0), w, r.height() + 4)
        self.resize(w + 30, r.height() + 36)

    def reveal(self, n):
        self.shown = n
        self.label.setText(self.full[:n])

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        top = 10 if self.below else 0
        body = QRectF(1.5, top + 1.5, self.width() - 3, self.height() - 13)
        path = QPainterPath()
        path.addRoundedRect(body, 14, 14)
        tx = body.left() + body.width() * self.tail_x
        tail = QPainterPath()
        if self.below:
            tail.moveTo(tx - 8, body.top() + 1)
            tail.lineTo(tx, 1)
            tail.lineTo(tx + 8, body.top() + 1)
        else:
            tail.moveTo(tx - 8, body.bottom() - 1)
            tail.lineTo(tx, self.height() - 1)
            tail.lineTo(tx + 8, body.bottom() - 1)
        path = path.united(tail)
        p.setPen(QPen(self.color, 2))
        p.setBrush(QColor(255, 253, 250, 245))
        p.drawPath(path)

    def mousePressEvent(self, e):
        self.clicked.emit()


class InputBar(QWidget):
    submitted = Signal(str)
    mic = Signal()
    closed = Signal()

    def __init__(self):
        super().__init__(None, FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.color = "#b9a4e8"
        self.level = 0.0
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(5)
        self.micbtn = QPushButton("🎤")
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("和我说点什么…  Enter 发送 · Esc 收起")
        self.send = QPushButton("➤")
        for b in (self.micbtn, self.send):
            b.setFixedSize(30, 30)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        lay.addWidget(self.micbtn)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.send)
        self.edit.returnPressed.connect(self._submit)
        self.send.clicked.connect(self._submit)
        self.micbtn.clicked.connect(self.mic.emit)
        self.resize(320, 44)
        self.restyle()

    def restyle(self, recording=False):
        mic_bg = "#ff6b6b" if recording else "#ffffff"
        self.setStyleSheet(f"""
            QLineEdit {{ border:none; background:transparent; color:#2b2733; font:10pt "{FONT}"; }}
            QPushButton {{ border:none; border-radius:15px; background:#ffffff; font:11pt "Segoe UI Emoji"; }}
            QPushButton:hover {{ background:{self.color}; }}
        """)
        self.micbtn.setStyleSheet(f"background:{mic_bg};")
        self.update()

    def set_color(self, c):
        self.color = c
        self.restyle()

    def set_status(self, text):
        self.edit.setPlaceholderText(text or "和我说点什么…  Enter 发送 · Esc 收起")

    def _submit(self):
        t = self.edit.text().strip()
        if t:
            self.edit.clear()
            self.submitted.emit(t)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(1.5, 1.5, self.width() - 3, self.height() - 3)
        p.setPen(QPen(QColor(self.color), 2))
        p.setBrush(QColor(255, 253, 250, 248))
        p.drawRoundedRect(r, 21, 21)
        if self.level > 0.02:
            p.setPen(Qt.PenStyle.NoPen)
            c = QColor("#ff6b6b")
            c.setAlpha(150)
            p.setBrush(c)
            w = (self.width() - 60) * min(1.0, self.level)
            p.drawRoundedRect(QRectF(30, self.height() - 7, w, 3), 1.5, 1.5)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.hide()
            self.closed.emit()
        else:
            super().keyPressEvent(e)

    def open_at(self, pos: QPoint):
        self.move(pos)
        self.show()
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(30, self.edit.setFocus)


class HistoryWindow(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("MoePal · 对话记录")
        self.resize(420, 520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.view = QTextBrowser()
        self.view.setStyleSheet(f'QTextBrowser{{background:#fffdfa;border:none;padding:10px;font:10pt "{FONT}";}}')
        lay.addWidget(self.view)

    def render(self, history, char):
        rows = []
        for role, text in history:
            t = (text.replace("&", "&amp;").replace("<", "&lt;").replace("\n", "<br>"))
            if role == "user":
                rows.append(f'<p align="right"><span style="color:#888">你</span><br>{t}</p>')
            else:
                rows.append(f'<p><span style="color:{char.color}"><b>{char.short}</b></span><br>{t}</p>')
        self.view.setHtml("".join(rows) or "<p style='color:#999'>还没有对话。</p>")
        sb = self.view.verticalScrollBar()
        sb.setValue(sb.maximum())


def screen_rect_at(pos: QPoint):
    scr = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
    return scr.availableGeometry()
