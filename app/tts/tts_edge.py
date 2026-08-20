"""edge-tts 实现（钉版 7.2.8，20 条稳定性实测见 tests/edge_tts_log.md）。

WordBoundary 事件 offset/duration 单位为 100ns → 除以 10000 得毫秒。
"""

from pathlib import Path

import edge_tts
from mutagen.mp3 import MP3

from app.core.schema import TTSConfig
from app.tts.base import TTSProvider, TTSResult, TTSWord


class EdgeTTS:
    """edge-tts 7.2.8 实现：微软在线语音，免费，中文音色 zh-CN-YunxiNeural。"""

    def __init__(self, config: TTSConfig | None = None):
        self.config = config or TTSConfig()

    async def synth(self, text: str, out_mp3: Path) -> TTSResult:
        out_mp3 = Path(out_mp3)
        out_mp3.parent.mkdir(parents=True, exist_ok=True)

        words: list[TTSWord] = []
        # boundary 必须显式指定：7.2.8 默认 SentenceBoundary（只有整句事件，无词级时间戳）
        communicate = edge_tts.Communicate(
            text, self.config.voice, rate=self.config.rate, boundary="WordBoundary"
        )
        with open(out_mp3, "wb") as f:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    start = chunk["offset"] / 10000
                    end = (chunk["offset"] + chunk["duration"]) / 10000
                    words.append(
                        TTSWord(text=chunk["text"], start_ms=round(start), end_ms=round(end))
                    )

        if out_mp3.stat().st_size == 0:
            raise RuntimeError("edge-tts 未产出音频（0 字节），可能网络/服务异常")
        if not words:
            raise RuntimeError("edge-tts 无 WordBoundary 事件（词级时间戳缺失）")

        duration_ms = round(MP3(out_mp3).info.length * 1000)
        return TTSResult(mp3=out_mp3, words=words, duration_ms=duration_ms)
