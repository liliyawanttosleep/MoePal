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
.venv\Scripts\python tools\upscale_assets.py                   # 把 assets/ 的动图超分成 @2x（需 data/models/realesr-animevideov3.pth）
.venv\Scripts\python tools\render_ui.py <输出目录> [角色]        # 不上屏不出声地渲染界面检查图
.venv\Scripts\python tools\make_views.py                       # 从三视图抠出正/侧/背立绘（角色档案、转身动作用）
```

环境：Python 3.13 独立 venv（`.venv`，不动 Anaconda）；依赖见 `requirements.txt`（`torch` 只有超分脚本用，装 CPU 版即可）。
开发时注意：用户可能正在全屏玩游戏，**不要随手跑 `ui_test.py`**（会弹窗、出声、抢焦点）；用 `render_ui.py` 和 `smoke_test.py`。

## 结构 / Layout

| 文件 | 职责 |
|---|---|
| `moepal/characters.py` | 三个角色：人设 prompt、背景故事与资料、候选声线、音色族、专属粒子、本地台词；`COMMON_RULES` 是共用的对话规则（情绪标签、共情优先等） |
| `moepal/stage.py` | 流式逐帧解码（`Clip`，优先 `@2x`）、后台解码 + 帧间混合、26 种程序化运动、粒子系统（字形图片缓存）、`ACTIONS` 动作表（57 个）、`EMO_ACTION` 情绪→动作 |
| `moepal/sfx.py` | numpy 合成的角色音频轨道：19 个事件音 + 晕眩 + 呼噜 + 8 个咿呀语音节，三种音色族 crystal / musicbox / bubble |
| `moepal/audio.py` | `Audio`：音效播放、跟随默认输出设备；`Listener`：麦克风录音 + faster-whisper 本地识别 |
| `moepal/voice.py` | 说话：`plan` 把文本拆句并给每句定语速/音高/音量/停顿；`Voice` 并行合成、顺序播放；引擎 edge（在线）/ kokoro（本地） |
| `moepal/brain.py` | 模型后端 `claude_cli` / `codex_cli` / `deepseek_api`，按角色优先级依次尝试并自动降级；prompt 组装；回复解析；本地关键词情绪 |
| `moepal/deskctx.py` | 前台窗口、空闲时间、CPU、电量、应用分类、给模型看的桌面状态描述 |
| `moepal/pet.py` | 桌宠窗口：状态机、情绪（愉悦 v / 精力 e / 好感 a）、鼠标互动、桌面反应、说话流程、对话流程、菜单 |
| `moepal/widgets.py` | 主题（深/浅色 + 角色强调色）、气泡、输入栏、菜单样式、对话记录窗口、角色档案窗口 |
| `moepal/main.py` | 入口：托盘、全局热键 Ctrl+Alt+M、单实例 |
| `claude-mod/ojousama-study/` | Claude Code 桌面端的主题 mod（大小姐书房配色 + 背景图 + 会话开始时召唤桌宠） |
| `assets/` | 导入的动图（**不进 git**，见下方许可） |
| `data/` | 设置、情绪/记忆/对话记录、缓存（不进 git） |

## 关键设计 / Key decisions

- **组合动作** = 序列帧 clip × 程序化运动 motion × 粒子 emit。没有图像生成工具时也能扩充动作；新增一个动作只需在 `ACTIONS` 里加一行。
- **一角色一 Agent**：白龙娘只走 Codex CLI（Codex 桌面应用自带的那个），Claude 大小姐只走 Claude Code CLI，互不降级。
- **情绪标签协议**：模型每条回复第一行是 `[emo:xxx]`，可选第二行 `[think:心里话]`，`brain.parse_reply` 解析后驱动动作、音效、语音语速音高和情绪数值。
- **先本地后模型**：用户消息先过 `quick_sentiment`，立刻给出担心/开心的表情，模型回复到了再细化。鼠标和桌面反应全部用本地台词，不耗额度。
- **聊天模式不给工具**：Claude CLI 以 `--tools ""` 运行；只有用户在菜单里手动开启“工作模式”并选了文件夹，才用 `--permission-mode acceptEdits` 在该文件夹内读写。
- **隐私**：只把前台窗口标题（截断 80 字）等文字状态发给模型，不截屏；可在菜单「行为」里关闭。真人语音走微软在线 TTS（edge-tts），会把要念的文字发到微软；选「咿呀语」则完全离线。

## 素材许可（重要）/ Asset licence (important)

`assets/` 里的角色图来自社区二创（上游：ai-moe-atlas、ZipZipPipe 等），要求**署名、非商业**，更广的改编许可未确认；
原文在 `assets/LICENSES/`。因此：**不要把 `assets/` 推到公开仓库，不要发布含素材的安装包**。代码可以单独开源。

## 已验证 / 未验证

见 DEVLOG 的「验证状态」一节。
