# -*- coding: utf-8 -*-
"""KeShare main window (tkinter).

Tabs: 附近设备 / 我的文件 / 传输记录 / 设置 [/ 开发者].

Threading model: sockets, discovery and file transfers run on worker threads and
report back through a :class:`queue.Queue` that the Tk main loop drains every
100 ms -- tkinter itself is only touched from the main thread.
"""

import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from .. import KSAPP_DISPLAY, KSAPP_NAME
from .. import clipboard, files, protocol
from ..client import KSClient
from . import glass
from .theme import font as theme_font
from .dev_tab import DevTab
from .remote_browser import RemoteBrowser
from .settings_tab import SettingsTab
from .transfer_panel import TransferPanel, human_size

DARK = {
    "bg": "#1e2126", "panel": "#262a30", "field": "#2f343b",
    "fg": "#e8e8e8", "muted": "#9aa0a6", "accent": "#4a90d9",
    "ok": "#4caf50", "warn": "#d08a30", "err": "#d9534f",
}
LIGHT = {
    "bg": "#f4f5f7", "panel": "#ffffff", "field": "#e9ecef",
    "fg": "#202428", "muted": "#6b7280", "accent": "#2f6fb3",
    "ok": "#2e7d32", "warn": "#a06010", "err": "#c0392b",
}



