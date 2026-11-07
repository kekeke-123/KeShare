# -*- coding: utf-8 -*-
"""KeShare 视觉库：液态玻璃 / 磨砂玻璃（纯 tkinter 自绘，无第三方依赖）。

设计目标
--------
* **液态玻璃（liquid）**：高光渐变 + 内描边 + 顶部镜面反射，通透感强
* **磨砂玻璃（frosted）**：低透明 + 高频噪点 + 更强模糊感
* **温控降质**：CPU/主板温度 > 60°C 降低透明度与噪点质量，> 80°C 自动切磨砂
* **用户可选**：默认液态 / 默认磨砂 / 强制液态；自动切换默认关闭

所有面板都绘制在 Canvas 上：圆角矩形用平滑多边形近似，渐变用逐行填充，
噪点用固定随机种子生成的抖点（保证同一面板重绘稳定，不闪烁）。
"""
from __future__ import annotations

import ctypes
import math
import random
from . import haze
import tkinter as tk
from tkinter import font as tkfont

# ---------------------------------------------------------------- 调色
DARK = {
    "bg": "#0B0F1A",
    "bg2": "#0E1422",
    "panel": "#151B2B",
    "field": "#1E2637",
    "stroke": "#2A3550",
    "fg": "#F2F5FA",
    "muted": "#8A94AC",
    "accent": "#5CB8FF",
    "accent2": "#A06BFF",
    "ok": "#4ADE80",
    "warn": "#FBBF24",
    "err": "#F87171",
}

LIGHT = {
    "bg": "#F3F5FA",
    "bg2": "#E9EDF6",
    "panel": "#FFFFFF",
    "field": "#EDF1F8",
    "stroke": "#D5DCEC",
    "fg": "#151A26",
    "muted": "#5C667E",
    "accent": "#0A84FF",
    "accent2": "#7A4DFF",
    "ok": "#16A34A",
    "warn": "#D97706",
    "err": "#DC2626",
}

FONT_FAMILY = "Microsoft YaHei UI"


def font(size=10, weight="normal"):
    return (FONT_FAMILY, size, weight)


