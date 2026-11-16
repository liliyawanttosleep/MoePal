"""冒烟测试：不开窗口，逐项检查能离线验证的部分。 Headless checks."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from moepal import brain, sfx
from moepal.characters import CHARACTERS
from moepal.config import CACHE, Settings
from moepal.deskctx import DeskState

ok = True


def check(name, fn):
    global ok
    t = time.time()
    try:
        r = fn()
        print(f"[ OK ] {name} ({time.time() - t:.1f}s) {r if r is not None else ''}")
    except Exception as e:
        ok = False
        print(f"[FAIL] {name}: {e!r}")


def t_sfx():
    files = sfx.build_all(CACHE / "sfx", CHARACTERS)
    n = sum(len(v) for v in files.values())
    assert all(p.exists() and p.stat().st_size > 2000 for v in files.values() for p in v.values())
    return f"{n} 个 wav"


def t_clips():
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication.instance() or QGuiApplication([])
    from moepal.stage import ACTIONS, CLIPS, Stage
    out = []
    for c in CHARACTERS.values():
        st = Stage(c)
        frames = sum(st.bank.get(k).n for k in CLIPS)
        for a in ACTIONS:
            st.set_action(a)
            st.advance(0.3)
            st.frame()
        out.append(f"{c.id}:{frames}帧")
    return " ".join(out) + f"，{len(ACTIONS)} 个动作"


def t_desk():
    d = DeskState()
    d.poll()
    return d.describe().replace("\n", " | ")


def t_tts():
    import asyncio
    import edge_tts
    p = CACHE / "tts_test.mp3"
    asyncio.run(edge_tts.Communicate("本龙驾到，今天要一起做什么呀？", "zh-CN-XiaoyiNeural", rate="+6%", pitch="+18Hz").save(str(p)))
    assert p.stat().st_size > 3000
    return f"{p.stat().st_size} bytes"


def t_backend(name):
    def run():
        s = Settings()
        c = CHARACTERS["gpt"]
        b = brain.Brain(s)
        prompt = b.build_prompt([], "唉，今天作业好多，写不完了，好烦。", "现在时间：23:40\n前台窗口：Code.exe《hw9.tex》",
                                "心情不错，精神一般", {"task": "写回归分析作业"})
        from moepal.characters import COMMON_RULES
        raw = brain.CALLS[name](c.persona + COMMON_RULES + "\n【模式】聊天模式。", prompt, s)
        emo, txt, thought = brain.parse_reply(raw)
        return f"[{emo}] ({thought}) {txt}"
    return run


check("音效合成", t_sfx)
check("序列帧与动作", t_clips)
check("桌面感知", t_desk)
if "--net" in sys.argv:
    check("edge-tts 语音合成", t_tts)
    check("Claude CLI 后端", t_backend("claude_cli"))
    check("Codex CLI 后端", t_backend("codex_cli"))
sys.exit(0 if ok else 1)
