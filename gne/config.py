"""Configuration loading for the extraction engine."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

CONFIG_FILENAME = ".gne"


def read_config(path: str | os.PathLike[str] = CONFIG_FILENAME) -> dict[str, Any]:
    """Load the optional YAML configuration file from ``path``."""
    config_path = Path(path)
    if not config_path.exists():
        return {}
    with config_path.open(encoding="utf-8") as config_file:
        data = yaml.safe_load(config_file)
    return data if isinstance(data, dict) else {}


# 配置在模块导入时读取一次，与原实现的加载时机保持一致。
config = read_config()
