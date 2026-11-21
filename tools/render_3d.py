"""三维网格 + 原画投影 → 360° 转身序列帧（透明背景），给桌宠的“转身展示”用。
Render a 360° turnaround from the 3D mesh, coloured by projecting the original 2D views onto it.

为什么不用 AI 生成贴图：生成的贴图和原画风格不一样，转身时会“换了个人”。这里在着色时实时把原画
（正面 / 侧面 / 镜像侧面 / 背面）从四个方向投影到模型上，带遮挡判断（每个方向先渲一张深度图），
按表面朝向混合——颜色、线条都来自原画，再加一圈描边保持动漫感。

  D:\\MoePal\\.conda-3d\\python.exe tools\\render_3d.py [gpt claude deepseek]
输入：data/3d/<角色>/shape.glb，assets/<角色>/view_front|side|back.png，assets/<角色>/idle@2x.webp（对齐大小和脚底）
输出：assets/<角色>/turn360@2x.webp + .json，data/3d/<角色>/sheet.png（检查用）
"""
import json
import sys
from pathlib import Path

import moderngl
import numpy as np
import trimesh
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
UP = np.array([0.0, 1.0, 0.0])
N_FRAMES, FPS = 120, 50

VS_DEPTH = """
#version 330
uniform vec3 R; uniform vec3 U; uniform vec3 D; uniform vec4 M; uniform float RAD;
in vec3 pos; in vec3 nrm;
void main() {
    vec2 uv = vec2(dot(pos, R) * M.x + M.y, dot(pos, U) * M.z + M.w);
    gl_Position = vec4(uv.x * 2.0 - 1.0, 1.0 - uv.y * 2.0, -dot(pos, D) / RAD, 1.0);
}"""
FS_DEPTH = "#version 330\nout vec4 c; void main(){ c = vec4(1.0); }"

VS_MAIN = """
#version 330
uniform mat3 ROT; uniform vec3 CR; uniform vec3 CU; uniform vec3 CD; uniform vec4 CM; uniform float RAD;
uniform float OUTLINE;
in vec3 pos; in vec3 nrm;
out vec3 v_pos; out vec3 v_nrm; out vec3 v_nrot;
void main() {
    vec3 p = ROT * (pos + nrm * OUTLINE);
    vec2 uv = vec2(dot(p, CR) * CM.x + CM.y, dot(p, CU) * CM.z + CM.w);
    gl_Position = vec4(uv.x * 2.0 - 1.0, 1.0 - uv.y * 2.0, -dot(p, CD) / RAD, 1.0);
    v_pos = pos; v_nrm = nrm; v_nrot = ROT * nrm;
}"""
FS_MAIN = """
#version 330
uniform sampler2D TEX[4]; uniform sampler2D DEP[4];
uniform vec3 PR[4]; uniform vec3 PU[4]; uniform vec3 PD[4]; uniform vec4 PM[4];
uniform float RAD; uniform vec3 CD; uniform int MODE; uniform vec3 LINE;
in vec3 v_pos; in vec3 v_nrm; in vec3 v_nrot;
out vec4 col;
void main() {
    if (MODE == 1) { col = vec4(LINE, 1.0); return; }           // 描边
    vec3 n = normalize(v_nrm);
    vec3 acc = vec3(0.0); float wsum = 0.0;
    vec3 fb = vec3(0.0); float fbw = 0.0;
    for (int i = 0; i < 4; i++) {
        vec2 uv = vec2(dot(v_pos, PR[i]) * PM[i].x + PM[i].y, dot(v_pos, PU[i]) * PM[i].z + PM[i].w);
        if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) continue;
        float facing = max(dot(n, PD[i]), 0.0);
        vec4 t = texture(TEX[i], uv);
        if (t.a < 0.5) continue;
        float z = (-dot(v_pos, PD[i]) / RAD) * 0.5 + 0.5;
        float d = texture(DEP[i], uv).r;
        float vis = z <= d + 0.004 ? 1.0 : 0.0;
        float w = vis * pow(facing, 22.0);       // 越正对哪张原画就越只用哪张，减少重影
        acc += t.rgb * w; wsum += w;
        fb += t.rgb * (facing + 0.05); fbw += facing + 0.05;
    }
    vec3 c = wsum > 1e-4 ? acc / wsum : (fbw > 0.0 ? fb / fbw : vec3(0.85));
    float lit = 0.94 + 0.06 * max(dot(normalize(v_nrot), normalize(vec3(-0.4, 0.6, 0.7))), 0.0);
    col = vec4(c * lit, 1.0);
}"""


