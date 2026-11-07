# -*- coding: utf-8 -*-
"""Scrollable transfer list: one row (name, direction, progress bar) per file.

Rows are keyed by the 8 hex transfer id from the CTL messages, so both the
server-side and client-side events for the same transfer update the same row.
"""

import os
import tkinter as tk
from tkinter import ttk


def human_size(size):
    try:
        size = float(size)
    except (TypeError, ValueError):
        return "-"
    if size < 1024:
        return "%d B" % (int(size),)
    if size < 1024 * 1024:
        return "%.1f KiB" % (size / 1024.0,)
    if size < 1024 * 1024 * 1024:
        return "%.1f MiB" % (size / (1024.0 * 1024.0),)
    return "%.2f GiB" % (size / (1024.0 ** 3),)


class TransferPanel(ttk.Frame):
    """Scrollable list of transfers with a progress bar per transfer."""

    def __init__(self, master, palette):
        ttk.Frame.__init__(self, master, padding=(10, 8))
        self.palette = palette
        self.rows = {}

        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(0, 6))
        ttk.Label(toolbar, text="传输记录（双击打开所在文件夹）").pack(side="left")
        ttk.Button(toolbar, text="清空", command=self.clear).pack(side="right")

        outer = ttk.Frame(self)
        outer.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(outer, highlightthickness=0,
                                background=palette["bg"])
        scroll = ttk.Scrollbar(outer, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.inner.bind(
            "<Configure>",
            lambda event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind(
            "<Configure>",
            lambda event: self.canvas.itemconfigure(self._window, width=event.width))
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _on_wheel(self, event):
        try:
            self.canvas.yview_scroll(int(-event.delta / 120), "units")
        except Exception:
            pass

    # -- rows --------------------------------------------------------------
    def start(self, info):
        tid = str(info.get("id") or "")
        if not tid:
            return None
        row = self.rows.get(tid)
        if row is None:
            row = self._create_row(tid, info)
            self.rows[tid] = row
        row["name_var"].set(str(info.get("name") or "?"))
        row["direction_var"].set("接收" if info.get("direction") == "in" else "发送")
        size = int(info.get("size") or 0)
        row["total"] = size
        row["dest"] = info.get("dest") or row.get("dest")
        row["progress"]["maximum"] = max(size, 1)
        row["progress"]["value"] = 0
        row["status_var"].set("准备中" if not size else "0 / %s" % human_size(size))
        return row

    def _create_row(self, tid, info):
        frame = ttk.Frame(self.inner, padding=6)
        frame.pack(fill="x", expand=True, pady=1)
        frame.columnconfigure(2, weight=1)
        name_var = tk.StringVar(value=str(info.get("name") or "?"))
        direction_var = tk.StringVar(value="接收")
        status_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=name_var, width=34, anchor="w").grid(
            row=0, column=0, sticky="w")
        ttk.Label(frame, textvariable=direction_var, width=5,
                  foreground=self.palette["muted"]).grid(row=0, column=1, sticky="w")
        bar = ttk.Progressbar(frame, orient="horizontal", mode="determinate",
                              length=220, maximum=100, value=0)
        bar.grid(row=0, column=2, sticky="ew", padx=8)
        ttk.Label(frame, textvariable=status_var, width=18,
                  foreground=self.palette["muted"], anchor="e").grid(
            row=0, column=3, sticky="e")
        row = {"frame": frame, "name_var": name_var, "direction_var": direction_var,
               "status_var": status_var, "progress": bar, "total": 0,
               "dest": info.get("dest") or ""}
        frame.bind("<Double-1>", lambda event, r=row: self._open_row(r))
        return row

    def _open_row(self, row):
        dest = row.get("dest") or ""
        folder = os.path.dirname(os.path.abspath(dest)) if dest else ""
        if folder and os.path.isdir(folder):
            try:
                os.startfile(folder)
            except (OSError, AttributeError):
                pass

    def progress(self, tid, sent, total):
        row = self.rows.get(str(tid))
        if row is None:
            return
        total = int(total or row.get("total") or 0)
        row["total"] = total
        row["progress"]["maximum"] = max(total, 1)
        row["progress"]["value"] = min(int(sent or 0), max(total, 1))
        if total:
            row["status_var"].set("%s / %s" % (human_size(sent), human_size(total)))
        else:
            row["status_var"].set(human_size(sent))

    def finish(self, tid, ok, err="", dest="", digest="", size=None):
        row = self.rows.get(str(tid))
        if row is None:
            return
        if digest:
            row["digest"] = digest
        if ok:
            row["progress"]["value"] = row["progress"]["maximum"]
            row["status_var"].set("完成 %s" % human_size(
                size if size is not None else row.get("total") or 0))
        else:
            row["status_var"].set("失败: %s" % (err or "未知"))
        if dest:
            row["dest"] = dest

    def clear(self):
        for row in self.rows.values():
            row["frame"].destroy()
        self.rows = {}
