"""入口：托盘、全局热键、单实例。 Entry point."""
import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QSharedMemory, Qt
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from .audio import Audio, Listener
from .brain import Brain
from .config import ASSETS, ROOT, Settings
from .pet import Pet

WM_HOTKEY = 0x0312
MODS = {"alt": 1, "ctrl": 2, "shift": 4, "win": 8}


class HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, cb):
        super().__init__()
        self.cb = cb

    def nativeEventFilter(self, etype, message):
        if etype == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                self.cb()
                return True, 0
        return False, 0


def register_hotkey(spec: str) -> bool:
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    mod = sum(MODS.get(p, 0) for p in parts[:-1]) | 0x4000   # MOD_NOREPEAT
    key = parts[-1].upper()
    vk = ord(key) if len(key) == 1 else {"SPACE": 0x20}.get(key, 0)
    return bool(vk and ctypes.windll.user32.RegisterHotKey(None, 1, mod, vk))


def tray_icon(char_id) -> QIcon:
    ico = ROOT / "assets" / "icon.ico"
    if ico.exists():
        return QIcon(str(ico))
    png = ASSETS / char_id / "idle.png"
    img = QImage(str(png))
    if img.isNull():
        return QIcon()
    side = min(img.width(), 230)
    head = img.copy((img.width() - side) // 2, 95, side, side)
    return QIcon(QPixmap.fromImage(head))


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("MoePal")
    lock = QSharedMemory("MoePal-single-instance")
    if not lock.create(1):
        return 0
    settings = Settings()
    for a in sys.argv[1:]:
        if a.startswith("--character="):
            settings["character"] = a.split("=", 1)[1]
    audio = Audio(settings)
    listener = Listener(settings)
    brain = Brain(settings)
    from .voice import Voice
    voice = Voice(settings)
    pet = Pet(settings, audio, listener, brain, voice)

    tray = QSystemTrayIcon(tray_icon(pet.char.id), app)
    tray.setToolTip("MoePal 萌伴 · Ctrl+Alt+M 召唤 / 对话 / 躲起来")

    def tray_click(reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            pet.toggle()
        elif reason == QSystemTrayIcon.ActivationReason.Context:
            pet._menu = pet.build_menu()
            from PySide6.QtGui import QCursor
            pet._menu.popup(QCursor.pos())

    tray.activated.connect(tray_click)
    tray.show()

    filt = HotkeyFilter(pet.toggle)
    app.installNativeEventFilter(filt)
    if not register_hotkey(settings["hotkey"]):
        tray.showMessage("MoePal", f"热键 {settings['hotkey']} 被占用，可以用托盘图标召唤。")

    pet.warm_voice()

    def quit_():
        pet.save_state()
        settings.save()
        ctypes.windll.user32.UnregisterHotKey(None, 1)
        tray.hide()
        app.quit()

    pet.quit_requested.connect(quit_)
    app.aboutToQuit.connect(pet.save_state)
    from .voice import ZIPVOICE
    app.aboutToQuit.connect(ZIPVOICE.close)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
