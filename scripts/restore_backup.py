"""Verify and restore a backup to a new folder, leaving the active system untouched."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / ".localdeps")]

from support_agent.services.backups import restore_backup  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    restored = restore_backup(args.backup, args.output)
    print(f"恢复并校验完成：{restored}")
    print("原系统未覆盖；切换到恢复库前须停止服务并调整 DATABASE_URL。")
