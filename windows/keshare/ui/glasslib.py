# -*- coding: utf-8 -*-
"""glasslib —— KeShare 自绘磨砂玻璃控件库（依赖 frosted 模块）

设计
----
tkinter 的原生控件不透明，做不出"处处透明"。所以本库的做法是：
**整个窗口就是一张由 frosted 合成的位图**，所有控件都画在这张图上，
交互靠 canvas 的鼠标事件 + 命中测试完成。没有 ttk 控件参与渲染。

分层
----
    GlassApp       根窗口：场景、帧循环、事件分发、DPI
    GlassSurface   单帧画布：PIL 图像 + 文字 + 圆角 + 玻璃面板
    GlassWidget    控件基类：rect / draw / hit / 事件
    ├ GlassPanel   玻璃容器（可嵌套子控件）
    ├ GlassLabel   文字
    ├ GlassButton  胶囊按钮
    ├ GlassList    自绘列表（滚动、选中、hover、行高亮）
    └ GlassSidebar 侧边栏（列表 + 选中指示条）

用法
----
    app = GlassApp("KeShare", 1180, 760)
    app.add(GlassSidebar(app, (0, 0, 232, 760), items=[...]))
    app.add(GlassPanel(app, (260, 90, 880, 260), radius=24))
    app.run()

坐标系：控件 rect 用窗口逻辑像素 (x, y, w, h)。
"""
from __future__ import annotations

import math
import os
import time

import tkinter as tk
from PIL import Image, ImageDraw, ImageFont, ImageTk

from . import haze

# ---------------------------------------------------------------- 主题
_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\segoeui.ttf",
]


def _load_font(size, bold=False):
    for path in (_FONT_CANDIDATES[1], _FONT_CANDIDATES[0]) if bold else _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


class GlassTheme(object):
    """颜色与度量。三端共用同一组数值。"""

    bg = (11, 15, 26)
    bg2 = (14, 20, 34)
    panel = (21, 27, 43)
    stroke = (42, 53, 80)
    fg = (242, 245, 250)
    muted = (138, 148, 172)
    accent = (92, 184, 255)
    accent2 = (160, 107, 255)
    ok = (74, 222, 128)
    warn = (251, 191, 36)
    danger = (248, 113, 113)
    err = (248, 113, 113)

    tint = (18, 24, 38)          # 磨砂玻璃底色
    radius_panel = 26
    radius_pill = 40
    pad = 16

    @classmethod
    def palette(cls):
        return {
            "bg": "#%02x%02x%02x" % cls.bg,
            "bg2": "#%02x%02x%02x" % cls.bg2,
            "panel": "#%02x%02x%02x" % cls.panel,
            "stroke": "#%02x%02x%02x" % cls.stroke,
            "fg": "#%02x%02x%02x" % cls.fg,
            "muted": "#%02x%02x%02x" % cls.muted,
            "accent": "#%02x%02x%02x" % cls.accent,
        }


