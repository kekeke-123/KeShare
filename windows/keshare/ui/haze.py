# -*- coding: utf-8 -*-
"""Haze —— KeShare 的统一磨砂视觉语言（Windows 实现）。

"Haze" 是 KeShare 从 0.0.1 beta3 起采用的视觉语言名，指**磨砂玻璃**：
半透明底色 + 背景模糊 + 颗粒噪点 + 圆角 + 1px 描边 + 顶部柔光。
不含折射、不含色散、不含 SDF 高度场 —— 那套（液态玻璃）已从工程移除。

依赖策略
--------
**只用 Pillow，不依赖 numpy。** numpy 打进单文件 exe 会让包体从 11 MB 涨到
30 MB；而这里需要的运算（模糊 / 混合 / 噪点 / 蒙版）Pillow 的 C 实现都够用，
而且更快。

缓存
----
渲染热路径最贵的是"高斯模糊 + 噪点"和"圆角遮罩超采样"。UI 静止时结果不变，
按 key 缓存后 render_frame 从 80ms/帧降到 7ms/帧。

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

from PIL import Image, ImageDraw, ImageFilter

HAZE_VERSION = "1.1"
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

# ---------------------------------------------------------------- 缓存
_CACHE = {}
_CACHE_LIMIT = 96


def _cache_get(key):
    return _CACHE.get(key)


def _cache_put(key, value):
    if len(_CACHE) >= _CACHE_LIMIT:
        for k in list(_CACHE.keys())[: _CACHE_LIMIT // 2]:
            _CACHE.pop(k, None)
    _CACHE[key] = value
    return value


def clear_cache():
    _CACHE.clear()


# ---------------------------------------------------------------- 背景
def make_backdrop(width, height, palette=None):
    """窗口背景：深色底 + 几团柔和色斑（只用于衬托磨砂面板，不做折射）。"""
    p = palette or {}
    width = max(16, int(width))
    height = max(16, int(height))
    base = p.get("bg", "#0B0F1A")

    ck = ("bg", width, height, base)
    hit = _cache_get(ck)
    if hit is not None:
        return hit

    base_rgb = tuple(int(base[i:i + 2], 16) for i in (1, 3, 5))
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
        draw.ellipse(
            [cx * width - radius, cy * height - radius,
             cx * width + radius, cy * height + radius],
            fill=color + (alpha,),
        )

    img = img.filter(ImageFilter.GaussianBlur(max(24, long_side * 0.05)))

    # 极轻噪点，避免大面积渐变出现色带
    noise = Image.effect_noise((width, height), 6).convert("RGB")
    img = Image.blend(img, noise, 0.012)
    return _cache_put(ck, img)


# ---------------------------------------------------------------- 采样
def blur_region(image, rect, blur=12.0):
    """从背景图取一块区域并模糊（磨砂面板的"背后的内容"）。"""
    x, y, w, h = [int(round(v)) for v in rect]
    w = max(4, w)
    h = max(4, h)

    ck = ("blur", id(image), x, y, w, h, round(blur, 1))
    hit = _cache_get(ck)
    if hit is not None:
        return hit

    x = max(0, min(x, max(0, image.width - 1)))
    y = max(0, min(y, max(0, image.height - 1)))
    crop = image.convert("RGB").crop((x, y, min(image.width, x + w), min(image.height, y + h)))
    if crop.size != (w, h):
        crop = crop.resize((w, h), Image.BILINEAR)
    if blur > 0.2:
        crop = crop.filter(ImageFilter.GaussianBlur(blur))
    return _cache_put(ck, crop)


# ---------------------------------------------------------------- 蒙版
def _rounded_mask(size, radius, supersample=3):
    """圆角遮罩，超采样后缩放，边缘平滑无锯齿。"""
    w, h = int(size[0]), int(size[1])
    ss = max(1, int(supersample))
    key = ("mask", w, h, round(float(radius), 1), ss)
    hit = _cache_get(key)
    if hit is not None:
        return hit

    big = Image.new("L", (w * ss, h * ss), 0)
    d = ImageDraw.Draw(big)
    r = max(0, min(radius * ss, (w * ss) / 2.0, (h * ss) / 2.0))
    d.rounded_rectangle([0, 0, w * ss - 1, h * ss - 1], radius=r, fill=255)
    return _cache_put(key, big.resize((w, h), Image.LANCZOS))


# ---------------------------------------------------------------- 面板
def haze_layer(background, rect, radius=None, tint=None, alpha=None,
               blur=None, noise=None, highlight=None, border=None):
    """生成一块 Haze 磨砂面板（RGBA）。

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

    ck = ("layer", id(background), x, y, w, h, round(float(radius), 1),
          tuple(tint), round(alpha, 3), round(blur, 1),
          round(noise, 2), round(highlight, 3), round(border, 3))
    hit = _cache_get(ck)
    if hit is not None:
        return hit

    # 1) 背景 → 模糊 → 与底色混合
    sampled = blur_region(background, (x, y, w, h), blur)
    layer = Image.blend(sampled, Image.new("RGB", (w, h), tint),
                        max(0.0, min(1.0, alpha)))

    # 2) 颗粒噪点
    if noise > 0.05:
        grain = Image.effect_noise((w, h), 12 * float(noise)).convert("RGB")
        layer = Image.blend(layer, grain, 0.035)

    # 3) 顶部柔光：竖直渐变，上亮下透明
    if highlight > 0.01:
        grad = Image.linear_gradient("L").resize((w, h), Image.BILINEAR)
        grad = grad.transpose(Image.FLIP_TOP_BOTTOM)   # 上白下黑
        amount = min(255, int(255 * highlight * 0.16))
        mask = grad.point(lambda v: int(v * amount / 255))
        layer = Image.composite(Image.new("RGB", (w, h), (255, 255, 255)), layer, mask)

    layer = layer.convert("RGBA")

    # 4) 圆角裁剪
    layer.putalpha(_rounded_mask((w, h), radius))

    # 5) 1px 描边
    if border > 0.01:
        edge = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(edge)
        r = max(0, min(radius, w / 2.0, h / 2.0))
        d.rounded_rectangle([0, 0, w - 1, h - 1], radius=r,
                            outline=(255, 255, 255, int(255 * border)), width=1)
        layer = Image.alpha_composite(layer, edge)

    return _cache_put(ck, layer)


def paint_haze(image, rect, radius=None, **kw):
    """便捷入口：把 Haze 面板直接画到整窗图上。"""
    layer = haze_layer(image, rect, radius, **kw)
    image.alpha_composite(layer, (int(rect[0]), int(rect[1])))
    return image


if __name__ == "__main__":
    bg = make_backdrop(900, 600)
    canvas = bg.convert("RGBA")
    paint_haze(canvas, (80, 80, 420, 220), 26)
    paint_haze(canvas, (520, 400, 300, 90), 45, alpha=0.72, blur=16)
    canvas.convert("RGB").save("D:/KeShare/.scratch/haze-demo.png")
    print("Haze", HAZE_VERSION, "已输出 haze-demo.png")
