"""TTS 提供商协议（IMPLEMENTATION_PLAN 2.2）。

输入 narration → 输出 mp3 + word 时间戳（毫秒级）。
edge-tts / 豆包等实现共用此协议 —— 2.1 实测不合格时直接换实现，不改调用方。

TTSWord / TTSResult 是管线内的临时值，不落盘；
落盘的配音 Asset 与字幕见 app.core.schema。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class TTSWord:
    """一个词/字的边界。ms 为整数（100ns 单位事件四舍五入而来）。"""

    text: str
    start_ms: int
    end_ms: int


@dataclass
class TTSResult:
    """一次合成的产物：mp3 文件 + 词级时间戳 + 实际音频时长。"""

    mp3: Path
    words: list[TTSWord] = field(default_factory=list)
    duration_ms: int = 0   # mutagen 读实际 mp3（与 M0 配音时长同源）


class TTSProvider(Protocol):
    """TTS 提供商接口：async 合成（edge-tts 原生 async，其余实现自适配）。"""

    async def synth(self, text: str, out_mp3: Path) -> TTSResult:
        """合成 text 到 out_mp3（含时间戳）。失败必须抛异常，不返回空结果。"""
        ...
