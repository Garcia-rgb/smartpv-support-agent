"""Run the local assistant in a persistent Windows WebView2 desktop window."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / ".localdeps")]


def ready_version(url: str) -> str | None:
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(url + "/ready", timeout=1) as response:
            payload = json.load(response)
        return payload.get("version") if payload.get("status") == "ready" else None
    except (OSError, ValueError):
        return None


def main() -> int:
    os.chdir(ROOT)
    import webview
    from start_client import HOST, pick_port, reachable

    from support_agent import __version__

    server = None
    url = f"http://{HOST}:8000"
    if ready_version(url) != __version__:
        import uvicorn

        from support_agent.main import app

        port = pick_port()
        if reachable(HOST, port):
            raise RuntimeError("本机服务端口均被占用，请关闭旧启动窗口后重试。")
        url = f"http://{HOST}:{port}"
        server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=port, access_log=False))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 45
        while ready_version(url) != __version__:
            if not thread.is_alive() or time.monotonic() > deadline:
                server.should_exit = True
                raise RuntimeError("本机服务启动失败，请查看 .logs/desktop.log。")
            time.sleep(.25)
    profile = ROOT / ".desktop" / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    webview.settings["ALLOW_DOWNLOADS"] = True
    window = webview.create_window(
        f"光伏技术支持 · v{__version__}", url,
        width=1320, height=900, min_size=(900, 650), background_color="#ffffff",
    )
    def loaded(*_args):
        try:
            state = window.evaluate_js(
                "({title:document.title,adminPosition:getComputedStyle("
                "document.getElementById('opsScreen')).position,"
                "usersPosition:getComputedStyle(document.getElementById('usersScreen')).position})"
            )
            (profile.parent / "window-ready.json").write_text(
                json.dumps({"version": __version__, "url": url, "page": state},
                           ensure_ascii=False), encoding="utf-8",
            )
        except Exception:
            # Readiness telemetry must never prevent the desktop window opening.
            pass

    window.events.loaded += loaded
    try:
        # Authentication and authorization remain in the local API. No Python
        # bridge is exposed to page JavaScript.
        webview.start(gui="edgechromium", private_mode=False, storage_path=str(profile))
    finally:
        if server:
            server.should_exit = True
            thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    import traceback

    try:
        if sys.stdout is None or sys.stderr is None:
            log_dir = ROOT / ".logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            stream = (log_dir / "desktop.log").open("a", encoding="utf-8", buffering=1)
            sys.stdout = sys.stderr = stream
        raise SystemExit(main())
    except Exception:
        log = ROOT / ".logs" / "desktop.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(traceback.format_exc(), encoding="utf-8")
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                0, f"客户端启动失败。请查看日志：{log}", "光伏技术支持", 0x10,
            )
        raise
