# -*- coding: utf-8 -*-
"""Settings tab: edits settings.json and applies changes at runtime.

Also owns the "8 tap" developer-mode Easter egg: clicking the version label
eight times (with a 3 second inactivity reset) sets ``developer.unlocked``,
saves the JSON and asks the main window to add the 开发者 tab.
"""

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import KSAPP_VERSION

TAPS_TO_UNLOCK = 8
TAP_RESET_MS = 3000
HINT_AFTER = 4


class SettingsTab(ttk.Frame):
    """Form with the settings that matter at runtime."""

    def __init__(self, master, config, log, on_save=None, on_unlock_dev=None):
        ttk.Frame.__init__(self, master, padding=12)
        self.config = config
        self.log = log
        self.on_save = on_save
        self.on_unlock_dev = on_unlock_dev
        self._taps = 0
        self._reset_job = None

        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)

        self._build_device_box()
        self._build_network_box()
        self._build_footer()
        self.refresh()

    # ------------------------------------------------------------------
    # layout
    # ------------------------------------------------------------------
    def _build_device_box(self):
        box = ttk.LabelFrame(self, text="设备与共享", padding=10)
        box.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 8))
        box.columnconfigure(1, weight=1)

        self.name_var = tk.StringVar()
        self.root_var = tk.StringVar()
        self.receive_var = tk.StringVar()

        self._label(box, 0, "设备名称")
        entry = ttk.Entry(box, textvariable=self.name_var)
        entry.grid(row=0, column=1, columnspan=2, sticky="ew", pady=3)

        self._label(box, 1, "共享目录")
        entry = ttk.Entry(box, textvariable=self.root_var)
        entry.grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Button(box, text="浏览…", width=7,
                   command=self._browse_root).grid(row=1, column=2, padx=(6, 0))

        self._label(box, 2, "接收目录")
        entry = ttk.Entry(box, textvariable=self.receive_var)
        entry.grid(row=2, column=1, columnspan=2, sticky="ew", pady=3)
        ttk.Label(box, text="相对共享目录，或填写绝对路径",
                  foreground="#888888").grid(row=3, column=1, columnspan=2,
                                             sticky="w")

        self._label(box, 4, "设备 ID")
        ttk.Label(box, text=self.config.device_id or "-").grid(
            row=4, column=1, columnspan=2, sticky="w", pady=(8, 0))

    def _build_network_box(self):
        outer = ttk.Frame(self)
        outer.grid(row=0, column=1, sticky="nsew", pady=(0, 8))
        outer.columnconfigure(0, weight=1)

        net = ttk.LabelFrame(outer, text="网络", padding=10)
        net.grid(row=0, column=0, sticky="ew")
        net.columnconfigure(1, weight=1)

        self.port_var = tk.StringVar()
        self.discovery_var = tk.StringVar()
        self.auto_accept_var = tk.BooleanVar()

        self._label(net, 0, "端口")
        self.port_spin = tk.Spinbox(net, from_=1024, to=65535, width=8,
                                    textvariable=self.port_var)
        self.port_spin.grid(row=0, column=1, sticky="w", pady=3)

        self._label(net, 1, "发现方式")
        combo = ttk.Combobox(net, textvariable=self.discovery_var, width=10,
                             state="readonly",
                             values=("both", "udp", "mdns", "off"))
        combo.grid(row=1, column=1, sticky="w", pady=3)

        ttk.Checkbutton(net, text="自动接受新设备（配对）",
                        variable=self.auto_accept_var).grid(
            row=2, column=1, sticky="w", pady=3)

        trans = ttk.LabelFrame(outer, text="传输", padding=10)
        trans.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        trans.columnconfigure(1, weight=1)

        self.speed_var = tk.StringVar()
        self.chunk_var = tk.StringVar()
        self.allow_upload_var = tk.BooleanVar()
        self.allow_clip_var = tk.BooleanVar()

        self._label(trans, 0, "限速 (KiB/s)")
        self.speed_spin = tk.Spinbox(trans, from_=0, to=1048576, width=8,
                                     textvariable=self.speed_var)
        self.speed_spin.grid(row=0, column=1, sticky="w", pady=3)
        ttk.Label(trans, text="0 = 不限速", foreground="#888888").grid(
            row=0, column=2, sticky="w", padx=(6, 0))

        self._label(trans, 1, "分块 (KiB)")
        self.chunk_spin = tk.Spinbox(trans, from_=4, to=4096, width=8,
                                     textvariable=self.chunk_var)
        self.chunk_spin.grid(row=1, column=1, sticky="w", pady=3)

        ttk.Checkbutton(trans, text="允许接收推送", variable=self.allow_upload_var).grid(
            row=2, column=1, columnspan=2, sticky="w", pady=3)
        ttk.Checkbutton(trans, text="允许剪贴板同步", variable=self.allow_clip_var).grid(
            row=3, column=1, columnspan=2, sticky="w", pady=3)

        ui = ttk.LabelFrame(outer, text="界面", padding=10)
        ui.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ui.columnconfigure(1, weight=1)
        self.theme_var = tk.StringVar()
        self._label(ui, 0, "主题")
        ttk.Combobox(ui, textvariable=self.theme_var, width=10, state="readonly",
                     values=("dark", "light")).grid(row=0, column=1, sticky="w")

    def _build_footer(self):
        footer = ttk.Frame(self)
        footer.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        footer.columnconfigure(1, weight=1)

        ttk.Button(footer, text="保存", command=self.save).grid(
            row=0, column=0, sticky="w")

        self.status_var = tk.StringVar(value="")
        ttk.Label(footer, textvariable=self.status_var).grid(
            row=0, column=1, sticky="w", padx=12)

        about = ttk.Frame(footer)
        about.grid(row=0, column=2, sticky="e")
        self.version_label = ttk.Label(
            about, text="KeShare %s" % (KSAPP_VERSION,),
            foreground="#4a90d9", cursor="hand2")
        self.version_label.pack(anchor="e")
        self.version_label.bind("<Button-1>", self._on_version_click)
        self.hint_var = tk.StringVar(value="")
        ttk.Label(about, textvariable=self.hint_var,
                  foreground="#c08020").pack(anchor="e")

    @staticmethod
    def _label(parent, row, text):
        ttk.Label(parent, text=text).grid(row=row, column=0, sticky="w",
                                          padx=(0, 8), pady=3)

    # ------------------------------------------------------------------
    # data binding
    # ------------------------------------------------------------------
    def refresh(self):
        cfg = self.config
        self.name_var.set(cfg.device_name)
        self.root_var.set(cfg.share_root)
        self.receive_var.set(cfg.data["share"]["receive_dir"])
        self.port_var.set(str(cfg.port))
        self.discovery_var.set(cfg.discovery_mode)
        self.auto_accept_var.set(cfg.auto_accept_pairing)
        self.speed_var.set(str(cfg.max_speed_kbps))
        self.chunk_var.set(str(cfg.data["share"]["chunk_kb"]))
        self.allow_upload_var.set(cfg.allow_upload)
        self.allow_clip_var.set(cfg.allow_clipboard)
        self.theme_var.set(cfg.theme)
        self.status_var.set("")

    def _browse_root(self):
        folder = filedialog.askdirectory(initialdir=self.root_var.get() or os.path.expanduser("~"))
        if folder:
            self.root_var.set(folder.replace("\\", "/"))

    # ------------------------------------------------------------------
    # save
    # ------------------------------------------------------------------
    def save(self):
        errors = []
        name = self.name_var.get().strip()
        if not name:
            errors.append("设备名称不能为空")
        port = self._to_int(self.port_var.get(), 47377)
        if not (1 <= port <= 65535):
            errors.append("端口必须在 1-65535 之间")
        speed = self._to_int(self.speed_var.get(), 0)
        if speed < 0:
            errors.append("限速不能为负数")
        chunk = self._to_int(self.chunk_var.get(), 64)
        if not (4 <= chunk <= 4096):
            errors.append("分块大小必须在 4-4096 KiB 之间")
        root = self.root_var.get().strip()
        if not root:
            errors.append("共享目录不能为空")
        if errors:
            messagebox.showerror("设置无效", "\n".join(errors), parent=self)
            return False

        cfg = self.config
        cfg.set("device.name", name)
        cfg.set("share.root", root.replace("\\", "/"))
        cfg.set("share.receive_dir", self.receive_var.get().strip() or "Received")
        cfg.set("network.port", port)
        cfg.set("network.discovery", self.discovery_var.get() or "both")
        cfg.set("network.auto_accept_pairing", bool(self.auto_accept_var.get()))
        cfg.set("share.max_speed_kbps", speed)
        cfg.set("share.chunk_kb", chunk)
        cfg.set("share.allow_upload", bool(self.allow_upload_var.get()))
        cfg.set("share.allow_clipboard", bool(self.allow_clip_var.get()))
        cfg.set("ui.theme", self.theme_var.get() or "dark")
        cfg.normalise()
        try:
            cfg.save()
        except OSError as exc:
            messagebox.showerror("保存失败", "无法写入 %s\n%s" % (cfg.path, exc),
                                 parent=self)
            return False
        self.status_var.set("已保存 %s" % (cfg.path,))
        if self.on_save is not None:
            try:
                self.on_save(cfg)
            except Exception as exc:                      # pragma: no cover - UI
                self.log.error("应用设置失败: %s" % (exc,))
        return True

    @staticmethod
    def _to_int(text, default):
        try:
            return int(str(text).strip())
        except (TypeError, ValueError):
            return default

    # ------------------------------------------------------------------
    # eight-tap developer mode
    # ------------------------------------------------------------------
    def _on_version_click(self, event=None):
        self._taps += 1
        if self._reset_job is not None:
            try:
                self.after_cancel(self._reset_job)
            except Exception:
                pass
        self._reset_job = self.after(TAP_RESET_MS, self._reset_taps)

        if self._taps >= TAPS_TO_UNLOCK:
            self._unlock()
            return
        if self._taps >= HINT_AFTER:
            self.hint_var.set("再点 %d 次进入开发者模式" % (TAPS_TO_UNLOCK - self._taps,))
        else:
            self.hint_var.set("")

    def _reset_taps(self):
        self._reset_job = None
        self._taps = 0
        self.hint_var.set("")

    def _unlock(self):
        """8 连击后：先验证开发者密码，通过才真正解锁。"""
        self._taps = 0
        if self._reset_job is not None:
            try:
                self.after_cancel(self._reset_job)
            except Exception:
                pass
            self._reset_job = None

        from .. import devmode
        from .theme import PillButton

        dialog = tk.Toplevel(self)
        dialog.title("开发者模式")
        dialog.transient(self.winfo_toplevel())
        dialog.resizable(False, False)
        dialog.configure(bg=palette["bg"])
        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="需要开发者密码",
                  font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        ttk.Label(frame, text="密码包含大小写字母、数字与符号",
                  foreground="#888888").pack(anchor="w", pady=(4, 10))

        password_var = tk.StringVar()
        entry = ttk.Entry(frame, textvariable=password_var, show="•", width=34)
        entry.pack(fill="x")
        entry.focus_set()

        error_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=error_var, foreground="#d9534f").pack(anchor="w", pady=(6, 0))
        self._dev_attempts = getattr(self, "_dev_attempts", 0)

        def submit(event=None):
            if devmode.verify(password_var.get()):
                dialog.destroy()
                self._grant_developer()
                return
            self._dev_attempts += 1
            password_var.set("")
            if self._dev_attempts >= 5:
                dialog.destroy()
                self.hint_var.set("尝试次数过多，请重新连点")
                self.log.warn("开发者密码连续错误，已退出解锁流程")
                self._dev_attempts = 0
                return
            error_var.set("密码不正确（还可尝试 %d 次）" % (5 - self._dev_attempts,))

        entry.bind("<Return>", submit)

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(12, 0))
        ok = PillButton(buttons, self.palette if hasattr(self, "palette") else None,
                        "解锁", kind="primary")
        ok.command = submit
        ok.pack(side="left")
        cancel = PillButton(buttons, self.palette if hasattr(self, "palette") else None,
                            "取消", kind="ghost")
        cancel.command = dialog.destroy
        cancel.pack(side="left", padx=8)
        dialog.grab_set()

    def _grant_developer(self):
        self._dev_attempts = 0
        self.config.set("developer.unlocked", True)
        try:
            self.config.save()
        except OSError as exc:
            self.log.error("保存开发者模式失败: %s" % (exc,))
        self.hint_var.set("开发者模式已解锁")
        self.log.info("开发者模式已解锁")
        if self.on_unlock_dev is not None:
            self.on_unlock_dev()

            self.on_unlock_dev()
