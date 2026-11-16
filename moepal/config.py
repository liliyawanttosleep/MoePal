"""路径与设置。 Paths and persisted settings."""
import json
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
DATA = ROOT / "data"
CACHE = DATA / "cache"
for _p in (DATA, CACHE, DATA / "brain_cwd"):
    _p.mkdir(parents=True, exist_ok=True)

DEFAULTS = {
    "character": "gpt",
    "scale": 0.72,            # 逻辑像素缩放；150% DPI 下约等于素材原始分辨率
    "voice_mode": "tts",      # tts | babble | mute
    "sfx_volume": 0.55,
    "voice_volume": 0.9,
    "share_desktop_context": True,   # 把前台窗口标题等发给模型
    "proactive": True,               # 主动搭话（本地台词）
    "proactive_llm_minutes": 0,      # >0 时每隔这么多分钟用模型主动关心一次（耗额度）
    "stroll": True,                  # 偶尔自己散步
    "hotkey": "ctrl+alt+m",
    "claude_model": "haiku",
    "codex_model": "",               # 空 = 用 ~/.codex/config.toml 的默认
    "deepseek_api_key_env": "DEEPSEEK_API_KEY",
    "deepseek_base_url": "https://api.deepseek.com",
    "deepseek_model": "deepseek-chat",
    "work_mode": False,              # 工作模式：允许 Claude 后端在 work_dir 里读写文件
    "work_dir": "",
    "whisper_model": "small",
    "pos": None,
}


class Settings:
    def __init__(self):
        self.path = DATA / "settings.json"
        self.d = dict(DEFAULTS)
        try:
            self.d.update(json.loads(self.path.read_text(encoding="utf-8")))
        except Exception:
            pass

    def __getitem__(self, k):
        return self.d.get(k, DEFAULTS.get(k))

    def __setitem__(self, k, v):
        self.d[k] = v
        self.save()

    def save(self):
        try:
            self.path.write_text(json.dumps(self.d, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


def load_json(name, default):
    try:
        return json.loads((DATA / name).read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(name, obj):
    try:
        (DATA / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
