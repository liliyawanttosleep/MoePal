# MoePal 萌伴

Windows 桌面上的 AI Q 版小伙伴：透明置顶的小人，会动、有情绪、有自己的声音，对鼠标和桌面状态有反应，
可以打字或用麦克风和它聊天，背后接真实的大模型（Claude Code CLI / Codex CLI / DeepSeek API）。

An AI chibi companion for the Windows desktop: an always-on-top transparent character with moods, its own
synthesized voice, reactions to the mouse and to what you are doing, and real LLM chat by text or microphone.

> **本仓库只有代码，不含角色美术素材。** 角色图是社区二创，许可为署名 + 非商业，不能随代码分发。
> 没有 `assets/` 程序无法启动；你需要自备透明背景的动图（见下）。
>
> **Code only — no character art.** The artwork is community fan art under attribution + non-commercial terms and
> is not redistributed here. The app will not start without `assets/`; bring your own (see below).

## 功能 / Features

- 38 个组合动作：序列帧 × 程序化运动（弹跳、摇摆、眩晕、挤压回弹、后空翻…）× 表情粒子
- 每个角色一套 numpy 合成的音效与“咿呀语”；说话用 edge-tts，语速音高随情绪变化，离线自动降级
- 鼠标互动：靠近、吓一跳、摸头、晃晕、戳、拖拽、缩放
- 桌面感知：前台应用类别、连续使用时长、深夜、CPU、电量、空闲打瞌睡
- 情绪系统（愉悦 / 精力 / 好感）驱动姿态、语调，并写进给模型的提示
- 对话：输入栏或麦克风（faster-whisper 本地识别）；模型用 `[emo:x]` 标签驱动表情
- 托盘、全局热键 `Ctrl+Alt+M`、躲到屏幕边探头
- 附带一个 Claude Code 桌面端主题 mod（`claude-mod/ojousama-study`，同样不含背景图）

## 运行 / Run

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python tools\import_assets.py <你的素材文件夹>
.venv\Scripts\pythonw MoePal.pyw
```

素材布局：`assets/<gpt|claude|deepseek>/<动作>.webp`（透明背景动图）和同名 `.png` 静态图，动作名为
`jump wipe down anxious daze startle cry wave idle read hum special`。缺的动作会自动用别的顶替。

模型后端复用本机已登录的 Claude Code 或 Codex 命令行；DeepSeek 需要环境变量 `DEEPSEEK_API_KEY`。

## 隐私 / Privacy

只把前台窗口标题（截断 80 字）等文字状态发给模型，不截屏，可在右键菜单关闭。
真人语音使用微软在线合成，会把要念的文字发给该服务；选“咿呀语”则完全离线。

## 文档 / Docs

[AGENTS.md](AGENTS.md) · [开发日志](docs/DEVLOG.zh-CN.md) · [Dev log](docs/DEVLOG.en.md)

## 许可 / Licence

代码：MIT（见 [LICENSE](LICENSE)）。`claude-mod/ojousama-study/hooks/make-colors.*` 来自 Claude Code 的 mod 模板。
角色形象属于各自的原作者，不在本仓库内，也不受此许可覆盖。
