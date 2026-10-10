"""本地演示客户端的一键启动器。

做三件事：确认知识库里有多少语料、确认模型能不能连上、起服务并自动打开浏览器。

它不是生产入口——生产用 `uvicorn support_agent.main:app`。这里的价值在于双击即用：
端口被占会自动顺延，模型直连不通会自动尝试本机常见代理端口，启动失败会把堆栈留在窗口里
而不是一闪而过。
"""

from __future__ import annotations

import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
# 必须以项目根为工作目录：默认 SQLite 路径、.env 都是相对路径，否则会读到别处的库。
DATA_ROOT = Path(os.environ.get("SUPPORT_AGENT_DATA_DIR", ROOT)).resolve()
os.chdir(DATA_ROOT)
sys.path.insert(0, str(ROOT / "src"))

HOST = "127.0.0.1"
PORT_CANDIDATES = (8000, 8010, 8020, 8123, 8765)
# 常见的本地代理端口（Clash / V2Ray / Shadowsocks），直连失败时依次尝试。
LOCAL_PROXY_PORTS = (7897, 7890, 10809, 10808, 1080)


def pick_port() -> int:
    """返回第一个没有被监听的本机端口；连通性探测失败即视为可用。"""
    for port in PORT_CANDIDATES:
        with socket.socket() as probe:
            probe.settimeout(0.4)
            if probe.connect_ex((HOST, port)) != 0:
                return port
    return PORT_CANDIDATES[0]


def reachable(host: str, port: int, timeout: float = 2.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def corpus_summary() -> str:
    """不导入应用，直接读 SQLite 汇报语料规模，避免为了打印一行信息付出启动代价。"""
    database = DATA_ROOT / "support_agent.db"
    if not database.exists():
        return "本地知识库为空（还没有导入任何文档）"
    try:
        with sqlite3.connect(database) as connection:
            documents = connection.execute("SELECT count(*) FROM source_documents").fetchone()[0]
            chunks = connection.execute("SELECT count(*) FROM document_chunks").fetchone()[0]
        return f"{documents} 篇文档 / {chunks} 个片段"
    except sqlite3.Error as exc:
        return f"读取失败：{exc}"


def report_model(settings) -> None:
    """报告模型模式；远程模型连不上时自动挂本机代理，省掉一次「怎么又报错了」。"""
    if settings.privacy_routing_enabled:
        print("  资料分库：已开启；两类问答均可交由远程模型整理")
    if not settings.llm_enabled:
        print("  远程模型未启用，使用本地检索摘要")
        return

    parsed = urlparse(settings.llm_base_url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    if reachable(host, port):
        print(f"  模型模式：{settings.llm_model}（{host} 可直连）")
        return

    for proxy_port in LOCAL_PROXY_PORTS:
        if reachable(HOST, proxy_port, timeout=0.4):
            proxy = f"http://{HOST}:{proxy_port}"
            os.environ["HTTP_PROXY"] = os.environ["HTTPS_PROXY"] = proxy
            os.environ["NO_PROXY"] = "127.0.0.1,localhost,::1"
            print(f"  模型模式：{settings.llm_model}（直连不通，已自动走本机代理 {proxy}）")
            return

    print(f"  模型模式：{settings.llm_model}")
    print(f"  ！警告：{host}:{port} 直连不通，也没发现常见本地代理。")
    print("     若提问时报连接错误：设好 HTTPS_PROXY 再启动，")
    print("     或清空 .env 里的密钥，改用本地规则模型。")


def start_local_model(settings) -> subprocess.Popen | None:
    """本机配置了 Ollama 时，随客户端启动服务；已有服务则直接复用。"""
    if not settings.privacy_routing_enabled or not settings.local_llm_enabled:
        return None
    parsed = urlparse(settings.local_llm_base_url)
    if parsed.port != 11434 or reachable(HOST, 11434):
        return None
    configured = os.environ.get("OLLAMA_BIN")
    candidates = [
        Path(configured) if configured else None,
        Path(shutil.which("ollama")) if shutil.which("ollama") else None,
        ROOT.parents[1] / "tools" / "ollama" / "ollama.exe",
    ]
    binary = next((path for path in candidates if path and path.is_file()), None)
    if binary is None:
        print("  本地模型：未找到 Ollama 程序，请先安装或设置 OLLAMA_BIN。")
        return None
    process_env = os.environ.copy()
    process_env.setdefault("OLLAMA_MODELS", str(binary.parent / "models"))
    process_env["OLLAMA_HOST"] = "127.0.0.1:11434"
    process_env["OLLAMA_NO_CLOUD"] = "1"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    log_path = binary.parent / "server.launcher.log"
    with log_path.open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            [str(binary), "serve"], cwd=binary.parent, env=process_env,
            stdout=log, stderr=subprocess.STDOUT, creationflags=flags,
        )
    for _ in range(40):
        if reachable(HOST, 11434, timeout=0.1):
            return process
        if process.poll() is not None:
            print(f"  本地模型启动失败，查看日志：{log_path}")
            return None
        time.sleep(0.2)
    print(f"  本地模型尚未就绪，查看日志：{log_path}")
    return process


CONSOLE_TITLE = "光伏电站技术支持 Agent"


def set_console_title(title: str) -> None:
    """把控制台标题设成中文。

    标题不写在 .cmd 里：批处理文件只能用 ASCII，中文一进去就会被 cmd 按错误代码页
    解析并报错。这里用宽字符接口设置，不受代码页影响。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleTitleW(title)
    except (AttributeError, OSError):
        pass


def open_browser_later(url: str) -> None:
    """等服务真正起来再开浏览器，否则会先看到连接被拒绝。"""
    time.sleep(1.5)
    webbrowser.open(url)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import uvicorn

    from support_agent.config import get_settings
    from support_agent.main import app

    settings = get_settings()
    local_process = start_local_model(settings)
    port = pick_port()
    url = f"http://{HOST}:{port}"
    set_console_title(CONSOLE_TITLE)

    print("=" * 62)
    print("  光伏电站技术支持 Agent · 本地客户端")
    print("=" * 62)
    print(f"  知识库：{corpus_summary()}")
    report_model(settings)
    print(f"  访问地址：{url}")
    print("  停止服务：在本窗口按 Ctrl+C，或直接关闭窗口。")
    print("=" * 62)

    # 设 SUPPORT_AGENT_NO_BROWSER=1 可只起服务不开浏览器（自动化验证时用）。
    if not os.environ.get("SUPPORT_AGENT_NO_BROWSER"):
        threading.Thread(target=open_browser_later, args=(url,), daemon=True).start()

    try:
        uvicorn.run(app, host=HOST, port=port, log_level="info", access_log=False)
    finally:
        if local_process and local_process.poll() is None:
            local_process.terminate()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已停止。")
    except Exception:
        # 双击启动时窗口会随进程结束而关闭，必须把原因留住。
        traceback.print_exc()
        input("\n启动失败，按回车键关闭窗口…")
