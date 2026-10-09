"""在桌面创建一键启动的本地客户端入口。

先说为什么不生成 .lnk：Windows 的快捷方式除了 LinkInfo，还要求带一段
LinkTargetIDList（shell 命名空间的二进制目录链）。缺了它，文件在资源管理器里
看着像快捷方式，ShellExecute 却会直接以「没有应用程序与此操作的指定文件有关联」
拒绝执行。而构造 IDList 需要手写 shell 的 ITEMID 结构，容易出错且难验证；
正规途径 WScript.Shell COM 在受限环境里又未必可用。

所以这里退一步：直接在桌面放一个双击即用的启动脚本。功能与快捷方式完全一致
（双击启动独立桌面窗口），而且是纯文本，看得见、改得动。

注意启动脚本内容必须是纯 ASCII：cmd.exe 解析批处理时用的是「读取该行时的代码页」，
而文件自身的编码是固定的，两边的中文一旦对不上就会报「命令语法不正确」。
所以窗口标题和交互提示都不写在批处理里——放弃的只是标题，换来的是任何代码页下都能跑。

用法：python scripts/create_desktop_launcher.py
重复执行会覆盖同名文件。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_NAME = "光伏电站技术支持 Agent.cmd"

TEMPLATE = r"""@echo off
chcp 65001 >nul
title PV Station Support Agent
cd /d "{root}"
set "PYTHONPATH={root}\src;{root}\.localdeps"
start "" /B "{python}" "{script}"
exit /b
"""


def desktop_directory() -> Path:
    """读注册表拿桌面路径，能正确处理被 OneDrive 重定向的情况。"""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
        ) as key:
            return Path(winreg.QueryValueEx(key, "Desktop")[0])
    except OSError:
        return Path.home() / "Desktop"


def main() -> int:
    if sys.platform != "win32":
        print("该脚本只用于 Windows。")
        return 1

    script = ROOT / "scripts" / "start_desktop.py"
    if not script.exists():
        print(f"找不到启动脚本：{script}")
        return 1

    windowed_python = Path(sys.executable).with_name("pythonw.exe")
    interpreter = str(windowed_python) if windowed_python.exists() else sys.executable
    content = TEMPLATE.format(root=ROOT, python=interpreter, script=script)
    destination = desktop_directory() / LAUNCHER_NAME
    # 用 CRLF 写：cmd.exe 对纯 LF 的批处理偶尔会解析异常。
    destination.write_text(content, encoding="utf-8", newline="\r\n")

    print(f"启动器已创建：{destination}")
    print(f"  解释器：{sys.executable}")
    print(f"  项目目录：{ROOT}")
    print("双击即可打开独立桌面客户端。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