def human_time(stamp):
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(stamp)))
    except (TypeError, ValueError, OSError):
        return "-"


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class KeShareApp(tk.Tk):
    def __init__(self, config, logger, server, discovery, registry):
        tk.Tk.__init__(self)
        self.config_obj = config
        self.log = logger
        self.server = server
        self.discovery = discovery
        self.registry = registry
        self._client = None
        self._events = queue.Queue()
        self._share = files.ShareRoot(config.share_root)
        self._browse_rel = ""
        self._file_entries = {}
        self._device_peers = {}
        self._dev_tab = None
        self._transfer_busy = False
        self._remote_browser = None
        self.palette = glass.DARK if config.theme == "dark" else glass.LIGHT
        glass.apply_tk_scaling(self)
        # 磨砂玻璃开销低，不做温控降质

        self.title("%s %s" % (KSAPP_NAME, KSAPP_DISPLAY))
        self.geometry("1000x660")
        self.minsize(880, 560)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self._apply_theme()
        self._build_header()
        self._build_notebook()
        self._build_statusbar()

        self.discovery.table.set_listener(lambda: self.post("peers", {}))
        if config.developer_unlocked:
            self._add_dev_tab()
        self.refresh_devices()
        self.refresh_files("")
        self._update_channel_labels()
        self.after(100, self._pump)
        if config.data["ui"].get("start_minimized"):
            try:
                self.iconify()
            except Exception:
                pass
        self.log.info("%s %s 界面已就绪（%s）" % (KSAPP_NAME, KSAPP_DISPLAY, config.path))

    # ------------------------------------------------------------------
    # theming / layout
    # ------------------------------------------------------------------
    def _apply_theme(self):
        self.palette = glass.DARK if self.config_obj.theme == "dark" else glass.LIGHT
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        p = self.palette
        base_font = ("Microsoft YaHei UI", 9)
        style.configure(".", background=p["bg"], foreground=p["fg"],
                        fieldbackground=p["field"], font=base_font)
        style.configure("TFrame", background=p["bg"])
        style.configure("TLabel", background=p["bg"], foreground=p["fg"])
        style.configure("Muted.TLabel", foreground=p["muted"])
        style.configure("TButton", background=p["field"], foreground=p["fg"],
                        borderwidth=1, padding=(8, 4))
        style.map("TButton", background=[("active", p["accent"])],
                  foreground=[("active", "#ffffff")])
        style.configure("Header.TFrame", background=p["panel"])
        style.configure("Header.TLabel", background=p["panel"], foreground=p["fg"])
        style.configure("Title.TLabel", background=p["panel"], foreground=p["fg"],
                        font=("Microsoft YaHei UI", 15, "bold"))
        style.configure("Sub.TLabel", background=p["panel"], foreground=p["muted"],
                        font=("Microsoft YaHei UI", 9))
        style.configure("TNotebook", background=p["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=p["field"], foreground=p["fg"],
                        padding=(16, 7))
        style.map("TNotebook.Tab",
                  background=[("selected", p["panel"])],
                  foreground=[("selected", p["accent"])])
        style.configure("TLabelframe", background=p["bg"], foreground=p["fg"])
        style.configure("TLabelframe.Label", background=p["bg"], foreground=p["muted"])
        style.configure("TEntry", fieldbackground=p["field"], foreground=p["fg"],
                        insertcolor=p["fg"])
        style.configure("Treeview", background=p["panel"], fieldbackground=p["panel"],
                        foreground=p["fg"], rowheight=26, borderwidth=0)
        style.configure("Treeview.Heading", background=p["field"], foreground=p["fg"],
                        padding=(4, 4))
        style.map("Treeview", background=[("selected", p["accent"])],
                  foreground=[("selected", "#ffffff")])
        style.configure("TProgressbar", background=p["accent"],
                        troughcolor=p["field"], borderwidth=0)
        style.configure("TCheckbutton", background=p["bg"], foreground=p["fg"])
        style.configure("TRadiobutton", background=p["bg"], foreground=p["fg"])
        style.configure("TCombobox", fieldbackground=p["field"], foreground=p["fg"])
        self.configure(background=p["bg"])

    def _build_header(self):
        header = ttk.Frame(self, style="Header.TFrame", padding=(16, 10))
        header.pack(fill="x")
        left = ttk.Frame(header, style="Header.TFrame")
        left.pack(side="left", fill="y")
        ttk.Label(left, text="KeShare", style="Title.TLabel").pack(anchor="w")
        ttk.Label(left, text="局域网文件互传  ·  %s" % (KSAPP_DISPLAY,),
                  style="Sub.TLabel").pack(anchor="w")

        right = ttk.Frame(header, style="Header.TFrame")
        right.pack(side="right", fill="y")
        self.ctl_var = tk.StringVar(value="控制通道: WiFi 回退")
        self.data_var = tk.StringVar(value="数据通道: WiFi")
        self.integrity_var = tk.StringVar(value="完整性: SHA-256")
        ttk.Label(right, textvariable=self.ctl_var, style="Header.TLabel").pack(anchor="e")
        ttk.Label(right, textvariable=self.data_var, style="Header.TLabel").pack(anchor="e")
        ttk.Label(right, textvariable=self.integrity_var, style="Sub.TLabel").pack(anchor="e")

    def _build_notebook(self):
        """左侧边栏 + 右侧内容区（替代原来的平铺 Notebook）。"""
        shell = tk.Frame(self, bg=self.palette["bg"])
        shell.pack(fill="both", expand=True)

        # ---- 侧边栏 ----
        self.sidebar = tk.Frame(shell, bg=self.palette["bg2"], width=214)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        brand = tk.Frame(self.sidebar, bg=self.palette["bg2"], height=78)
        brand.pack(fill="x")
        tk.Label(brand, text="KeShare", bg=self.palette["bg2"],
                 fg=self.palette["fg"], font=glass.font(16, "bold"),
                 anchor="w").pack(fill="x", padx=22, pady=(18, 0))
        tk.Label(brand, text="0.0.1 beta1", bg=self.palette["bg2"],
                 fg=self.palette["muted"], font=glass.font(9),
                 anchor="w").pack(fill="x", padx=22)

        self._nav_items = {}
        nav_specs = [
            ("devices", "◉", "设备"),
            ("files", "▤", "文件"),
            ("settings", "⚙", "设置"),
            ("about", "ⓘ", "关于"),
        ]
        for key, glyph, text in nav_specs:
            item = glass.SidebarItem(self.sidebar, self.palette, glyph, text,
                                     command=lambda k=key: self.show_page(k))
            item.pack(fill="x", pady=2)
            self._nav_items[key] = item

        # 温控 / 材质提示区
        footer = tk.Frame(self.sidebar, bg=self.palette["bg2"])
        footer.pack(side="bottom", fill="x", pady=12)
        self.glass_mode_var = tk.StringVar(value="材质：液态玻璃")
        tk.Label(footer, textvariable=self.glass_mode_var, bg=self.palette["bg2"],
                 fg=self.palette["muted"], font=glass.font(9),
                 anchor="w").pack(fill="x", padx=22)

        # ---- 内容区 ----
        self.content = tk.Frame(shell, bg=self.palette["bg"])
        self.content.pack(side="left", fill="both", expand=True)

        self.notebook = glass.PageStack(self.content, self.palette)
        self.notebook.pack(fill="both", expand=True)

        self.devices_tab = ttk.Frame(self.notebook, padding=10)
        self.files_tab = ttk.Frame(self.notebook, padding=10)
        self.transfers_tab = TransferPanel(self.notebook, self.palette)
        self.settings_tab = SettingsTab(
            self.notebook, self.config_obj, self.log,
            on_save=self._on_settings_saved,
            on_unlock_dev=self._add_dev_tab)
        self.about_tab = self._build_about_tab()

        self.notebook.add(self.devices_tab, text="设备")
        self.notebook.add(self.files_tab, text="文件")
        self.notebook.add(self.transfers_tab, text="传输")
        self.notebook.add(self.settings_tab, text="设置")
        self.notebook.add(self.about_tab, text="关于")

        self._page_map = {
            "devices": self.devices_tab,
            "files": self.files_tab,
            "transfers": self.transfers_tab,
            "settings": self.settings_tab,
            "about": self.about_tab,
        }

        self._build_devices_tab()
        self._build_files_tab()
        self.show_page("devices")

    def _ensure_backdrop(self):
        """生成/更新窗口背景（深色底 + 色斑），供玻璃面板做折射采样。"""
        try:
            from . import haze
            w = max(320, self.winfo_width())
            h = max(240, self.winfo_height())
            key = (w // 16, h // 16)
            if getattr(self, "_backdrop_key", None) == key and getattr(self, "_backdrop_img", None):
                return
            self._backdrop_key = key
            self._backdrop_img = haze.make_backdrop(w, h, self.palette)
            self._backdrop_origin = (self.winfo_rootx(), self.winfo_rooty())
        except Exception:
            self._backdrop_img = None

    def _backdrop_provider(self):
        self._ensure_backdrop()
        origin = getattr(self, "_backdrop_origin", (self.winfo_rootx(), self.winfo_rooty()))
        return getattr(self, "_backdrop_img", None), origin
    def show_page(self, key):
        page = self._page_map.get(key)
        if page is None:
            return
        self.notebook.select(page)
        for name, item in self._nav_items.items():
            item.set_selected(name == key)

    def _build_about_tab(self):
        tab = ttk.Frame(self.notebook, padding=18)
        card = glass.GlassPanel(tab, self.palette, style=self.glass_style(), height=260,
                                backdrop=self._backdrop_provider)
        card.pack(fill="x", pady=(0, 12))
        body = card.body
        tk.Label(body, text="KeShare", bg=self.palette["panel"],
                 fg=self.palette["fg"], font=glass.font(18, "bold"),
                 anchor="w").pack(fill="x")
        tk.Label(body, text="局域网 / 热点 / USB 直连文件互传", bg=self.palette["panel"],
                 fg=self.palette["muted"], font=glass.font(10), anchor="w").pack(fill="x", pady=(2, 12))
        rows = [
            ("版本", KSAPP_DISPLAY),
            ("内核版本", "0.0.1-beta1+kernel.1"),
            ("协议版本", "KS/1"),
            ("完整性校验", "SHA-256"),
            ("数据通道", "TCP 47377"),
        ]
        for name, value in rows:
            row = tk.Frame(body, bg=self.palette["panel"])
            row.pack(fill="x", pady=3)
            tk.Label(row, text=name, bg=self.palette["panel"], fg=self.palette["muted"],
                     font=glass.font(10), anchor="w", width=12).pack(side="left")
            tk.Label(row, text=value, bg=self.palette["panel"], fg=self.palette["fg"],
                     font=glass.font(10), anchor="w").pack(side="left")
        card.redraw()
        return tab

    def glass_style(self):
        """当前玻璃风格：liquid / frosted，受设置与温控影响。"""
        mode = self.config_obj.data.get("ui", {}).get("glass_mode", "auto")
        if mode == "frosted":
            return "frosted"
        if mode == "liquid":
            return "liquid"
        return "liquid"

    def _build_devices_tab(self):
        tab = self.devices_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        bar = ttk.Frame(tab)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(bar, text="刷新", command=self._refresh_devices_clicked).pack(side="left")
        ttk.Button(bar, text="连接", command=self._connect_selected).pack(side="left", padx=6)
        ttk.Button(bar, text="断开", command=self._disconnect).pack(side="left")
        ttk.Button(bar, text="手动连接…", command=self._manual_connect).pack(side="left", padx=6)
        ttk.Button(bar, text="USB 连接", command=self._usb_connect).pack(side="left")
        ttk.Button(bar, text="开启热点", command=self._toggle_hotspot).pack(side="left", padx=6)
        self.device_hint = tk.StringVar(value="双击设备或点击“连接”建立控制通道")
        ttk.Label(bar, textvariable=self.device_hint,
                  foreground=self.palette["muted"]).pack(side="left", padx=12)

        columns = ("name", "kind", "osv", "ip", "port", "state")
        self.device_tree = ttk.Treeview(tab, columns=columns, show="headings",
                                        height=12, selectmode="browse")
        headings = {"name": "设备名称", "kind": "类型", "osv": "系统",
                    "ip": "地址", "port": "端口", "state": "状态"}
        widths = {"name": 220, "kind": 90, "osv": 170, "ip": 140,
                  "port": 70, "state": 80}
        for column in columns:
            self.device_tree.heading(column, text=headings[column])
            self.device_tree.column(column, width=widths[column],
                                    anchor="w" if column != "port" else "center")
        self.device_tree.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(tab, orient="vertical", command=self.device_tree.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.device_tree.configure(yscrollcommand=scroll.set)
        self.device_tree.bind("<Double-1>", lambda event: self._connect_selected())

    def _build_files_tab(self):
        tab = self.files_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        bar = ttk.Frame(tab)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(bar, text="上级", width=6,
                   command=self._go_up).pack(side="left")
        ttk.Button(bar, text="刷新", width=6,
                   command=lambda: self.refresh_files(self._browse_rel)).pack(
            side="left", padx=(6, 10))
        self.path_var = tk.StringVar(value="/")
        ttk.Entry(bar, textvariable=self.path_var, state="readonly").pack(
            side="left", fill="x", expand=True)
        ttk.Button(bar, text="打开目录", command=self._open_share_folder).pack(
            side="left", padx=(10, 0))

        actions = ttk.Frame(tab)
        actions.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        ttk.Button(actions, text="推送到设备", command=self._push_selected).pack(side="left")
        ttk.Button(actions, text="浏览远端…",
                   command=self._open_remote_browser).pack(side="left", padx=6)
        ttk.Button(actions, text="下载远端文件…",
                   command=self._download_remote).pack(side="left")
        ttk.Button(actions, text="复制路径",
                   command=self._copy_selected_path).pack(side="left", padx=6)
        self.files_hint = tk.StringVar(value="双击文件夹进入，双击文件打开所在位置")
        ttk.Label(actions, textvariable=self.files_hint,
                  foreground=self.palette["muted"]).pack(side="left", padx=12)

        columns = ("name", "size", "mtime", "kind")
        self.file_tree = ttk.Treeview(tab, columns=columns, show="headings",
                                      height=12, selectmode="browse")
        headings = {"name": "名称", "size": "大小", "mtime": "修改时间", "kind": "类型"}
        widths = {"name": 380, "size": 110, "mtime": 160, "kind": 80}
        for column in columns:
            self.file_tree.heading(column, text=headings[column])
            self.file_tree.column(column, width=widths[column],
                                  anchor="w" if column == "name" else "center")
        self.file_tree.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(tab, orient="vertical", command=self.file_tree.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.file_tree.configure(yscrollcommand=scroll.set)
        self.file_tree.bind("<Double-1>", self._on_file_double_click)

    def _build_statusbar(self):
        bar = ttk.Frame(self, padding=(14, 4))
        bar.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(bar, textvariable=self.status_var,
                  foreground=self.palette["muted"]).pack(side="left")
        self.port_var = tk.StringVar(value="端口 %d" % (self.server.port,))
        ttk.Label(bar, textvariable=self.port_var,
                  foreground=self.palette["muted"]).pack(side="right")

    # ------------------------------------------------------------------
    # event pump
    # ------------------------------------------------------------------
    def post(self, kind, payload):
        """Thread safe event injector for the Tk main loop."""
        self._events.put((kind, payload))

    def _pump(self):
        try:
            while True:
                kind, payload = self._events.get_nowait()
                try:
                    self._handle_event(kind, payload)
                except Exception as exc:                  # pragma: no cover - UI safety
                    self.log.error("界面事件处理失败（%s）: %s" % (kind, exc))
        except queue.Empty:
            pass
        self.after(100, self._pump)

    def _handle_event(self, kind, payload):
        if kind == "transfer":
            state = payload.get("state")
            tid = payload.get("id")
            if state == "start":
                self.transfers_tab.start(payload)
            elif state == "progress":
                self.transfers_tab.progress(tid, payload.get("sent"),
                                            payload.get("total"))
            elif state == "done":
                self.transfers_tab.finish(tid, True, dest=payload.get("dest") or "",
                                          digest=payload.get("digest") or "",
                                          size=payload.get("size"))
                if self.log is not None:
                    self.log.debug("传输完成 %s" % (tid,))
            elif state == "error":
                self.transfers_tab.finish(tid, False, payload.get("err") or "错误")
        elif kind == "peers":
            self.refresh_devices()
        elif kind == "connected":
            client = payload.get("client")
            previous = self._client
            self._client = client
            if previous is not None and previous is not client:
                try:
                    previous.close()
                except Exception:
                    pass
            self.device_hint.set("已连接: %s" % (client.peer_name,))
            self.files_hint.set("已连接: %s，可推送文件或下载远端文件" % (client.peer_name,))
            self._update_channel_labels()
            self.status_var.set("控制通道已连接 %s" % (client.peer_name,))
        elif kind == "connect_failed":
            messagebox.showerror("错误！！！",
                                 "错误信息：\n ERROR-FrontOFF\n常见错误环境：对方断开 WIFI、"
                                 "对方没电关机、对方取消传输、对方切到后台等\n\n详情：%s" % (payload.get("err") or "",),
                                 parent=self)
            self.device_hint.set("连接失败: %s" % (payload.get("err") or "",))
            self._client = None
            self._update_channel_labels()
        elif kind == "disconnected":
            self._client = None
            self._close_remote_browser()
            self.device_hint.set("已断开")
            self.files_hint.set("双击文件夹进入，双击文件打开所在位置")
            self._update_channel_labels()
            self.status_var.set("已断开控制通道")
        elif kind == "ctl_open":
            self._update_channel_labels()
            self.status_var.set("对端 %s 已连接（%s）" % (
                payload.get("name"), payload.get("transport_label")))
        elif kind == "ctl_close":
            self._update_channel_labels()
        elif kind == "action_done":
            self.status_var.set("%s完成: %s" % (payload.get("op"), payload.get("path")))
        elif kind == "action_failed":
            messagebox.showwarning(payload.get("op") or "操作失败",
                                   payload.get("err") or "未知错误", parent=self)
            self.status_var.set("%s失败" % (payload.get("op"),))
        elif kind == "transfer_idle":
            self._transfer_busy = False

    # ------------------------------------------------------------------
    # devices
    # ------------------------------------------------------------------
    def _ble_scan_worker(self):
        """后台扫描 BLE，把带 IP 的广播直接加进设备列表（UDP 被拦时的兜底）。"""
        try:
            from .. import btwin
            peers = btwin.ble_peers(4)
        except Exception as exc:
            self.log.warn("蓝牙扫描失败：%s" % (exc,))
            return
        if not peers:
            self.log.info("蓝牙未扫到 KeShare 设备")
            return
        for peer in peers:
            self.log.info("蓝牙发现 %s (%s:%s)" % (peer["name"], peer["ip"], peer["port"]))
            self.post("ble_peer", peer)

    def _start_ble_scan(self):
        self.device_hint.set("正在通过蓝牙扫描…")
        thread = threading.Thread(target=self._ble_scan_worker, name="ks-ble")
        thread.daemon = True
        thread.start()

    def _refresh_devices_clicked(self):
        self.discovery.probe_now()
        self.refresh_devices()

    def refresh_devices(self):
        try:
            peers = self.discovery.snapshot()
        except Exception:
            peers = []
        selected = self._selected_device_did()
        self.device_tree.delete(*self.device_tree.get_children())
        self._device_peers = {}
        for peer in peers:
            iid = peer["did"] or peer["ip"]
            self._device_peers[iid] = peer
            state = "在线" if peer.get("online") else "离线"
            self.device_tree.insert(
                "", "end", iid=iid,
                values=(peer.get("name"), peer.get("kind"), peer.get("osv"),
                        peer.get("ip"), peer.get("port"), state),
                tags=("online" if peer.get("online") else "offline",))
        self.device_tree.tag_configure("offline", foreground=self.palette["muted"])
        if selected and self.device_tree.exists(selected):
            self.device_tree.selection_set(selected)
        self._update_channel_labels()

    def _selected_device_did(self):
        selection = self.device_tree.selection()
        return selection[0] if selection else None

    def _selected_peer(self):
        did = self._selected_device_did()
        if not did:
            messagebox.showinfo("提示", "请先在列表中选择一个设备", parent=self)
            return None
        return self._device_peers.get(did)

    # ------------------------------------------------------------ Apple 驱动
    def _apple_driver_dialog(self):
        """未安装 Apple Mobile Device Service 时的提示：安装 / 忽略。"""
        from .. import appledrv
        from .theme import PillButton

        dialog = tk.Toplevel(self)
        dialog.title("缺少 Apple 驱动")
        dialog.transient(self)
        dialog.resizable(False, False)
        dialog.configure(bg=self.palette["bg"])
        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="无法通过 USB 传输文件",
                  font=theme_font(12, "bold")).pack(anchor="w")
        ttk.Label(frame,
                  text="当前设备未安装 Apple Mobile Device Service 驱动，\n"
                       "无法通过 USB 传输文件，可点击下方按钮安装或忽略。",
                  foreground=self.palette["muted"], justify="left").pack(anchor="w", pady=(8, 12))

        log_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=log_var, foreground=self.palette["muted"],
                  justify="left", wraplength=460).pack(anchor="w", pady=(0, 10))

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")

        install_btn = PillButton(buttons, self.palette, "安装驱动", kind="primary")

        def do_install():
            log_var.set("正在通过 winget 安装 iTunes（含驱动），可能需要几分钟…")
            install_btn.set_enabled(False)

            def worker():
                result = appledrv.install_itunes_via_winget()

                def done():
                    install_btn.set_enabled(True)
                    if result.get("ok"):
                        log_var.set("安装完成，请重开本程序后再点「USB 连接」。")
                    else:
                        log_var.set("winget 安装失败：%s\n可改用「应用商店安装」或官网。"
                                    % (result.get("err") or "",))
                self.after(0, done)
            threading.Thread(target=worker, name="ks-winget", daemon=True).start()

        install_btn.command = do_install
        install_btn.pack(side="left")

        def do_store():
            appledrv.open_store_page()
            log_var.set("已打开 Microsoft Store 的「Apple 设备」页面，安装后重开本程序。")

        def do_ignore():
            try:
                self.config_obj.data.setdefault("usb", {})["driver_prompt_dismissed"] = True
                self.config_obj.save()
            except Exception:
                pass
            dialog.destroy()

        PillButton(buttons, self.palette, "应用商店安装", command=do_store, kind="ghost").pack(side="left", padx=8)
        PillButton(buttons, self.palette, "忽略", command=do_ignore, kind="ghost").pack(side="right")
        dialog.grab_set()

    def _ensure_apple_driver(self):
        """返回 True 表示 USB 传输可用；否则弹出提示并返回 False。"""
        try:
            from .. import appledrv
        except Exception:
            return False
        status = appledrv.status()
        if status.get("ready"):
            return True
        if status.get("present"):
            if appledrv.start_service() and appledrv.port_reachable():
                return True
        try:
            if self.config_obj.data.get("usb", {}).get("driver_prompt_dismissed"):
                return False
        except Exception:
            pass
        self._apple_driver_dialog()
        return False

    def _usb_connect(self):
        """USB 传输：经 iTunes 的 usbmuxd 隧道连接 iPhone（无需 WiFi/蓝牙）。"""
        if not self._ensure_apple_driver():
            return
        try:
            from .. import usb as usbmod
        except Exception as exc:
            messagebox.showerror("USB 不可用", str(exc), parent=self)
            return
        try:
            devices = usbmod.list_devices()
        except Exception as exc:
            messagebox.showerror(
                "USB 不可用",
                "%s\n\n需要：1) 已安装 iTunes 或「Apple 设备」\n"
                "2) 数据线已连接\n3) 手机已点「信任本电脑」" % (exc,), parent=self)
            return
        if not devices:
            messagebox.showinfo(
                "没有设备",
                "没有检测到 USB 连接的 iOS 设备。\n请插好数据线，并在手机上点「信任」。",
                parent=self)
            return
        dev = devices[0]
        self.device_hint.set("正在通过 USB 连接 %s …" % (dev.get("name") or "iPhone",))
        peer = {"did": "usb-%s" % (dev.get("device_id"),), "name": dev.get("name") or "iPhone(USB)",
                "kind": "ios", "osv": "", "ip": "usb", "port": protocol.KS_PORT}
        self._device_peers[peer["did"]] = peer

        def worker():
            client = KSClient(self.config_obj, "usb", protocol.KS_PORT, log=self.log,
                              local_port=self.server.port, registry=self.registry,
                              on_event=self.post, resolve_ip=self._resolve_ip)
            try:
                client.connect_usb(device_id=dev.get("device_id"))
            except Exception as exc:
                self.post("connect_failed", {"err": str(exc)})
                return
            self.post("connected", {"client": client})

        thread = threading.Thread(target=worker, name="ks-usb")
        thread.daemon = True
        thread.start()

    def _toggle_hotspot(self):
        """一键开关 Windows 移动热点（需要管理员权限）。"""
        try:
            from .. import btwin
        except Exception as exc:
            messagebox.showerror("热点不可用", str(exc), parent=self)
            return
        status = btwin.hotspot_status()
        state = str(status.get("state") or "")
        if "On" in state:
            result = btwin.hotspot_stop()
            messagebox.showinfo("热点", "热点已关闭", parent=self)
            return
        ssid = "KeShare-%s" % (self.config_obj.device_id[-4:].upper(),)
        password = "keshare123"
        result = btwin.hotspot_start(ssid, password)
        if result.get("ok"):
            messagebox.showinfo(
                "热点已开启",
                "SSID：%s\n密码：%s\n\n在 iPhone 上连接该 Wi-Fi，然后回到 KeShare。\n"
                "（若失败，请以管理员身份运行本程序）" % (ssid, password), parent=self)
        else:
            messagebox.showerror("开启热点失败",
                                 "%s\n\n提示：需要管理员权限，且无线网卡需支持承载网络。"
                                 % (result.get("err") or "未知错误"), parent=self)
    def _manual_connect(self):
        """手动输入 IP 连接：蓝牙/UDP 发现不到时使用（如手机热点场景）。"""
        dialog = tk.Toplevel(self)
        dialog.title("手动连接")
        dialog.transient(self)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="对方 IP 地址").grid(row=0, column=0, sticky="w")
        ip_var = tk.StringVar(value="172.20.10.1")
        ttk.Entry(frame, textvariable=ip_var, width=24).grid(row=0, column=1, padx=8, pady=4)
        ttk.Label(frame, text="端口").grid(row=1, column=0, sticky="w")
        port_var = tk.StringVar(value=str(protocol.KS_PORT))
        ttk.Entry(frame, textvariable=port_var, width=10).grid(row=1, column=1, sticky="w", padx=8, pady=4)
        ttk.Label(frame,
                  text="手机开热点时，Windows 连上该热点，对方 IP 通常是 172.20.10.1",
                  foreground=self.palette["muted"], justify="left").grid(
            row=2, column=0, columnspan=2, pady=(6, 8), sticky="w")

        def do_connect():
            ip = (ip_var.get() or "").strip()
            if not ip:
                return
            try:
                port = int(port_var.get())
            except Exception:
                port = protocol.KS_PORT
            peer = {"did": "manual-" + ip, "name": ip, "kind": "manual",
                    "osv": "", "ip": ip, "port": port}
            self._device_peers[peer["did"]] = peer
            dialog.destroy()
            self._connect_worker(peer)

        ttk.Button(frame, text="取消", command=dialog.destroy).grid(row=3, column=0, pady=(6, 0))
        ttk.Button(frame, text="连接", command=do_connect).grid(row=3, column=1, sticky="e", pady=(6, 0))
        dialog.grab_set()
    def _connect_selected(self):
        peer = self._selected_peer()
        if peer is None:
            return
        if not peer.get("ip"):
            messagebox.showwarning("无法连接", "该设备没有可用的 WiFi 地址", parent=self)
            return
        self.device_hint.set("正在连接 %s …" % (peer.get("name"),))
        thread = threading.Thread(target=self._connect_worker, args=(peer,),
                                  name="ks-connect")
        thread.daemon = True
        thread.start()

    def _connect_worker(self, peer):
        client = KSClient(
            self.config_obj, peer["ip"], peer.get("port") or protocol.KS_PORT,
            log=self.log, local_port=self.server.port, registry=self.registry,
            on_event=self.post, resolve_ip=self._resolve_ip)
        try:
            client.connect()
        except Exception as exc:
            self.post("connect_failed", {"err": str(exc)})
            return
        self.post("connected", {"client": client})

    def _disconnect(self):
        client = self._client
        self._client = None
        if client is None:
            return
        def worker():
            try:
                client.close()
            except Exception:
                pass
            self.post("disconnected", {})
        thread = threading.Thread(target=worker, name="ks-disconnect")
        thread.daemon = True
        thread.start()

    def _resolve_ip(self, did):
        try:
            for peer in self.discovery.snapshot():
                if peer.get("did") == did:
                    return peer.get("ip") or ""
        except Exception:
            pass
        return ""

    def _update_channel_labels(self):
        client = getattr(self, "_client", None)
        if client is not None:
            ctl = client.transport_label
        elif getattr(self.server, "bluetooth_active", False):
            ctl = "蓝牙"
        else:
            ctl = "WiFi 回退"
        self.ctl_var.set("控制通道: %s" % (ctl,))
        self.data_var.set("数据通道: WiFi")
        if hasattr(self, "port_var"):
            self.port_var.set("端口 %d · 设备 %d" % (
                self.server.port, len(self._device_peers)))

    # ------------------------------------------------------------------
    # local files
    # ------------------------------------------------------------------
    def refresh_files(self, rel_path):
        try:
            rel = files.normalise_rel(rel_path or "")
        except files.SandboxError:
            rel = ""
        try:
            self._share = files.ShareRoot(self.config_obj.share_root)
            listing = self._share.list_dir(rel, 500, 0)
        except (OSError, files.SandboxError) as exc:
            self.status_var.set("无法列出共享目录: %s" % (exc,))
            return
        self._browse_rel = rel
        self.path_var.set("/" + rel if rel else "/")
        self.file_tree.delete(*self.file_tree.get_children())
        self._file_entries = {}
        for entry in listing["files"]:
            iid = entry["id"]
            self._file_entries[iid] = entry
            self.file_tree.insert(
                "", "end", iid=iid,
                values=(entry["name"], human_size(entry.get("size")),
                        human_time(entry.get("mtime")),
                        "文件夹" if entry["kind"] == "dir" else "文件"))
        self.status_var.set("共享目录 %s：%d 项" % (rel or "/", listing["total"]))

    def _go_up(self):
        if not self._browse_rel:
            return
        parent = self._browse_rel.rsplit("/", 1)[0] if "/" in self._browse_rel else ""
        self.refresh_files(parent)

    def _open_share_folder(self):
        try:
            self.config_obj.ensure_dirs()
            os.startfile(self.config_obj.share_root)
        except (OSError, AttributeError) as exc:
            messagebox.showwarning("无法打开", str(exc), parent=self)

    def _selected_file(self):
        selection = self.file_tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选择一个文件", parent=self)
            return None
        return self._file_entries.get(selection[0])

    def _on_file_double_click(self, event=None):
        entry = self._selected_file()
        if entry is None:
            return
        if entry["kind"] == "dir":
            self.refresh_files(entry["path"])
            return
        try:
            os.startfile(os.path.dirname(self._share.resolve(entry["path"])))
        except (OSError, AttributeError, files.SandboxError):
            pass

    def _copy_selected_path(self):
        entry = self._selected_file()
        if entry is None:
            return
        try:
            clipboard.set_text(entry["path"])
            self.status_var.set("已复制路径: %s" % (entry["path"],))
        except clipboard.ClipboardError as exc:
            messagebox.showwarning("剪贴板不可用", str(exc), parent=self)

    # ------------------------------------------------------------------
    # transfers
    # ------------------------------------------------------------------
    def _push_selected(self):
        client = self._client
        if client is None:
            messagebox.showinfo("提示", "请先在“附近设备”中连接一台设备", parent=self)
            return
        entry = self._selected_file()
        if entry is None:
            return
        if entry["kind"] != "file":
            messagebox.showinfo("提示", "只能推送文件，不能推送文件夹", parent=self)
            return
        try:
            local_path = self._share.resolve(entry["path"])
        except files.SandboxError as exc:
            messagebox.showerror("路径无效", str(exc), parent=self)
            return
        self._run_transfer("推送", lambda: client.push(local_path),
                           local_path)

    def _download_remote(self):
        client = self._client
        if client is None:
            messagebox.showinfo("提示", "请先在“附近设备”中连接一台设备", parent=self)
            return
        path = simpledialog.askstring(
            "下载远端文件", "输入对端共享目录下的相对路径（例如 photos/a.jpg）：",
            parent=self)
        if not path:
            return
        self._run_transfer("下载", lambda: client.get(path), path)

    def _open_remote_browser(self):
        client = self._client
        if client is None:
            messagebox.showinfo("提示", "请先在“附近设备”中连接一台设备", parent=self)
            return
        browser = getattr(self, "_remote_browser", None)
        if browser is not None:
            try:
                browser.lift()
                browser.focus_set()
                return
            except tk.TclError:
                self._remote_browser = None
        self._remote_browser = RemoteBrowser(
            self, client, self.log, on_download=self._download_remote_path,
            palette=self.palette)
        self._remote_browser.protocol("WM_DELETE_WINDOW",
                                      self._close_remote_browser)

    def _close_remote_browser(self):
        browser = getattr(self, "_remote_browser", None)
        self._remote_browser = None
        if browser is not None:
            try:
                browser.destroy()
            except Exception:
                pass

    def _download_remote_path(self, path):
        client = self._client
        if client is None:
            raise RuntimeError("控制通道已断开")
        self._run_transfer("下载", lambda: client.get(path), path)

    def _run_transfer(self, op, action, label):
        if getattr(self, "_transfer_busy", False):
            messagebox.showinfo("请稍候", "已有传输正在进行，请完成后再试", parent=self)
            return
        self._transfer_busy = True
        self.status_var.set("%s中: %s" % (op, label))

        def worker():
            try:
                result = action()
            except Exception as exc:
                self.post("action_failed", {"op": op, "err": str(exc)})
                return
            path = ""
            if isinstance(result, dict):
                path = result.get("dest") or result.get("name") or ""
            self.post("action_done", {"op": op, "path": path})

        def runner():
            try:
                worker()
            finally:
                self.post("transfer_idle", {})

        thread = threading.Thread(target=runner, name="ks-transfer")
        thread.daemon = True
        thread.start()

    # ------------------------------------------------------------------
    # settings / developer
    # ------------------------------------------------------------------
    def _on_settings_saved(self, cfg):
        self.log.set_verbose(cfg.verbose_logging)
        self.log.set_ring_size(cfg.log_ring_size)
        self._apply_theme()
        self.server.apply_settings()
        old_port = self.server.port
        if int(cfg.port) != int(old_port):
            try:
                new_port = self.server.rebind(cfg.port)
                self.log.info("服务端口 %s -> %s" % (old_port, new_port))
            except Exception as exc:
                messagebox.showerror("端口切换失败", str(exc), parent=self)
            try:
                self.discovery.restart()
            except Exception as exc:
                self.log.warn("重新启动发现服务失败: %s" % (exc,))
        self.refresh_files(self._browse_rel)
        self._update_channel_labels()
        self.port_var.set("端口 %d" % (self.server.port,))
        self.status_var.set("设置已保存并生效")

    def _add_dev_tab(self):
        if self._dev_tab is not None:
            try:
                self.notebook.select(self._dev_tab)
            except tk.TclError:
                pass
            return
        self._dev_tab = DevTab(self.notebook, self.config_obj, self.log,
                               on_change=self._on_dev_changed,
                               server=self.server)
        self.notebook.add(self._dev_tab, text="开发者")
        self.notebook.select(self._dev_tab)
        self.log.info("开发者模式已启用")

    def _on_dev_changed(self):
        self.config_obj.normalise()
        self.log.set_verbose(self.config_obj.verbose_logging)
        self.log.set_ring_size(self.config_obj.log_ring_size)

    # ------------------------------------------------------------------
    # shutdown
    # ------------------------------------------------------------------
    def on_close(self):
        try:
            self.log.info("界面关闭，正在停止服务")
        except Exception:
            pass
        self._close_remote_browser()
        client = self._client
        self._client = None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        try:
            self.discovery.stop()
        except Exception:
            pass
        try:
            self.server.stop()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass
