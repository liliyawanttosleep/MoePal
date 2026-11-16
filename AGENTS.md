# MoePal 萌伴 — AI 桌面 Q 版助手 / AI desktop chibi companion

给接手的 AI agent / 开发者：读完本文件 + `docs/DEVLOG.zh-CN.md`（或 `docs/DEVLOG.en.md`）即可继续开发。
For the next agent: this file plus the DEVLOG is everything you need.

## 是什么 / What it is

Windows 桌面上的透明置顶 Q 版角色（GPT 白龙娘「小白」、Claude 大小姐、DeepSeek 大肥鱼「小鲸」）。
会动、有情绪、有自己的声音，对鼠标和桌面状态有反应，可以打字或用麦克风和它对话，背后接真实的大模型。

## 运行 / Run

```
D:\MoePal\.venv\Scripts\pythonw.exe D:\MoePal\MoePal.pyw      # 或双击桌面「MoePal 萌伴」
.venv\Scripts\python tools\smoke_test.py [--net]               # 无窗口自检（--net 会真的调用 TTS 和模型）
.venv\Scripts\python tools\ui_test.py <截图目录> [gpt|claude|deepseek]   # 脚本化端到端联调 + 截图
.venv\Scripts\python tools\import_assets.py                    # 从桌面素材文件夹重新导入 assets/
.venv\Scripts\python tools\export_actions.py                   # 把组合动作渲染成 WebP/GIF
.venv\Scripts\python tools\make_art.py                         # 重新生成图标和主题背景图
```

环境：Python 3.13 独立 venv（`.venv`，不动 Anaconda）；依赖见 `requirements.txt`。

## 结构 / Layout

| 文件 | 职责 |
|---|---|
| `moepal/characters.py` | 三个角色：人设 prompt、TTS 嗓音参数、音色族、专属粒子、本地台词；`COMMON_RULES` 是共用的对话规则（情绪标签、共情优先等） |
| `moepal/stage.py` | 序列帧解码缓存（`ClipBank`）、18 种程序化运动、粒子系统、`ACTIONS` 动作表（38 个组合动作）、`EMO_ACTION` 情绪→动作 |
| `moepal/sfx.py` | numpy 合成的角色音频轨道：19 个事件音 + 晕眩 + 呼噜 + 8 个咿呀语音节，三种音色族 crystal / musicbox / bubble |
| `moepal/audio.py` | `Audio`：音效播放、edge-tts 语音合成与播放、跟随默认输出设备；`Listener`：麦克风录音 + faster-whisper 本地识别 |
| `moepal/brain.py` | 模型后端 `claude_cli` / `codex_cli` / `deepseek_api`，按角色优先级依次尝试并自动降级；prompt 组装；回复解析；本地关键词情绪 |
| `moepal/deskctx.py` | 前台窗口、空闲时间、CPU、电量、应用分类、给模型看的桌面状态描述 |
| `moepal/pet.py` | 桌宠窗口：状态机、情绪（愉悦 v / 精力 e / 好感 a）、鼠标互动、桌面反应、说话流程、对话流程、菜单 |
| `moepal/widgets.py` | 气泡、输入栏、对话记录窗口 |
| `moepal/main.py` | 入口：托盘、全局热键 Ctrl+Alt+M、单实例 |
| `claude-mod/ojousama-study/` | Claude Code 桌面端的主题 mod（大小姐书房配色 + 背景图 + 会话开始时召唤桌宠） |
| `assets/` | 导入的动图（**不进 git**，见下方许可） |
| `data/` | 设置、情绪/记忆/对话记录、缓存（不进 git） |

## 关键设计 / Key decisions

- **组合动作** = 序列帧 clip × 程序化运动 motion × 粒子 emit。没有图像生成工具时也能扩充动作；新增一个动作只需在 `ACTIONS` 里加一行。
- **情绪标签协议**：模型每条回复第一行是 `[emo:xxx]`，`brain.parse_reply` 解析后驱动动作、音效、语音语速音高和情绪数值。
- **先本地后模型**：用户消息先过 `quick_sentiment`，立刻给出担心/开心的表情，模型回复到了再细化。鼠标和桌面反应全部用本地台词，不耗额度。
- **聊天模式不给工具**：Claude CLI 以 `--tools ""` 运行；只有用户在菜单里手动开启“工作模式”并选了文件夹，才用 `--permission-mode acceptEdits` 在该文件夹内读写。
- **隐私**：只把前台窗口标题（截断 80 字）等文字状态发给模型，不截屏；可在菜单「行为」里关闭。真人语音走微软在线 TTS（edge-tts），会把要念的文字发到微软；选「咿呀语」则完全离线。

## 素材许可（重要）/ Asset licence (important)

`assets/` 里的角色图来自社区二创（上游：ai-moe-atlas、ZipZipPipe 等），要求**署名、非商业**，更广的改编许可未确认；
原文在 `assets/LICENSES/`。因此：**不要把 `assets/` 推到公开仓库，不要发布含素材的安装包**。代码可以单独开源。

## 已验证 / 未验证

见 DEVLOG 的「验证状态」一节。
