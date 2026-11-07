# -*- coding: utf-8 -*-
"""Haze 界面层：把 KeShare 五个页面全部改成玻璃控件（替换原生 ttk）。

设计对应你的规范
----------------
* 左侧边栏固定五项：设备 / 文件 / 传输 / 设置 / 关于
* 每个页面**只有一个主操作**放在右上角，其余收进列表右键菜单
* 列表用 GlassList：一行 = 主标题 + 副标题 + 右侧状态 + `›`，单击直接行动
* 空列表有明确空状态（标题 + 副文案 + 主按钮）
* 底部微弹窗（GlassToast）5 秒自动消失

这一层只负责"怎么画"，业务动作通过回调交给 KeShareApp。
"""
from __future__ import annotations

import time

from . import glasslib as G

PAGE_TITLES = {
    "devices": "附近设备",
    "files": "文件",
    "transfers": "传输任务",
    "settings": "设置",
    "about": "关于",
    "setting_detail": "设置项",
    "transfer_detail": "任务详情",
}

SIDEBAR_ITEMS = [
    {"glyph": "device", "title": "设备"},
    {"glyph": "files", "title": "文件"},
    {"glyph": "transfer", "title": "传输"},
    {"glyph": "settings", "title": "设置"},
    {"glyph": "about", "title": "关于"},
]
SIDEBAR_KEYS = ["devices", "files", "transfers", "settings", "about"]


class GlassToast(object):
    """底部微弹窗：从底部滑上来，5 秒自动消失，不打断操作。"""

    def __init__(self, app):
        self.app = app
        self.text = ""
        self.hint = ""
        self.born = 0.0
        self.level = "info"

    def show(self, text, hint="", level="info"):
        self.text = str(text)
        self.hint = str(hint)
        self.level = level
        self.born = time.time()
        self.app.invalidate()

    def alive(self):
        return self.text and (time.time() - self.born) < 5.0

    def draw(self, surface):
        if not self.alive():
            return
        app = self.app
        w = min(560, app._win_w - 340)
        h = 62 if self.hint else 46
        x = (app._win_w - w) / 2 + 116
        y = app._win_h - h - 22

        # 淡出
        remain = max(0.0, 5.0 - (time.time() - self.born))
        fade = min(1.0, remain / 0.6)

        color = {"info": G.GlassTheme.muted, "ok": G.GlassTheme.ok,
                 "warn": G.GlassTheme.warn, "error": G.GlassTheme.danger}.get(
                     self.level, G.GlassTheme.muted)
        surface.rounded((x, y, w, h), h / 2, fill=(16, 22, 34, int(232 * fade)))
        surface.rounded((x, y, w, h), h / 2, outline=color + (int(120 * fade),), width=1)
        surface.text((x + 22, y + (14 if self.hint else h / 2 - 8)), self.text, 12,
                     color + (int(255 * fade),), True)
        if self.hint:
            surface.text((x + 22, y + 34), self.hint, 10,
                         G.GlassTheme.muted + (int(210 * fade),))
        app.invalidate()


