"""Startup/daily backups; prune only verified automatic archives we created."""

import asyncio
import json
import logging
import time
import zipfile
from pathlib import Path

from .backups import create_backup, list_backups
from .file_lock import file_lock

logger = logging.getLogger(__name__)


def backup_state(settings):
    try:
        return json.loads((Path(settings.backup_dir) / "automatic-state.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def automatic_backup(settings, *, force=False):
    if not settings.automatic_backup_enabled:
        return {"status": "disabled"}
    folder = Path(settings.backup_dir).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    try:
        with file_lock(folder / "automatic.lock"):
            previous = backup_state(settings)
            last = previous.get("last_success", 0)
            if not force and time.time() - last < settings.automatic_backup_interval_hours * 3600:
                return {**previous, "status": "not_due"}
            try:
                result = create_backup(settings, kind="automatic")
                state = {"status": "ok", "last_success": time.time(), "backup": result["name"]}
                automatic = []
                for item in list_backups(settings):
                    path = folder / item["name"]
                    try:
                        with zipfile.ZipFile(path) as archive:
                            if json.loads(archive.read("manifest.json")).get("kind") == "automatic":
                                automatic.append(path)
                    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
                        continue
                automatic.sort(key=lambda p: p.stat().st_mtime_ns, reverse=True)
                for path in automatic[max(1, settings.automatic_backup_keep):]:
                    path.unlink()  # Whitelisted archive in the verified backup folder only.
            except Exception as exc:
                state = {**previous, "status": "failed", "error": str(exc)[:300]}
                logger.exception("Automatic backup failed")
            temporary = folder / "automatic-state.partial"
            temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            temporary.replace(folder / "automatic-state.json")
            return state
    except OSError:
        return {"status": "busy"}


async def backup_loop(settings):
    while True:
        await asyncio.to_thread(automatic_backup, settings)
        await asyncio.sleep(300)
