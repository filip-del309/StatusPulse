import os
from pathlib import Path
from typing import Any

import yaml


def load_yaml_config() -> dict[str, Any]:
    default_path = Path(__file__).resolve().parents[2] / "config.yaml"
    config_path = Path(os.getenv("CONFIG_PATH", str(default_path))).expanduser()

    with config_path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)

    if not isinstance(data, dict):
        raise ValueError(f"Configuration file must contain a YAML mapping: {config_path}")

    return data
