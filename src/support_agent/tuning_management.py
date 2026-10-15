"""Local, reversible migration of legacy algorithm values out of .env."""

import os
import re
import uuid
from pathlib import Path

from dotenv import dotenv_values

from .config import Settings
from .tuning import BASE_CONFIG, TuningParameters, read_tuning


def migrate_env(env_file: Path, config_file: Path) -> dict:
    names = {key.upper() for key in TuningParameters.model_fields}
    if any(key in os.environ for key in names | {"AGENT_EXPERIMENT_FILE", "AGENT_CONFIG_FILE"}):
        raise ValueError("请先取消本进程的调参环境变量和实验覆盖，再迁移 .env")
    raw = dotenv_values(env_file, interpolate=False)
    if raw.get("AGENT_EXPERIMENT_FILE"):
        raise ValueError("请先取消 .env 中的实验覆盖，再迁移")
    if raw.get("AGENT_CONFIG_FILE"):
        selected = Path(raw["AGENT_CONFIG_FILE"]).expanduser().resolve()
        if selected != config_file.resolve():
            raise ValueError("--config 必须对应 .env 中的 AGENT_CONFIG_FILE")
    keys = names & raw.keys()
    if not keys:
        return {"migrated": [], "config_file": str(config_file.resolve())}
    before = Settings(
        _env_file=env_file,
        agent_config_file=str(config_file.resolve()) if config_file.exists() else None,
    ).tuning_snapshot()["parameters"]
    values = read_tuning(BASE_CONFIG)
    if config_file.exists():
        values.update(read_tuning(config_file))
    values.update({key.lower(): raw[key] for key in keys})
    values = TuningParameters.model_validate(values).model_dump()
    text = BASE_CONFIG.read_text("utf-8")
    for key, value in values.items():
        text = re.sub(
            rf"(?m)^({key}\s*=\s*)[^#\r\n]+",
            lambda match, value=value: match[1] + str(value) + "  ",
            text,
        )
    text = "\n".join(line.rstrip() for line in text.splitlines()) + "\n"
    original_env = env_file.read_bytes()
    original_config = config_file.read_bytes() if config_file.exists() else None
    lines = original_env.decode("utf-8-sig").splitlines(keepends=True)
    pattern = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
    retained = "".join(
        line for line in lines if not ((match := pattern.match(line)) and match[1] in keys)
    )
    backup = env_file.parent / ".desktop" / ("tuning-migration-" + uuid.uuid4().hex)
    backup.mkdir(parents=True)
    (backup / ".env.bak").write_bytes(original_env)
    if original_config is not None:
        (backup / "agent.toml.bak").write_bytes(original_config)
    config_file.parent.mkdir(parents=True, exist_ok=True)

    def replace(path, data):
        staged = path.with_name(path.name + ".migration-partial")
        staged.write_bytes(data)
        staged.replace(path)

    try:
        replace(config_file, text.encode("utf-8"))
        replace(env_file, retained.encode("utf-8"))
        after = Settings(
            _env_file=env_file, agent_config_file=str(config_file.resolve())
        ).tuning_snapshot()["parameters"]
        if before != after:
            raise ValueError("迁移前后有效参数不一致，已回滚")
    except Exception:
        replace(env_file, original_env)
        if original_config is not None:
            replace(config_file, original_config)
        else:
            config_file.unlink(missing_ok=True)
        raise
    return {
        "migrated": sorted(key.lower() for key in keys),
        "backup_dir": str(backup.resolve()),
        "config_file": str(config_file.resolve()),
    }
