# -*- coding: utf-8 -*-
"""Haze —— KeShare 的统一磨砂视觉语言（Windows 实现）。

"Haze" 是 KeShare 从 0.0.1 beta1 起采用的视觉语言名，指**磨砂玻璃**：
半透明底色 + 背景模糊 + 颗粒噪点 + 圆角 + 1px 描边 + 顶部柔光。
不含折射、不含色散、不含 SDF 高度场 —— 那套（液态玻璃）已从工程移除。

为什么只留磨砂
--------------
折射要每帧重算位移场，纯 CPU 下 30fps 就到极限，且容易出现"位移超过斜面
尺度而互相抵消"的问题。磨砂玻璃的观感收益/代价比高得多，也更稳。

对外接口
--------
    HAZE_VERSION                            视觉语言版本
    HAZE                                    设计令牌（圆角/模糊/噪点/描边/透明度）
    make_backdrop(w, h, palette)            窗口背景（深色底 + 柔和光斑）
    blur_region(image, rect, blur)          取指定区域并模糊
    haze_layer(background, rect, ...)       生成一块 Haze 面板（RGBA）
    paint_haze(image, rect, ...)            直接把 Haze 面板画到整窗图上
"""
from __future__ import annotations

import math
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HAZE_VERSION = "1.0"
HAZE_NAME = "Haze"

# Haze 设计令牌（三端共用同一组数值，改这里即三端同改）
HAZE = {
    "radius_panel": 26,      # 卡片圆角（Haze 偏圆）
    "radius_pill": 40,       # 胶囊圆角
    "blur": 12.0,            # 背景模糊半径
    "alpha": 0.62,           # 底色权重（0 全透 / 1 实心）
    "noise": 1.8,            # 颗粒噪点强度
    "highlight": 0.20,       # 顶部柔光
    "border": 0.16,          # 描边不透明度
    "tint": (18, 24, 38),    # 底色
}

# ---------------------------------------------------------------- 背景
def make_backdrop(width, height, palette=None):
    """窗口背景：深色底 + 几团柔和色斑（只用于衬托磨砂面板，不做折射）。"""
    p = palette or {}
    base = p.get("bg", "#0B0F1A")
    base_rgb = tuple(int(base[i:i + 2], 16) for i in (1, 3, 5))

    width = max(16, int(width))
    height = max(16, int(height))
    img = Image.new("RGB", (width, height), base_rgb)
    draw = ImageDraw.Draw(img, "RGBA")

    blobs = (
        (0.16, 0.12, 0.34, (56, 104, 220), 74),
        (0.66, 0.32, 0.30, (116, 66, 210), 66),
        (0.30, 0.86, 0.26, (32, 132, 165), 54),
        (0.90, 0.72, 0.22, (44, 84, 180), 46),
    )
    long_side = max(width, height)
    for cx, cy, r, color, alpha in blobs:
        radius = r * long_side
        box = [
            cx * width - radius, cy * height - radius,
            cx * width + radius, cy * height + radius,
        ]
        draw.ellipse(box, fill=color + (alpha,))

    # 大半径模糊 → 柔和光斑，不会有色带
    img = img.filter(ImageFilter.GaussianBlur(max(24, long_side * 0.05)))

    # 极轻噪点，避免大面积渐变出现色带
    arr = np.asarray(img).astype(np.float32)
    rng = np.random.default_rng(20261107)
    arr += rng.normal(0, 1.5, arr.shape).astype(np.float32)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


# ---------------------------------------------------------------- 采样
def blur_region(image, rect, blur=12.0):
    """从背景图取一块区域并模糊（磨砂面板的"背后的内容"）。"""
    x, y, w, h = [int(round(v)) for v in rect]
    w = max(4, w)
    h = max(4, h)
    x = max(0, min(x, max(0, image.width - 1)))
    y = max(0, min(y, max(0, image.height - 1)))
    box = (x, y, min(image.width, x + w), min(image.height, y + h))
    crop = image.convert("RGB").crop(box)
    if crop.size != (w, h):
        crop = crop.resize((w, h), Image.BILINEAR)
    if blur > 0.2:
        crop = crop.filter(ImageFilter.GaussianBlur(blur))
    return crop


