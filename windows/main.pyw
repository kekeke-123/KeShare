# -*- coding: utf-8 -*-
"""KeShare entry point -- works as ``python main.pyw`` or ``python main.py``.

Starts the TCP listener (CTL + DATA on port 47377), peer discovery, and the
tkinter window.  ``--no-ui`` runs the same services headless (useful for a
machine that only serves files), ``--version`` prints the version.
"""

import os
import sys


def _bootstrap():
    base = os.path.dirname(os.path.abspath(__file__))
    if base and base not in sys.path:
        sys.path.insert(0, base)


def _message_box(title, text):
    """Show a native message box (works under pythonw where there is no console)."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, str(text), str(title), 0x10)
        return
    except Exception:
        pass
    try:
        sys.stderr.write("%s: %s\n" % (title, text))
    except Exception:
        pass


def main(argv=None):
    _bootstrap()
    args = list(sys.argv[1:] if argv is None else argv)

    from keshare import KSAPP_NAME, KSAPP_VERSION
    if "--version" in args or "-V" in args:
        sys.stdout.write("%s %s\n" % (KSAPP_NAME, KSAPP_VERSION))
        return 0

    from keshare import config as ksconfig
    from keshare import data as ksdata
    from keshare import log as kslog
    from keshare.discovery import DiscoveryService
    from keshare.server import KSServer, ServerError

    try:
        config = ksconfig.Config.load()
    except Exception as exc:
        _message_box("KeShare 启动失败", "无法读取配置: %s" % (exc,))
        return 1

    logger = kslog.Log(path=os.path.join(ksconfig.app_dir(), "keshare.log"),
                       ring_size=config.log_ring_size,
                       verbose=config.verbose_logging or ("--debug" in args))
    kslog.set_log(logger)
    if "--debug" in args:
        logger.set_verbose(True)
    config.ensure_dirs()

    registry = ksdata.TransferRegistry()
    server = KSServer(config, logger, registry=registry)
    try:
        server.start()
    except ServerError as exc:
        logger.error("服务启动失败: %s" % (exc,))
        _message_box(
            "服务未能启动",
            "%s\n\n请检查端口 %s 是否被占用，或在“设置”中更换端口后保存。"
            % (exc, config.port))
        # The UI can still start; saving a new port rebinds the listener.

    discovery = DiscoveryService(config, logger)
    try:
        discovery.start()
    except Exception as exc:
        logger.warn("发现服务启动失败: %s" % (exc,))

    if "--no-ui" in args:
        import time
        logger.info("以无界面模式运行（Ctrl+C 退出）")
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass
        finally:
            discovery.stop()
            server.stop()
        return 0

    try:
        from keshare.ui.app import KeShareApp
    except Exception as exc:
        logger.error("无法加载界面（tkinter 不可用？）: %s" % (exc,))
        _message_box("无法启动界面", "tkinter 不可用: %s" % (exc,))
        discovery.stop()
        server.stop()
        return 2

    app = KeShareApp(config, logger, server, discovery, registry)
    # Wire the service callbacks to the freshly created UI event queue.
    server.on_event = app.post
    server.resolve_ip = app._resolve_ip
    try:
        app.mainloop()
    finally:
        try:
            discovery.stop()
        except Exception:
            pass
        try:
            server.stop()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:                              # pragma: no cover - last resort
        import traceback
        traceback.print_exc()
        _message_box("KeShare 意外退出", str(exc))
        raise SystemExit(1)