def mix(c1, c2, t):
    a = tuple(int(c1[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(c2[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


# ---------------------------------------------------------------- 温控
def read_cpu_temperature():
    """读取温度（摄氏度）。读不到返回 None。

    优先 WMI 的 MSAcpi_ThermalZoneTemperature（部分主板不支持），
    失败再尝试 OpenHardwareMonitor 的 WMI 命名空间。
    """
    for script in (
        "(Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature "
        "-ErrorAction Stop | Select-Object -First 1).CurrentTemperature",
        "(Get-CimInstance -Namespace root/OpenHardwareMonitor -ClassName Sensor "
        "-ErrorAction Stop | Where-Object SensorType -eq 'Temperature' | "
        "Measure-Object Value -Average).Average",
    ):
        out = _run_powershell(script)
        if not out:
            continue
        try:
            value = float(out.strip().replace(",", "."))
        except ValueError:
            continue
        if value > 300:          # 开尔文 * 10
            value = value / 10.0 - 273.15
        if -20 < value < 150:
            return value
    return None


def _run_powershell(script, timeout=6):
    import subprocess
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            timeout=timeout, check=False,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        text = (result.stdout or b"").decode("mbcs", "replace").strip()
        return text
    except Exception:
        return ""


class ThermalGuard(object):
    """按温度决定玻璃画质。默认不开启自动切换。"""

    def __init__(self, enabled=False, hot_c=60.0, switch_c=80.0):
        self.enabled = bool(enabled)
        self.hot_c = hot_c
        self.switch_c = switch_c
        self.temperature = None
        self.level = "full"      # full | reduced | frosted
        self._listeners = []

    def on_change(self, callback):
        self._listeners.append(callback)

    def poll(self):
        if not self.enabled:
            self._set_level("full")
            return None
        temp = read_cpu_temperature()
        self.temperature = temp
        if temp is None:
            self._set_level("full")
            return None
        if temp >= self.switch_c:
            self._set_level("frosted")
        elif temp >= self.hot_c:
            self._set_level("reduced")
        else:
            self._set_level("full")
        return temp

    def _set_level(self, level):
        if level == self.level:
            return
        self.level = level
        for cb in list(self._listeners):
            try:
                cb(level)
            except Exception:
                pass


# ---------------------------------------------------------------- 绘制
def rounded_points(x1, y1, x2, y2, r):
    r = max(0, min(r, (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2,
        x1 + r, y2, x1, y2, x1, y2 - r,
        x1, y1 + r, x1, y1,
    ]


def draw_rounded(canvas, x1, y1, x2, y2, r, fill, outline="", width=1, tags=None):
    return canvas.create_polygon(rounded_points(x1, y1, x2, y2, r), smooth=True,
                                 splinesteps=24, fill=fill, outline=outline,
                                 width=width, tags=tags)


class GlassPanel(tk.Canvas):
    """玻璃面板：液态 / 磨砂，带圆角、渐变、顶部高光。"""

    def __init__(self, master, palette, style="liquid", quality="full",
                 radius=18, padding=14, height=None, backdrop=None, **kw):
        p = palette
        super().__init__(master, bg=p["bg"], highlightthickness=0, bd=0,
                         height=height or 120, **kw)
        self.p = p
        self.style = style
        self.quality = quality
        self.radius = radius
        self.pad = padding
        self.backdrop = backdrop          # 回调：返回 (PIL 背景图, 窗口左上角绝对坐标)
        self._photo = None
        self.body = tk.Frame(self, bg="#121826")
        self._win = self.create_window(padding, padding, anchor="nw", window=self.body)
        self._noise = []
        self._build_noise()
        self.bind("<Configure>", lambda e: self.redraw())

    def _build_noise(self):
        rng = random.Random(20261107)
        self._noise = [(rng.random(), rng.random()) for _ in range(600)]

    def set_style(self, style):
        self.style = style
        self.redraw()

    def set_quality(self, quality):
        self.quality = quality
        self.redraw()

    def redraw(self):
        w = max(2, self.winfo_width())
        h = max(2, self.winfo_height())
        p = self.p
        self.delete("chrome")

        # 磨砂玻璃：从窗口背景取样 + 模糊 + 半透明底色
        if self.backdrop is not None:
            try:
                from PIL import ImageTk
                image, origin = self.backdrop()
                if image is not None:
                    rx = self.winfo_rootx() - origin[0]
                    ry = self.winfo_rooty() - origin[1]
                    glass_img = haze.haze_layer(image, (rx, ry, w, h), self.radius,
                                                      tint=(18, 24, 38), alpha=0.62, blur=12.0)
                    self._photo = ImageTk.PhotoImage(glass_img)
                    self.create_image(0, 0, anchor="nw", image=self._photo, tags="chrome")
                    self.tag_lower("chrome")
            except Exception:
                pass
        base = mix(p["panel"], p["bg"], 0.25 if self.style == "liquid" else 0.55)
        if self._photo is not None:
            self.itemconfigure(self._win, width=max(1, w - self.pad * 2))
            need = self.body.winfo_reqheight() + self.pad * 2
            if need > h:
                self.configure(height=need)
            return
        if self.style == "liquid":
            draw_rounded(self, 1, 1, w - 1, h - 1, self.radius, base,
                         outline=mix(p["stroke"], p["accent"], 0.25), width=1, tags="chrome")
            # 顶部镜面高光
            steps = 14 if self.quality != "reduced" else 6
            for i in range(steps):
                t = i / float(steps)
                color = mix(mix(p["panel"], "#ffffff", 0.16), base, t)
                y0 = 1 + (h - 2) * t * 0.55
                self.create_line(2, y0, w - 2, y0, fill=color, tags="chrome")
        else:
            draw_rounded(self, 1, 1, w - 1, h - 1, self.radius, base,
                         outline=p["stroke"], width=1, tags="chrome")
            if self.quality != "reduced":
                for rx, ry in self._noise:
                    x = 2 + rx * (w - 4)
                    y = 2 + ry * (h - 4)
                    self.create_line(x, y, x + 1, y, fill=mix(base, "#ffffff", 0.10), tags="chrome")

        # 内描边，模拟玻璃厚度
        draw_rounded(self, 2, 2, w - 3, h - 3, max(2, self.radius - 2), "",
                     outline=mix(base, "#ffffff", 0.12), width=1, tags="chrome")
        self.tag_lower("chrome")
        self.itemconfigure(self._win, width=max(1, w - self.pad * 2))
        need = self.body.winfo_reqheight() + self.pad * 2
        if need > h:
            self.configure(height=need)


class GlassButton(tk.Canvas):
    """胶囊按钮（玻璃质感，hover/按下态）。"""

    def __init__(self, master, palette, text, command=None, kind="primary",
                 width=None, height=34, **kw):
        self.p = palette
        self.text = text
        self.command = command
        self.kind = kind
        self._h = height
        self._enabled = True
        f = tkfont.Font(family=FONT_FAMILY, size=10, weight="bold")
        w = width or (f.measure(text) + 40)
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
            return p["accent"], "#ffffff"
        if self.kind == "danger":
            return mix(p["panel"], p["err"], 0.24), p["err"]
        return mix(p["panel"], p["bg"], 0.35), p["fg"]

    def _draw(self, hover, press):
        fill, fg = self._colors()
        if not self._enabled:
            fill, fg = mix(self.p["bg"], self.p["panel"], 0.55), self.p["muted"]
        elif hover:
            fill = mix(fill, "#ffffff", 0.16)
        if press:
            fill = mix(fill, "#000000", 0.12)
        self.delete("all")
        draw_rounded(self, 1, 1, self._w - 1, self._h - 1, self._h / 2.0, fill,
                     outline=mix(fill, "#ffffff", 0.25))
        self.create_text(self._w / 2, self._h / 2 + 1, text=self.text,
                         fill=fg, font=font(10, "bold"))

    def _release(self, event):
        inside = 0 <= event.x <= self._w and 0 <= event.y <= self._h
        self._draw(inside, False)
        if inside and self._enabled and self.command:
            self.command()

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        self._draw(False, False)


class SidebarItem(tk.Canvas):
    """侧边栏条目：图标字形 + 文字，选中态高亮。"""

    def __init__(self, master, palette, glyph, text, command=None, height=46, **kw):
        self.p = palette
        self.glyph = glyph
        self.text = text
        self.command = command
        self._h = height
        self.selected = False
        super().__init__(master, width=210, height=height, bg=palette["bg2"],
                         highlightthickness=0, bd=0, **kw)
        self.bind("<Enter>", lambda e: self._draw(hover=True))
        self.bind("<Leave>", lambda e: self._draw(hover=False))
        self.bind("<Button-1>", self._click)
        self._draw()

    def _draw(self, hover=False):
        p = self.p
        self.delete("all")
        w, h = 210, self._h
        if self.selected:
            draw_rounded(self, 8, 4, w - 8, h - 4, 12,
                         mix(p["panel"], p["accent"], 0.20),
                         outline=mix(p["accent"], p["panel"], 0.35))
        elif hover:
            draw_rounded(self, 8, 4, w - 8, h - 4, 12, mix(p["bg2"], p["panel"], 0.7))
        fg = p["fg"] if self.selected else p["muted"]
        self.create_text(26, h / 2, text=self.glyph, fill=fg, font=font(13))
        self.create_text(52, h / 2, anchor="w", text=self.text, fill=fg,
                         font=font(10, "bold" if self.selected else "normal"))

    def set_selected(self, value):
        self.selected = bool(value)
        self._draw()

    def _click(self, event=None):
        if self.command:
            self.command()


class PageStack(tk.Frame):
    """页面容器：模仿 ttk.Notebook 的 add/select 接口，但切换靠 pack/forget。"""

    def __init__(self, master, palette, **kw):
        super().__init__(master, bg=palette["bg"], **kw)
        self.p = palette
        self.pages = []
        self.current = None

    def add(self, page, text=""):
        self.pages.append((page, text))
        page.pack_forget()

    def select(self, page):
        if isinstance(page, int):
            page = self.pages[page][0]
        for item, _text in self.pages:
            if item is not page:
                item.pack_forget()
        page.pack(fill="both", expand=True, padx=14, pady=12)
        self.current = page

    def tabs(self):
        return [text for _page, text in self.pages]


class GlassScroll(tk.Frame):
    """可滚动容器（侧边栏内容区用）。"""

    def __init__(self, master, palette, **kw):
        super().__init__(master, bg=palette["bg"], **kw)
        self.canvas = tk.Canvas(self, bg=palette["bg"], highlightthickness=0, bd=0)
        self.scroll = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=palette["bg"])
        self._win = self.canvas.create_window(0, 0, anchor="nw", window=self.inner)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scroll.pack(side="right", fill="y")
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _on_inner(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event):
        self.canvas.itemconfigure(self._win, width=event.width)

    def _on_wheel(self, event):
        try:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except Exception:
            pass


def enable_dpi_awareness():
    """必须在创建 Tk 之前调用，否则界面在高 DPI 下会被放大成像素块。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_AWARE
        return True
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return True
    except Exception:
        return False


def apply_tk_scaling(root):
    """让 tkinter 跟随系统 DPI，字体与控件不再发虚。"""
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)   # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        if dpi:
            root.tk.call("tk", "scaling", dpi / 72.0)
    except Exception:
        pass
