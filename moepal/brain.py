"""模型接入：Claude CLI / Codex CLI / DeepSeek(OpenAI 兼容) 三种后端，按角色的优先级依次尝试。
LLM backends. Runs in worker threads; results come back as Qt signals.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .characters import COMMON_RULES
from .config import DATA

NO_WINDOW = 0x08000000
EMOS = {"happy", "love", "shy", "calm", "think", "curious", "surprised", "worried", "sad", "cry", "angry",
        "sleepy", "proud", "cheer"}
BACKEND_ZH = {"claude_cli": "Claude Code", "codex_cli": "Codex",
              "deepseek_api": "DeepSeek API"}


class BackendError(Exception):
    pass


def find_claude():
    w = shutil.which("claude")
    if w:
        return w
    pats = [os.path.expandvars(r"%LOCALAPPDATA%\Claude-Data\*\claude-code\*\*\claude.exe"),
            os.path.expandvars(r"%APPDATA%\Claude\claude-code\*\claude.exe")]
    found = [p for pat in pats for p in glob.glob(pat)]
    return max(found, key=os.path.getmtime) if found else None


def find_codex():
    """优先用 Codex 桌面应用自带的 codex.exe（跟着应用自动更新）；PATH 里那个可能是很旧的独立安装。"""
    found = glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\OpenAI\Codex\bin\*\codex.exe"))
    if found:
        return max(found, key=os.path.getmtime)
    return shutil.which("codex") or shutil.which("codex.exe")


def _run(args, stdin_text, cwd, timeout):
    try:
        p = subprocess.run(args, input=stdin_text.encode("utf-8"), capture_output=True, cwd=cwd,
                           timeout=timeout, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise BackendError("超时")
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def call_claude(system, prompt, s):
    exe = find_claude()
    if not exe:
        raise BackendError("没找到 Claude Code 的 claude.exe")
    args = [exe, "-p", "--model", s["claude_model"] or "haiku", "--output-format", "json",
            "--system-prompt", system, "--strict-mcp-config", "--no-session-persistence"]
    work = bool(s["work_mode"] and s["work_dir"] and os.path.isdir(s["work_dir"]))
    if work:   # 工作模式：允许在指定文件夹里读写文件（用户在菜单里手动开启）
        args += ["--permission-mode", "acceptEdits"]
        cwd, timeout = s["work_dir"], 600
    else:      # 聊天模式：不给任何工具
        args += ["--tools", ""]
        cwd, timeout = str(DATA / "brain_cwd"), 90
    code, out, err = _run(args, prompt, cwd, timeout)
    try:
        j = json.loads(out.strip().splitlines()[-1])
    except Exception:
        raise BackendError((err or out or f"exit {code}").strip()[:200])
    if j.get("is_error") or not j.get("result"):
        raise BackendError(str(j.get("result") or j.get("subtype") or "空回复")[:200])
    return j["result"]


def call_codex(system, prompt, s):
    exe = find_codex()
    if not exe:
        raise BackendError("没找到 codex 命令")
    cwd = str(DATA / "brain_cwd")
    outf = Path(tempfile.gettempdir()) / f"moepal_codex_{os.getpid()}_{threading.get_ident()}.txt"
    outf.unlink(missing_ok=True)
    args = [exe, "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only",
            "-c", "model_reasoning_effort=low", "-C", cwd, "-o", str(outf)]
    if s["codex_model"]:
        args += ["-m", s["codex_model"]]
    args.append("-")
    full = f"<角色设定>\n{system}\n</角色设定>\n\n只做角色扮演对话，不要运行任何命令或工具。\n\n{prompt}"
    code, out, err = _run(args, full, cwd, 120)
    if outf.exists():
        txt = outf.read_text(encoding="utf-8", errors="replace").strip()
        outf.unlink(missing_ok=True)
        if txt:
            return txt
    blob = out + err
    if "requires a newer version of Codex" in blob:
        raise BackendError("Codex CLI 版本太旧，当前账号的模型要求升级 Codex")
    m = re.search(r'"message":"([^"]+)"', blob)
    raise BackendError((m.group(1) if m else blob.strip()[-200:]) or f"exit {code}")


def call_deepseek(system, prompt, s):
    key = os.environ.get(s["deepseek_api_key_env"] or "DEEPSEEK_API_KEY", "")
    if not key:
        raise BackendError(f"没有设置环境变量 {s['deepseek_api_key_env']}")
    body = json.dumps({"model": s["deepseek_model"], "max_tokens": 600,
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": prompt}]}).encode("utf-8")
    req = urllib.request.Request(s["deepseek_base_url"].rstrip("/") + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            j = json.loads(r.read().decode("utf-8"))
        return j["choices"][0]["message"]["content"]
    except Exception as e:
        raise BackendError(str(e)[:200])


CALLS = {"claude_cli": call_claude, "codex_cli": call_codex, "deepseek_api": call_deepseek}


def parse_reply(raw: str):
    txt = raw.strip()
    emo = "calm"
    m = re.match(r"^\s*\[emo[:：]\s*(\w+)\s*\]\s*", txt)
    if m:
        emo = m.group(1).lower() if m.group(1).lower() in EMOS else "calm"
        txt = txt[m.end():]
    thought = ""
    m = re.search(r"\[think[:：]\s*([^\]\n]{1,40})\]\s*", txt)
    if m:
        thought = m.group(1).strip()
        txt = txt[:m.start()] + txt[m.end():]
    txt = re.sub(r"\[(emo|think)[:：][^\]\n]*\]", "", txt)
    txt = re.sub(r"^#+\s*", "", txt, flags=re.M)
    txt = txt.replace("**", "").strip()
    return emo, txt, thought


NEG = ("累", "烦", "崩溃", "难受", "不想", "讨厌", "受不了", "焦虑", "压力", "失败", "挂了", "完蛋", "头疼", "头痛",
       "难过", "伤心", "委屈", "气死", "无语", "绝望", "想哭", "好难", "太难", "搞不定", "做不完", "来不及", "熬夜",
       "失眠", "不会做", "看不懂", "报错", "又错", "bug", "被骂", "被拒", "没过", "害怕", "孤独", "没意思", "emo")
POS = ("开心", "高兴", "搞定", "成功", "过了", "太好了", "哈哈", "好耶", "完成了", "做完了", "谢谢", "喜欢", "厉害",
       "棒", "赢了", "通过", "拿到", "爱你")


def quick_sentiment(text: str) -> int:
    """本地关键词情绪：-1 抱怨/低落，+1 开心，0 中性。用来在模型回答前就先给出表情。"""
    t = text.lower()
    if any(k in t for k in NEG):
        return -1
    if any(k in t for k in POS):
        return 1
    return 0


class Brain(QObject):
    reply = Signal(int, str, str, str, str)   # 请求号, 情绪, 正文, 后端, 心里话
    failed = Signal(int, str)
    notice = Signal(str)                   # 后端降级提示（只提示一次）

    def __init__(self, settings):
        super().__init__()
        self.s = settings
        self.seq = 0
        self.dead = {}        # backend -> (失效到期时间, 原因)
        self.noticed = set()

    def build_prompt(self, history, user_text, desk, mood, memory, proactive=False):
        parts = []
        if desk:
            parts.append("【桌面状态】\n" + desk)
        parts.append("【你现在的心情】\n" + mood)
        if memory.get("task"):
            parts.append("【用户说过他现在要做的事】\n" + memory["task"])
        if memory.get("facts"):
            parts.append("【你记得的关于用户的事】\n" + "\n".join("- " + f for f in memory["facts"][-8:]))
        if history:
            parts.append("【最近对话】\n" + "\n".join(("用户：" if r == "user" else "你：") + t for r, t in history[-10:]))
        if proactive:
            parts.append("【现在】用户没有说话。请你根据桌面状态和心情，主动说一句简短、自然、不打扰的话"
                         "（关心、打气或轻松的闲聊），不要提问太多。")
        else:
            parts.append("【用户刚说】\n" + user_text)
        return "\n\n".join(parts)

    def ask(self, char, prompt) -> int:
        self.seq += 1
        n = self.seq
        mode = ("【模式】工作模式：你可以在工作文件夹里读写文件来帮用户完成任务，做完后用一两句话说明做了什么。"
                if self.s["work_mode"] and self.s["work_dir"] else "【模式】聊天模式。")
        system = char.persona.strip() + "\n" + COMMON_RULES + "\n" + mode
        threading.Thread(target=self._work, args=(n, char, system, prompt), daemon=True).start()
        return n

    def _work(self, n, char, system, prompt):
        errors = []
        for b in char.backends:
            until, why = self.dead.get(b, (0, ""))
            if time.time() < until:
                errors.append(f"{BACKEND_ZH[b]}：{why}")
                continue
            try:
                raw = CALLS[b](system, prompt, self.s)
                emo, txt, thought = parse_reply(raw)
                if not txt:
                    raise BackendError("空回复")
                if errors and char.id not in self.noticed:
                    self.noticed.add(char.id)
                    self.notice.emit("；".join(errors) + f" → 暂时改用 {BACKEND_ZH[b]}")
                self.reply.emit(n, emo, txt, b, thought)
                return
            except BackendError as e:
                # 版本/配置类错误重试也没用 → 本次运行内不再试；其他错误 10 分钟后再试
                permanent = any(k in str(e) for k in ("版本太旧", "没找到", "没有设置环境变量"))
                self.dead[b] = (time.time() + (86400 if permanent else 600), str(e))
                errors.append(f"{BACKEND_ZH[b]}：{e}")
            except Exception as e:   # noqa
                errors.append(f"{BACKEND_ZH[b]}：{e!r}"[:200])
        self.failed.emit(n, "；".join(errors))