class HazeUI(object):
    """给 KeShareApp 用的界面 mixin。依赖 GlassApp 提供的 add/invalidate/roots。"""

    # ---------------------------------------------------------------- 骨架
    def _build_haze_ui(self):
        self.toast = GlassToast(self)
        self._current_page = None
        self._page_widgets = []
        self._haze_ready = True

        self.sidebar = G.GlassSidebar(self, (0, 0, 232, self._win_h),
                                      items=SIDEBAR_ITEMS,
                                      on_select=lambda i, item: self.show_haze_page(
                                          SIDEBAR_KEYS[i] if i < len(SIDEBAR_KEYS) else "devices"))
        self.add(self.sidebar)
        self.show_haze_page("devices")

    def _relayout_haze(self):
        """窗口尺寸变化时重排侧栏与新页面。"""
        if not getattr(self, "_haze_ready", False):
            return
        h = self._win_h
        self.sidebar.h = h
        self.sidebar.w = 232
        self.show_haze_page(self._current_page or "devices")

    def show_haze_page(self, key, param=None):
        """切换页面：清掉旧的页面控件，重建当前页。

        param 用于二级页（如设置项 key、传输任务 id）。
        """
        if key not in PAGE_TITLES:
            return
        self._current_page = key
        self._page_param = param
        try:
            self.sidebar.set_selected(SIDEBAR_KEYS.index(key), notify=False)
            self.sidebar.hover = -1          # 清掉 hover，避免与选中项同时高亮
        except (ValueError, AttributeError):
            pass

        for w in self._page_widgets:
            try:
                self.roots.remove(w)
            except ValueError:
                pass
            try:
                w.destroy()
            except Exception:
                pass
        self._page_widgets = []

        builder = getattr(self, "_page_%s" % key, None)
        if builder is not None:
            try:
                built = [w for w in builder() if w is not None]
            except Exception as exc:              # 页面构建失败不要让整个 UI 挂掉
                built = []
                try:
                    self.toast.show("页面加载失败", repr(exc), "error")
                except Exception:
                    pass
            self._page_widgets = built
            for w in built:                       # 关键：必须加入渲染树
                self.add(w)
        self.invalidate()

    # ---------------------------------------------------------------- 通用
    def _popup_menu(self, px, py, items):
        """在鼠标位置弹一个原生菜单（items = [(label, callback), ...]）。"""
        try:
            import tkinter as tk
            menu = tk.Menu(self, tearoff=0)
            for label, cb in items:
                if label == "-":
                    menu.add_separator()
                else:
                    menu.add_command(label=label, command=cb)
            menu.tk_popup(int(self.winfo_rootx() + px), int(self.winfo_rooty() + py))
        except Exception as exc:
            self.toast.show("菜单不可用", str(exc), "error")

    # ---------------------------------------------------------------- 公共块
    def _content_rect(self):
        return (264, 24, self._win_w - 264 - 28, self._win_h - 48)

    def _page_header(self, title, actions=(), subtitle=""):
        """页面标题卡片：大标题 + 右上角最多两个操作。"""
        x, y, w, h = self._content_rect()
        widgets = [G.GlassPanel(self, (x, y, w, 102), radius=28, solid_alpha=200)]
        widgets.append(G.GlassLabel(self, (x + 26, y + 22, w - 220, 32), title, 21,
                                    G.GlassTheme.fg, True))
        if subtitle:
            widgets.append(G.GlassLabel(self, (x + 26, y + 60, w - 220, 22), subtitle, 11,
                                        G.GlassTheme.muted))
        # 操作按钮从右往左排
        bx = x + w - 22
        for label, cb, accent in reversed(list(actions)):
            bw = max(78, len(label) * 14 + 34)
            bx -= bw
            widgets.append(G.GlassButton(self, (bx, y + 30, bw, 42), label, cb, accent=accent))
            bx -= 10
        return widgets

    def _page_list(self, rows, on_select=None, empty_text="暂无内容", empty_hint="",
                   header_height=126, on_context=None):
        x, y, w, h = self._content_rect()
        top = y + header_height
        panel = G.GlassPanel(self, (x, top, w, h - header_height), radius=28, solid_alpha=210)
        lst = G.GlassList(self, (x + 16, top + 16, w - 32, h - header_height - 32),
                          rows=rows, row_height=64, on_select=on_select,
                          empty_text=empty_text, empty_hint=empty_hint,
                          on_context=on_context)
        return [panel, lst]

    # ---------------------------------------------------------------- 设备页
    def _page_devices(self):
        peers = self._haze_peer_rows()
        code, hint = self._haze_status_code()
        widgets = self._page_header(
            "附近设备",
            actions=[("刷新", self._refresh_devices_clicked, False),
                     ("手动连接", self._manual_connect, True)],
            subtitle="%s  %s" % (code, hint),
        )
        widgets += self._page_list(
            peers, on_select=self._haze_device_selected,
            empty_text="附近没有设备",
            empty_hint="让电脑开热点 KeShare-XXXX，或用「手动连接」填 IP",
            on_context=self._haze_device_context)
        return widgets

    def _haze_status_code(self):
        """状态码条（替代自由文本），对应规范附录 A。"""
        try:
            peers = self.discovery.snapshot()
        except Exception:
            peers = []
        online = [p for p in peers if p.get("online")]
        if online:
            return "007", "%d 台设备在线" % len(online)
        if peers:
            return "010", "%d 台设备离线" % len(peers)
        return "001", "正在搜索附近设备…"

    def _haze_peer_rows(self):
        try:
            peers = self.discovery.snapshot()
        except Exception:
            peers = []
        rows = []
        for peer in peers:
            name = peer.get("name") or peer.get("ip") or "未知设备"
            kind = peer.get("kind") or ""
            osv = peer.get("osv") or ""
            ip = peer.get("ip") or ""
            port = peer.get("port") or ""
            state = "已连接" if peer.get("online") else "未连接"
            sub = " · ".join([p for p in (osv or kind, "%s:%s" % (ip, port) if ip else "") if p])
            rows.append({
                "glyph": "device",
                "title": name,
                "subtitle": sub or "地址未知",
                "right": state,
                "color": G.GlassTheme.ok if peer.get("online") else None,
                "_peer": peer,
            })
        return rows

    def _haze_device_selected(self, index, row):
        """单击一行直接发起连接（无需确认）。"""
        peer = row.get("_peer") if isinstance(row, dict) else None
        if not peer:
            return
        if not peer.get("ip"):
            self.toast.show("无法连接", "该设备没有可用的 WiFi 地址", "warn")
            return
        import threading
        self.toast.show("正在连接…", peer.get("name") or peer.get("ip") or "", "info")
        th = threading.Thread(target=self._connect_worker, args=(peer,), name="ks-connect")
        th.daemon = True
        th.start()

    def _haze_device_context(self, index, row, px, py):
        """设备行右键：复制 IP / 手动重连 / 移除。"""
        peer = (row or {}).get("_peer") or {}
        ip = peer.get("ip") or ""
        name = peer.get("name") or ip or "设备"

        def copy_ip():
            try:
                from .. import clipboard
                clipboard.copy_text(ip)
                self.toast.show("已复制 IP", ip, "ok")
            except Exception as exc:
                self.toast.show("复制失败", str(exc), "error")

        def reconnect():
            self._haze_device_selected(index, row)

        def remove():
            try:
                self.discovery.forget(peer.get("did"))
                self.toast.show("已移除", name, "warn")
                self.show_haze_page("devices")
            except Exception as exc:
                self.toast.show("移除失败", str(exc), "error")

        self._popup_menu(px, py, [
            ("复制 IP", copy_ip),
            ("手动重连", reconnect),
            "-",
            ("从列表移除", remove),
        ])

    # ---------------------------------------------------------------- 文件页
    def _page_files(self):
        widgets = self._page_header(
            "文件",
            actions=[("导入发送", self._haze_import_and_send, True),
                     ("打开目录", self._open_share_folder, False)],
            subtitle="接收目录：%s" % (getattr(self.config_obj, "receive_dir", "") or "—"),
        )
        widgets += self._page_list(
            self._haze_file_rows(), on_select=self._haze_file_selected,
            empty_text="还没有接收的文件",
            empty_hint="点右上角「导入发送」选择文件，或等对端推送",
            on_context=self._haze_file_context)
        return widgets

    def _haze_file_rows(self):
        rows = []
        # 文件页只做「已接收」：读接收目录，不是共享根
        import os as _os
        root = getattr(self.config_obj, "receive_dir", "") or ""
        entries = []
        try:
            for name in _os.listdir(root):
                full = _os.path.join(root, name)
                try:
                    st = _os.stat(full)
                except OSError:
                    continue
                entries.append({
                    "name": name,
                    "kind": "dir" if _os.path.isdir(full) else "file",
                    "size": int(st.st_size),
                    "mtime": int(st.st_mtime),
                })
            entries.sort(key=lambda e: (0 if e.get("kind") == "dir" else 1,
                                        (e.get("name") or "").lower()))
        except OSError:
            entries = []
        for entry in entries:
            name = entry.get("name") or ""
            if entry.get("kind") == "dir":
                rows.append({"glyph": "files", "title": name, "subtitle": "文件夹",
                             "right": "›", "_dir": True})
            else:
                rows.append({"glyph": "files", "title": name,
                             "subtitle": G.human_size(entry.get("size")),
                             "right": time.strftime("%m-%d %H:%M",
                                                    time.localtime(entry.get("mtime") or 0))})
        return rows

    def _haze_file_selected(self, index, row):
        if not isinstance(row, dict):
            return
        name = row.get("title") or ""
        rel = (self._browse_rel + "/" + name).lstrip("/") if self._browse_rel else name
        try:
            if row.get("_dir"):
                self._browse_rel = rel
                self.show_haze_page("files")
            else:
                self._haze_push_path(rel)
        except Exception as exc:
            self.toast.show("操作失败", str(exc), "error")

    def _haze_file_context(self, index, row, px, py):
        """文件行右键：发送 / 复制路径 / 在资源管理器打开 / 删除。"""
        name = (row or {}).get("title") or ""
        if not name:
            return
        rel = (self._browse_rel + "/" + name).lstrip("/") if self._browse_rel else name
        is_dir = bool((row or {}).get("_dir"))

        def send():
            self._haze_push_path(rel)

        def copy_path():
            try:
                from .. import clipboard
                clipboard.copy_text(_os.path.join(self.config_obj.receive_dir, name))
                self.toast.show("已复制路径", rel, "ok")
            except Exception as exc:
                self.toast.show("复制失败", str(exc), "error")

        def reveal():
            try:
                import subprocess
                path = _os.path.join(self.config_obj.receive_dir, name)
                flag = "/select," if not is_dir else ""
                subprocess.Popen(["explorer", flag + path])
            except Exception as exc:
                self.toast.show("打开失败", str(exc), "error")

        def delete():
            from tkinter import messagebox
            if not messagebox.askyesno("确认删除",
                                       "确定删除「%s」？此操作不可撤销。" % name,
                                       parent=self):
                return
            try:
                import os
                import shutil
                path = _os.path.join(self.config_obj.receive_dir, name)
                if is_dir:
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                self.toast.show("已删除", name, "warn")
                self.show_haze_page("files")
            except Exception as exc:
                self.toast.show("删除失败", str(exc), "error")

        items = []
        if not is_dir:
            items.append(("发送到已连接设备", send))
        items += [("复制完整路径", copy_path),
                  ("在资源管理器中显示", reveal),
                  "-",
                  ("删除", delete)]
        self._popup_menu(px, py, items)

    def _haze_push_path(self, rel_path):
        """把共享目录里的文件推给已连接设备；没有连接则提示。"""
        client = getattr(self, "_client", None)
        if client is None or not getattr(client, "connected", lambda: False)():
            self.toast.show("还没有连接设备", "先在「设备」页选一台", "warn")
            return
        try:
            self._push_path(rel_path)
            self.toast.show("已发送", rel_path.rsplit("/", 1)[-1], "ok")
        except Exception as exc:
            self.toast.show("发送失败", str(exc), "error")

    def _haze_import_and_send(self):
        try:
            self._import_files_and_send()
        except AttributeError:
            self.toast.show("导入不可用", "缺少导入实现", "error")
        except Exception as exc:
            self.toast.show("导入失败", str(exc), "error")

    # ---------------------------------------------------------------- 传输页
    def _page_transfers(self):
        widgets = self._page_header(
            "传输任务",
            actions=[("全部取消", self._haze_cancel_all, False)],
            subtitle=self._haze_transfer_summary(),
        )
        widgets += self._page_list(
            self._haze_transfer_rows(), on_select=self._haze_transfer_selected,
            empty_text="没有传输任务",
            empty_hint="从「文件」页推送文件，或在「设备」页接收对端推送",
            on_context=self._haze_transfer_context)
        return widgets

    def _haze_transfer_rows(self):
        rows = []
        try:
            tasks = [x.to_dict() for x in self.registry.items()]
        except Exception:
            tasks = []
        for t in tasks:
            done = int(t.get("done") or 0)
            total = int(t.get("size") or 0)
            pct = (100.0 * done / total) if total else 0.0
            direction = "接收" if t.get("direction") == "in" else "发送"
            rows.append({
                "glyph": "transfer",
                "title": t.get("name") or "未命名",
                "subtitle": "%s · %s / %s · %.0f%%" % (
                    direction, G.human_size(done), G.human_size(total), pct),
                "right": t.get("state") or "",
                "progress": (done / total) if total else 0.0,
                "_tid": t.get("id") or t.get("tid"),
                "_paused": bool(t.get("paused")),
            })
        return rows

    def _haze_transfer_selected(self, index, row):
        tid = (row or {}).get("_tid")
        if tid:
            self.show_haze_page("transfer_detail", tid)

    def _haze_transfer_context(self, index, row, px, py):
        """传输行右键：取消这个任务 / 复制文件名 / 打开所在目录。"""
        tid = (row or {}).get("_tid")
        name = (row or {}).get("title") or ""

        def cancel_one():
            try:
                self.registry.cancel(tid)
                self.toast.show("已取消", name, "warn")
                self.show_haze_page("transfers")
            except Exception as exc:
                self.toast.show("取消失败", str(exc), "error")

        def copy_name():
            try:
                from .. import clipboard
                clipboard.copy_text(name)
                self.toast.show("已复制文件名", name, "ok")
            except Exception as exc:
                self.toast.show("复制失败", str(exc), "error")

        def open_detail():
            self.show_haze_page("transfer_detail", tid)

        def toggle_pause():
            try:
                t = self.registry.get(tid)
                if t is None:
                    return
                if getattr(t, "paused", False):
                    self.registry.resume(tid)
                    self.toast.show("已继续", name, "ok")
                else:
                    self.registry.pause(tid)
                    self.toast.show("已暂停", name, "warn")
                self.show_haze_page("transfers")
            except Exception as exc:
                self.toast.show("操作失败", str(exc), "error")

        items = [("复制文件名", copy_name)]
        if tid:
            pause_label = "继续" if (row or {}).get("_paused") else "暂停"
            items = [("查看详情", open_detail),
                     (pause_label, toggle_pause),
                     "‑",
                     ("取消这个任务", cancel_one)] + items
        self._popup_menu(px, py, items)

    def _haze_transfer_summary(self):
        try:
            tasks = [x.to_dict() for x in self.registry.items()]
        except Exception:
            tasks = []
        active = [t for t in tasks if t.get("state") in ("running", "verifying")]
        return "%d 个进行中 / 共 %d 个" % (len(active), len(tasks))

    def _haze_cancel_all(self):
        try:
            for x in list(self.registry.items()):
                self.registry.cancel(x.tid)
            self.toast.show("已取消全部任务", "", "warn")
        except Exception as exc:
            self.toast.show("取消失败", str(exc), "error")
        self.show_haze_page("transfers")

    # ---------------------------------------------------------------- 设置页
    def _page_settings(self):
        rows = [
            {"glyph": "device", "title": "设备名称", "subtitle": self.config_obj.device_name,
             "right": "›"},
            {"glyph": "device", "title": "设备 ID", "subtitle": self.config_obj.device_id[:16],
             "right": "›"},
            {"glyph": "transfer", "title": "分块大小",
             "subtitle": "%d KB · 并发 %d" % (
                 int(self.config_obj.data["share"]["chunk_kb"]),
                 int(self.config_obj.data["share"].get("concurrency", 4))), "right": "›"},
            {"glyph": "settings", "title": "自动接受", "subtitle":
                "开" if self.config_obj.auto_accept else "关", "right": "›"},
            {"glyph": "files", "title": "接收目录",
             "subtitle": getattr(self.config_obj, "receive_dir", ""), "right": "›"},
            {"glyph": "about", "title": "版本", "subtitle": G.KS_DISPLAY if hasattr(G, "KS_DISPLAY") else "0.0.1 beta3", "right": "›"},
            {"glyph": "about", "title": "用户协议与隐私政策", "subtitle": "点击查看", "right": "›"},
        ]
        widgets = self._page_header("设置", subtitle="所有设置项都在这里")
        widgets += self._page_list(rows, on_select=self._haze_setting_selected,
                                   empty_text="没有设置项")
        return widgets

    def _haze_setting_selected(self, index, row):
        title = (row or {}).get("title") if isinstance(row, dict) else ""
        if title == "接收目录":
            self._open_share_folder()
            return
        if title == "用户协议与隐私政策":
            try:
                from .legal import present_if_needed
                present_if_needed(self)
            except Exception:
                self.toast.show("法律条款暂不可用", "", "warn")
            return
        self.show_haze_page("setting_detail", title)

    # ---------------------------------------------------------------- 设置子页
    # 字段定义：(显示名, config 路径, 类型, 提示)
    SETTING_FIELDS = {
        "设备名称": (("device", "name"), "str", "对端看到的名字"),
        "端口": (("network", "port"), "int", "默认 47377"),
        "分块大小": (("share", "chunk_kb"), "int", "单位 KB，默认 1024"),
        "并发": (("share", "concurrency"), "int", "同时传输的任务数"),
        "自动接受": (("share", "auto_accept"), "bool", "对端推送时不询问"),
        "断点保留": (("share", "resume_keep_days"), "int", "天，0 表示永不清理"),
    }

    def _setting_get(self, path):
        node = self.config_obj.data
        for key in path:
            node = node.get(key, {}) if isinstance(node, dict) else {}
        return node

    def _setting_set(self, path, value):
        node = self.config_obj.data
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = value

    def _page_setting_detail(self):
        title = self._page_param or ""
        spec = self.SETTING_FIELDS.get(title)
        x, y, w, h = self._content_rect()
        widgets = [G.GlassPanel(self, (x, y, w, 102), radius=28, solid_alpha=200)]
        widgets.append(G.GlassLabel(self, (x + 26, y + 22, w - 260, 32), title or "设置项",
                                    21, G.GlassTheme.fg, True))
        widgets.append(G.GlassButton(self, (x + w - 130, y + 30, 108, 42), "返回",
                                     lambda: self.show_haze_page("settings")))

        if spec is None:
            widgets.append(G.GlassPanel(self, (x, y + 126, w, 180), radius=28, solid_alpha=210))
            widgets.append(G.GlassLabel(self, (x + 26, y + 160, w - 52, 24),
                                        "该项暂不支持在这里编辑", 14, G.GlassTheme.muted))
            return widgets

        path, kind, hint = spec
        cur = self._setting_get(path)

        panel_top = y + 126
        widgets.append(G.GlassPanel(self, (x, panel_top, w, 230), radius=28, solid_alpha=210))
        widgets.append(G.GlassLabel(self, (x + 26, panel_top + 22, w - 52, 22),
                                    hint, 11, G.GlassTheme.muted))

        if kind == "bool":
            def toggle():
                new = not bool(self._setting_get(path))
                self._setting_set(path, new)
                self._save_settings()
                self.toast.show("已保存", "%s：%s" % (title, "开" if new else "关"), "ok")
                self.show_haze_page("setting_detail", title)

            label = "当前：%s → 点击切换" % ("开" if cur else "关")
            widgets.append(G.GlassButton(self, (x + 26, panel_top + 62, 240, 44),
                                         label, toggle, accent=bool(cur)))
        else:
            widgets.append(G.GlassInput(self, (x + 26, panel_top + 58, min(420, w - 52), 40),
                                        value=str(cur if cur is not None else ""),
                                        placeholder=hint))
            holder = {}

            def save():
                raw = holder["input"].text() if "input" in holder else ""
                try:
                    value = int(raw) if kind == "int" else str(raw).strip()
                except (TypeError, ValueError):
                    self.toast.show("保存失败", "请输入数字", "error")
                    return
                if kind == "int" and value < 0:
                    self.toast.show("保存失败", "不能为负数", "error")
                    return
                self._setting_set(path, value)
                self._save_settings()
                self.toast.show("已保存", "%s：%s" % (title, value), "ok")
                self.show_haze_page("setting_detail", title)

            btn = G.GlassButton(self, (x + 26, panel_top + 116, 120, 42), "保存", save,
                                accent=True)
            widgets.append(btn)
            # 把输入框引用挂到按钮上，供 save 读取
            inp = widgets[-2]
            if isinstance(inp, G.GlassInput):
                holder["input"] = inp

        widgets.append(G.GlassLabel(self, (x + 26, panel_top + 176, w - 52, 20),
                                    "配置保存在 settings.json，重启后仍然生效", 10,
                                    G.GlassTheme.muted))
        return widgets

    def _save_settings(self):
        try:
            self.config_obj.save()
        except Exception as exc:
            self.toast.show("保存到磁盘失败", str(exc), "error")

    # ---------------------------------------------------------------- 传输详情页
    def _page_transfer_detail(self):
        tid = self._page_param
        x, y, w, h = self._content_rect()
        info = {}
        try:
            t = self.registry.get(tid)
            if t is not None:
                info = t.to_dict()
        except Exception:
            info = {}

        name = info.get("name") or "未知任务"
        widgets = [G.GlassPanel(self, (x, y, w, 102), radius=28, solid_alpha=200)]
        short = name[:40] + ("…" if len(name) > 40 else "")
        widgets.append(G.GlassLabel(self, (x + 26, y + 22, w - 260, 32), short,
                                    21, G.GlassTheme.fg, True))
        widgets.append(G.GlassButton(self, (x + w - 130, y + 30, 108, 42), "返回",
                                     lambda: self.show_haze_page("transfers")))

        done = int(info.get("done") or info.get("received") or 0)
        total = int(info.get("size") or 0)
        ratio = (done / total) if total else 0.0
        state = info.get("state") or ("暂停" if info.get("paused") else "进行中")

        rows = [
            {"glyph": "transfer", "title": "方向",
             "subtitle": "接收" if info.get("direction") == "in" else "发送", "right": ""},
            {"glyph": "transfer", "title": "进度",
             "subtitle": "%s / %s（%.1f%%）" % (G.human_size(done), G.human_size(total),
                                               ratio * 100), "right": state},
            {"glyph": "transfer", "title": "状态", "subtitle": state, "right": ""},
            {"glyph": "files", "title": "保存位置", "subtitle": info.get("path") or "—",
             "right": ""},
        ]
        widgets += self._page_list(rows, empty_text="任务不存在",
                                   empty_hint="可能已完成并被清理")

        # 底部操作：暂停/继续、取消
        pan_top = y + h - 96
        widgets.append(G.GlassPanel(self, (x, pan_top, w, 76), radius=32, solid_alpha=220))
        action = "继续" if info.get("paused") else "暂停"

        def toggle_pause():
            try:
                if info.get("paused"):
                    self.registry.resume(tid)
                    self.toast.show("已继续", name, "ok")
                else:
                    self.registry.pause(tid)
                    self.toast.show("已暂停", name, "warn")
            except Exception as exc:
                self.toast.show("操作失败", str(exc), "error")
            self.show_haze_page("transfer_detail", tid)

        def cancel_one():
            from tkinter import messagebox
            if not messagebox.askyesno("确认取消", "取消「%s」？" % name, parent=self):
                return
            try:
                self.registry.cancel(tid)
                self.toast.show("已取消", name, "warn")
            except Exception as exc:
                self.toast.show("取消失败", str(exc), "error")
            self.show_haze_page("transfers")

        widgets.append(G.GlassButton(self, (x + 24, pan_top + 16, 110, 44), action,
                                     toggle_pause))
        widgets.append(G.GlassButton(self, (x + 146, pan_top + 16, 110, 44), "取消任务",
                                     cancel_one))
        return widgets

    # ---------------------------------------------------------------- 关于页
    def _page_about(self):
        rows = [
            {"glyph": "about", "title": "KeShare", "subtitle": "局域网文件互传", "right": ""},
            {"glyph": "about", "title": "版本", "subtitle": "0.0.1 beta3", "right": ""},
            {"glyph": "about", "title": "内核版本", "subtitle": "0.0.1-beta3+kernel.1", "right": ""},
            {"glyph": "about", "title": "协议版本", "subtitle": "KS/1", "right": ""},
            {"glyph": "about", "title": "完整性校验", "subtitle": "SHA-256", "right": ""},
            {"glyph": "about", "title": "视觉语言", "subtitle": "Haze（磨砂玻璃）", "right": ""},
        ]
        widgets = self._page_header("关于", subtitle="内核版本长按 10 秒可进入开发者模式")
        widgets += self._page_list(rows, empty_text="—")
        return widgets