# ---------------------------------------------------------------- 磨砂面板
def _rounded_mask(size, radius, supersample=3):
    """圆角遮罩，超采样后缩放，边缘平滑无锯齿。"""
    w, h = size
    ss = max(1, int(supersample))
    big = Image.new("L", (w * ss, h * ss), 0)
    d = ImageDraw.Draw(big)
    r = max(0, min(radius * ss, (w * ss) / 2.0, (h * ss) / 2.0))
    d.rounded_rectangle([0, 0, w * ss - 1, h * ss - 1], radius=r, fill=255)
    return big.resize((w, h), Image.LANCZOS)


def haze_layer(background, rect, radius=None, tint=None, alpha=None,
               blur=None, noise=None, highlight=None, border=None):
    """生成一块磨砂玻璃（RGBA）。

    background : 整窗背景图
    rect       : 面板在窗口中的位置 (x, y, w, h)
    tint       : 底色（越接近窗口底色越"厚"）
    alpha      : 底色权重，0=完全透出背景，1=完全实心
    blur       : 背景模糊半径
    noise      : 噪点强度（磨砂颗粒感）
    highlight  : 顶部高光强度
    border     : 描边强度
    """
    radius = HAZE["radius_panel"] if radius is None else radius
    tint = HAZE["tint"] if tint is None else tint
    alpha = HAZE["alpha"] if alpha is None else alpha
    blur = HAZE["blur"] if blur is None else blur
    noise = HAZE["noise"] if noise is None else noise
    highlight = HAZE["highlight"] if highlight is None else highlight
    border = HAZE["border"] if border is None else border

    x, y, w, h = [int(round(v)) for v in rect]
    w = max(8, w)
    h = max(8, h)

    sampled = np.asarray(blur_region(background, (x, y, w, h), blur)).astype(np.float32)

    # 底色混合
    t = np.array(tint, dtype=np.float32)
    out = sampled * (1.0 - alpha) + t * alpha

    # 磨砂颗粒
    if noise > 0:
        rng = np.random.default_rng(7)
        out += rng.normal(0, noise, out.shape).astype(np.float32)

    # 顶部一道柔和高光（模拟顶光）
    yy = np.arange(h, dtype=np.float32)[:, None]
    top = np.clip(1.0 - yy / max(1.0, h * 0.42), 0.0, 1.0) ** 2.2
    out += top[..., None] * 255.0 * highlight * 0.16

    out = np.clip(out, 0, 255).astype(np.uint8)
    layer = Image.fromarray(out, "RGB").convert("RGBA")

    # 圆角遮罩
    mask = _rounded_mask((w, h), radius)
    layer.putalpha(mask)

    # 1px 描边
    if border > 0:
        edge = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(edge)
        r = max(0, min(radius, w / 2.0, h / 2.0))
        d.rounded_rectangle([0, 0, w - 1, h - 1], radius=r,
                            outline=(255, 255, 255, int(255 * border)), width=1)
        layer = Image.alpha_composite(layer, edge)

    return layer


def paint_haze(image, rect, radius=None, **kw):
    """便捷入口：把磨砂面板直接画到整窗图上。"""
    layer = haze_layer(image, rect, radius, **kw)
    x, y = int(rect[0]), int(rect[1])
    image.alpha_composite(layer, (x, y))
    return image


if __name__ == "__main__":
    bg = make_backdrop(900, 600)
    canvas = bg.convert("RGBA")
    paint_haze(canvas, (80, 80, 420, 220), 22)
    paint_haze(canvas, (520, 400, 300, 90), 45, alpha=0.72, blur=16)
    canvas.convert("RGB").save("D:/KeShare/.scratch/haze-demo.png")
    print("Haze", HAZE_VERSION, "已输出 haze-demo.png")
