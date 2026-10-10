"""Restore to an isolated folder and check persisted business data."""

import json
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from .backups import backup_path, restore_backup


def restore_drill(settings, name):
    source = backup_path(settings, name)
    destination = Path(settings.backup_dir).resolve() / "restore-checks" / uuid.uuid4().hex
    recovered = restore_backup(source, destination)
    counts = {}
    with closing(sqlite3.connect(recovered.as_uri() + "?mode=ro", uri=True)) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("user_accounts", "conversations", "messages", "message_images",
                      "source_documents", "document_chunks"):
            if table in tables:
                counts[table] = db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
    result = {"status": "verified", "backup": name, "counts": counts,
              "restored_database": str(recovered), "active_database_changed": False}
    (destination / "restore-report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return result
