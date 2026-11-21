# Dev log (English)

## 2026-11-21 · v0.5 3D turnaround, emotional voice, character PVs

- **3D**: `tools/make_3d.py` (env `.conda-3d`, torch 2.7.1 cu128) builds a mesh per character with Hunyuan3D-2mv from the
  front/side/back views (shadows stripped first). Weights come from ModelScope (Hugging Face is unreachable here);
  transformers must be 4.48.x. `tools/render_3d.py` (moderngl) colours the mesh by projecting the original art from four
  directions with depth-tested visibility and a sharp facing-based blend (no AI texture → same style as the 2D art),
  adds a thin outline, aligns to the idle sprite and writes `turn360@2x.webp` (120 frames). The pet uses it for the
  turnaround and for a fast 3D spin when appearing / being summoned.
- **Smoother actions**: 0.22 s crossfade on every action change; particle bursts no longer cover the face.
- **Voice**: `moepal/cosy_worker.py` runs Fun-CosyVoice3 (Apache-2.0) with emotion instructions — measured clear pitch/
  tempo differences per emotion — but its LLM is ~1.7x slower than real time on Windows. For chat,
  `tools/make_emotion_refs.py` records 45 per-character/per-emotion/per-sweetness reference clips with CosyVoice, and
  ZipVoice (~0.35x real time) picks the matching one per sentence, inheriting the mood. Sweetness menu (0/14/28 Hz),
  slower default rate.
- **Muse**: occasional silent inner-thought bubbles depending on mood and time.
- **PVs**: `tools/pv_scripts.py` + `tools/make_pv.py` produce a ~5-minute 1080p PV per character: CosyVoice narration,
  auto-timed timeline, synthesized BGM with ducking, Qt-rendered frames reusing the pet renderer, libx264 (NVENC fails
  in this ffmpeg build). No new hand-drawn frames.

