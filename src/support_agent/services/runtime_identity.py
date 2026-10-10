import hashlib
from pathlib import Path

from sqlalchemy.engine import make_url


def runtime_identity(settings):
    url = make_url(settings.database_url)
    value = str(Path(url.database).resolve()) if url.get_backend_name() == "sqlite" else (
        str(url.render_as_string(hide_password=True))
    )
    return hashlib.sha256(value.encode()).hexdigest()[:16]
