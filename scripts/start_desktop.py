"""Run the local assistant in a persistent Windows WebView2 desktop window."""

from __future__ import annotations

import html
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(os.environ.get("SUPPORT_AGENT_DATA_DIR", ROOT)).resolve()
sys.path[:0] = [str(ROOT / "src"), str(ROOT / ".localdeps")]

LOADING_HTML = """<!doctype html><meta charset="utf-8"><title>正在启动</title>
<body style="background:#eef3f8;color:#18334a;font:18px Microsoft YaHei;padding:15vh 12vw">
<h1>光伏技术支持</h1><p>正在加载本机服务，请稍候…</p>
<p>首次启动需要准备运行环境，无需重复双击。窗口加载完成后会自动进入登录页面。</p>
<p id="elapsed">已等待 0 秒</p><script>const began=Date.now();setInterval(()=>
document.getElementById('elapsed').textContent='已等待 '+
Math.floor((Date.now()-began)/1000)+' 秒',1000);</script></body>"""


def request_activation(data_root=DATA_ROOT):
    folder = data_root / ".desktop"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "activate.request").write_text(secrets.token_hex(16), encoding="ascii")


def activate_window(window):
    window.restore()
    window.show()


def ready_version(url: str, instance: str | None = None) -> str | None:
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(url + "/ready", timeout=1) as response:
            payload = json.load(response)
        return payload.get("version") if payload.get("status") == "ready" and (
            instance is None or payload.get("instance") == instance
        ) else None
    except (OSError, ValueError):
        return None


