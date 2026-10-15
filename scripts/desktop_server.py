"""Run the desktop's local API independently of the Windows GUI runtime."""

import faulthandler
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(os.environ.get("SUPPORT_AGENT_DATA_DIR", ROOT)).resolve()
sys.path[:0] = [str(ROOT / "src"), str(ROOT / ".localdeps")]


def run(port: int, token: str, parent_pid: int):
    os.chdir(DATA_ROOT)
    began = time.monotonic()
    folder = DATA_ROOT / ".desktop"
    (DATA_ROOT / ".logs").mkdir(parents=True, exist_ok=True)
    diagnostic = (DATA_ROOT / ".logs/startup-stacks.log").open("a", encoding="utf-8")
    faulthandler.dump_traceback_later(10, repeat=True, file=diagnostic)
    from support_agent.desktop_runtime import prefer_python_sqlalchemy

    prefer_python_sqlalchemy()
    try:
        print("Desktop API stage: import uvicorn", flush=True)
        import uvicorn

        from support_agent import __version__
        print("Desktop API stage: import settings", flush=True)
        from support_agent.config import get_settings
        print("Desktop API stage: import application", flush=True)
        from support_agent.main import app
        from support_agent.services.runtime_identity import runtime_identity
    finally:
        faulthandler.cancel_dump_traceback_later()
        diagnostic.close()

    state = {"pid": os.getpid(), "token": token, "version": __version__,
             "instance": runtime_identity(get_settings()), "port": port,
             "startup_seconds": round(time.monotonic() - began, 3), "sqlalchemy_mode": "python"}
    partial = folder / ("service-" + token + ".partial")
    partial.write_text(json.dumps(state), encoding="utf-8")
    partial.replace(folder / "service-ready.json")
    print(f"Desktop API imports: {time.monotonic() - began:.2f}s", flush=True)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, access_log=False))
    finished = threading.Event()

    def monitor_parent():
        handle = None
        if sys.platform == "win32":
            import ctypes
            kernel = ctypes.windll.kernel32
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel.OpenProcess(0x00100000, False, parent_pid)
            if not handle:
                server.should_exit = True
                return
        try:
            while not finished.wait(.4):
                try:
                    stop = (folder / "service-stop.request").read_text("ascii") == token
                except FileNotFoundError:
                    stop = False
                parent_gone = (kernel.WaitForSingleObject(handle, 0) == 0) if handle else (
                    os.getppid() != parent_pid
                )
                if stop or parent_gone:
                    server.should_exit = True
                    return
        finally:
            if handle:
                kernel.CloseHandle(handle)

    watcher = threading.Thread(target=monitor_parent, daemon=True)
    watcher.start()
    try:
        server.run()
    finally:
        finished.set()
        watcher.join(timeout=2)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--parent", type=int, required=True)
    args = parser.parse_args()
    run(args.port, args.token, args.parent)
