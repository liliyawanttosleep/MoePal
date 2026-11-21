"""用 CosyVoice（慢，但会按情绪换语气）为每位角色录一套“情绪参考音频”，供聊天时的 ZipVoice（快）模仿语气。
Generate per-character, per-emotion reference clips with CosyVoice; ZipVoice then imitates both timbre and mood in real time.

  .venv\\Scripts\\python tools\\make_emotion_refs.py [gpt claude deepseek] [--sweet 0,1,2]
输出：data/voices/<角色>/emo_<情绪组>_s<甜度>.wav + .txt
情绪组：happy / sad / angry / soft / calm（moepal/voice.py 的 EMO_GROUP 把细分情绪归到这几组）。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from moepal import voice  # noqa: E402
from moepal.characters import CHARACTERS  # noqa: E402

# 每组用一个“代表情绪”去驱动 CosyVoice 的语气指令（cosy_worker.INSTRUCT）
DRIVER = {"happy": "happy", "sad": "sad", "angry": "angry", "soft": "shy", "calm": "calm"}
TEXT = {
    "gpt": {
        "happy": "哇，太好啦！本龙就知道你一定可以的！嘿嘿，尾巴都要甩起来了！",
        "sad": "唉……今天好像有点不顺利呢。没关系的，本龙陪着你。",
        "angry": "喂！不许再戳本龙的角了！再戳我真的要喷火了！",
        "soft": "嗯……这么晚了，你还没睡吗？本龙有点担心你哦。",
        "calm": "我把今天要做的事理了一下，我们先从第一件开始吧。",
    },
    "claude": {
        "happy": "真的写完了？太好了。今天就好好奖励一下自己吧。",
        "sad": "唉……没关系的。难过的时候，就先什么都别想了。",
        "angry": "够了哦。再这样戳下去，我可真的要生气了。",
        "soft": "嗯，我在呢。别着急，慢慢说，我听着。",
        "calm": "我们先把要做的事写下来，然后一件一件地完成。",
    },
    "deepseek": {
        "happy": "真的吗？主人夸我了！嘿嘿，小鲸要记在小本本上！",
        "sad": "呜……小鲸又搞砸了。对不起主人，下次一定会做好的。",
        "angry": "不是大肥鱼！是小鲸！小鲸要生气了，真的哦！",
        "soft": "主人，好晚了哦……要不要先喝杯温水，休息一下？",
        "calm": "今天的清单小鲸整理好了，我们一项一项来吧。",
    },
}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sweets = [int(x) for x in (sys.argv[sys.argv.index("--sweet") + 1].split(",") if "--sweet" in sys.argv else "0,1,2".split(","))]
    for cid in args or list(TEXT):
        spec = next(v for v in CHARACTERS[cid].voices if v["engine"] in ("cosy", "zipvoice"))
        for s in sweets:
            base_wav, base_txt = voice.ensure_prompt(cid, spec, s)
            for grp, text in TEXT[cid].items():
                out = voice.VOICES / cid / f"emo_{grp}_s{s}.wav"
                if out.exists():
                    continue
                t = time.time()
                r = voice.COSY.synth(text, base_wav, base_txt, out, emo=DRIVER[grp])
                out.with_suffix(".txt").write_text(text, encoding="utf-8")
                print(f"{cid} 甜度{s} {grp:5}: {r['dur']}s 音频，用时 {time.time() - t:.1f}s", flush=True)
    voice.COSY.close()


if __name__ == "__main__":
    main()