# ---------------------------------------------------------------- 画布
class GlassSurface(object):
    """一帧的绘制上下文：包装 PIL 图像与绘制原语。"""

    def __init__(self, image, scale=1.0):
        self.image = image
        self._backdrop_image = None
        self.draw = ImageDraw.Draw(image, "RGBA")
        self.scale = scale
        self._fonts = {}

    def font(self, size, bold=False):
        key = (int(size * self.scale), bold)
        if key not in self._fonts:
            self._fonts[key] = _load_font(key[0], bold)
        return self._fonts[key]

    def text(self, xy, content, size=13, color=None, bold=False, anchor="la"):
        self.draw.text(xy, content, font=self.font(size, bold),
                       fill=color or GlassTheme.fg, anchor=anchor)

    def text_width(self, content, size=13, bold=False):
        return self.draw.textlength(content, font=self.font(size, bold))

    def rounded(self, rect, radius, fill=None, outline=None, width=1):
        x, y, w, h = rect
        self.draw.rounded_rectangle([x, y, x + w - 1, y + h - 1], radius=radius,
                                    fill=fill, outline=outline, width=width)

    def glass(self, rect, radius=None, kind="frosted", strength=0.0, blur=12.0,
              material="acrylic", opacity=None, alpha=0.62):
        """画一块磨砂玻璃（不做折射）。

        rect    : (x, y, w, h)
        blur    : 背景模糊半径
        alpha   : 底色权重（0 全透，1 实心）
        opacity : 兼容旧参数，传入时覆盖 alpha
        """
        radius = GlassTheme.radius_panel if radius is None else radius
        if opacity is not None:
            alpha = max(0.05, min(0.98, 1.0 - opacity * 0.5))
        layer = haze.haze_layer(self._backdrop_image, rect, radius,
                                      tint=GlassTheme.tint, alpha=alpha, blur=blur)
        self.image.alpha_composite(layer, (int(rect[0]), int(rect[1])))
    def attach_scene(self, backdrop_image):

        self._backdrop_image = backdrop_image

_GLYPH_PAINTERS = {}


def register_glyph(name):
    def deco(fn):
        _GLYPH_PAINTERS[name] = fn
        return fn
    return deco


def draw_glyph(surface, name, cx, cy, size=9, color=None):
    """自绘几何图标——不依赖字体的符号字形（避免缺字变方块）。"""
    color = color or GlassTheme.muted
    draw = surface.draw
    s = size
    if name == "device":
        draw.ellipse([cx - s, cy - s, cx + s, cy + s], outline=color, width=2)
        draw.ellipse([cx - s * 0.35, cy - s * 0.35, cx + s * 0.35, cy + s * 0.35], fill=color)
    elif name == "files":
        draw.rounded_rectangle([cx - s, cy - s * 0.8, cx + s, cy + s * 0.8], radius=2,
                               outline=color, width=2)
        draw.line([cx - s, cy - s * 0.3, cx + s, cy - s * 0.3], fill=color, width=2)
    elif name == "transfer":
        draw.line([cx, cy - s, cx, cy + s], fill=color, width=2)
        draw.polygon([(cx - 4, cy - s + 2), (cx + 4, cy - s + 2), (cx, cy - s - 3)], fill=color)
        draw.polygon([(cx - 4, cy + s - 2), (cx + 4, cy + s - 2), (cx, cy + s + 3)], fill=color)
    elif name == "settings":
        draw.ellipse([cx - s * 0.55, cy - s * 0.55, cx + s * 0.55, cy + s * 0.55],
                     outline=color, width=2)
        for k in range(8):
            ang = k * math.pi / 4
            x1 = cx + math.cos(ang) * s * 0.62
            y1 = cy + math.sin(ang) * s * 0.62
            x2 = cx + math.cos(ang) * s
            y2 = cy + math.sin(ang) * s
            draw.line([x1, y1, x2, y2], fill=color, width=2)
    elif name == "about":
        draw.ellipse([cx - s, cy - s, cx + s, cy + s], outline=color, width=2)
        draw.line([cx, cy - 2, cx, cy + s * 0.55], fill=color, width=2)
        draw.ellipse([cx - 1.5, cy - s * 0.6, cx + 1.5, cy - s * 0.6 + 3], fill=color)
    else:
        draw.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=color)

def human_size(num):
    """人类可读的字节数。"""
    try:
        num = float(num or 0)
    except (TypeError, ValueError):
        return "-"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0 or unit == "TB":
            return ("%.0f %s" % (num, unit)) if unit == "B" else ("%.1f %s" % (num, unit))
        num /= 1024.0
    return "%.1f TB" % num


# ---------------------------------------------------------------- 动画
def approach(current, target, dt, speed=14.0):
    """指数平滑：每帧向目标逼近，用于 hover / 选中 / 指示条等过渡。

    比动画队列简单得多：不需要维护状态机，帧率变化也不会跳变。
    """
    if dt <= 0:
        return current
    return current + (target - current) * min(1.0, dt * speed)


