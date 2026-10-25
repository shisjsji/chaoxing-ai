# -*- coding: utf-8 -*-
"""生成程序图标：深色底 + 进度弧。纯几何矢量绘制，非 AI 生成。

输出：
  assets/app-icon.png      1024x1024 主图
  assets/app.ico           多尺寸图标（16/32/48/64/128/256）
"""
import os
from PIL import Image, ImageDraw

SIZE = 1024
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
BG = (15, 23, 42)          # #0F172A
ARC = (37, 99, 235)        # #2563EB
HILITE = (96, 165, 250)    # #60A5FA


def rounded_mask(size, radius, supersample=4):
    """做一张带抗锯齿的圆角方形遮罩。"""
    big = size * supersample
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, big - 1, big - 1],
                                           radius=radius * supersample, fill=255)
    return mask.resize((size, size), Image.LANCZOS)


def build(size=SIZE):
    ss = 4                      # 超采样，边缘才干净
    s = size * ss
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    import math

    # 底：深色圆角方块
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.224), fill=BG)

    # 环：外圆填充 + 内圆挖空，比 arc 更干净、对称
    pad = int(s * 0.245)
    box = [pad, pad, s - pad - 1, s - pad - 1]
    width = int(s * 0.086)
    d.ellipse(box, fill=ARC)
    d.ellipse([box[0] + width, box[1] + width, box[2] - width, box[3] - width], fill=BG)

    cx = (box[0] + box[2]) / 2
    cy = (box[1] + box[3]) / 2
    r = (box[2] - box[0]) / 2 - width / 2

    # 环上 2 点方向一个更亮的进度点
    a = math.radians(-52)
    px, py = cx + r * math.cos(a), cy + r * math.sin(a)
    dot = width * 1.06
    d.ellipse([px - dot / 2, py - dot / 2, px + dot / 2, py + dot / 2], fill=HILITE)

    # 中心：两条横线，垂直居中，上长下短
    bar_h = int(s * 0.040)
    gap = int(s * 0.070)
    total_h = gap + bar_h
    top = cy - total_h / 2
    for i, frac in enumerate((0.62, 0.38)):
        w = int(s * 0.26 * frac)
        x0 = int(cx - w / 2)
        y0 = int(top + i * gap)
        d.rounded_rectangle([x0, y0, x0 + w, y0 + bar_h],
                            radius=bar_h // 2, fill=HILITE)

    img = img.resize((size, size), Image.LANCZOS)
    # 圆角裁切
    mask = rounded_mask(size, int(size * 0.224))
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    icon = build(SIZE)
    png_path = os.path.join(OUT_DIR, "app-icon.png")
    icon.save(png_path)

    ico_path = os.path.join(OUT_DIR, "app.ico")
    icon.save(ico_path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                               (64, 64), (128, 128), (256, 256)])
    print("已生成:")
    print(" ", png_path)
    print(" ", ico_path)


if __name__ == "__main__":
    main()