# -*- coding: utf-8 -*-
"""Remote file browser (uses the ``list`` CTL message).

Read-only window listing the connected peer's share root.  Double-click a
folder to descend, double-click a file to download it through the normal
``get`` flow (the main window owns the transfer list, so this window only calls
back with the chosen remote path).
"""

import os
import threading
import tkinter as tk
from tkinter import messagebox, ttk


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


def human_time(stamp):
    try:
        import time
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(stamp)))
    except (TypeError, ValueError, OSError):
        return "-"


class RemoteBrowser(tk.Toplevel):
    """Browse ``peer:/`` over the CTL ``list`` message."""

    def __init__(self, master, client, log, on_download=None, palette=None):
        tk.Toplevel.__init__(self, master)
        self.client = client
        self.log = log
        self.on_download = on_download
        self.palette = palette or {}
        self._entries = {}
        self._path = ""
        self._busy = False
        self._pending_path = ""
        self._pending_result = None

        self.title("远端文件 - %s" % (getattr(client, "peer_name", "") or "对端",))
        self.geometry("660x440")
        self.minsize(520, 320)
        self.transient(master)

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        bar = ttk.Frame(self, padding=(10, 8))
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(2, weight=1)
        ttk.Button(bar, text="上一级", width=7, command=self._go_up).grid(
            row=0, column=0)
        ttk.Button(bar, text="刷新", width=7,
                   command=lambda: self._load(self._path)).grid(row=0, column=1,
                                                               padx=4)
        self.path_var = tk.StringVar(value="/")
        ttk.Entry(bar, textvariable=self.path_var, state="readonly").grid(
            row=0, column=2, sticky="ew", padx=6)
        ttk.Button(bar, text="下载", width=7,
                   command=self._download_selected).grid(row=0, column=3)

        columns = ("name", "size", "mtime", "kind")
        self.tree = ttk.Treeview(self, columns=columns, show="headings",
                                 selectmode="browse")
        for column, title, width in (("name", "名称", 320), ("size", "大小", 100),
                                     ("mtime", "修改时间", 150),
                                     ("kind", "类型", 70)):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width,
                             anchor="w" if column == "name" else "center")
        self.tree.grid(row=1, column=0, sticky="nsew", padx=10)
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        scroll.grid(row=1, column=1, sticky="ns", padx=(0, 10))
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<Double-1>", self._on_double)

        self.status_var = tk.StringVar(value="正在读取…")
        ttk.Label(self, textvariable=self.status_var,
                  foreground=self.palette.get("muted", "#888888")).grid(
            row=2, column=0, columnspan=2, sticky="w", padx=12, pady=6)

        self._load("")

    # ------------------------------------------------------------------
    def _load(self, path):
        if self._busy:
            return
        self._busy = True
        self._pending_path = path
        self._pending_result = None
        self.status_var.set("正在读取 /%s …" % (path,))

        def worker():
            try:
                response = self.client.list_files(path)
            except Exception as exc:
                self._pending_result = ("error", str(exc))
            else:
                self._pending_result = ("ok", response.get("files") or [])

        thread = threading.Thread(target=worker, name="ks-remote-list")
        thread.daemon = True
        thread.start()
        self.after(100, self._poll_result)

    def _poll_result(self):
        result = self._pending_result
        if result is None:
            try:
                self.after(100, self._poll_result)
            except Exception:
                pass
            return
        self._pending_result = None
        self._busy = False
        self._show(self._pending_path, result)

    def _show(self, path, payload):
        state, data = payload
        if state == "error":
            self.status_var.set("读取失败: %s" % (data,))
            if self.log is not None:
                self.log.warn("远端列表失败: %s" % (data,))
            return
        self._path = path
        self.path_var.set("/" + path if path else "/")
        self.tree.delete(*self.tree.get_children())
        self._entries = {}
        for entry in data:
            iid = str(entry.get("path") or entry.get("name") or len(self._entries))
            self._entries[iid] = entry
            self.tree.insert(
                "", "end", iid=iid,
                values=(entry.get("name") or "", human_size(entry.get("size")),
                        human_time(entry.get("mtime")),
                        "文件夹" if entry.get("kind") == "dir" else "文件"))
        self.status_var.set("共 %d 项" % (len(data),))

    def _go_up(self):
        if not self._path:
            return
        parent = self._path.rsplit("/", 1)[0] if "/" in self._path else ""
        self._load(parent)

    def _selected(self):
        selection = self.tree.selection()
        if not selection:
            return None
        return self._entries.get(selection[0])

    def _on_double(self, event=None):
        entry = self._selected()
        if entry is None:
            return
        if entry.get("kind") == "dir":
            self._load(str(entry.get("path") or ""))
        else:
            self._download(entry)

    def _download_selected(self):
        entry = self._selected()
        if entry is None:
            messagebox.showinfo("提示", "请先选择一个文件", parent=self)
            return
        if entry.get("kind") == "dir":
            self._load(str(entry.get("path") or ""))
            return
        self._download(entry)

    def _download(self, entry):
        path = str(entry.get("path") or "")
        if not path:
            return
        if self.on_download is not None:
            try:
                self.on_download(path)
                self.status_var.set("已开始下载 %s" % (os.path.basename(path),))
            except Exception as exc:
                self.status_var.set("下载失败: %s" % (exc,))
                if self.log is not None:
                    self.log.warn("远端下载失败: %s" % (exc,))
