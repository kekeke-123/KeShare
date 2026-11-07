# -*- coding: utf-8 -*-
"""生成 KeShare 的 App 图标（无资产目录，直接用 PNG 文件）。

用法：python tools/make_icons.py
输出：ios/Resources/ 下的各尺寸 PNG + iTunesArtwork
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ios", "Resources")

# iOS 需要的尺寸：文件名 -> 像素
SIZES = {
    "Icon-20@2x.png": 40,
    "Icon-20@3x.png": 60,
    "Icon-29@2x.png": 58,
    "Icon-29@3x.png": 87,
    "Icon-40@2x.png": 80,
    "Icon-40@3x.png": 120,
    "Icon-60@2x.png": 120,
    "Icon-60@3x.png": 180,
    "Icon-76.png": 76,
    "Icon-76@2x.png": 152,
    "Icon-83.5@2x.png": 167,
    "Icon-1024.png": 1024,
}


def lerp(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def draw_icon(size):
    """深色渐变底 + 双向箭头（表示互传）+ 高光。"""
    scale = 4  # 超采样，边缘更干净
    s = size * scale
    img = Image.new("RGB", (s, s), (10, 14, 24))
    draw = ImageDraw.Draw(img)

    # 对角渐变：蓝紫 -> 青
    c1, c2 = (92, 184, 255), (160, 107, 255)
    for y in range(s):
        t = y / max(1, s - 1)
        row = lerp(c1, c2, t)
        draw.line([(0, y), (s, y)], fill=row)

    # 中间压暗，突出白色图形
    overlay = Image.new("L", (s, s), 0)
    od = ImageDraw.Draw(overlay)
    od.ellipse([-s * 0.3, -s * 0.2, s * 1.3, s * 1.4], fill=70)
    img = Image.composite(Image.new("RGB", (s, s), (8, 12, 22)), img, overlay)
    draw = ImageDraw.Draw(img)

    # 两个箭头：上箭头向右，下箭头向左
    pad = s * 0.26
    thickness = int(s * 0.072)
    head = int(s * 0.095)

    def arrow(x1, y, x2, up):
        draw.line([(x1, y), (x2, y)], fill=(255, 255, 255), width=thickness)
        tip = x2
        back = tip + head if up else tip - head
        points = [(tip, y - head), (tip, y + head), (back, y)]
        draw.polygon(points, fill=(255, 255, 255))

    y1 = s * 0.40
    y2 = s * 0.60
    arrow(pad, y1, s - pad, False)
    arrow(s - pad, y2, pad, True)

    # 顶部高光
    gloss = Image.new("L", (s, s), 0)
    gd = ImageDraw.Draw(gloss)
    gd.ellipse([-s * 0.4, -s * 0.7, s * 1.4, s * 0.45], fill=48)
    gloss = gloss.filter(ImageFilter.GaussianBlur(s * 0.05))
    img = Image.composite(Image.new("RGB", (s, s), (255, 255, 255)), img, gloss)

    return img.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(OUT, exist_ok=True)
    made = []
    for name, size in SIZES.items():
        image = draw_icon(size)
        path = os.path.join(OUT, name)
        image.save(path, "PNG")
        made.append((name, size, os.path.getsize(path)))
    # 1024 的 iTunesArtwork（老式，但对部分安装器有用）
    draw_icon(1024).save(os.path.join(OUT, "iTunesArtwork.png"), "PNG")
    for name, size, length in made:
        print("%-18s %4dpx  %6.1f KB" % (name, size, length / 1024.0))


if __name__ == "__main__":
    main()
