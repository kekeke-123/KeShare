# -*- coding: utf-8 -*-
"""Developer tab, unlocked by the 8-tap gesture on the version label.

Exports
-------
* 导出日志      ring buffer as TXT
* 导出配置      settings dict as pretty JSON
* 导出调试参数  pid / python / platform / cwd / config path / IPs / sockets / threads

Toggles
-------
* 详细日志            ``developer.verbose_logging`` (DEBUG lines reach the ring)
* 显示协议调试帧      CTL/DATA frame tracing (``log.protocol_debug``)
* 日志条数            ring buffer size (``developer.log_ring_size``)

The raw panel at the bottom always shows the last ~200 lines.
"""

import json
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import KSAPP_NAME, KSAPP_VERSION, diag


class DevTab(ttk.Frame):
    def __init__(self, master, config, log, on_change=None, server=None):
        ttk.Frame.__init__(self, master, padding=10)
        self.config = config
        self.log = log
        self.on_change = on_change
        self.server = server
        self._build()

    # ------------------------------------------------------------------
    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        export = ttk.LabelFrame(self, text="导出", padding=8)
        export.grid(row=0, column=0, sticky="ew")
        ttk.Button(export, text="导出日志 (TXT)",
                   command=self.export_log).pack(side="left", padx=4)
        ttk.Button(export, text="导出配置 (JSON)",
                   command=self.export_config).pack(side="left", padx=4)
        ttk.Button(export, text="导出调试参数 (JSON)",
                   command=self.export_debug).pack(side="left", padx=4)

        options = ttk.LabelFrame(self, text="调试选项", padding=8)
        options.grid(row=1, column=0, sticky="ew", pady=(8, 8))

        self.verbose_var = tk.BooleanVar(value=self.config.verbose_logging)
        self.frames_var = tk.BooleanVar(value=self.config.verbose_logging)
        self.ring_var = tk.StringVar(value=str(self.config.log_ring_size))

        ttk.Checkbutton(options, text="详细日志", variable=self.verbose_var,
                        command=self._apply_toggles).grid(row=0, column=0,
                                                          sticky="w", padx=(0, 16))
        ttk.Checkbutton(options, text="显示协议调试帧", variable=self.frames_var,
                        command=self._apply_toggles).grid(row=0, column=1,
                                                          sticky="w", padx=(0, 16))
        ttk.Label(options, text="日志条数").grid(row=0, column=2, padx=(0, 6))
        spin = tk.Spinbox(options, from_=100, to=100000, width=8,
                          textvariable=self.ring_var, command=self._apply_toggles)
        spin.grid(row=0, column=3, sticky="w")
        spin.bind("<Return>", lambda event: self._apply_toggles())
        spin.bind("<FocusOut>", lambda event: self._apply_toggles())

        panel = ttk.LabelFrame(self, text="调试（最近 200 行）", padding=6)
        panel.grid(row=2, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(0, weight=1)

        self.text = tk.Text(panel, height=16, wrap="none", state="disabled",
                            background="#111417", foreground="#d0d0d0",
                            insertbackground="#d0d0d0", relief="flat")
        self.text.grid(row=0, column=0, sticky="nsew")
        scroll_y = ttk.Scrollbar(panel, orient="vertical", command=self.text.yview)
        scroll_y.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=scroll_y.set)

        bar = ttk.Frame(panel)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(bar, text="刷新", command=self.refresh).pack(side="left")
        ttk.Button(bar, text="清空缓冲", command=self._clear).pack(side="left", padx=6)
        self.status_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.status_var).pack(side="right")

        self.refresh()

    # ------------------------------------------------------------------
    def refresh(self):
        lines = self.log.lines(200)
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("end", "\n".join(lines))
        self.text.see("end")
        self.text.configure(state="disabled")
        self.status_var.set("%d 行 / 缓冲 %d" % (len(lines), self.log.ring_size))

    def _clear(self):
        self.log.clear()
        self.refresh()

    def _apply_toggles(self):
        verbose = bool(self.verbose_var.get())
        self.config.set("developer.verbose_logging", verbose)
        try:
            ring = int(self.ring_var.get())
        except (TypeError, ValueError):
            ring = self.config.log_ring_size
        self.config.set("developer.log_ring_size", max(100, min(ring, 100000)))
        self.config.normalise()
        self.log.set_verbose(verbose)
        # "显示协议调试帧" is runtime-only (it must not add keys to settings.json).
        self.log.set_protocol_debug(bool(self.frames_var.get()))
        self.log.set_ring_size(self.config.log_ring_size)
        try:
            self.config.save()
        except OSError as exc:
            self.log.error("保存调试选项失败: %s" % (exc,))
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                pass
        self.refresh()

    # ------------------------------------------------------------------
    # exports
    # ------------------------------------------------------------------
    def _ask_path(self, suffix, kind, default_name):
        return filedialog.asksaveasfilename(
            parent=self,
            title="导出%s" % (kind,),
            defaultextension=suffix,
            initialfile=default_name,
            filetypes=[(kind, "*" + suffix), ("全部文件", "*.*")],
        )

    def export_log(self):
        path = self._ask_path(".txt", "日志", "keshare-log.txt")
        if not path:
            return
        try:
            self.log.export_txt(path)
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)
            return
        self.status_var.set("日志已导出: %s" % (path,))

    def export_config(self):
        path = self._ask_path(".json", "配置", "keshare-settings.json")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(self.config.as_dict(), handle, ensure_ascii=False,
                          indent=2, sort_keys=True)
                handle.write("\n")
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)
            return
        self.status_var.set("配置已导出: %s" % (path,))

    def export_debug(self):
        path = self._ask_path(".json", "调试参数", "keshare-debug.json")
        if not path:
            return
        info = diag.process_info(config=self.config, logger=self.log,
                                 server=self.server)
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(info, handle, ensure_ascii=False, indent=2,
                          sort_keys=True)
                handle.write("\n")
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)
            return
        self.status_var.set("调试参数已导出: %s" % (path,))

    # ------------------------------------------------------------------
    def about_text(self):
        return "%s %s (Python %s)" % (KSAPP_NAME, KSAPP_VERSION,
                                      sys.version.split()[0])