def ease_out_cubic(t):
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


# ---------------------------------------------------------------- 控件
class GlassWidget(object):
    def __init__(self, app, rect, radius=GlassTheme.radius_panel):
        self.app = app
        rect = tuple(rect)
        if len(rect) == 2:
            rect = (rect[0], rect[1], 0, 0)
        self.x, self.y, self.w, self.h = rect[:4]
        self.radius = radius
        self.children = []
        self.visible = True
        self.enabled = True
        self._hover = False
        self._pressed = False
        self.cursor = None

    # -- 布局 --
    def add(self, widget):
        widget.app = self.app
        self.children.append(widget)
        return widget

    @property
    def rect(self):
        return (self.x, self.y, self.w, self.h)

    def contains(self, px, py):
        return (self.x <= px < self.x + self.w) and (self.y <= py < self.y + self.h)

    # -- 绘制 --
    def draw(self, surface):
        if not self.visible:
            return
        self.draw_self(surface)
        for child in self.children:
            child.draw(surface)

    def draw_self(self, surface):
        pass

    # -- 事件 --
    def on_motion(self, px, py):
        hover = self.contains(px, py)
        if hover != self._hover:
            self._hover = hover
            self.app.invalidate()
        for child in reversed(self.children):
            child.on_motion(px, py)

    def on_press(self, px, py):
        if not self.contains(px, py):
            return False
        for child in reversed(self.children):
            if child.on_press(px, py):
                return True
        self._pressed = True
        self.app.invalidate()
        return True

    def on_release(self, px, py):
        fired = False
        for child in reversed(self.children):
            if child.on_release(px, py):
                fired = True
        was = self._pressed
        self._pressed = False
        if was and self.contains(px, py):
            self.on_click()
            fired = True
        self.app.invalidate()
        return fired

    def on_click(self):
        pass

    def on_wheel(self, px, py, delta):
        for child in reversed(self.children):
            if child.on_wheel(px, py, delta):
                return True
        return False

    def destroy(self):
        """释放附属资源（默认没有）。"""
        return None

    def on_context(self, px, py):
        """右键；返回 True 表示已处理。"""
        for child in reversed(self.children):
            if child.on_context(px, py):
                return True
        return False

    def step_anims(self, dt):
        """推进自身动画；返回 True 表示仍在变化（需要继续重绘）。"""
        moving = False
        for child in self.children:
            if child.step_anims(dt):
                moving = True
        return moving


class GlassPanel(GlassWidget):
    """玻璃容器：先合成玻璃，再画子控件。"""

    def __init__(self, app, rect, radius=GlassTheme.radius_panel, kind="liquid",
                 strength=10.0, blur=7.0, material="acrylic", padding=None,
                 solid_alpha=130):
        super().__init__(app, rect, radius)
        self.kind = kind
        self.strength = strength
        self.blur = blur
        self.material = material
        self.solid_alpha = solid_alpha
        self.padding = GlassTheme.pad if padding is None else padding

    def draw_self(self, surface):
        surface.glass((self.x, self.y, self.w, self.h), self.radius, self.kind,
                      self.strength, self.blur, self.material)
        # 内容底板：按 HIG，玻璃是给浮层用的，正文不该直接坐在玻璃上；
        # 压一层高不透明度底色，保证文字对比度。
        if self.solid_alpha > 0:
            surface.rounded(self.rect, self.radius, fill=(9, 13, 22, self.solid_alpha))
        surface.rounded(self.rect, self.radius, fill=None,
                        outline=(255, 255, 255, 30), width=1)

