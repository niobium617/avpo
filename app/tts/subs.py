"""字幕聚合（IMPLEMENTATION_PLAN 2.3）。

把 TTS 词级时间戳聚合成字幕行，规则（IMPLEMENTATION_PLAN §5）：

- 按句末标点（。！？）断句 —— 出现即断。
- 超 max_chars（默认 18 字，去空白计）时在最近的软标点（，；、…）处断；
  段内无软标点则在达到上限的那个词后断。
- 所有断点都在词边界上 → 永不截断词。
- 最后一段不足 min_tail_ms（默认 500ms）并入前段。

不变式（验收断言依据）：
- 字幕时间不重叠（后段 start ≥ 前段 end）。
- 去空白后，字幕文本拼接 == 词文本拼接（覆盖全文）。
  TTS 词事件不含标点时（edge-tts 实测如此），先 reattach_punctuation 回贴再断言。
"""

import re
from dataclasses import dataclass

from app.core.schema import Subtitle
from app.tts.base import TTSWord

HARD_PUNCT = "。！？"      # 句末标点 → 出现即断
SOFT_PUNCT = "，；、…"     # 软断点：段过长时优先在此断
DEFAULT_MAX_CHARS = 18
DEFAULT_MIN_TAIL_MS = 500


@dataclass
class Segment:
    """一段字幕：词列表 + 起止毫秒。"""

    words: list[TTSWord]

    @property
    def start_ms(self) -> int:
        return self.words[0].start_ms

    @property
    def end_ms(self) -> int:
        return self.words[-1].end_ms

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    @property
    def text(self) -> str:
        """词文本拼接 + 空白归一化（英文词可能自带空格）。"""
        joined = "".join(w.text for w in self.words)
        return re.sub(r"\s+", " ", joined).strip()


def _char_len(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def _is_content(ch: str) -> bool:
    """内容字符：中文/字母/数字。标点（含中文标点）与空白不算。"""
    return ch.isalnum() or ("一" <= ch <= "鿿")


def reattach_punctuation(words: list[TTSWord], narration: str) -> list[TTSWord]:
    """把 narration 中位于词间的标点/空白回贴到前一词尾部。

    edge-tts 的 WordBoundary 文本不含标点（实测，见 tests/edge_tts_log.md 词数 < 字数），
    回贴后「按 。！？ 断句」的聚合规则才可用，字幕文本也与文案逐字对齐。
    词内容与文案内容不一致（增删改字）时抛 ValueError。
    """
    content_positions = [i for i, ch in enumerate(narration) if _is_content(ch)]
    target = "".join(narration[i] for i in content_positions)
    joined = "".join(ch for w in words for ch in w.text if _is_content(ch))
    if joined != target:
        raise ValueError(f"词流与文案不一致（文案 {len(target)} 内容字，词流 {len(joined)}）")

    out: list[TTSWord] = []
    target_cursor = 0
    narr_cursor = 0
    for w in words:
        content = "".join(ch for ch in w.text if _is_content(ch))
        if not content:
            continue                              # 纯标点词：丢弃（标点已回贴）
        idx = target.find(content, target_cursor)
        if idx == -1:
            raise ValueError(f"词 {content!r} 无法对齐到文案")
        start = content_positions[idx]
        end = content_positions[idx + len(content) - 1]
        if out:
            out[-1].text += narration[narr_cursor:start]   # 词间标点/空白回贴到前词
        out.append(TTSWord(text=w.text, start_ms=w.start_ms, end_ms=w.end_ms))
        narr_cursor = end + 1
        target_cursor = idx + len(content)
    if out:
        out[-1].text += narration[narr_cursor:]           # 尾标点回贴到末词
    return out


def aggregate(
    words: list[TTSWord],
    max_chars: int = DEFAULT_MAX_CHARS,
    min_tail_ms: int = DEFAULT_MIN_TAIL_MS,
) -> list[Segment]:
    """词流 → 字幕段。断点永远落在词边界。"""
    if not words:
        return []

    segments: list[Segment] = []
    current: list[TTSWord] = []

    for word in words:
        current.append(word)
        joined = "".join(w.text for w in current)
        if any(p in word.text for p in HARD_PUNCT):
            segments.append(Segment(current))
            current = []
        elif _char_len(joined) >= max_chars:
            last_soft = _find_last_soft(current)
            if last_soft >= 0:
                # 在最近的软标点处断：软标点留在本段尾部
                segments.append(Segment(current[: last_soft + 1]))
                current = current[last_soft + 1:]
            else:
                segments.append(Segment(current))
                current = []

    if current:
        segments.append(Segment(current))

    # 尾段过短并入前段
    if len(segments) >= 2 and segments[-1].duration_ms < min_tail_ms:
        merged = Segment(segments[-2].words + segments[-1].words)
        segments[-2:] = [merged]

    return segments


def _find_last_soft(words: list[TTSWord]) -> int:
    for i in range(len(words) - 1, -1, -1):
        if any(p in words[i].text for p in SOFT_PUNCT):
            return i
    return -1


def build_subtitles(
    words: list[TTSWord],
    scene_id: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    min_tail_ms: int = DEFAULT_MIN_TAIL_MS,
) -> list[Subtitle]:
    """聚合并绑定 scene_id，产出可落盘的字幕行（app.core.schema.Subtitle）。"""
    return [
        Subtitle(scene_id=scene_id, start_ms=seg.start_ms, end_ms=seg.end_ms, text=seg.text)
        for seg in aggregate(words, max_chars=max_chars, min_tail_ms=min_tail_ms)
    ]
