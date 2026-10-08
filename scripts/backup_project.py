"""Create a verified local backup; usable from Windows Task Scheduler."""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path[:0] = [str(ROOT / "src"), str(ROOT / ".localdeps")]

from support_agent.config import get_settings  # noqa: E402
from support_agent.services.backups import create_backup  # noqa: E402

if __name__ == "__main__":
    print(json.dumps(create_backup(get_settings()), ensure_ascii=False))