class GlassLabel(GlassWidget):
    def __init__(self, app, rect, text="", size=13, color=None, bold=False,
                 align="left"):
        super().__init__(app, rect, 0)
        self.text = text
        self.size = size
        self.color = color or GlassTheme.fg
        self.bold = bold
        self.align = align

    def draw_self(self, surface):
        if not self.text:
            return
        if self.align == "center":
            surface.text((self.x + self.w / 2, self.y), self.text, self.size,
                         self.color, self.bold, anchor="ma")
        elif self.align == "right":
            surface.text((self.x + self.w, self.y), self.text, self.size,
                         self.color, self.bold, anchor="ra")
        else:
            surface.text((self.x, self.y), self.text, self.size, self.color, self.bold)


class GlassButton(GlassWidget):
    """胶囊按钮：hover / press 有光影变化。"""

    def __init__(self, app, rect, text="", command=None, kind="pill",
                 accent=False, size=13):
        super().__init__(app, rect, min(rect[3] // 2, GlassTheme.radius_pill))
        self.text = text
        self.command = command
        self.kind = kind
        self.accent = accent
        self.size = size
        self._hover_t = 0.0
        self._press_t = 0.0

    def step_anims(self, dt):
        moving = super().step_anims(dt)
        nh = approach(self._hover_t, 1.0 if self._hover else 0.0, dt, 18.0)
        np_ = approach(self._press_t, 1.0 if self._pressed else 0.0, dt, 26.0)
        if abs(nh - self._hover_t) > 0.002 or abs(np_ - self._press_t) > 0.002:
            moving = True
        self._hover_t, self._press_t = nh, np_
        return moving

    def draw_self(self, surface):
        if self.accent:
            base = GlassTheme.accent
            if self._hover:
                base = tuple(min(255, int(c * 1.15)) for c in base)
            if self._pressed:
                base = tuple(int(c * 0.88) for c in base)
            surface.rounded(self.rect, self.radius, fill=base + (235,))
            color = (12, 18, 30)
        else:
            # 实心按钮：玻璃做按钮底在深色背景上会发白，导致白底白字
            base = (int(26 + 10 * self._hover_t), int(34 + 12 * self._hover_t), int(50 + 16 * self._hover_t))
            base = (int(26 + 10 * self._hover_t), int(34 + 12 * self._hover_t), int(50 + 16 * self._hover_t))
            surface.rounded(self.rect, self.radius, fill=base + (238,),
                            outline=(255, 255, 255, 62), width=1)
            color = GlassTheme.fg
        tw = surface.text_width(self.text, self.size, True)
        surface.text((self.x + self.w / 2 - tw / 2, self.y + self.h / 2 - self.size * 0.72),
                     self.text, self.size, color, True)

    def on_click(self):
        if self.enabled and self.command:
            self.command()


class GlassList(GlassWidget):
    """自绘列表：行高固定、支持滚动、hover、选中、行内副标题与右侧文字。"""

    def __init__(self, app, rect, rows=None, row_height=58, on_select=None,
                 empty_text="暂无内容", empty_hint="", on_context=None):
        super().__init__(app, rect, 0)
        self.rows = rows or []
        self.row_height = row_height
        self.on_select = on_select
        self.on_context_cb = on_context
        self.empty_text = empty_text
        self.empty_hint = empty_hint
        self.selected = -1
        self.hover_row = -1
        self.scroll = 0.0
        self.glyph_size = 18

    def set_rows(self, rows):
        self.rows = rows or []
        self.selected = min(self.selected, len(self.rows) - 1)
        self.scroll = 0.0
        self.app.invalidate()

    def _visible_rows(self):
        if not self.rows:
            return 0
        return max(0, int(self.h // self.row_height))

    def _row_rect(self, index):
        top = self.y + index * self.row_height - self.scroll
        return (self.x, top, self.w, self.row_height)

    def draw_self(self, surface):
        if not self.rows:
            cy = self.y + self.h / 2 - 26
            surface.text((self.x + self.w / 2, cy), self.empty_text, 15,
                         GlassTheme.muted, True, anchor="ma")
            if self.empty_hint:
                surface.text((self.x + self.w / 2, cy + 26), self.empty_hint, 12,
                             (110, 120, 145), False, anchor="ma")
            return

        total = len(self.rows)
        first = max(0, int(self.scroll // self.row_height))
        last = min(total, first + self._visible_rows() + 2)
        for i in range(first, last):
            row = self.rows[i]
            rx, ry, rw, rh = self._row_rect(i)
            if ry + rh < self.y or ry > self.y + self.h:
                continue
            if i == self.selected:
                surface.rounded((rx + 6, ry + 4, rw - 12, rh - 8), 18,
                                fill=GlassTheme.accent + (46,),
                                outline=GlassTheme.accent + (90,), width=1)
            elif i == self.hover_row:
                surface.rounded((rx + 6, ry + 4, rw - 12, rh - 8), 18,
                                fill=(255, 255, 255, 16))

            glyph = row.get("glyph", "")
            title = row.get("title", "")
            subtitle = row.get("subtitle", "")
            right = row.get("right", "")
            color = row.get("color", GlassTheme.fg)

            tx = rx + 20
            if glyph:
                draw_glyph(surface, glyph, tx + 8, ry + rh / 2, 9, color)
                tx += 30
            # 标题按可用宽度截断（右侧留给状态文字与边距）
            avail = max(60, rw - (tx - rx) - (150 if right else 40))
            title = self._fit(surface, title, 13, avail, True)
            if subtitle:
                surface.text((tx, ry + 12), title, 13, color, True)
                surface.text((tx, ry + 32), subtitle, 11, GlassTheme.muted)
            else:
                surface.text((tx, ry + rh / 2 - 10), title, 13, color, True)
            if right:
                surface.text((rx + rw - 20, ry + rh / 2 - 9), right, 12,
                             GlassTheme.muted, anchor="ra")

            ratio = row.get("progress")
            if ratio is not None:
                self._draw_row_progress(surface, (rx, ry, rw, rh), ratio)

    # -- 事件 --
    @staticmethod
    def _fit(surface, text, size, max_width, bold=False):
        """按像素宽度截断文本，超出加省略号。"""
        text = str(text or "")
        try:
            if surface.text_width(text, size, bold) <= max_width:
                return text
        except Exception:
            return text
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            try:
                w = surface.text_width(text[:mid] + "…", size, bold)
            except Exception:
                return text[:mid]
            if w <= max_width:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo] + ("…" if lo < len(text) else "")

    def _draw_row_progress(self, surface, rect, ratio):
        """行内进度条（传输任务用）。"""
        rx, ry, rw, rh = rect
        bar_h = 3
        by = ry + rh - bar_h - 6
        surface.rounded((rx + 18, by, rw - 36, bar_h), bar_h / 2,
                        fill=(255, 255, 255, 22))
        w = max(2, (rw - 36) * max(0.0, min(1.0, ratio)))
        surface.rounded((rx + 18, by, w, bar_h), bar_h / 2,
                        fill=GlassTheme.accent + (220,))

    def _index_at(self, px, py):
        if not self.contains(px, py) or not self.rows:
            return -1
        rel = py - self.y + self.scroll
        idx = int(rel // self.row_height)
        return idx if 0 <= idx < len(self.rows) else -1

    def on_motion(self, px, py):
        idx = self._index_at(px, py)
        if idx != self.hover_row:
            self.hover_row = idx
            self.app.invalidate()
        super().on_motion(px, py)

    def on_press(self, px, py):
        idx = self._index_at(px, py)
        if idx >= 0:
            self.selected = idx
            self.app.invalidate()
            if self.on_select:
                self.on_select(idx, self.rows[idx])
            return True
        return super().on_press(px, py)

    def on_context(self, px, py):
        if not self.contains(px, py):
            return False
        idx = self._index_at(px, py)
        if idx < 0 or idx >= len(self.rows):
            return False
        self.selected = idx
        self.app.invalidate()
        if self.on_context_cb:
            self.on_context_cb(idx, self.rows[idx], px, py)
        return True

    def on_wheel(self, px, py, delta):
        if not self.contains(px, py):
            return False
        max_scroll = max(0.0, len(self.rows) * self.row_height - self.h)
        self.scroll = max(0.0, min(max_scroll, self.scroll - delta * 48))
        self.app.invalidate()
        return True




class GlassInput(GlassWidget):
    """输入框：玻璃底 + 原生 Entry 覆盖（自绘光标成本高，直接借用 Entry）。

    Entry 是独立 widget，用 place 浮在 canvas 上，销毁时要一起清掉。
    """

    def __init__(self, app, rect, value="", placeholder="", on_change=None):
        super().__init__(app, rect, 10)
        import tkinter as tk
        self.value = str(value or "")
        self.placeholder = placeholder
        self.on_change = on_change
        self.entry = tk.Entry(
            app,
            bg="#111827", fg="#F2F5FA", insertbackground="#5CB8FF",
            relief="flat", bd=0, highlightthickness=1,
            highlightbackground="#2A3550", highlightcolor="#5CB8FF",
            font=("Microsoft YaHei UI", 10),
        )
        if self.value:
            self.entry.insert(0, self.value)
        self.entry.place(x=int(app.winfo_rootx() * 0 + rect[0]),
                         y=int(rect[1]), width=int(rect[2]), height=int(rect[3]))
        if on_change:
            self.entry.bind("<KeyRelease>", lambda e: on_change(self.entry.get()))

    def text(self):
        try:
            return self.entry.get()
        except Exception:
            return self.value

    def destroy(self):
        try:
            self.entry.destroy()
        except Exception:
            pass


class GlassSidebar(GlassWidget):
    """侧边栏：玻璃底板 + 条目列表 + 选中指示条。"""

    def __init__(self, app, rect, items=None, on_select=None, width=232):
        super().__init__(app, rect, 0)
        self.items = items or []
        self.on_select = on_select
        self.selected = 0
        self.hover = -1
        self.item_top = 96
        self.item_height = 48
        self.width = width

    def _item_rect(self, index):
        return (self.x + 12, self.y + self.item_top + index * self.item_height,
                self.width - 24, self.item_height - 6)

    def draw_self(self, surface):
        # 侧栏底板：磨砂玻璃，压暗以区别于内容区
        surface.glass((self.x, self.y, self.width, self.h), 0, "liquid", strength=10, blur=10.0)
        surface.rounded((self.x, self.y, self.width, self.h), 0, fill=(8, 11, 20, 120))

        # 品牌区
        surface.text((self.x + 24, self.y + 26), "KeShare", 22, GlassTheme.fg, True)
        surface.text((self.x + 24, self.y + 58), getattr(GlassTheme, "version_text", ""), 12,
                     GlassTheme.muted)

        for i, item in enumerate(self.items):
            rx, ry, rw, rh = self._item_rect(i)
            active = (i == self.selected)
            if active:
                surface.rounded((rx, ry, rw, rh), 18,
                                fill=GlassTheme.accent + (48,),
                                outline=GlassTheme.accent + (110,), width=1)
                surface.rounded((rx - 8, ry + 12, 3, rh - 24), 2,
                                fill=GlassTheme.accent + (255,))
            elif i == self.hover:
                surface.rounded((rx, ry, rw, rh), 18, fill=(255, 255, 255, 14))

            color = GlassTheme.fg if active else GlassTheme.muted
            draw_glyph(surface, item.get("glyph", ""), rx + 26, ry + rh / 2, 8, color)
            surface.text((rx + 44, ry + rh / 2 - 10), item.get("title", ""), 14, color, active)

    def set_selected(self, index, notify=True):
        """选中某项。立即对齐指示条与渐变，避免切页后侧栏停留。"""
        if not self.items:
            return
        index = max(0, min(int(index), len(self.items) - 1))
        self.selected = index
        self.hover = -1
        self._indicator_y = float(self.y + self.item_top + index * self.item_height)
        if len(self._t) < len(self.items):
            self._t.extend([0.0] * (len(self.items) - len(self._t)))
        for i in range(len(self._t)):
            self._t[i] = 1.0 if i == index else 0.0
        if notify and self.on_select:
            self.on_select(index, self.items[index])
        self.app.invalidate()

    def _index_at(self, px, py):
        if not (self.x <= px < self.x + self.width):
            return -1
        rel = py - self.y - self.item_top
        if rel < 0:
            return -1
        idx = int(rel // self.item_height)
        return idx if 0 <= idx < len(self.items) else -1

    def on_motion(self, px, py):
        idx = self._index_at(px, py)
        if idx != self.hover:
            self.hover = idx
            self.app.invalidate()
        super().on_motion(px, py)

    def on_press(self, px, py):
        idx = self._index_at(px, py)
        if idx >= 0:
            self.selected = idx
            self.app.invalidate()
            if self.on_select:
                self.on_select(idx, self.items[idx])
            return True
        return False


# ---------------------------------------------------------------- 应用
class GlassApp(tk.Tk):
    """全自绘根窗口：磨砂合成 + 事件分发 + 脏标记重绘。"""

    def __init__(self, title="KeShare", width=1180, height=760, fps=30,
                 drift=False, quality="full"):
        super().__init__()
        try:
            from .glass import enable_dpi_awareness, apply_tk_scaling
            enable_dpi_awareness()
        except Exception:
            pass

        self.title(title)
        self.geometry("%dx%d" % (width, height))
        self.minsize(880, 560)
        self.configure(bg="#0B0F1A")

        self._win_w, self._win_h = width, height
        self._backdrop_image = haze.make_backdrop(width, height, GlassTheme.palette())
        self.roots = []
        self._dirty = True
        self._last_draw = 0.0
        self._fps = max(10, min(60, fps))
        self._photo = None
        self._quality = quality
        self._last_motion = (0, 0)

        self.canvas = tk.Canvas(self, bg="#0B0F1A", highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self._image_item = self.canvas.create_image(0, 0, anchor="nw")

        for seq, handler in (
            ("<Configure>", self._on_configure),
            ("<Motion>", lambda e: self._on_motion(e.x, e.y)),
            ("<Button-1>", lambda e: self._on_press(e.x, e.y)),
            ("<ButtonRelease-1>", lambda e: self._on_release(e.x, e.y)),
            ("<Button-3>", lambda e: self._on_context(e.x, e.y)),
            ("<MouseWheel>", lambda e: self._on_wheel(e.x, e.y, e.delta / 120.0)),
            ("<Leave>", lambda e: self._on_motion(-10, -10)),
        ):
            self.bind(seq, handler)

        try:
            from .glass import apply_tk_scaling
            apply_tk_scaling(self)
        except Exception:
            pass

        self.after(16, self._tick)

    # -- 结构 --
    def add(self, widget):
        widget.app = self
        self.roots.append(widget)
        self.invalidate()
        return widget

    def invalidate(self):
        self._dirty = True

    # -- 事件 --
    def _on_configure(self, event):
        if event.widget is not self:
            return
        if (event.width, event.height) == (self._win_w, self._win_h):
            return
        self._win_w, self._win_h = event.width, event.height
        self._backdrop_image = haze.make_backdrop(self._win_w, self._win_h, GlassTheme.palette())
        # Haze 页面按窗口尺寸绝对定位，尺寸变了必须重排
        if hasattr(self, "_relayout_haze"):
            try:
                self._relayout_haze()
            except Exception:
                pass
        self.invalidate()

    def _on_motion(self, px, py):
        self._last_motion = (px, py)
        for widget in reversed(self.roots):
            widget.on_motion(px, py)

    def _on_press(self, px, py):
        for widget in reversed(self.roots):
            if widget.on_press(px, py):
                break

    def _on_release(self, px, py):
        for widget in reversed(self.roots):
            if widget.on_release(px, py):
                break

    def _on_context(self, px, py):
        for widget in reversed(self.roots):
            if widget.on_context(px, py):
                self.invalidate()
                return

    def _on_wheel(self, px, py, delta):
        for widget in reversed(self.roots):
            if widget.on_wheel(px, py, delta):
                break

    # -- 渲染 --
    def render_frame(self):
        backdrop = self._backdrop_image
        frame = backdrop.convert("RGBA")
        surface = GlassSurface(frame)
        surface.attach_scene(self._backdrop_image)
        for widget in self.roots:
            widget.draw(surface)
        return frame.convert("RGB")

    def _tick(self):
        # 先注册下一帧：本帧即使抛异常，渲染时钟也不会停
        self.after(16, self._tick)
        now = time.time()
        dt = max(0.0, min(0.1, now - getattr(self, "_last_tick", now)))
        self._last_tick = now
        try:
            for widget in self.roots:
                if widget.step_anims(dt):
                    self.invalidate()
        except Exception as exc:
            self._anim_error = repr(exc)

        if self._dirty and (now - self._last_draw) >= 1.0 / self._fps:
            self._last_draw = now
            self._dirty = False
            try:
                image = self.render_frame()
                self._photo = ImageTk.PhotoImage(image)
                self.canvas.itemconfigure(self._image_item, image=self._photo)
                self._last_size = image.size
            except Exception as exc:
                import traceback
                self._render_error = repr(exc)
                traceback.print_exc()

    def run(self):
        self.mainloop()


# ---------------------------------------------------------------- 演示
def demo():
    app = GlassApp("KeShare · glasslib demo", 1180, 760)

    sidebar = GlassSidebar(app, (0, 0, 232, 760), items=[
        {"glyph": "◉", "title": "设备"},
        {"glyph": "▤", "title": "文件"},
        {"glyph": "⇅", "title": "传输"},
        {"glyph": "⚙", "title": "设置"},
        {"glyph": "ⓘ", "title": "关于"},
    ])
    app.add(sidebar)

    header = GlassPanel(app, (264, 40, 872, 116), radius=26)
    app.add(header)
    app.add(GlassLabel(app, (288, 62), "附近设备", 24, bold=True))
    app.add(GlassLabel(app, (288, 100), "001 正在搜索附近设备…", 12, GlassTheme.muted))

    search_btn = GlassButton(app, (900, 66, 108, 42), "刷新", lambda: None)
    app.add(search_btn)
    usb_btn = GlassButton(app, (1018, 66, 108, 42), "USB 连接", lambda: None, accent=True)
    app.add(usb_btn)

    panel = GlassPanel(app, (264, 176, 872, 420), radius=26)
    app.add(panel)
    rows = [
        {"glyph": "◉", "title": "iPhone 12 Pro", "subtitle": "iOS 17 · 192.168.3.14", "right": "已连接"},
        {"glyph": "◉", "title": "Windows-PC", "subtitle": "Win10 · 192.168.3.23", "right": "未连接"},
        {"glyph": "◉", "title": "192.168.3.40", "subtitle": "手动添加", "right": "未连接"},
    ]
    app.add(GlassList(app, (280, 200, 840, 372), rows=rows, row_height=64,
                      empty_text="附近没有设备",
                      empty_hint="让电脑开热点 KeShare-XXXX，或手动添加 IP"))

    pill = GlassPanel(app, (264, 620, 872, 76), radius=38, kind="liquid", strength=30)
    app.add(pill)
    app.add(GlassLabel(app, (292, 646), "传输任务  0 个进行中", 13, GlassTheme.muted))
    app.add(GlassButton(app, (1000, 638, 120, 40), "查看", lambda: None))

    app.run()


if __name__ == "__main__":
    demo()
