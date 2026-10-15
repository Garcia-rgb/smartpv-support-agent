"""Show effective non-secret parameters or migrate legacy .env algorithm values."""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / ".localdeps")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--experiment", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--migrate-env", action="store_true")
    args = parser.parse_args()
    config = args.config.resolve() if args.config else None
    experiment = args.experiment.resolve() if args.experiment else None
    os.chdir(Path(os.environ.get("SUPPORT_AGENT_DATA_DIR", ROOT)))
    from support_agent.config import Settings
    from support_agent.tuning_management import migrate_env

    if args.migrate_env:
        if experiment:
            parser.error("迁移时不能指定实验配置")
        print(json.dumps(migrate_env(Path(".env"), config or Path("config/agent.toml")),
                         ensure_ascii=False, indent=2))
    overrides = {}
    if config:
        overrides["agent_config_file"] = str(config)
    if experiment:
        overrides["agent_experiment_file"] = str(experiment)
    snapshot = Settings(**overrides).tuning_snapshot()
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