Verified: all three PVs rendered and spot-checked per section (300.0 s, 1920x1080, 30 fps, AAC stereo, in the Desktop
asset folder `角色PV\`); 3D summon spin checked offscreen. A frame the background decoder has not produced yet is now
decoded on the spot (with a lock) instead of repeating the last one.
Not verified: how the voices sound (the agent cannot hear); overall PV feel; the 3D summon animation in a real window.

## 2026-11-19 · v0.4 RIFE re-interpolation, GPU voice with reference timbre, any model for both agents

- Measured first: `tools/fps_probe.py` shows 125 repaints/s with zero drops and ~1 ms per paint — rendering is not
  the bottleneck. The source clips jump at every keyframe (optical-flow interpolation from 8 keyframes was poor).
- `tools/interp_assets.py` rebuilds every clip from the keyframes: Real-ESRGAN 2x → rife-ncnn-vulkan (rife-v4.6, GPU)
  to 50 fps with seamless loops; RGB and alpha interpolated separately. Idle: median frame-to-frame change 2.84 → 1.80,
  loop seam 7.43 → 1.24.
- The WebP encoder merges identical consecutive frames; per-frame durations are stored in a sidecar `.json` and
  `Clip.at(ms)` plays by timeline (v0.3 never actually read real durations).
- `moepal/tts_worker.py`: ZipVoice zero-shot TTS + Vocos in a subprocess, CUDA first. Each character has a reference
  timbre in `data/voices/<char>/` generated from a Microsoft synthetic voice — no real voice actor recordings were used;
  users can drop in their own licensed recording. ~1.5–2 s per 4 s sentence on the RTX 5070 Ti after a one-time warm-up.
- Model menus: Claude Code (default / Haiku / Sonnet / Opus / Fable / any name) and Codex (default / every model in the
  Codex app's cache / any name).

Not verified: how it sounds (the agent cannot hear), on-screen feel of the new clips, DeepSeek clips interpolated from
4 very different v1 keyframes, microphone and real mouse gestures.

## 2026-11-16 · v0.3 real high frame rate, continuous speech, character profiles, Claude model choice

1. **Frame rate** — the user's display is 240 Hz; painting at 60 fps looked choppy. Now renders at
   min(refresh, 120) using an integer divisor of the refresh rate (halved on battery). `Clip.pair()` + `Stage._mix()`
   linearly blend adjacent 25 fps source frames so every refresh shows a new image (measured 120 distinct frames/s,
   was ~25). Decoding moved to a background thread with 6-frame read-ahead; emoji particles are cached as images;
   first frames are preloaded. Measured 1.1 ms mean / 1.7 ms p95 per frame (v0.2: 4.2 / 10.6, 23 with particles).
2. **Speech** — per-clause synthesis with per-clause pitch (v0.2) sounded disjointed. New default `plan_flow`
   sends the whole reply (split only at sentence ends beyond ~48 chars) at one pitch; candidate voices no longer
   pitch-shift; cue sounds no longer overlap speech and are quieter. Added a local MeloTTS voice.
3. **Profiles** — `tools/make_views.py` cuts transparent front/side/back views out of the turnaround sheets;
   `ProfileWindow` shows the views, a fact table and a written backstory per character; the backstory is also part of
   the persona prompt.
4. **View-based actions** — turnaround, turn-away (used after being poked angry), side gaze. 57 actions.
5. **Claude model menu** — Haiku / Sonnet / Opus / Fable, all four verified to answer.

Not verified: how the voices sound (the developer agent cannot hear), on-screen look in a real window (the user was
in a fullscreen game), microphone and real mouse gestures.

## 2026-11-16 · v0.2 smoothness, sharpness, UI, voice, one agent per character

Feedback: choppy animation, blurry character edges, plain menu and windows, robotic voice; wire the dragon girl to
Codex and the Claude character to Claude Code (DeepSeek not installed, skip).

1. **Smoothness** — `stage.Clip` now stream-decodes (only the current frame in memory), so all 25 fps source frames
   are used instead of every third; the window repaints at 60 fps. Measured 4.2 ms mean / 10.6 ms p95 per frame.
2. **Sharpness** — `tools/upscale_assets.py` upscales every frame with Real-ESRGAN animevideov3 (CPU PyTorch):
   colour-bled RGB and the alpha channel go through the network separately; output is stored as `<clip>@2x.webp`.
3. **UI** — `widgets.py` rewritten: follows system light/dark, character accent colour, soft shadows, fades,
   vector icons, typing dots while waiting, rounded translucent menu with a header naming the connected agent.
4. **Voice** — new `voice.py`: replies are split into clauses with per-clause rate/pitch/volume and pauses,
   synthesized in parallel, played in order, bubble text synced per clause. Global pitch shifting removed.
   Voice candidates per character (4 online neural voices + 7 local Kokoro v1.1 voices via sherpa-onnx) can be
   auditioned and chosen from the menu.
5. **Agents** — dragon girl → Codex (uses the Codex desktop app's bundled CLI 0.167; the 0.140 on PATH is too old),
   Claude character → Claude Code, no cross-fallback. DeepSeek character still answered by Claude, labelled as such.
6. **Inner thought** — optional `[think:…]` line shown above the reply in the bubble, never spoken.
7. **16 new actions** (54 total), incl. rain cloud when the mood is low and sunshine when it recovers.
8. **Fullscreen courtesy** — the pet hides and stays silent while a fullscreen app is in the foreground.
9. `tools/render_ui.py` renders the UI offscreen and silently for inspection.

Verified: smoke test incl. real Codex and Claude replies, all 54 actions, @2x decoding; offscreen UI sheets inspected
in both themes; per-clause speech observed in one live run.
Not verified: how the voices actually sound (the developer agent cannot hear audio — audition them in the menu);
real mouse gestures and microphone input; the new actions are not re-exported as GIFs; fullscreen detection on
various games.

## 2026-11-15 · v0.1 first build

### Starting point
`Desktop\AI动漫助手_角色素材\` already held community turnaround sheets for three characters, `情绪动作_v1`
(8 emotions × 4 frames each) and `情绪动作_v2` (12 actions × 8 keyframes, optical-flow interpolated to 96 frames,
for GPT and Claude; only 2 of 12 for DeepSeek).

### Done
1. **Animation** — `tools/import_assets.py` imports v2 (v1 where missing) into `assets/<char>/<action>.webp`.
   `moepal/stage.py` adds 18 procedural motions and an emoji particle system and combines them with the clips into
   **38 composite actions**. DeepSeek's four missing clips fall back to others (`Character.fallback`).
   `tools/export_actions.py` renders 35 of them per character to transparent WebP + GIF (210 files) into
   `情绪动作_v3_组合动作\` in the Desktop asset folder.
2. **Per-character audio track** (`moepal/sfx.py`, fully synthesized): crystal bells + dragon chirp (GPT dragon girl),
   music box + page flips (Claude), bubble plops + whale song + sonar (DeepSeek). 19 event cues + dizzy + purr +
   8 babble syllables each = 87 wavs. Speech uses edge-tts voices with mood-dependent rate/pitch, falls back to
   babble when offline. Output follows the system default device.
3. **Mouse interaction** — slow approach, fast approach (startle), head petting, frantic shaking (dizzy), staring (shy),
   poke / poke-spam (angry), drag and drop with landing squash, wheel to resize, body leans toward the cursor.
4. **Desktop awareness** — foreground app category comments, break reminder after 50 min, late-night worry, CPU and
   battery warnings, dozing off after 4 idle minutes, silence during fullscreen apps, idle fidgets and strolling.
5. **Mood** — valence / energy / affection; shifts with interaction and conversation, decays to a baseline, drives
   idle pose, voice prosody and is included in the LLM prompt.
6. **Conversation** — input bar (double-click or Ctrl+Alt+M), microphone → local faster-whisper, replies tagged
   `[emo:x]` on the first line. Asks for today's task on the first chat of the day and remembers it.
7. **Backends** (`brain.py`) — `claude_cli`, `codex_cli`, `deepseek_api`, tried in per-character order with fallback.
8. **Hide / summon** — peek from the screen edge, full hide, tray icon, global hotkey.
9. **Claude Code look mod** (`claude-mod/ojousama-study`) — warm dark palette, character backdrop, launches the pet.
10. App icon and Desktop shortcut.

### Verification
Verified by running: `tools/smoke_test.py --net` (sfx, all clips and actions, desktop state, edge-tts, Claude CLI
backend incl. an empathy case) and `tools/ui_test.py` (real window; greeting → chat → complaint → empathic reply
with speech → dizzy → good news → hide → summon; screenshots inspected). Backend fallback from Codex to Claude works.

Not verified / known issues:
- **Codex backend currently fails**: installed codex-cli 0.140.0 is too old for the account's default model.
  Until Codex is upgraded the dragon girl's replies come from Claude (haiku).
- **Microphone + Whisper path untested** (nobody spoke during development; first use downloads the ~460 MB model).
- **Real mouse gestures untested** — thresholds are estimates; the UI test drove the states directly.
- **Look mod**: manifest and hooks validate; colour contrast and the backdrop need a visual check in the desktop app.
  It currently lives in one session's dev-mods folder.
- DeepSeek API backend untested (no key). Code is public at GitHub liliyawanttosleep/MoePal (without ssets/ and the
  mod backdrop). No exe build, no Release, no website entry: a usable build needs the art, which cannot be shipped.
