"""桌面状态感知（仅 Windows）：前台窗口、空闲时间、CPU、电量。 Desktop awareness."""
import ctypes
import time
from ctypes import wintypes
from datetime import datetime

import psutil

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def idle_seconds() -> float:
    li = LASTINPUTINFO()
    li.cbSize = ctypes.sizeof(li)
    if not user32.GetLastInputInfo(ctypes.byref(li)):
        return 0.0
    return max(0.0, (kernel32.GetTickCount() - li.dwTime) / 1000.0)


def foreground():
    """返回 (进程名, 窗口标题, 是否全屏)。"""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "", "", False
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        proc = psutil.Process(pid.value).name()
    except Exception:
        proc = ""
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    full = (rect.left <= 0 and rect.top <= 0 and rect.right >= sw and rect.bottom >= sh
            and proc.lower() not in ("explorer.exe", ""))
    return proc, buf.value, full


CATEGORIES = [
    ("code", ("code.exe", "devenv.exe", "pycharm", "idea", "cursor.exe", "windowsterminal.exe", "powershell", "cmd.exe",
              "rstudio", "texstudio", "sublime", "notepad++", "codex")),
    ("study", ("acrobat", "sumatrapdf", "zotero", "winword", "powerpnt", "excel", "onenote", "obsidian", "notion",
               "foxit", "wps")),
    ("video", ("potplayer", "vlc", "bcut", "obs", "cloudmusic", "qqlive", "mpv")),
    ("game", ("ra2", "gamemd", "yuanshen", "genshin", "starrail", "steam", "mumu", "zenless", "hyp.exe")),
    ("chat", ("wechat", "weixin", "qq.exe", "telegram", "teams", "slack", "discord", "tencentmeeting", "zoom")),
    ("browse", ("chrome.exe", "msedge.exe", "firefox.exe")),
]
TITLE_HINTS = [
    ("video", ("bilibili", "哔哩", "youtube", "netflix", "爱奇艺", "腾讯视频", "抖音", "twitch")),
    ("study", (".pdf", "canvas", "lecture", "homework", "overleaf", "课件", "作业", "arxiv", "wikipedia", "stat ")),
    ("code", ("github", "stack overflow", "stackoverflow", "localhost", "jupyter", "colab")),
    ("chat", ("gmail", "outlook", "邮箱", "mail")),
]


def classify(proc: str, title: str) -> str:
    p, t = proc.lower(), title.lower()
    cat = "other"
    for c, keys in CATEGORIES:
        if any(k in p for k in keys):
            cat = c
            break
    if cat in ("browse", "other"):
        for c, keys in TITLE_HINTS:
            if any(k in t for k in keys):
                return c
    return cat


CAT_ZH = {"code": "写代码/终端", "study": "看文档/学习", "video": "看视频/听歌", "game": "玩游戏",
          "chat": "聊天/邮件/会议", "browse": "浏览网页", "other": "其他"}


class DeskState:
    """轮询一次得到一份快照，并累计“连续活跃时长”。"""

    def __init__(self):
        self.active_since = time.time()
        self.last_seen_active = time.time()
        self.proc = self.title = ""
        self.cat = "other"
        self.full = False
        self.idle = 0.0
        self.cpu = 0.0
        self.cpu_high_since = None
        self.battery = None
        self.plugged = True
        psutil.cpu_percent(None)

    def poll(self, own_pid_names=("python.exe", "pythonw.exe", "moepal.exe")):
        now = time.time()
        self.idle = idle_seconds()
        proc, title, full = foreground()
        if proc.lower() not in own_pid_names:       # 桌宠自己拿到焦点时保留上一个窗口
            self.proc, self.title, self.full = proc, title, full
            self.cat = classify(proc, title)
        if self.idle < 60:
            if now - self.last_seen_active > 300:   # 离开超过 5 分钟 → 重新计时
                self.active_since = now
            self.last_seen_active = now
        self.cpu = psutil.cpu_percent(None)
        if self.cpu > 88:
            self.cpu_high_since = self.cpu_high_since or now
        else:
            self.cpu_high_since = None
        try:
            b = psutil.sensors_battery()
            if b:
                self.battery, self.plugged = b.percent, b.power_plugged
        except Exception:
            pass

    @property
    def active_minutes(self):
        return (self.last_seen_active - self.active_since) / 60

    def describe(self) -> str:
        now = datetime.now()
        wk = "一二三四五六日"[now.weekday()]
        lines = [f"现在时间：{now:%Y-%m-%d %H:%M}，星期{wk}"]
        if self.proc:
            lines.append(f"前台窗口：{self.proc}《{self.title[:80]}》（大概在{CAT_ZH[self.cat]}）")
        lines.append(f"已连续使用电脑约 {self.active_minutes:.0f} 分钟；键鼠空闲 {self.idle:.0f} 秒")
        extra = f"CPU {self.cpu:.0f}%"
        if self.battery is not None:
            extra += f"；电量 {self.battery:.0f}%{'（充电中）' if self.plugged else '（未插电）'}"
        lines.append(extra)
        return "\n".join(lines)