def load_mesh(p):
    m = trimesh.load(p, force="mesh")
    v = np.asarray(m.vertices, dtype=np.float64)
    c = (v.max(0) + v.min(0)) / 2
    v = v - c
    v /= (v[:, 1].max() - v[:, 1].min()) / 2          # 高度归一到 2（y 向上）
    m.vertices = v
    # 去掉粘在脚底的薄“地板”：最底下 1.5% 高度里的面（只有那片板和鞋底，鞋底正常视角看不到）
    low = v[:, 1] < v[:, 1].min() + 0.03
    keep = ~low[m.faces].all(axis=1)
    m.update_faces(keep)
    m.remove_unreferenced_vertices()
    return m


def rgba(path, flip=False):
    im = Image.open(path).convert("RGBA")
    im = im.crop(im.getchannel("A").getbbox())
    return im.transpose(Image.FLIP_LEFT_RIGHT) if flip else im


def solid_mask(im, h=160):
    """原画的“实心”轮廓：去掉脚下那层浅灰阴影（低饱和、很亮、半透明）。"""
    a = np.asarray(im.getchannel("A"), np.float32) / 255
    rgb = np.asarray(im.convert("RGB"), np.float32) / 255
    mx, mn = rgb.max(2), rgb.min(2)
    shadow = (mn > 0.72) & (mx - mn < 0.06) & (a < 0.9)
    m = Image.fromarray(((a > 0.5) & ~shadow).astype(np.uint8) * 255)
    w = max(8, round(im.width * h / im.height))
    return np.asarray(m.resize((w, h), Image.BILINEAR)) > 127


def basis(d, mirror=False):
    r = np.cross(UP, d)
    return (-r if mirror else r), UP.copy(), d


def silhouette(v, r, u, shape, s, ox, oy):
    h, w = shape
    xs = (v @ r) * s + ox
    ys = -(v @ u) * s + oy
    m = np.zeros((h, w), bool)
    ix, iy = np.clip(xs.astype(int), 0, w - 1), np.clip(ys.astype(int), 0, h - 1)
    ok = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
    m[iy[ok], ix[ok]] = True
    img = Image.fromarray(m.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(3))
    return np.asarray(img) > 127


def fit(v, r, u, mask):
    """在小范围里找缩放和偏移，让模型投影轮廓和原画轮廓重合得最好。返回 (IoU, s, ox, oy)，单位是 mask 像素。"""
    h, w = mask.shape
    xs, ys = v @ r, v @ u
    ys_ext = ys.max() - ys.min()
    best = (-1, 0, 0, 0)
    for k in np.linspace(0.88, 1.06, 10):
        s = h / ys_ext * k
        for dx in np.linspace(-0.06, 0.06, 7) * w:
            for dy in np.linspace(-0.05, 0.05, 6) * h:
                ox = w / 2 - (xs.max() + xs.min()) / 2 * s + dx
                oy = ys.max() * s + dy
                sil = silhouette(v, r, u, (h, w), s, ox, oy)
                iou = (sil & mask).sum() / max(1, (sil | mask).sum())
                if iou > best[0]:
                    best = (iou, s, ox, oy)
    return best


