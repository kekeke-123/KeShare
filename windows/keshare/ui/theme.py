# -*- coding: utf-8 -*-
"""KeShare Windows 视觉层：暗色玻璃卡片 + 圆角 + 强调色。

只用 tkinter（无第三方依赖），所有控件都画在 Canvas 上，
以便得到和 iOS 端一致的观感：深色背景、半透明卡片、1px 描边、圆角。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont

# ---------------------------------------------------------------- 调色板
DARK = {
    "bg":        "#0B0F1A",   # 窗口底色
    "bg2":       "#111827",   # 次级背景
    "panel":     "#151B2B",   # 卡片
    "field":     "#1E2637",   # 输入框
    "stroke":    "#2A3550",   # 卡片描边
    "fg":        "#F2F5FA",
    "muted":     "#8A94AC",
    "accent":    "#5CB8FF",
    "accent2":   "#A06BFF",
    "ok":        "#4ADE80",
    "warn":      "#FBBF24",
    "err":       "#F87171",
}

LIGHT = {
    "bg":        "#F3F5FA",
    "bg2":       "#E9EDF6",
    "panel":     "#FFFFFF",
    "field":     "#EDF1F8",
    "stroke":    "#D5DCEC",
    "fg":        "#151A26",
    "muted":     "#5C667E",
    "accent":    "#0A84FF",
    "accent2":   "#7A4DFF",
    "ok":        "#16A34A",
    "warn":      "#D97706",
    "err":       "#DC2626",
}

FONT_FAMILY = "Microsoft YaHei UI"


def font(size=9, weight="normal"):
    return (FONT_FAMILY, size, weight)


def mix(c1, c2, t):
    """在两个 #RRGGBB 之间插值。"""
    a = tuple(int(c1[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(c2[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def rounded(canvas, x1, y1, x2, y2, r, **kw):
    """在 canvas 上画圆角矩形（用平滑多边形近似，tkinter 没有原生圆角）。"""
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2,
        x1 + r, y2, x1, y2, x1, y2 - r,
        x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(pts, smooth=True, splinesteps=24, **kw)


class Card(tk.Canvas):
    """圆角卡片容器：内部放一个 Frame，背景与卡片同色。"""

    def __init__(self, master, palette, radius=16, padding=14,
                 fill=None, stroke=None, height=None, **kw):
        p = palette
        bg = p["bg"]
        super().__init__(master, bg=bg, highlightthickness=0, bd=0,
                         height=height or 1, **kw)
        self.p = p
        self.radius = radius
        self._fill = fill or p["panel"]
        self._stroke = stroke or p["stroke"]
        self.body = tk.Frame(self, bg=self._fill)
        self._pad = padding
        self._win = self.create_window(padding, padding, anchor="nw", window=self.body)
        self.bind("<Configure>", self._redraw)

    def _redraw(self, event=None):
        w = self.winfo_width()
        h = max(self.winfo_height(), self.body.winfo_reqheight() + self._pad * 2)
        self.delete("chrome")
        rounded(self, 1, 1, w - 1, h - 1, self.radius,
                fill=self._fill, outline=self._stroke, width=1, tags="chrome")
        self.tag_lower("chrome")
        self.itemconfigure(self._win, width=max(1, w - self._pad * 2))
        if h > self.winfo_height():
            self.configure(height=h)

    def set_fill(self, color):
        self._fill = color
        self.body.configure(bg=color)
        self._redraw()


class PillButton(tk.Canvas):
    """胶囊按钮，带 hover / 按下 / 禁用状态。"""

    def __init__(self, master, palette, text, command=None,
                 kind="primary", width=None, height=30, **kw):
        self.p = palette
        self.text = text
        self.command = command
        self.kind = kind
        self._h = height
        self._enabled = True
        f = font(9, "bold")
        tmp = tkfont.Font(family=FONT_FAMILY, size=9, weight="bold")
        w = width or (tmp.measure(text) + 34)
        super().__init__(master, width=w, height=height, bg=palette["bg"],
                         highlightthickness=0, bd=0, **kw)
        self._w = w
        self._draw(False, False)
        self.bind("<Enter>", lambda e: self._draw(True, False))
        self.bind("<Leave>", lambda e: self._draw(False, False))
        self.bind("<ButtonPress-1>", lambda e: self._draw(True, True))
        self.bind("<ButtonRelease-1>", self._release)

    def _colors(self):
        p = self.p
        if self.kind == "primary":
            return p["accent"], "#ffffff", p["accent"]
        if self.kind == "ghost":
            return p["field"], p["fg"], p["stroke"]
        if self.kind == "danger":
            return mix(p["panel"], p["err"], 0.22), p["err"], mix(p["panel"], p["err"], 0.5)
        return p["field"], p["fg"], p["stroke"]

    def _draw(self, hover, press):
        fill, fg, stroke = self._colors()
        if not self._enabled:
            fill, fg = mix(self.p["bg"], self.p["panel"], 0.5), self.p["muted"]
        elif self.kind == "primary" and hover:
            fill = mix(self.p["accent"], "#ffffff", 0.15)
        elif self.kind != "primary" and hover:
            fill = mix(fill, self.p["accent"], 0.18)
        if press:
            fill = mix(fill, "#000000", 0.12)
        self.delete("all")
        r = self._h / 2
        rounded(self, 1, 1, self._w - 1, self._h - 1, r, fill=fill, outline=stroke, width=1)
        self.create_text(self._w / 2, self._h / 2 + 1, text=self.text,
                         fill=fg, font=font(9, "bold"))

    def _release(self, event):
        inside = 0 <= event.x <= self._w and 0 <= event.y <= self._h
        self._draw(inside, False)
        if inside and self._enabled and self.command:
            self.command()

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        self._draw(False, False)

    def set_text(self, text):
        self.text = text
        self._draw(False, False)


class Toggle(tk.Canvas):
    """iOS 风格开关。"""

    def __init__(self, master, palette, value=False, command=None, width=42, height=24, **kw):
        self.p = palette
        self.value = bool(value)
        self.command = command
        self._w, self._h = width, height
        super().__init__(master, width=width, height=height, bg=palette["panel"],
                         highlightthickness=0, bd=0, **kw)
        self.bind("<Button-1>", self._click)
        self._draw()

    def _draw(self):
        p = self.p
        self.delete("all")
        r = self._h / 2
        track = p["accent"] if self.value else p["field"]
        rounded(self, 1, 1, self._w - 1, self._h - 1, r, fill=track,
                outline=p["stroke"] if not self.value else track)
        cx = self._w - r if self.value else r
        self.create_oval(cx - r + 3, 3, cx + r - 3, self._h - 3,
                         fill="#ffffff", outline="")

    def _click(self, event=None):
        self.value = not self.value
        self._draw()
        if self.command:
            self.command(self.value)

    def set(self, value, notify=False):
        self.value = bool(value)
        self._draw()
        if notify and self.command:
            self.command(self.value)


class StatusDot(tk.Canvas):
    def __init__(self, master, palette, color=None, size=8, **kw):
        self.p = palette
        super().__init__(master, width=size, height=size, bg=palette["panel"],
                         highlightthickness=0, bd=0, **kw)
        self.create_oval(0, 0, size, size, fill=color or palette["ok"], outline="")

    def set_color(self, color):
        self.delete("all")
        s = int(self.cget("width"))
        self.create_oval(0, 0, s, s, fill=color, outline="")


class Avatar(tk.Canvas):
    """圆形首字母头像，颜色由名字派生，保证同设备颜色稳定。"""

    def __init__(self, master, palette, name, size=38, **kw):
        self.p = palette
        super().__init__(master, width=size, height=size, bg=palette["panel"],
                         highlightthickness=0, bd=0, **kw)
        self.size = size
        self.draw(name)

    def draw(self, name):
        p = self.p
        self.delete("all")
        seed = sum(ord(c) for c in (name or "?"))
        palette_colors = [(p["accent"], p["accent2"]),
                          (p["accent2"], "#FF7AB6"),
                          (p["ok"], p["accent"]),
                          (p["warn"], p["accent2"])]
        c1, c2 = palette_colors[seed % len(palette_colors)]
        s = self.size
        # 用两段椭圆模拟渐变
        self.create_oval(0, 0, s, s, fill=mix(c1, c2, 0.3), outline="")
        self.create_oval(0, 0, s, s * 0.75, fill=mix(c1, c2, 0.6), outline="")
        letter = (name or "?").strip()[:1].upper()
        self.create_text(s / 2, s / 2, text=letter, fill="#ffffff",
                         font=font(int(s * 0.4), "bold"))
