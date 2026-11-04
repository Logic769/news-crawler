"""Configuration loading for the extraction engine."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

LOG = logging.getLogger("gne.config")
CONFIG_FILENAME = ".gne"


def read_config(path: str | os.PathLike[str] = CONFIG_FILENAME) -> dict[str, Any]:
    """Load the optional YAML configuration file from ``path``."""
    config_path = Path(path)
    # 配置文件是可选的：不存在、不可读、路径是目录或 YAML 语法错误都不能让
    # 导入 gne 直接失败（旧实现会在 import 时抛异常，任何 CWD 里有坏 .gne 的
    # 进程都会起不来）。这里统一降级为「没有配置」并留下警告。
    try:
        if not config_path.exists():
            return {}
        with config_path.open(encoding="utf-8") as config_file:
            data = yaml.safe_load(config_file)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        LOG.warning("读取 GNE 配置失败，已忽略 %s：%s", config_path, exc)
        return {}
    return data if isinstance(data, dict) else {}


# 配置在模块导入时读取一次，与原实现的加载时机保持一致。
config = read_config()
