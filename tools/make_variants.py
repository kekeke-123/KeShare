# -*- coding: utf-8 -*-
"""从一个源图标生成两个版本（稳定版 / Lite 青春版）的图标集，并打包成两个 IPA。

稳定版：原图（左下角 “Free”）
Lite  ：把 “Free” 改写为 “Lite”，其余保持一致

用法：
    python tools/make_variants.py [--ipa 路径] [--out 目录]
"""
from __future__ import annotations

import argparse
import os
import shutil
import zipfile

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_ICON = r"C:\Users\Administrator\Pictures\KeShare.icon"

SIZES = {
    "Icon-20@2x.png": 40, "Icon-20@3x.png": 60,
    "Icon-29@2x.png": 58, "Icon-29@3x.png": 87,
    "Icon-40@2x.png": 80, "Icon-40@3x.png": 120,
    "Icon-60@2x.png": 120, "Icon-60@3x.png": 180,
    "Icon-76.png": 76, "Icon-76@2x.png": 152,
    "Icon-83.5@2x.png": 167, "Icon-1024.png": 1024,
}

# “Free” 文字所在的区域（按 1047x1047 源图估计）
BADGE_BOX = (20, 830, 260, 950)

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\ARLRDBD.TTF",   # Arial Rounded MT Bold
    r"C:\Windows\Fonts\ariblk.ttf",
    r"C:\Windows\Fonts\seguisb.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
]


def load_font(size):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def dominant_color(image, box):
    """取区域内出现最多的颜色（用于取绿色三角与黄色文字）。"""
    region = image.crop(box).convert("RGB")
    return max(region.getcolors(maxcolors=1 << 24) or [(1, (0, 0, 0))],
               key=lambda item: item[0])[1]


def make_lite(source):
    """把左下角绿色三角上的 “Free” 改写为 “Lite”。

    做法：只替换黄色文字像素（用邻近绿色填补），再用同样的黄色在原包围盒内画 “Lite”，
    避免矩形填充留下可见色块边。
    """
    image = source.convert("RGB").copy()
    pixels = image.load()
    width, height = image.size

    def is_yellow(rgb):
        r, g, b = rgb
        return r > 170 and g > 140 and b < 150 and (r - b) > 60 and (g - b) > 40

    # 在左下角搜索黄色文字像素，得到包围盒
    x0, y0, x1, y1 = width, height, 0, 0
    for y in range(height):
        for x in range(width):
            if is_yellow(pixels[x, y]):
                x0, y0 = min(x0, x), min(y0, y)
                x1, y1 = max(x1, x), max(y1, y)
    if x1 <= x0 or y1 <= y0:
        return image                      # 找不到就原样返回

    # 采样文字黄色与背景绿色
    yellow = pixels[(x0 + x1) // 2, (y0 + y1) // 2]
    green = pixels[x0, min(height - 1, y1 + 12)]

    # 把黄色像素抹成绿色
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            if is_yellow(pixels[x, y]):
                pixels[x, y] = green

    # 在原包围盒内居中绘制 Lite
    draw = ImageDraw.Draw(image)
    box_w, box_h = x1 - x0 + 1, y1 - y0 + 1
    size = int(box_h * 1.05)
    font = load_font(size)
    bbox = draw.textbbox((0, 0), "Lite", font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = x0 + (box_w - tw) // 2 - bbox[0]
    ty = y0 + (box_h - th) // 2 - bbox[1]
    draw.text((tx, ty), "Lite", font=font, fill=yellow)
    return image


def export_icons(image, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for name, size in SIZES.items():
        image.resize((size, size), Image.LANCZOS).save(os.path.join(out_dir, name), "PNG")
    image.resize((1024, 1024), Image.LANCZOS).save(
        os.path.join(out_dir, "iTunesArtwork.png"), "PNG")


def build_ipa(source_ipa, out_ipa, icons_dir, display_name, bundle_id):
    """把图标与显示名替换进已构建的 IPA。"""
    work = out_ipa + ".work"
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    with zipfile.ZipFile(source_ipa) as zf:
        zf.extractall(work)

    app_dir = os.path.join(work, "Payload", "KeShare.app")
    if not os.path.isdir(app_dir):
        raise SystemExit("IPA 结构不符：%s" % app_dir)

    # 换图标
    for name in os.listdir(icons_dir):
        shutil.copy2(os.path.join(icons_dir, name), os.path.join(app_dir, name))

    # 换显示名与 bundle id
    plist_path = os.path.join(app_dir, "Info.plist")
    with open(plist_path, "r", encoding="utf-8") as fh:
        plist = fh.read()
    plist = plist.replace("<key>CFBundleDisplayName</key>\n\t<string>KeShare</string>",
                          "<key>CFBundleDisplayName</key>\n\t<string>%s</string>" % display_name)
    plist = plist.replace("<string>KeShare</string>", "<string>%s</string>" % display_name, 1)
    plist = plist.replace("<string>com.keshare.app</string>", "<string>%s</string>" % bundle_id)
    with open(plist_path, "w", encoding="utf-8") as fh:
        fh.write(plist)

    # 重新打包（保留 Payload 顶层结构）
    if os.path.exists(out_ipa):
        os.remove(out_ipa)
    with zipfile.ZipFile(out_ipa, "w", zipfile.ZIP_DEFLATED) as zf:
        for base, _dirs, files in os.walk(work):
            for name in files:
                full = os.path.join(base, name)
                rel = os.path.relpath(full, work)
                zf.write(full, rel)
    shutil.rmtree(work, ignore_errors=True)
    return out_ipa


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ipa", default=os.path.join(ROOT, "version", "KeShare-0.0.1-beta1.ipa"))
    parser.add_argument("--out", default=os.path.join(ROOT, "version"))
    args = parser.parse_args()

    source = Image.open(SRC_ICON)
    stable_dir = os.path.join(ROOT, ".scratch", "icons-stable")
    lite_dir = os.path.join(ROOT, ".scratch", "icons-lite")
    export_icons(source, stable_dir)
    export_icons(make_lite(source), lite_dir)

    os.makedirs(args.out, exist_ok=True)
    stable_ipa = os.path.join(args.out, "KeShare-0.0.1-beta1-stable.ipa")
    lite_ipa = os.path.join(args.out, "KeShare-0.0.1-beta1-lite.ipa")
    build_ipa(args.ipa, stable_ipa, stable_dir, "KeShare", "com.keshare.app")
    build_ipa(args.ipa, lite_ipa, lite_dir, "KeShare Lite", "com.keshare.lite")
    print("稳定版:", stable_ipa, "%.1f KB" % (os.path.getsize(stable_ipa) / 1024))
    print("青春版:", lite_ipa, "%.1f KB" % (os.path.getsize(lite_ipa) / 1024))


if __name__ == "__main__":
    main()