def main():
    chars = sys.argv[1:] or ["gpt", "claude", "deepseek"]
    ctx = moderngl.create_standalone_context(require=330)
    for cid in chars:
        mesh = load_mesh(ROOT / "data" / "3d" / cid / "shape.glb")
        v = np.asarray(mesh.vertices, np.float32)
        nrm = np.asarray(mesh.vertex_normals, np.float32)
        a = ROOT / "assets" / cid
        imgs = {"front": rgba(a / "view_front.png"), "side": rgba(a / "view_side.png"),
                "side_m": rgba(a / "view_side.png", True), "back": rgba(a / "view_back.png")}
        masks = {k: solid_mask(im) for k, im in imgs.items()}
        sub = v[:: max(1, len(v) // 30000)]

        # 1) 找“正面”朝哪个方向（以及坐标系是否镜像）：三张图的轮廓一起对
        best = None
        for f in (np.array([0, 0, 1.0]), np.array([0, 0, -1.0]), np.array([1.0, 0, 0]), np.array([-1.0, 0, 0])):
            for mir in (False, True):
                side_d = np.cross(UP, f) * (-1 if mir else 1)
                views = [("front", f), ("side", side_d), ("back", -f)]
                score = sum(fit(sub, *basis(d, mir)[:2], masks[k])[0] for k, d in views)
                if best is None or score > best[0]:
                    best = (score, f, mir)
        score, F, MIR = best
        print(f"{cid}: 正面方向 {F.tolist()} 镜像 {MIR}，三视图轮廓平均重合 {score / 3:.2f}", flush=True)

        # 2) 四个投影方向：正面、背面、侧面（原图）、另一侧（镜像侧图）
        side_d = np.cross(UP, F) * (-1 if MIR else 1)
        proj = [("front", F), ("back", -F), ("side", side_d), ("side_m", -side_d)]
        RAD = float(np.linalg.norm(v, axis=1).max() * 1.05)
        vbo = ctx.buffer(np.hstack([v, nrm]).astype("f4").tobytes())
        ibo = ctx.buffer(np.asarray(mesh.faces, np.int32).tobytes())
        pd = ctx.program(vertex_shader=VS_DEPTH, fragment_shader=FS_DEPTH)
        pm = ctx.program(vertex_shader=VS_MAIN, fragment_shader=FS_MAIN)
        vao_d = ctx.vertex_array(pd, [(vbo, "3f 3x4", "pos")], ibo)       # 深度图只要位置
        vao_m = ctx.vertex_array(pm, [(vbo, "3f 3f", "pos", "nrm")], ibo)
        texs, deps, params = [], [], []
        for i, (key, d) in enumerate(proj):
            im = imgs[key]
            r, u, _ = basis(d, MIR)
            mk = masks[key]
            iou, s, ox, oy = fit(sub, r, u, mk)
            k = im.height / mk.shape[0]                  # mask 像素 → 原图像素
            W, H = im.size
            M = (s * k / W, ox * k / W, -s * k / H, oy * k / H)
            t = ctx.texture(im.size, 4, im.tobytes())
            t.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
            t.build_mipmaps()
            dt = ctx.depth_texture((min(W, 1024), min(H, 1024)))
            dt.compare_func = ""
            fbo = ctx.framebuffer(depth_attachment=dt)
            fbo.use()
            fbo.clear(depth=1.0)
            ctx.enable(moderngl.DEPTH_TEST)
            ctx.disable(moderngl.CULL_FACE)
            for name, val in (("R", r), ("U", u), ("D", d)):
                pd[name].value = tuple(float(x) for x in val)
            pd["M"].value = M
            pd["RAD"].value = RAD
            vao_d.render()
            texs.append(t)
            deps.append(dt)
            params.append((r, u, d, M))
            print(f"  投影 {key}: 轮廓重合 {iou:.2f}", flush=True)

        # 3) 和待机动图对齐：同样的身高、脚底、水平中心
        ref = next(a / f"{k}@2x.webp" for k in ("idle", "anxious", "down", "wave") if (a / f"{k}@2x.webp").exists())
        idle = Image.open(ref).convert("RGBA")             # 待机动图（没有就用 576×832 的替代动作）
        bb = idle.getchannel("A").point(lambda x: 255 if x > 40 else 0).getbbox()
        OW, OH = idle.size
        SS = 2                                             # 超采样
        W, H = OW * SS, OH * SS
        cr, cu, cd = basis(F, MIR)
        ys = v @ cu
        xs = v @ cr
        s = (bb[3] - bb[1]) / (ys.max() - ys.min()) * SS
        ox = (bb[0] + bb[2]) / 2 * SS - (xs.max() + xs.min()) / 2 * s
        oy = bb[1] * SS + ys.max() * s
        CM = (s / W, ox / W, -s / H, oy / H)

        color = ctx.renderbuffer((W, H), 4, samples=8)
        depth = ctx.depth_renderbuffer((W, H), samples=8)
        fbo_ms = ctx.framebuffer([color], depth)
        out_c = ctx.texture((W, H), 4)
        fbo_out = ctx.framebuffer([out_c])
        for i in range(4):
            texs[i].use(location=i)
            deps[i].use(location=4 + i)
        pm["TEX"].value = [0, 1, 2, 3]
        pm["DEP"].value = [4, 5, 6, 7]
        pm["PR"].value = [tuple(map(float, p[0])) for p in params]
        pm["PU"].value = [tuple(map(float, p[1])) for p in params]
        pm["PD"].value = [tuple(map(float, p[2])) for p in params]
        pm["PM"].value = [p[3] for p in params]
        pm["RAD"].value = RAD
        pm["CR"].value, pm["CU"].value, pm["CD"].value = (tuple(map(float, x)) for x in (cr, cu, cd))
        pm["CM"].value = CM
        line = {"gpt": (0.36, 0.30, 0.42), "claude": (0.33, 0.18, 0.12), "deepseek": (0.12, 0.16, 0.32)}.get(cid, (0.2, 0.2, 0.2))
        pm["LINE"].value = line
        frames = []
        for fi in range(N_FRAMES):
            yaw = 2 * np.pi * fi / N_FRAMES
            c, sn = np.cos(yaw), np.sin(yaw)
            rot = np.array([[c, 0, sn], [0, 1, 0], [-sn, 0, c]], np.float32)      # 绕竖直轴转
            pm["ROT"].write(rot.T.astype("f4").tobytes())
            fbo_ms.use()
            fbo_ms.clear(0, 0, 0, 0, depth=1.0)
            ctx.enable(moderngl.DEPTH_TEST | moderngl.CULL_FACE)
            ctx.front_face = "ccw"
            ctx.cull_face = "front"                        # 描边：只画背面、沿法线外扩一点
            pm["MODE"].value, pm["OUTLINE"].value = 1, 0.0028
            vao_m.render()
            ctx.disable(moderngl.CULL_FACE)
            pm["MODE"].value, pm["OUTLINE"].value = 0, 0.0
            vao_m.render()
            ctx.copy_framebuffer(fbo_out, fbo_ms)
            arr = np.flipud(np.frombuffer(out_c.read(), np.uint8).reshape(H, W, 4)).copy()   # OpenGL 从最底行开始读
            im = Image.fromarray(arr, "RGBA").resize((OW, OH), Image.LANCZOS)  # 预乘颜色下缩小，边缘不发黑
            p = np.asarray(im, np.float32)
            al = p[..., 3:4] / 255
            p[..., :3] = np.where(al > 0, p[..., :3] / np.maximum(al, 1e-3), 0)
            frames.append(Image.fromarray(np.clip(p, 0, 255).astype(np.uint8), "RGBA"))
        dst = a / "turn360@2x.webp"
        tmp = a / "turn360@2x.tmp.webp"
        frames[0].save(tmp, save_all=True, append_images=frames[1:], duration=round(1000 / FPS), loop=0, quality=92,
                       method=3)
        tmp.replace(dst)
        im = Image.open(dst)
        durs = []
        for i in range(im.n_frames):
            im.seek(i)
            im.load()
            durs.append(int(im.info.get("duration") or 20))
        dst.with_suffix(".json").write_text(json.dumps({"durations": durs, "source": "3d"}), encoding="utf-8")
        sheet = Image.new("RGBA", (OW // 2 * 8, OH // 2), (40, 46, 62, 255))
        for k, fi in enumerate(range(0, N_FRAMES, N_FRAMES // 8)):
            fr = frames[fi].resize((OW // 2, OH // 2))
            sheet.alpha_composite(fr, (k * OW // 2, 0))
        sheet.save(ROOT / "data" / "3d" / cid / "sheet.png")
        print(f"{cid}: {N_FRAMES} 帧 → {dst}（{dst.stat().st_size // 1024}KB）", flush=True)


if __name__ == "__main__":
    main()