def main() -> int:
    began = time.monotonic()
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    os.chdir(DATA_ROOT)
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SmartPVSupport.Desktop")
    import webview

    from support_agent import __version__
    profile = DATA_ROOT / ".desktop" / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    webview.settings["ALLOW_DOWNLOADS"] = True
    window = webview.create_window(
        f"光伏技术支持 · v{__version__}", html=LOADING_HTML,
        width=1320, height=900, min_size=(900, 650), background_color="#ffffff",
    )
    def loaded(*_args):
        try:
            state = window.evaluate_js(
                "document.getElementById('opsScreen') && "
                "({title:document.title,adminPosition:getComputedStyle("
                "document.getElementById('opsScreen')).position,"
                "usersPosition:getComputedStyle(document.getElementById('usersScreen')).position})"
            )
            if not state or not url:
                return
            (profile.parent / "window-ready.json").write_text(
                json.dumps({"version": __version__, "url": url, "page": state,
                            "pid": os.getpid()},
                           ensure_ascii=False), encoding="utf-8",
            )
            stage("ready")
        except Exception:
            # Readiness telemetry must never prevent the desktop window opening.
            pass

    window.events.loaded += loaded
    stopping = threading.Event()
    server = None
    url = None
    port = None
    launch_token = secrets.token_hex(16)
    (DATA_ROOT / ".logs").mkdir(parents=True, exist_ok=True)
    service_log = (DATA_ROOT / ".logs/desktop-service.log").open("a", encoding="utf-8")

    # Ignore requests left by previous runs. Never wait on a window that has
    # not been shown yet: pywebview's API wrappers otherwise block for 20s.
    try:
        initial_activation = (profile.parent / "activate.request").read_text("ascii")
    except FileNotFoundError:
        initial_activation = None

    def stage(value):
        print(f"Desktop startup: {value} ({time.monotonic() - began:.1f}s)", flush=True)
        (profile.parent / "startup.json").write_text(json.dumps({
            "version": __version__, "pid": os.getpid(), "stage": value,
            "elapsed_seconds": round(time.monotonic() - began, 2),
        }), encoding="utf-8")

    def launch_service():
        interpreter = Path(sys.executable)
        if interpreter.name.lower() == "pythonw.exe":
            interpreter = interpreter.with_name("python.exe")
        env = os.environ.copy()
        env["SUPPORT_AGENT_DATA_DIR"] = str(DATA_ROOT)
        env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + str(ROOT / ".localdeps")
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONNOUSERSITE"] = "1"
        env.pop("PYTHONHOME", None)
        return subprocess.Popen([
            str(interpreter), "-u", str(ROOT / "scripts/desktop_server.py"),
            "--port", str(port), "--token", launch_token, "--parent", str(os.getpid()),
        ], cwd=DATA_ROOT, env=env, stdout=service_log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)

    def initialize():
        nonlocal server, url, port
        try:
            # pythonw can inherit a hidden startup flag from a batch launcher.
            # Restore/show explicitly so the first launch is visible as well.
            stage("loading_service")
            from start_client import HOST, pick_port, reachable

            if stopping.is_set():
                return
            port = pick_port()
            if reachable(HOST, port):
                raise RuntimeError("本机服务端口均被占用，请关闭旧启动窗口后重试。")
            target = f"http://{HOST}:{port}"
            server = launch_service()
            stage("waiting_service")
            deadline = time.monotonic() + 180
            while not stopping.is_set():
                if server.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("本机服务启动失败，请查看 .logs/desktop-service.log。")
                try:
                    state = json.loads((profile.parent / "service-ready.json").read_text("utf-8"))
                    owned = state["token"] == launch_token and state["pid"] == server.pid
                    if owned and ready_version(target, state["instance"]) == __version__:
                        break
                except (OSError, ValueError, KeyError):
                    pass
                stopping.wait(.25)
            if not stopping.is_set():
                url = target
                stage("loading_page")
                window.load_url(target + "/?v=" + __version__)
        except Exception:
            import traceback

            print(traceback.format_exc(), flush=True)
            stage("failed")
            if server:
                (profile.parent / "service-stop.request").write_text(launch_token, encoding="ascii")
            if not stopping.is_set():
                window.load_html('<meta charset="utf-8"><h2>启动失败</h2><p>请查看日志：</p><pre>'
                                 + html.escape(str(DATA_ROOT / ".logs/desktop-service.log"))
                                 + "</pre><p>请关闭窗口后重新打开。</p>")

    def shown(*_args):
        activate_window(window)

    window.events.shown += shown

    def watch_service():
        nonlocal server
        restarts = 0
        activation = initial_activation
        while not stopping.wait(.4):
            try:
                requested = (profile.parent / "activate.request").read_text("ascii")
                if requested != activation and window.events.shown.is_set():
                    activate_window(window)
                    activation = requested
                    (profile.parent / "activation.json").write_text(json.dumps({
                        "pid": os.getpid(), "request": requested,
                    }), encoding="utf-8")
            except FileNotFoundError:
                pass
            except Exception as exc:
                print("Desktop activation failed:", exc, flush=True)
            if url and server and server.poll() is not None and restarts < 3:
                restarts += 1
                print("Local service exited; restarting", restarts)
                server = launch_service()

    threading.Thread(target=watch_service, daemon=True).start()
    try:
        # Authentication and authorization remain in the local API. No Python
        # bridge is exposed to page JavaScript.
        webview.start(initialize, gui="edgechromium", private_mode=False, storage_path=str(profile),
                      icon=str(ROOT / "static/app-icon.ico"))
    finally:
        stopping.set()
        if server:
            (profile.parent / "service-stop.request").write_text(launch_token, encoding="ascii")
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.terminate()
                server.wait(timeout=5)
        service_log.close()
    return 0


if __name__ == "__main__":
    import traceback

    try:
        # Batch launchers may leave non-null but closed console handles behind.
        # Always own the log stream instead of inheriting those handles.
        log_dir = DATA_ROOT / ".logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stream = (log_dir / "desktop.log").open("a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = stream
        from contextlib import ExitStack

        from support_agent.services.file_lock import file_lock

        with ExitStack() as stack:
            try:
                stack.enter_context(file_lock(DATA_ROOT / ".desktop" / "instance.lock"))
            except OSError as exc:
                if getattr(exc, "errno", None) in {13, 11}:
                    request_activation()
                    raise SystemExit(0) from exc
                raise
            raise SystemExit(main())
    except Exception:
        log = DATA_ROOT / ".logs" / "desktop.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as stream:
            stream.write(traceback.format_exc())
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                0, f"客户端启动失败。请查看日志：{log}", "光伏技术支持", 0x10,
            )
        raise
