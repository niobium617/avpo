"""统一环境入口 —— CLI 与 Web 工作台共用的根路径 / .env / 数据目录解析。

CWD 无关：所有路径由本文件位置推导（app/core/env.py → 项目根）。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"


def load_project_env() -> None:
    """加载项目根 .env（不存在则跳过），把渠道 key/base_url 注入环境变量。"""
    env_path = PROJECT_ROOT / ".env"
    if env_path.is_file():
        load_dotenv(env_path)


def resolve_data_dir() -> Path:
    """数据目录：环境变量 AVPO_DATA 优先（测试/自定义），否则项目根 data/。"""
    return Path(os.environ.get("AVPO_DATA") or DEFAULT_DATA_DIR)
