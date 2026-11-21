"""三视图 → 三维网格（Hunyuan3D-2mv，腾讯开源；用正面 / 侧面 / 背面三张图一起生成形状）。
Turnaround views → 3D mesh with Hunyuan3D-2mv (shape only; colours come from projecting the original art, see render_3d.py).

在独立环境里运行（PyTorch CUDA）：
  D:\\MoePal\\.conda-3d\\python.exe tools\\make_3d.py [gpt claude deepseek]
需要：data/tools/Hunyuan3D-2（官方代码），data/hy3dgen/tencent/Hunyuan3D-2mv/hunyuan3d-dit-v2-mv（权重，ModelScope 下载）。
输出：data/3d/<角色>/shape.glb
许可：Hunyuan3D 2.0 采用 Tencent Hunyuan 3D 2.0 Community License（不适用于欧盟、英国、韩国；个人非商业使用可以）。
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("HY3DGEN_MODELS", str(ROOT / "data" / "hy3dgen"))
sys.path.insert(0, str(ROOT / "data" / "tools" / "Hunyuan3D-2"))

import torch  # noqa: E402
from PIL import Image  # noqa: E402


def prep(path, size=1024):
    """透明立绘放到正方形中央，留边，背景白色（和官方示例输入一致）。"""
    import numpy as np
    im = Image.open(path).convert("RGBA")
    # 去掉脚下的浅灰阴影（很亮、几乎没颜色、半透明），否则模型会多出一块“地板”
    arr = np.asarray(im).astype(np.float32)
    rgb, a = arr[..., :3] / 255, arr[..., 3] / 255
    shadow = (rgb.min(2) > 0.72) & (rgb.max(2) - rgb.min(2) < 0.06) & (a < 0.9)
    h = arr.shape[0]
    shadow[: int(h * 0.75)] = False                      # 只处理下方四分之一
    arr[shadow, 3] = 0
    im = Image.fromarray(arr.astype(np.uint8), "RGBA")
    bb = im.getchannel("A").point(lambda v: 255 if v > 20 else 0).getbbox()
    im = im.crop(bb)
    side = int(max(im.size) / 0.8)
    canvas = Image.new("RGBA", (side, side), (255, 255, 255, 0))
    canvas.alpha_composite(im, ((side - im.width) // 2, (side - im.height) // 2))
    return canvas.resize((size, size), Image.LANCZOS)


def main():
    from hy3dgen.shapegen import DegenerateFaceRemover, FaceReducer, FloaterRemover, Hunyuan3DDiTFlowMatchingPipeline
    chars = sys.argv[1:] or ["gpt", "claude", "deepseek"]
    pipe = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained("tencent/Hunyuan3D-2mv", subfolder="hunyuan3d-dit-v2-mv",
                                                            variant="fp16")
    for cid in chars:
        a = ROOT / "assets" / cid
        views = {"front": prep(a / "view_front.png"), "left": prep(a / "view_side.png"), "back": prep(a / "view_back.png")}
        out = ROOT / "data" / "3d" / cid
        out.mkdir(parents=True, exist_ok=True)
        for k, v in views.items():
            v.save(out / f"in_{k}.png")
        t = time.time()
        mesh = pipe(image=views, num_inference_steps=50, octree_resolution=384, num_chunks=20000,
                    generator=torch.manual_seed(1234), output_type="trimesh")[0]
        mesh = FloaterRemover()(mesh)
        mesh = DegenerateFaceRemover()(mesh)
        mesh = FaceReducer()(mesh, max_facenum=200000)
        mesh.export(out / "shape.glb")
        print(f"{cid}: {len(mesh.vertices)} 顶点 {len(mesh.faces)} 面 {time.time() - t:.0f}s → {out / 'shape.glb'}", flush=True)
        torch.cuda.empty_cache()


if __name__ == "__main__":
    main()
