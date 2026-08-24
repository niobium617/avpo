"""M5 结构化进度事件：pipeline 节点内的进度回调载荷。

进度回调签名升级为 Callable[[ProgressEvent], None] | None（默认 None 向后兼容，
CLI 不传仍工作）。percent 为节点内完成度 0.0~1.0，None 表示不定进度。
"""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class ProgressEvent:
    node: str                       # 节点名（PIPELINE_NODES 之一）
    message: str                    # 阶段文字（UI 直接展示）
    percent: float | None = None    # 节点内 0.0~1.0；None = 不定进度


ProgressCallback = Callable[[ProgressEvent], None]
